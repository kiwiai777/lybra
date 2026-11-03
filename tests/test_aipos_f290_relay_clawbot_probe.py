"""AIPOS-F290 件③ — Weixin ClawBot (iLink Bot API) QR login + receive-only probe against a local fake server.

Protocol per Tencent's channel plugin github.com/Tencent/openclaw-weixin docs/protocol_zh_CN.md. Covers: login writes
the issued token to a 600 credential file without printing it; run calls only getupdates / notifystart / notifystop /
CDN download (never sendmessage / sendtyping / getconfig / getuploadurl); the four Owner samples are stored as the raw
getupdates response text (uint64 message ids intact); attachments decrypted (AES-128-ECB) with sha256; the cursor is
persisted; summarize output. Temporary HOME / config dir / probe root only.
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
from relay.channels import clawbot  # noqa: E402
from relay_probe_fixtures import FakeIlinkServer  # noqa: E402

TOKEN = "fixture-bot-tkn-8sKq2LmZ0pXv"   # fixture value; asserted absent from outputs and probe files
IMG = b"\x89PNG\r\n\x1a\n" + b"clawbot-screenshot" * 30
IMG2 = b"\xff\xd8\xff\xe0" + b"single-photo" * 40
XLSX = b"PK\x03\x04" + b"spreadsheet-body" * 60
BIG_ID = 7428394839201938475   # > 2**53: must survive byte-for-byte in the raw record
FORBIDDEN = ("/ilink/bot/sendmessage", "/ilink/bot/sendtyping", "/ilink/bot/getconfig", "/ilink/bot/getuploadurl")


def _msg(mid: int, items: list[dict], ctx: str = "ctx-token-fixture") -> dict:
    return {"seq": mid % 1000, "message_id": mid, "from_user_id": "owner@im.wechat", "to_user_id": "bot@im.bot",
            "create_time_ms": 1762156800000, "session_id": "sess-1", "message_type": 1, "message_state": 2,
            "item_list": items, "context_token": ctx}


@pytest.fixture()
def rig(tmp_path: Path):
    k_img, k_img2, k_xlsx = os.urandom(16), os.urandom(16), os.urandom(16)
    cdn = {"EQP-IMG": aes.ilink_encrypt(k_img, IMG), "EQP-IMG2": aes.ilink_encrypt(k_img2, IMG2),
           "EQP-XLSX": aes.ilink_encrypt(k_xlsx, XLSX)}
    merged = _msg(BIG_ID, [
        {"type": 1, "text_item": {"text": "[聊天记录] 客户群: 合同第三条要改成按月结算, 请确认"}},
        {"type": 2, "image_item": {"aeskey": k_img.hex(),
                                   "media": {"encrypt_query_param": "EQP-IMG",
                                             "aes_key": base64.b64encode(k_img.hex().encode()).decode(), "encrypt_type": 1}}},
    ])
    seq = [_msg(BIG_ID + 1, [{"type": 1, "text_item": {"text": "逐条转发第一条"}}]),
           _msg(BIG_ID + 2, [{"type": 1, "text_item": {"text": "逐条转发第二条"},
                              "ref_msg": {"title": "引用摘要", "message_item": {"type": 1, "text_item": {"text": "原话"}}}}])]
    single = _msg(BIG_ID + 3, [{"type": 2, "image_item": {"media": {"encrypt_query_param": "EQP-IMG2",
                                                                    "aes_key": base64.b64encode(k_img2).decode()}}}])
    yield {"tmp": tmp_path, "cdn": cdn, "k_xlsx": k_xlsx, "merged": merged, "seq": seq, "single": single,
           "home": tmp_path / "home", "config_dir": tmp_path / "cfg", "root": tmp_path / "probe"}


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env.update(HOME=str(home), LYBRA_LANG="zh", PYTHONPATH=str(REPO_ROOT))
    return env


def _probe(rig: dict, *args: str, timeout: float = 60.0, stdin: str | None = None) -> subprocess.CompletedProcess:
    rig["home"].mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-m", "relay.probe", *args], cwd=REPO_ROOT, env=_env(rig["home"]),
                          capture_output=True, text=True, timeout=timeout, input=stdin)


def _write_cred(rig: dict, base: str) -> None:
    rig["config_dir"].mkdir(mode=0o700, exist_ok=True)
    cred = rig["config_dir"] / "clawbot.env"
    cred.write_text(f"CLAWBOT_BOT_TOKEN={TOKEN}\nCLAWBOT_BASE_URL={base}\nCLAWBOT_CDN_BASE_URL={base}/c2c\n", encoding="utf-8")
    os.chmod(cred, 0o600)


def test_login_saves_token_privately_without_printing_it(rig):
    statuses = [{"status": "wait"}, {"status": "scaned"},
                {"status": "confirmed", "bot_token": TOKEN, "ilink_bot_id": "bot-1@im.bot", "ilink_user_id": "owner@im.wechat"}]
    with FakeIlinkServer(TOKEN, [], {}, qr_statuses=statuses) as server:
        proc = _probe(rig, "login", "--channel", "clawbot", "--config-dir", str(rig["config_dir"]),
                      "--probe-root", str(rig["root"]), "--api-base", server.base)
        again = _probe(rig, "login", "--channel", "clawbot", "--config-dir", str(rig["config_dir"]),
                       "--probe-root", str(rig["root"]), "--api-base", server.base)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "https://liteapp.weixin.qq.com/q/fixture?qrcode=QR-FIXTURE-1" in proc.stdout
    assert "已扫码" in proc.stdout and "登录成功" in proc.stdout
    assert TOKEN not in proc.stdout + proc.stderr
    cred = rig["config_dir"] / "clawbot.env"
    assert stat.S_IMODE(cred.stat().st_mode) == 0o600
    assert stat.S_IMODE(rig["config_dir"].stat().st_mode) == 0o700
    values = dict(line.split("=", 1) for line in cred.read_text(encoding="utf-8").splitlines())
    assert values["CLAWBOT_BOT_TOKEN"] == TOKEN and values["CLAWBOT_USER_ID"] == "owner@im.wechat"
    assert ("POST", "/ilink/bot/get_bot_qrcode") in server.calls
    assert again.returncode == 2 and "--force" in again.stderr     # no silent overwrite


def test_probe_records_samples_raw_and_never_sends(rig):
    with FakeIlinkServer(TOKEN, [], rig["cdn"]) as server:
        file_msg = _msg(BIG_ID + 4, [{"type": 4, "file_item": {
            "file_name": "排期表.xlsx", "len": str(len(XLSX)),
            "media": {"full_url": server.base + "/c2c/download?encrypted_query_param=EQP-XLSX",
                      "aes_key": base64.b64encode(rig["k_xlsx"]).decode()}}}])
        batch1 = json.dumps({"ret": 0, "msgs": [rig["merged"], *rig["seq"]], "get_updates_buf": "BUF-1",
                             "longpolling_timeout_ms": 1000}, ensure_ascii=False)
        batch2 = json.dumps({"ret": 0, "msgs": [rig["single"], file_msg], "get_updates_buf": "BUF-2",
                             "longpolling_timeout_ms": 1000}, ensure_ascii=False)
        server.updates = [batch1, batch2]
        _write_cred(rig, server.base)
        proc = _probe(rig, "run", "--channel", "clawbot", "--config-dir", str(rig["config_dir"]),
                      "--probe-root", str(rig["root"]), "--max-seconds", "5")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    paths = [p for _m, p in server.calls]
    assert not set(paths) & set(FORBIDDEN), paths
    assert set(paths) <= {"/ilink/bot/getupdates", "/ilink/bot/msg/notifystart", "/ilink/bot/msg/notifystop", "/c2c/download"}
    assert paths[0] == "/ilink/bot/msg/notifystart" and paths.count("/ilink/bot/msg/notifystop") == 1
    assert set(server.auth_headers) == {f"Bearer {TOKEN}"}
    assert server.update_bodies[0]["get_updates_buf"] == "" and server.update_bodies[1]["get_updates_buf"] == "BUF-1"
    assert server.update_bodies[0]["base_info"]["bot_agent"].startswith("LybraRelayProbe/")

    day = [p for p in (rig["root"] / "clawbot").iterdir() if p.is_dir()][0]
    frames = [json.loads(line) for line in (day / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [f["raw"] for f in frames] == [batch1, batch2]       # raw response text, only non-empty batches
    assert str(BIG_ID) in frames[0]["raw"]
    state = json.loads((rig["root"] / "clawbot" / "state.json").read_text(encoding="utf-8"))
    assert state == {"get_updates_buf": "BUF-2"}

    atts = {a["path"] + "|" + a["msgid"]: a for a in
            (json.loads(line) for line in (day / "attachments.jsonl").read_text(encoding="utf-8").splitlines())}
    assert len(atts) == 3, atts
    expect = {f"item_list[1].image_item.media|{BIG_ID}": IMG, f"item_list[0].image_item.media|{BIG_ID + 3}": IMG2,
              f"item_list[0].file_item.media|{BIG_ID + 4}": XLSX}
    for key, plain in expect.items():
        rec = atts[key]
        assert rec["decrypt"] == "ok", rec
        assert rec["decrypted_sha256"] == hashlib.sha256(plain).hexdigest()
        assert (day / "attachments" / rec["decrypted_file"]).read_bytes() == plain
    assert atts[f"item_list[0].file_item.media|{BIG_ID + 4}"]["declared_name"] == "排期表.xlsx"

    assert TOKEN not in proc.stdout + proc.stderr
    for f in (p for p in rig["root"].rglob("*") if p.is_file()):
        assert TOKEN.encode() not in f.read_bytes(), f
        assert stat.S_IMODE(f.stat().st_mode) == 0o600

    summary = _probe(rig, "summarize", "--channel", "clawbot", "--probe-root", str(rig["root"]))
    assert summary.returncode == 0, summary.stderr
    out = summary.stdout
    assert "text+image" in out
    assert "[聊天记录] 客户群: 合同第三条要改成…" in out
    assert "请确认" not in out
    assert "排期表.xlsx" in out
    assert "图片附件 2 个, 下载并解密成功 2 个" in out
    assert "item_list[].ref_msg.title" in out      # title-like field reported
    q4 = [line for line in out.splitlines() if "群名 / 标题类字段" in line][0]
    assert "file_name" not in q4                    # attachment names belong to question 3, not group/title
    assert "#1.1 " in out and f"msgid={BIG_ID + 2}" in out   # batch members get distinct refs


def test_session_expired_exits_with_relogin_hint(rig):
    with FakeIlinkServer(TOKEN, [json.dumps({"ret": -14, "errcode": -14, "errmsg": "session timeout"})], {}) as server:
        _write_cred(rig, server.base)
        proc = _probe(rig, "run", "--channel", "clawbot", "--config-dir", str(rig["config_dir"]),
                      "--probe-root", str(rig["root"]), "--max-seconds", "10", "--no-lifecycle-notify")
    assert proc.returncode == 6, proc.stdout + proc.stderr
    assert "login --channel clawbot --force" in proc.stdout
    assert [p for _m, p in server.calls] == ["/ilink/bot/getupdates"]


def test_media_key_forms_and_client_version():
    raw = os.urandom(16)
    assert clawbot.decode_media_key(raw.hex(), None) == raw
    assert clawbot.decode_media_key(None, base64.b64encode(raw).decode()) == raw
    assert clawbot.decode_media_key(None, base64.b64encode(raw.hex().encode()).decode().rstrip("=")) == raw
    assert clawbot.decode_media_key(None, None) is None
    assert clawbot.client_version("1.0.11") == str(0x0001000B)
    assert set(clawbot.ALLOWED_ENDPOINTS).isdisjoint({p.lstrip("/") for p in FORBIDDEN})
