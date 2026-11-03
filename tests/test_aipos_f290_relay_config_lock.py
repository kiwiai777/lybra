"""AIPOS-F290 验收③④ — credential refusal (missing / too-open / bad keys / symlink) without leaking values, HOME-derived
default paths, and the per-channel single-instance lock (a second probe refuses to start and never connects).
Temporary HOME / config dir / probe root only; the real ~/.config/lybra/relay and ~/.lybra/relay are never touched.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from relay import config  # noqa: E402
from relay.texts import TEXTS, resolve_lang  # noqa: E402
from relay_probe_fixtures import FakeWecomServer  # noqa: E402

BOT_ID = "aib-lock-bot"
SECRET = "fixture-secret-Hn3Rt6Yu8Io0"   # fixture value; asserted absent from every output


def _env(home: Path, lang: str = "zh") -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env.update(HOME=str(home), LYBRA_LANG=lang, PYTHONPATH=str(REPO_ROOT))
    return env


def _probe(home: Path, *args: str, lang: str = "zh") -> subprocess.CompletedProcess:
    home.mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-m", "relay.probe", *args], cwd=REPO_ROOT, env=_env(home, lang),
                          capture_output=True, text=True, timeout=60)


def _cred(config_dir: Path, text: str, mode: int = 0o600) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "wecom.env"
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


def test_default_paths_derive_from_home():
    env = {"HOME": "/srv/someone"}
    assert config.default_config_dir(env) == Path("/srv/someone/.config/lybra/relay")
    assert config.default_probe_root(env) == Path("/srv/someone/.lybra/relay/probe")
    with pytest.raises(config.ConfigError) as exc:
        config.default_config_dir({})
    assert exc.value.code == "no_home"


def test_missing_credentials_refuse_with_path_in_default_location(tmp_path):
    home = tmp_path / "home"
    proc = _probe(home, "run", "--channel", "wecom", "--max-seconds", "2")
    assert proc.returncode == 2
    assert f"拒启动: 凭据文件不存在: {home}/.config/lybra/relay/wecom.env" in proc.stderr
    assert not (home / ".lybra").exists()          # refused before creating any probe directory


def test_too_open_mode_refuses_and_never_prints_value(tmp_path):
    cfg = tmp_path / "cfg"
    path = _cred(cfg, f"WECOM_BOT_ID={BOT_ID}\nWECOM_BOT_SECRET={SECRET}\n", mode=0o644)
    proc = _probe(tmp_path / "home", "run", "--channel", "wecom", "--config-dir", str(cfg),
                  "--probe-root", str(tmp_path / "probe"), "--max-seconds", "2")
    assert proc.returncode == 2
    assert "权限为 644, 过宽" in proc.stderr and f"chmod 600 {path}" in proc.stderr
    assert SECRET not in proc.stdout + proc.stderr
    check = _probe(tmp_path / "home", "check", "--channel", "wecom", "--config-dir", str(cfg), lang="en")
    assert check.returncode == 2 and "has mode 644, too open" in check.stderr


@pytest.mark.parametrize("text, needle", [
    (f"WECOM_BOT_ID={BOT_ID}\n", "缺键或值为空: WECOM_BOT_SECRET"),
    (f"WECOM_BOT_ID={BOT_ID}\nWECOM_BOT_SECRET=\n", "缺键或值为空: WECOM_BOT_SECRET"),
    (f"WECOM_BOT_ID={BOT_ID}\nWECOM_BOT_SECRET={SECRET}\nWECOM_TYPO=1\n", "未声明的键: WECOM_TYPO"),
    (f"WECOM_BOT_ID={BOT_ID}\n{SECRET}\n", "第 2 行不是 KEY=VALUE"),
])
def test_bad_credential_content_refuses_without_value(tmp_path, text, needle):
    cfg = tmp_path / "cfg"
    _cred(cfg, text)
    proc = _probe(tmp_path / "home", "check", "--channel", "wecom", "--config-dir", str(cfg))
    assert proc.returncode == 2, proc.stdout
    assert needle in proc.stderr
    assert SECRET not in proc.stdout + proc.stderr


def test_symlinked_credentials_refused(tmp_path):
    real = _cred(tmp_path / "elsewhere", f"WECOM_BOT_ID={BOT_ID}\nWECOM_BOT_SECRET={SECRET}\n")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "wecom.env").symlink_to(real)
    proc = _probe(tmp_path / "home", "check", "--channel", "wecom", "--config-dir", str(cfg))
    assert proc.returncode == 2 and "不是普通文件" in proc.stderr


def test_check_ok_lists_key_names_only(tmp_path):
    cfg = tmp_path / "cfg"
    _cred(cfg, f"# comment\nexport WECOM_BOT_ID=\"{BOT_ID}\"\nWECOM_BOT_SECRET='{SECRET}'\n")
    proc = _probe(tmp_path / "home", "check", "--channel", "wecom", "--config-dir", str(cfg))
    assert proc.returncode == 0, proc.stderr
    assert "WECOM_BOT_ID, WECOM_BOT_SECRET" in proc.stdout
    assert SECRET not in proc.stdout and BOT_ID not in proc.stdout
    assert config.load_credentials(cfg, config.channel_spec("wecom"))["WECOM_BOT_SECRET"] == SECRET


def test_second_instance_refuses_while_first_is_connected(tmp_path):
    cfg = tmp_path / "cfg"
    _cred(cfg, f"WECOM_BOT_ID={BOT_ID}\nWECOM_BOT_SECRET={SECRET}\n")
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "probe"
    with FakeWecomServer(BOT_ID, SECRET, []) as server:
        first = subprocess.Popen([sys.executable, "-m", "relay.probe", "run", "--channel", "wecom", "--config-dir", str(cfg),
                                  "--probe-root", str(root), "--ws-url", server.url, "--max-seconds", "60"],
                                 cwd=REPO_ROOT, env=_env(home), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        subscribed = threading.Event()
        lines: list[str] = []

        def reader() -> None:
            for line in first.stdout:
                lines.append(line)
                if "订阅成功" in line:
                    subscribed.set()

        threading.Thread(target=reader, daemon=True).start()
        try:
            assert subscribed.wait(20), "".join(lines)
            second = _probe(home, "run", "--channel", "wecom", "--config-dir", str(cfg), "--probe-root", str(root),
                            "--ws-url", server.url, "--max-seconds", "5")
            assert second.returncode == 3, second.stdout + second.stderr
            assert "本渠道已有探针在运行" in second.stderr and f"进程 {first.pid}" in second.stderr
            assert server.connections == 1            # the refused instance never connected
        finally:
            first.terminate()
            rc = first.wait(timeout=20)
    assert rc == 0, "".join(lines) + first.stderr.read()
    assert SECRET not in "".join(lines)
    # lock released by the kernel when the first exits: a fresh run can take it
    with FakeWecomServer(BOT_ID, SECRET, []) as server2:
        third = _probe(home, "run", "--channel", "wecom", "--config-dir", str(cfg), "--probe-root", str(root),
                       "--ws-url", server2.url, "--max-seconds", "2")
    assert third.returncode == 0, third.stdout + third.stderr


def test_texts_bilingual_and_language_resolution():
    assert set(TEXTS["zh"]) == set(TEXTS["en"])
    assert resolve_lang(None, {}) == "zh"
    assert resolve_lang(None, {"LYBRA_LANG": "en"}) == "en"
    assert resolve_lang("zh", {"LYBRA_LANG": "en"}) == "zh"
    for table in TEXTS.values():
        assert not any("{secret" in v.lower() or "{token" in v.lower() for v in table.values())
