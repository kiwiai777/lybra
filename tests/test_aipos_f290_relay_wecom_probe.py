"""AIPOS-F290 件② — WeCom smart-bot long-connection probe against a local fake server (127.0.0.1 only).

Covers acceptance ②③⑤ for WeCom: handshake / subscribe / heartbeat / protocol pong; the four Owner samples
(merged forward with screenshot, item-by-item forward, single image, file — frames built from the official fields of
developer.work.weixin.qq.com/document/path/100719) stored byte-for-byte; attachments downloaded, decrypted
(AES-256-CBC) and recorded with sha256; summarize output; the probe never sends a business message; the secret never
appears in output or on disk; auth failure and disconnected_event stop without retry.
Every run uses a temporary HOME / config dir / probe root — never the real ~/.config/lybra/relay or ~/.lybra/relay.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from relay import aes  # noqa: E402
from relay_probe_fixtures import FakeWecomServer, MediaServer  # noqa: E402

BOT_ID = "aib-fixture-bot"
SECRET = "fixture-secret-Q7w9ZpL2xK4m"   # fixture value; asserted absent from every output and file
PNG = b"\x89PNG\r\n\x1a\n" + b"screenshot-bytes-" * 40
PNG2 = b"\x89PNG\r\n\x1a\n" + b"single-image-" * 33
JPG = b"\xff\xd8\xff\xe0" + b"seq-forward-photo" * 21
PDF = b"%PDF-1.7\n" + b"requirement document body " * 50
BUSINESS_CMDS = ("aibot_respond_msg", "aibot_respond_welcome_msg", "aibot_respond_update_msg", "aibot_send_msg",
                 "aibot_upload_media_init", "aibot_upload_media_chunk", "aibot_upload_media_finish")


def _key() -> tuple[bytes, str]:
    raw = os.urandom(32)
    return raw, base64.b64encode(raw).decode("ascii").rstrip("=")  # lenient padding, as the SDK notes


def _callback(req_id: str, body: dict) -> str:
    return json.dumps({"cmd": "aibot_msg_callback", "headers": {"req_id": req_id}, "body": body}, ensure_ascii=False)


def _base(msgid: str, msgtype: str) -> dict:
    return {"msgid": msgid, "aibotid": BOT_ID, "chattype": "single", "from": {"userid": "owner-userid"},
            "response_url": "https://example.invalid/response", "msgtype": msgtype}


@pytest.fixture()
def rig(tmp_path: Path):
    k1, a1 = _key()
    k2, a2 = _key()
    k3, a3 = _key()
    k4, a4 = _key()
    files = {
        "/media/merge-shot": (aes.wecom_encrypt(k1, PNG), None),
        "/media/seq-photo": (aes.wecom_encrypt(k2, JPG), None),
        "/media/single": (aes.wecom_encrypt(k3, PNG2), None),
        "/media/doc": (aes.wecom_encrypt(k4, PDF), "attachment; filename*=UTF-8''%E9%9C%80%E6%B1%82%E8%AF%B4%E6%98%8E.pdf"),
    }
    with MediaServer(files) as media:
        merged = _base("m-merge-1", "mixed")
        merged["mixed"] = {"msg_item": [
            {"msgtype": "text", "text": {"content": "【聊天记录】客户群: 这个功能下周能不能上线, 我们老板在催"}},
            {"msgtype": "image", "image": {"url": media.base + "/media/merge-shot", "aeskey": a1}},
            {"msgtype": "text", "text": {"content": "截图里是报错页面"}},
        ]}
        seq1 = _base("m-seq-1", "text")
        seq1["text"] = {"content": "客户甲: 第一条逐条转发的消息"}
        seq2 = _base("m-seq-2", "text")
        seq2["text"] = {"content": "客户乙: 第二条"}
        seq2["quote"] = {"msgtype": "text", "text": {"content": "被引用的原话"}}
        seq3 = _base("m-seq-3", "image")
        seq3["image"] = {"url": media.base + "/media/seq-photo", "aeskey": a2}
        single = _base("m-image-1", "image")
        single["image"] = {"url": media.base + "/media/single", "aeskey": a3}
        doc = _base("m-file-1", "file")
        doc["file"] = {"url": media.base + "/media/doc", "aeskey": a4}
        enter = json.dumps({"cmd": "aibot_event_callback", "headers": {"req_id": "ev-0"},
                            "body": {"msgid": "ev-enter", "aibotid": BOT_ID, "from": {"userid": "owner-userid"},
                                     "chattype": "single", "event": {"eventtype": "enter_chat"}}}, ensure_ascii=False)
        pushes = [_callback("cb-1", merged), enter, _callback("cb-2", seq1), _callback("cb-3", seq2),
                  _callback("cb-4", seq3), _callback("cb-5", single), _callback("cb-6", doc)]
        home = tmp_path / "home"
        config_dir = tmp_path / "cfg"
        config_dir.mkdir(mode=0o700)
        cred = config_dir / "wecom.env"
        cred.write_text(f"# fixture\nWECOM_BOT_ID={BOT_ID}\nWECOM_BOT_SECRET={SECRET}\n", encoding="utf-8")
        os.chmod(cred, 0o600)
        yield {"media": media, "pushes": pushes, "home": home, "config_dir": config_dir, "root": tmp_path / "probe",
               "plain": {"merge-shot": PNG, "seq-photo": JPG, "single": PNG2, "doc": PDF}}


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env.update(HOME=str(home), LYBRA_LANG="zh", PYTHONPATH=str(REPO_ROOT))
    return env


def _probe(rig: dict, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    rig["home"].mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-m", "relay.probe", *args], cwd=REPO_ROOT, env=_env(rig["home"]),
                          capture_output=True, text=True, timeout=timeout)


def _run(rig: dict, url: str, seconds: float = 6.0) -> subprocess.CompletedProcess:
    return _probe(rig, "run", "--channel", "wecom", "--config-dir", str(rig["config_dir"]), "--probe-root", str(rig["root"]),
                  "--ws-url", url, "--heartbeat", "1", "--max-seconds", str(seconds))


def _day_dir(root: Path) -> Path:
    days = [p for p in (root / "wecom").iterdir() if p.is_dir()]
    assert len(days) == 1, days
    return days[0]


def _all_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def test_probe_records_four_samples_raw_and_downloads_attachments(rig):
    with FakeWecomServer(BOT_ID, SECRET, rig["pushes"]) as server:
        proc = _run(rig, server.url)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # ①握手 / 订阅 / 心跳 / 协议 pong; ⑤只发协议帧, 不发任何业务消息
    cmds = server.cmds()
    assert cmds.count("aibot_subscribe") == 1, cmds
    assert cmds.count("ping") >= 2, cmds
    assert set(cmds) <= {"aibot_subscribe", "ping"}, cmds
    assert not set(cmds) & set(BUSINESS_CMDS)
    assert server.pongs == [b"srv-ping"]
    assert server.unmasked_client_frames == 0
    assert "订阅成功" in proc.stdout

    day = _day_dir(rig["root"])
    frames = [json.loads(line) for line in (day / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
    raws = [f["raw"] for f in frames]
    for pushed in rig["pushes"]:               # 原样落盘: 推送的每一帧文本逐字节出现在记录里
        assert raws.count(pushed) == 1, pushed[:60]
    assert all(f["kind"] == "ws_text" for f in frames)

    sent = [json.loads(line) for line in (day / "sent.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {s["cmd"] for s in sent} == {"aibot_subscribe", "ping"}
    assert all("body" not in s for s in sent)

    atts = [json.loads(line) for line in (day / "attachments.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(atts) == 4, atts
    by_url = {a["url"].rsplit("/", 1)[-1]: a for a in atts}
    for name, plain in rig["plain"].items():
        rec = by_url[name]
        assert rec["decrypt"] == "ok", rec
        assert rec["decrypted_sha256"] == hashlib.sha256(plain).hexdigest()
        assert (day / "attachments" / rec["decrypted_file"]).read_bytes() == plain
        enc = (day / "attachments" / rec["encrypted_file"]).read_bytes()
        assert rec["encrypted_sha256"] == hashlib.sha256(enc).hexdigest()
    assert by_url["doc"]["content_disposition_filename"] == "需求说明.pdf"
    assert by_url["merge-shot"]["path"] == "mixed.msg_item[1].image"
    assert by_url["single"]["sniffed_type"] == "png"

    # 权限: 目录 700 / 文件 600
    for p in [rig["root"], *rig["root"].rglob("*")]:
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode == (0o700 if p.is_dir() else 0o600), (p, oct(mode))

    # 凭据值不出现在任何输出与落盘文件里
    assert SECRET not in proc.stdout + proc.stderr
    for f in _all_files(rig["root"]):
        assert SECRET.encode() not in f.read_bytes(), f

    summary = _probe(rig, "summarize", "--channel", "wecom", "--probe-root", str(rig["root"]))
    assert summary.returncode == 0, summary.stderr
    out = summary.stdout
    assert "mixed [text, image, text]" in out
    assert "【聊天记录】客户群: 这个功能下周能不能…" in out   # 正文只显示前 20 字
    assert "老板在催" not in out
    assert "需求说明.pdf" in out
    assert "图片附件 3 个, 下载并解密成功 3 个" in out
    assert "quote:text" in out
    assert "mixed.msg_item[].image.aeskey" in out


def test_auth_failure_stops_without_retry(rig):
    with FakeWecomServer(BOT_ID, SECRET, rig["pushes"], scenario="auth_fail") as server:
        proc = _run(rig, server.url, seconds=10.0)
    assert proc.returncode == 4, proc.stdout + proc.stderr
    assert server.cmds() == ["aibot_subscribe"]
    assert server.connections == 1
    assert "订阅被拒(errcode=40058" in proc.stdout
    assert SECRET not in proc.stdout + proc.stderr


def test_disconnected_event_stops_without_reconnect(rig):
    with FakeWecomServer(BOT_ID, SECRET, [], scenario="replace") as server:
        proc = _run(rig, server.url, seconds=10.0)
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert server.connections == 1
    assert "disconnected_event" in proc.stdout
    assert set(server.cmds()) <= {"aibot_subscribe", "ping"}


def test_plain_ws_refused_for_non_loopback_host(rig):
    proc = _probe(rig, "run", "--channel", "wecom", "--config-dir", str(rig["config_dir"]), "--probe-root", str(rig["root"]),
                  "--ws-url", "ws://example.invalid:9", "--max-seconds", "3")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "拒绝连接" in proc.stdout + proc.stderr
