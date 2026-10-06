"""AIPOS-F113: 远程接入(enroll_deliver --ssh)不在远端命令行暴露注册码与 Owner 凭据、参数正确转义、回滚 fail-closed。

靶场(全部在 tmp_path 内, 禁连接任何真实主机):
  - 假 ssh(PATH 最前的 `ssh` 替身, 与 AIPOS-F110 tests/fake_ssh.py 等价): 记录本机交给 ssh 的 argv 与 stdin, 再按 OpenSSH 语义
    (远端命令 = 余下参数以空格拼接, 交远端登录 shell 解释)用 /bin/sh -c 在靶场 cwd 内"远端执行"。
  - 假远端 python3(只在假远端 PATH 上): 记录远端实际收到的 argv 与 stdin, 按命令回 enroll/distribute 的 JSON(或按开关失败)。
  - 门替身: 替换 enroll_deliver.GateClient, 记录 lybra_roles_enroll_code / lybra_roles_enroll_revoke 调用(不碰任何真实门)。
所有凭据/注册码均为假值; 断言只比对假值是否出现, 测试输出不打印明文。

四类靶场:
  ① 凭据不上远端命令行: 远端 argv 不含注册码与 Owner 凭据明文; 码经 stdin 逐字节送达; Owner 凭据只在本机 HTTP 层(门替身)出现
  ② 远端命令正确转义: 含空格/单双引号/$()/反引号的路径与实例名逐字节到达远端 argv, 远端 shell 不执行注入
  ③ 回滚 fail-closed: 远端失败 → 经门吊销码; 吊销也失败 → EnrollDeliverRollbackError / CLI 非零退出, 消息含手工清理出口, 无吞错
  ④ 本机 CLI 输出与日志只出现指纹: main() 的 stdout/stderr(成功与失败)不含明文; Owner 凭据只经 stdin / connection.json 入
"""
from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli import enroll_client, enroll_deliver  # noqa: E402
from tools.aipos_cli.service_mode import secret_fingerprint  # noqa: E402

FAKE_OWNER_TOKEN = "fake-owner-token-F113-not-a-secret-0123456789"
FAKE_CODE = "LYBRAENROLL1.fake-self-contained-code-F113-not-a-secret"
SPECIAL = "we ird 'q' \"dq\" $(touch PWNED_DOLLAR) `touch PWNED_TICK` ;touch PWNED_SEMI"

FAKE_SSH = r'''#!{python}
import json, os, subprocess, sys
args = sys.argv[1:]
data = sys.stdin.read()
with open(os.environ["F113_FAKE_SSH_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"argv": args, "stdin": data}}) + "\n")
if not args or args[0] != "--" or len(args) < 3:
    sys.stderr.write("fake ssh: expected `ssh -- <target> <command>`\n")
    sys.exit(255)
remote_cmd = " ".join(args[2:])  # OpenSSH: 余下参数以空格拼成一条命令串, 交远端登录 shell 解释
env = {{k: v for k, v in os.environ.items() if k.startswith("F113_")}}
env["PATH"] = os.environ["F113_REMOTE_BIN"] + ":/usr/bin:/bin"
proc = subprocess.run(["/bin/sh", "-c", remote_cmd], input=data, text=True, capture_output=True,
                      cwd=os.environ["F113_REMOTE_CWD"], env=env)
sys.stdout.write(proc.stdout)
sys.stderr.write(proc.stderr)
sys.exit(proc.returncode)
'''

FAKE_REMOTE_PYTHON = r'''#!{python}
import json, os, sys
argv = sys.argv[1:]
data = sys.stdin.read()
with open(os.environ["F113_REMOTE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"argv": argv, "stdin": data}}) + "\n")
fail = os.environ.get("F113_REMOTE_FAIL", "")
if "tools.aipos_cli.enroll_client" in argv:
    if fail == "enroll":
        # 远端出错时把收到的 stdin 原样回显到 stderr —— 验证本机侧对远端输出脱敏
        sys.stderr.write("remote enroll exploded; stdin was: " + data.strip() + "\n")
        sys.exit(1)
    print(json.dumps({{"ok": True, "fingerprint": "sha256:fakeroletok", "scopes": ["executor"]}}))
elif "tools.distribute_tools" in argv:
    if fail == "distribute":
        sys.stderr.write("remote distribute exploded\n")
        sys.exit(1)
    print(json.dumps({{"ok": True, "distributed": ["skills/lybra-executor"], "errors": []}}))
else:
    sys.stderr.write("unexpected remote command\n")
    sys.exit(3)
'''


class FakeGateClient:
    """门替身: 记录调用; 发码返回假自包含码; 吊销按开关成功/失败。"""

    calls: list[tuple[str, dict]] = []
    tokens_seen: list[str] = []
    revoke_mode = "ok"  # ok | gate_reject | raise

    def __init__(self, base_url: str, token: str, *, timeout: float | None = None) -> None:
        FakeGateClient.tokens_seen.append(token)
        self._token = token

    @property
    def token_fingerprint(self) -> str:
        return secret_fingerprint(self._token)

    def call_tool(self, name: str, arguments: dict, *, timeout: float | None = None) -> dict:
        FakeGateClient.calls.append((name, dict(arguments)))
        if name == "lybra_roles_enroll_code":
            return {"ok": True, "code_id": "enroll_f113fake", "self_contained_code": FAKE_CODE,
                    "enrollment": {"code_id": "enroll_f113fake", "fingerprint": "sha256:fakecodefp00"}}
        if name == "lybra_roles_enroll_revoke":
            if FakeGateClient.revoke_mode == "raise":
                raise RuntimeError("gate unreachable during rollback (fixture); bearer was " + self._token)
            if FakeGateClient.revoke_mode == "gate_reject":
                return {"ok": False, "errors": [{"message": "fixture reject"}]}
            return {"ok": True, "revoked": {"code_id": arguments.get("code_id")}}
        raise AssertionError(f"unexpected gate tool {name}")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    local_bin = tmp_path / "local-bin"
    remote_bin = tmp_path / "remote-bin"
    remote_cwd = tmp_path / "remote-cwd"
    for d in (local_bin, remote_bin, remote_cwd):
        d.mkdir()
    ssh = local_bin / "ssh"
    ssh.write_text(FAKE_SSH.format(python=sys.executable), encoding="utf-8")
    ssh.chmod(0o755)
    py = remote_bin / "python3"
    py.write_text(FAKE_REMOTE_PYTHON.format(python=sys.executable), encoding="utf-8")
    py.chmod(0o755)
    monkeypatch.setenv("PATH", f"{local_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("F113_FAKE_SSH_LOG", str(tmp_path / "ssh.jsonl"))
    monkeypatch.setenv("F113_REMOTE_LOG", str(tmp_path / "remote.jsonl"))
    monkeypatch.setenv("F113_REMOTE_BIN", str(remote_bin))
    monkeypatch.setenv("F113_REMOTE_CWD", str(remote_cwd))
    monkeypatch.delenv("F113_REMOTE_FAIL", raising=False)
    # 禁连接任何真实主机: ssh 必须解析到靶场替身
    assert shutil.which("ssh") == str(ssh), shutil.which("ssh")
    FakeGateClient.calls = []
    FakeGateClient.tokens_seen = []
    FakeGateClient.revoke_mode = "ok"
    monkeypatch.setattr(enroll_deliver, "GateClient", FakeGateClient)

    class Rig:
        tmp = tmp_path
        cwd = remote_cwd

        @staticmethod
        def ssh_log() -> list[dict]:
            return _read_jsonl(tmp_path / "ssh.jsonl")

        @staticmethod
        def remote_log() -> list[dict]:
            return _read_jsonl(tmp_path / "remote.jsonl")

    return Rig


def _deliver(**overrides):
    kwargs = dict(role="executor", instance="exec.f113.fake", workspace_root="/remote/ws", harness_root="/remote/harness",
                  ssh_target="tester@fake-host", gate_url="http://gate.invalid:7118", owner_policy_ref="pol_fixture",
                  owner_token=FAKE_OWNER_TOKEN)
    kwargs.update(overrides)
    return enroll_deliver.enroll_deliver_ssh(**kwargs)


def _show(label: str, obj) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=1) if not isinstance(obj, str) else obj
    # 靶场原文输出前自检: 假凭据/假码明文永不打印(只打印指纹)
    for secret in (FAKE_OWNER_TOKEN, FAKE_CODE):
        text = text.replace(secret, f"«redacted:{secret_fingerprint(secret)}»")
    print(f"[F113 {label}] {text}")


# ── ① 凭据不上远端命令行 ──────────────────────────────────────────────────────────────

def test_item1_no_code_or_owner_token_in_any_argv_code_via_stdin(rig):
    result = _deliver()
    ssh_calls = rig.ssh_log()
    remote_calls = rig.remote_log()
    _show("①ssh-argv", [c["argv"] for c in ssh_calls])
    _show("①remote-argv", [c["argv"] for c in remote_calls])
    assert result["ok"] is True and len(ssh_calls) == 2 and len(remote_calls) == 2
    for call in ssh_calls + remote_calls:
        joined = "\0".join(call["argv"])
        assert FAKE_CODE not in joined and FAKE_OWNER_TOKEN not in joined
        assert "--bootstrap-token" not in call["argv"] and "--code" not in call["argv"]
    enroll_call = remote_calls[0]
    assert enroll_call["argv"][:3] == ["-m", "tools.aipos_cli.enroll_client", "--code-stdin"]
    # stdin 正确送达: 码逐字节到达远端 enroll 的 stdin; distribute 的 stdin 为空; Owner 凭据从不出本机
    assert enroll_call["stdin"] == FAKE_CODE + "\n"
    assert remote_calls[1]["stdin"] == ""
    assert all(FAKE_OWNER_TOKEN not in c["stdin"] for c in ssh_calls + remote_calls)
    # Owner 凭据只在本机门调用层(HTTP Authorization)出现: 发码走门动词(发码唯一实现)
    assert FakeGateClient.tokens_seen == [FAKE_OWNER_TOKEN]
    assert [name for name, _ in FakeGateClient.calls] == ["lybra_roles_enroll_code"]
    assert FakeGateClient.calls[0][1]["owner_authorization_ref"] == "pol_fixture"
    # 结果只含指纹
    dumped = json.dumps(result, ensure_ascii=False)
    assert FAKE_CODE not in dumped and FAKE_OWNER_TOKEN not in dumped
    assert result["enrollment"]["fingerprint"] == "sha256:fakecodefp00"
    assert result["owner_token_fingerprint"] == secret_fingerprint(FAKE_OWNER_TOKEN)


def test_item1_enroll_client_code_stdin_reaches_enroll(monkeypatch, tmp_path, capsys):
    """远端侧: enroll_client --code-stdin 从 stdin 取码(不进 argv), 交 enroll(); 空 stdin / 与 --code 并用 → 拒。"""
    seen: dict = {}

    def fake_enroll(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "operation": "enroll", "fingerprint": "sha256:x", "scopes": []}

    monkeypatch.setattr(enroll_client, "enroll", fake_enroll)
    argv = ["enroll_client", "--code-stdin", "--gate-url", "http://gate.invalid:7118", "--workspace", str(tmp_path / "ws"), "--json"]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(sys, "stdin", io.StringIO(FAKE_CODE + "\n"))
    assert enroll_client.main() == 0
    assert seen["code"] == FAKE_CODE
    assert FAKE_CODE not in capsys.readouterr().out
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert enroll_client.main() == 2
    monkeypatch.setattr(sys, "argv", argv + ["--code", "x"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(FAKE_CODE + "\n"))
    assert enroll_client.main() == 2


def test_item1_source_no_secret_into_argv():
    """静态: enroll_deliver 不再把 token/code 放进远端命令行, CLI 不再收 --owner-token 明文参数。"""
    src = (REPO_ROOT / "tools" / "aipos_cli" / "enroll_deliver.py").read_text(encoding="utf-8")
    assert '"--bootstrap-token"' not in src
    assert '"--code", code' not in src
    assert '" ".join(' not in src
    assert '"--owner-token",' not in src and "add_argument(\"--owner-token\"" not in src
    assert "except:" not in src


# ── ② 远端命令正确转义 ────────────────────────────────────────────────────────────────

def test_item2_special_chars_arrive_byte_exact_and_no_injection(rig):
    ws = f"/remote/{SPECIAL}/ws"
    hr = f"/remote/{SPECIAL}/harness"
    inst = f"exec.{SPECIAL}"
    result = _deliver(workspace_root=ws, harness_root=hr, instance=inst, owner_policy_ref=f"pol {SPECIAL}", force=True)
    remote_calls = rig.remote_log()
    _show("②remote-argv", [c["argv"] for c in remote_calls])
    _show("②remote-cwd", sorted(p.name for p in rig.cwd.iterdir()))
    assert result["ok"] is True
    enroll_argv = remote_calls[0]["argv"]
    assert enroll_argv[enroll_argv.index("--workspace") + 1] == ws
    assert enroll_argv[enroll_argv.index("--policy") + 1] == f"pol {SPECIAL}"
    assert enroll_argv[enroll_argv.index("--landed-host") + 1] == "tester@fake-host"
    assert remote_calls[1]["argv"] == ["-m", "tools.distribute_tools", hr, "executor", "--json", "--force"]
    # 远端 shell 未执行任何注入($() / 反引号 / ;)
    assert not any(rig.cwd.iterdir()), sorted(p.name for p in rig.cwd.iterdir())
    # 每个参数单独 shlex.quote: 远端命令串经 POSIX shell 词法切分 = 原 argv
    ssh_call = rig.ssh_log()[0]["argv"]
    assert ssh_call[0] == "--" and ssh_call[1] == "tester@fake-host" and len(ssh_call) == 3
    assert shlex.split(ssh_call[2])[3:] == enroll_argv[2:]


def test_item2_ssh_target_option_injection_rejected_before_minting(rig):
    for bad in ("-oProxyCommand=touch PWNED", "user@host evil", ""):
        with pytest.raises(ValueError):
            _deliver(ssh_target=bad)
    assert FakeGateClient.calls == [] and rig.ssh_log() == []


# ── ③ 回滚 fail-closed ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stage", ["enroll", "distribute"])
def test_item3_remote_failure_revokes_and_raises_redacted(rig, monkeypatch, stage):
    monkeypatch.setenv("F113_REMOTE_FAIL", stage)
    with pytest.raises(RuntimeError) as excinfo:
        _deliver()
    msg = str(excinfo.value)
    _show(f"③{stage}-失败-回滚成功", msg)
    assert not isinstance(excinfo.value, enroll_deliver.EnrollDeliverRollbackError)
    assert [name for name, _ in FakeGateClient.calls] == ["lybra_roles_enroll_code", "lybra_roles_enroll_revoke"]
    assert FakeGateClient.calls[1][1]["code_id"] == "enroll_f113fake"
    assert "已回滚" in msg and "enroll_f113fake" in msg
    # 远端把码回显到 stderr 时本机侧脱敏为指纹
    assert FAKE_CODE not in msg and FAKE_OWNER_TOKEN not in msg
    if stage == "enroll":
        assert f"«redacted:{secret_fingerprint(FAKE_CODE)}»" in msg
    else:
        assert "远端 enroll 已成功" in msg and "tools.distribute_tools" in msg


@pytest.mark.parametrize("revoke_mode", ["raise", "gate_reject"])
def test_item3_rollback_failure_is_loud_with_exit(rig, monkeypatch, revoke_mode):
    monkeypatch.setenv("F113_REMOTE_FAIL", "enroll")
    FakeGateClient.revoke_mode = revoke_mode
    with pytest.raises(enroll_deliver.EnrollDeliverRollbackError) as excinfo:
        _deliver()
    msg = str(excinfo.value)
    _show(f"③回滚失败-{revoke_mode}", msg)
    assert "回滚失败" in msg and "enroll_f113fake" in msg
    assert "lybra_roles_enroll_revoke" in msg and "lybra roles enroll-revoke enroll_f113fake" in msg
    assert "远端手工清理" in msg and ".lybra/connection.json" in msg and "exec.f113.fake" in msg
    assert FAKE_CODE not in msg and FAKE_OWNER_TOKEN not in msg


def test_item3_local_mode_rollback_failure_is_loud(monkeypatch, tmp_path):
    """同机模式同一回滚纪律: 兑换失败 + 吊销失败 → EnrollDeliverRollbackError(原 except: pass 吞错退役)。"""
    from tools.aipos_cli import enrollment

    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(tmp_path / "gate-ws"))
    monkeypatch.setattr(enroll_deliver, "create_enrollment_code",
                        lambda **k: {"code": FAKE_CODE, "code_id": "enroll_localfake", "fingerprint": "sha256:fp"})

    def boom_exchange(*a, **k):
        raise RuntimeError("exchange failed, code=" + FAKE_CODE)

    def boom_revoke(*a, **k):
        raise ValueError("revoke store unwritable (fixture)")

    monkeypatch.setattr(enroll_deliver, "exchange_enrollment_code", boom_exchange)
    monkeypatch.setattr(enrollment, "revoke_enrollment_code", boom_revoke)
    with pytest.raises(enroll_deliver.EnrollDeliverRollbackError) as excinfo:
        enroll_deliver.enroll_deliver_local(role="executor", instance="exec.f113.local", workspace_root=tmp_path / "ws",
                                            harness_root=tmp_path / "harness", gate_url="http://127.0.0.1:1",
                                            owner_policy_ref="p", owner_token=FAKE_OWNER_TOKEN)
    msg = str(excinfo.value)
    _show("③同机回滚失败", msg)
    assert "回滚失败" in msg and "lybra roles enroll-revoke enroll_localfake" in msg
    assert FAKE_CODE not in msg and FAKE_OWNER_TOKEN not in msg


# ── ④ 本机 CLI 输出/日志只出现指纹 ────────────────────────────────────────────────────

def _cli(monkeypatch, capsys, extra: list[str], stdin_text: str) -> tuple[int, str, str]:
    argv = ["enroll_deliver", "--role", "executor", "--instance", "exec.f113.fake", "--target-workspace", "/remote/ws",
            "--target-harness", "/remote/harness", "--gate-url", "http://gate.invalid:7118",
            "--owner-policy-ref", "pol_fixture", "--ssh", "tester@fake-host", *extra]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    rc = enroll_deliver.main()
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_item4_cli_success_and_failure_output_only_fingerprints(rig, monkeypatch, capsys):
    rc, out, err = _cli(monkeypatch, capsys, ["--owner-token-stdin", "--json"], FAKE_OWNER_TOKEN + "\n")
    _show("④CLI成功-stdout", out)
    assert rc == 0 and FAKE_CODE not in out + err and FAKE_OWNER_TOKEN not in out + err
    assert json.loads(out)["owner_token_fingerprint"] == secret_fingerprint(FAKE_OWNER_TOKEN)
    rc, out, err = _cli(monkeypatch, capsys, ["--owner-token-stdin"], FAKE_OWNER_TOKEN + "\n")
    assert rc == 0 and "sha256:fakecodefp00" in out and FAKE_CODE not in out + err and FAKE_OWNER_TOKEN not in out + err

    # 回滚失败: 非零退出, stderr 含出口, 无明文
    monkeypatch.setenv("F113_REMOTE_FAIL", "enroll")
    FakeGateClient.revoke_mode = "raise"
    rc, out, err = _cli(monkeypatch, capsys, ["--owner-token-stdin"], FAKE_OWNER_TOKEN + "\n")
    _show("④CLI回滚失败-stderr", err)
    assert rc == 1 and "回滚失败" in err and "远端手工清理" in err
    assert FAKE_CODE not in out + err and FAKE_OWNER_TOKEN not in out + err


def test_item4_cli_owner_token_never_on_argv(monkeypatch, capsys, rig):
    # --owner-token <明文> 已退役: argparse 拒(非零), 且不发码不 ssh
    with pytest.raises(SystemExit) as excinfo:
        _cli(monkeypatch, capsys, ["--owner-token", FAKE_OWNER_TOKEN], "")
    assert excinfo.value.code != 0
    assert FakeGateClient.calls == [] and rig.ssh_log() == []
    # 空 stdin → 拒
    rc, out, err = _cli(monkeypatch, capsys, ["--owner-token-stdin"], "")
    assert rc == 1 and "stdin carried no token" in err
    # connection.json 读入路径(凭据走文件, 不走 argv)
    conn = rig.tmp / "owner-connection.json"
    conn.write_text(json.dumps({"tokens": [{"role": "owner", "token": FAKE_OWNER_TOKEN}]}), encoding="utf-8")
    rc, out, err = _cli(monkeypatch, capsys, ["--connection-json", str(conn), "--json"], "")
    assert rc == 0, err
    assert FakeGateClient.tokens_seen[-1] == FAKE_OWNER_TOKEN and FAKE_OWNER_TOKEN not in out + err


def test_item4_help_has_no_plaintext_owner_token_option():
    out = subprocess.run([sys.executable, "-m", "tools.aipos_cli.enroll_deliver", "--help"], capture_output=True, text=True,
                         cwd=str(REPO_ROOT)).stdout
    assert "--owner-token-stdin" in out and "--owner-token " not in out.replace("--owner-token-stdin", "")
