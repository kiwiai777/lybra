"""AIPOS-F113: 远程接入(enroll_deliver --ssh)不在远端命令行暴露注册码与 Owner 凭据、参数正确转义、回滚 fail-closed。

靶场(全部在 tmp_path 内, 禁连接任何真实主机):
  - 假 ssh(PATH 最前的 `ssh` 替身, 与 AIPOS-F110 tests/fake_ssh.py 等价): 记录本机交给 ssh 的 argv 与 stdin, 再按 OpenSSH 语义
    (远端命令 = 余下参数以空格拼接, 交远端登录 shell 解释)用 /bin/sh -c 在靶场 cwd 内"远端执行"。
  - 假远端 python3(只在假远端 PATH 上): 记录远端实际收到的 argv 与 stdin, 按命令回 enroll/distribute 的 JSON(或按开关失败)。
  - 门替身: 注入 enroll_deliver.enroll_deliver 的门客户端(AIPOS-F151: 签码 = lybra_enroll_code_dry_run → _confirm, 回滚 =
    lybra_roles_enroll_revoke), 记录调用(不碰任何真实门); CLI 层(`lybra roles enroll-deliver`)替换 confirm_client.GateClient。
  - 本机治理根(AIPOS-F151 件③ 签码前覆盖信封预检读它): tmp 下已建项目 + 覆盖 executor 的生效 PreAuthorized 信封。
所有凭据/注册码均为假值; 断言只比对假值是否出现, 测试输出不打印明文。

四类靶场:
  ① 凭据不上远端命令行: 远端 argv 不含注册码与 Owner 凭据明文; 码经 stdin 逐字节送达; Owner 凭据只在本机 HTTP 层(门替身)出现
  ② 远端命令正确转义: 含空格/单双引号/$()/反引号的路径与实例名逐字节到达远端 argv, 远端 shell 不执行注入
  ③ 回滚 fail-closed: 远端失败 → 经门吊销码; 吊销也失败 → EnrollDeliverRollbackError / CLI 非零退出, 消息含手工清理出口, 无吞错
  ④ 本机 CLI(AIPOS-F151 起 = `lybra roles enroll-deliver`)输出与日志只出现指纹: stdout/stderr(成功与失败)不含明文; 门凭据只经
    connection.json 入; 旧模块入口已收为该子命令(只打印等价命令, 不签码不 ssh)
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
    """门替身: 记录调用; 签码两阶段返回假自包含码; 吊销按开关成功/失败。"""

    calls: list[tuple[str, dict]] = []
    tokens_seen: list[str] = []
    revoke_mode = "ok"  # ok | gate_reject | raise

    def __init__(self, base_url: str, token: str, *, timeout: float | None = None) -> None:
        FakeGateClient.tokens_seen.append(token)
        self._token = token

    @property
    def token_fingerprint(self) -> str:
        return secret_fingerprint(self._token)

    def redaction_needles(self) -> list[str]:  # 与 confirm_client.GateClient 同接口(AIPOS-F151)
        return [self._token]

    def call_tool(self, name: str, arguments: dict, *, timeout: float | None = None) -> dict:
        FakeGateClient.calls.append((name, dict(arguments)))
        if name == "lybra_enroll_code_dry_run":
            return {"ok": True, "dry_run_token": "enrolldr_f113fixture",
                    "preview": {"role": arguments.get("role"), "instance": arguments.get("instance"), "ttl_seconds": 86400,
                                "governance_root": arguments.get("governance_root"), "gate_url": "http://gate.invalid:7118"}}
        if name == "lybra_enroll_code_confirm":
            return {"ok": True, "code_id": "enroll_f113fake", "self_contained_code": FAKE_CODE, "fingerprint": "sha256:fakecodefp00",
                    "gate_url": "http://gate.invalid:7118", "expires_at": "2999-01-01T00:00:00Z"}
        if name == "lybra_roles_enroll_revoke":
            if FakeGateClient.revoke_mode == "raise":
                raise RuntimeError("gate unreachable during rollback (fixture); bearer was " + self._token)
            if FakeGateClient.revoke_mode == "gate_reject":
                return {"ok": False, "errors": [{"message": "fixture reject"}]}
            return {"ok": True, "revoked": {"code_id": arguments.get("code_id")}}
        raise AssertionError(f"unexpected gate tool {name}")


ISSUE_VERBS = ["lybra_enroll_code_dry_run", "lybra_enroll_code_confirm"]


def _governance_root(tmp: Path) -> Path:
    """本机已建治理根(project.json + 队列根)+ 覆盖 executor 的生效 PreAuthorized 信封(AIPOS-F151 件③ 签码前预检读)。"""
    from tools.aipos_cli.autonomy_policy import policies_dir

    gov = tmp / "home" / "f113"
    (gov / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    (gov / "project.json").write_text(json.dumps({"project": "f113", "config_version": 1}), encoding="utf-8")
    pdir = policies_dir(gov)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "pol_fixture.md").write_text(
        "---\nrecord_type: owner_autonomy_policy\npolicy_id: pol_fixture\nmode: PreAuthorized\nstatus: active\n"
        "approved_by_owner: true\nowner_approval_ref: dec_f113\nactive_from: '2020-01-01T00:00:00Z'\n"
        "expires_at: '2099-01-01T00:00:00Z'\nagent_or_role: executor\ntask_selector_task_mode: ''\n"
        "task_selector_project: f113\ntask_selector_task_ids: []\nmax_tasks: 50\n---\n# Owner Autonomy Policy: pol_fixture\n",
        encoding="utf-8")
    return gov


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
    gov = _governance_root(tmp_path)

    class Rig:
        tmp = tmp_path
        cwd = remote_cwd
        governance_root = gov

        @staticmethod
        def ssh_log() -> list[dict]:
            return _read_jsonl(tmp_path / "ssh.jsonl")

        @staticmethod
        def remote_log() -> list[dict]:
            return _read_jsonl(tmp_path / "remote.jsonl")

    return Rig


def _deliver(rig, **overrides):
    """AIPOS-F151: 唯一实现 enroll_deliver.enroll_deliver(--confirm 阶段); 门客户端 = 替身(持假门凭据)。"""
    kwargs = dict(client=FakeGateClient("http://gate.invalid:7118", FAKE_OWNER_TOKEN), role="executor", instance="exec.f113.fake",
                  governance_root=str(rig.governance_root), workspace="/remote/ws", harness_root="/remote/harness",
                  ssh_target="tester@fake-host", gate_url="http://gate.invalid:7118", owner_authorization_ref="pol_fixture",
                  policy="pol_fixture", confirm=True)
    kwargs.update(overrides)
    return enroll_deliver.enroll_deliver(**kwargs)


def _show(label: str, obj) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=1) if not isinstance(obj, str) else obj
    # 靶场原文输出前自检: 假凭据/假码明文永不打印(只打印指纹)
    for secret in (FAKE_OWNER_TOKEN, FAKE_CODE):
        text = text.replace(secret, f"«redacted:{secret_fingerprint(secret)}»")
    print(f"[F113 {label}] {text}")


# ── ① 凭据不上远端命令行 ──────────────────────────────────────────────────────────────

def test_item1_no_code_or_owner_token_in_any_argv_code_via_stdin(rig):
    result = _deliver(rig)
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
    # 门凭据只在本机门调用层(HTTP Authorization)出现: 签码走门两阶段动词(发码唯一实现在门内; AIPOS-F151)
    assert FakeGateClient.tokens_seen == [FAKE_OWNER_TOKEN]
    assert [name for name, _ in FakeGateClient.calls] == ISSUE_VERBS
    assert FakeGateClient.calls[0][1]["owner_authorization_ref"] == "pol_fixture"
    assert FakeGateClient.calls[0][1]["governance_root"] == str(rig.governance_root.resolve())
    # 结果只含指纹
    dumped = json.dumps(result, ensure_ascii=False)
    assert FAKE_CODE not in dumped and FAKE_OWNER_TOKEN not in dumped
    assert result["enrollment"]["fingerprint"] == "sha256:fakecodefp00"
    assert result["credential_fingerprint"] == secret_fingerprint(FAKE_OWNER_TOKEN)


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
    result = _deliver(rig, workspace=ws, harness_root=hr, instance=inst, policy=f"pol {SPECIAL}", force=True)
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
            _deliver(rig, ssh_target=bad)
    assert FakeGateClient.calls == [] and rig.ssh_log() == []


# ── ③ 回滚 fail-closed ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stage", ["enroll", "distribute"])
def test_item3_remote_failure_revokes_and_raises_redacted(rig, monkeypatch, stage):
    monkeypatch.setenv("F113_REMOTE_FAIL", stage)
    with pytest.raises(RuntimeError) as excinfo:
        _deliver(rig)
    msg = str(excinfo.value)
    _show(f"③{stage}-失败-回滚成功", msg)
    assert not isinstance(excinfo.value, enroll_deliver.EnrollDeliverRollbackError)
    assert [name for name, _ in FakeGateClient.calls] == ISSUE_VERBS + ["lybra_roles_enroll_revoke"]
    assert FakeGateClient.calls[2][1]["code_id"] == "enroll_f113fake"
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
        _deliver(rig)
    msg = str(excinfo.value)
    _show(f"③回滚失败-{revoke_mode}", msg)
    assert "回滚失败" in msg and "enroll_f113fake" in msg
    assert "lybra_roles_enroll_revoke" in msg and "lybra roles enroll-revoke enroll_f113fake" in msg
    assert "远端手工清理" in msg and ".lybra/connection.json" in msg and "exec.f113.fake" in msg
    assert FAKE_CODE not in msg and FAKE_OWNER_TOKEN not in msg


def test_item3_local_mode_rollback_failure_is_loud(rig, monkeypatch):
    """同机模式同一回滚纪律: 本进程 enroll 失败 + 门吊销失败 → EnrollDeliverRollbackError(出口含手工吊销命令; 无明文)。"""
    def boom_enroll(**kwargs):
        raise RuntimeError("exchange failed, code=" + kwargs["code"])

    monkeypatch.setattr(enroll_client, "enroll", boom_enroll)
    FakeGateClient.revoke_mode = "raise"
    with pytest.raises(enroll_deliver.EnrollDeliverRollbackError) as excinfo:
        _deliver(rig, ssh_target=None, workspace=str(rig.tmp / "ws"))
    msg = str(excinfo.value)
    _show("③同机回滚失败", msg)
    assert [name for name, _ in FakeGateClient.calls] == ISSUE_VERBS + ["lybra_roles_enroll_revoke"]
    assert "回滚失败" in msg and "lybra roles enroll-revoke enroll_f113fake" in msg and ".lybra/connection.json" in msg
    assert FAKE_CODE not in msg and FAKE_OWNER_TOKEN not in msg


# ── ④ 本机 CLI 输出/日志只出现指纹 ────────────────────────────────────────────────────

def _cli(monkeypatch, capsys, rig, extra: list[str]) -> tuple[int, str, str]:
    """`lybra roles enroll-deliver --ssh`(AIPOS-F151 唯一入口); 门凭据从 --connection-json 读(不上 argv), 门 = 替身。"""
    from tools.aipos_cli import aipos_cli, confirm_client

    conn = rig.tmp / "owner-connection.json"
    conn.write_text(json.dumps({"mcp": {"rpc_url": "http://gate.invalid:7118/mcp"},
                                "tokens": [{"role": "owner", "token": FAKE_OWNER_TOKEN}]}), encoding="utf-8")
    monkeypatch.setattr(confirm_client, "GateClient", FakeGateClient)
    argv = ["roles", "--workspace-root", str(rig.governance_root), "--connection-json", str(conn), "enroll-deliver",
            "--role", "executor", "--instance", "exec.f113.fake", "--governance-root", str(rig.governance_root),
            "--workspace", "/remote/ws", "--ssh", "tester@fake-host", "--owner-authorization-ref", "pol_fixture", *extra]
    try:
        rc = aipos_cli.main(argv)
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 1
    cap = capsys.readouterr()
    return int(rc or 0), cap.out, cap.err


def test_item4_cli_success_and_failure_output_only_fingerprints(rig, monkeypatch, capsys):
    rc, out, err = _cli(monkeypatch, capsys, rig, ["--confirm", "--json"])
    _show("④CLI成功-stdout", out)
    assert rc == 0, err
    assert FAKE_CODE not in out + err and FAKE_OWNER_TOKEN not in out + err
    assert json.loads(out)["credential_fingerprint"] == secret_fingerprint(FAKE_OWNER_TOKEN)
    rc, out, err = _cli(monkeypatch, capsys, rig, ["--confirm"])
    assert rc == 0 and "sha256:fakecodefp00" in out and FAKE_CODE not in out + err and FAKE_OWNER_TOKEN not in out + err

    # 回滚失败: 非零退出, stderr 含出口, 无明文
    monkeypatch.setenv("F113_REMOTE_FAIL", "enroll")
    FakeGateClient.revoke_mode = "raise"
    rc, out, err = _cli(monkeypatch, capsys, rig, ["--confirm"])
    _show("④CLI回滚失败-stderr", err)
    assert rc == 1 and "回滚失败" in err and "远端手工清理" in err
    assert FAKE_CODE not in out + err and FAKE_OWNER_TOKEN not in out + err


def test_item4_cli_owner_token_never_on_argv(monkeypatch, capsys, rig):
    # 新入口无任何凭据参数: --owner-token <明文> = argparse 拒(非零), 且不签码不 ssh
    rc, _out, _err = _cli(monkeypatch, capsys, rig, ["--owner-token", FAKE_OWNER_TOKEN])
    assert rc != 0
    assert FakeGateClient.calls == [] and rig.ssh_log() == []
    # 旧模块入口已收为 `lybra roles enroll-deliver`: 只打印等价命令(不含凭据)并退出 2, 不签码、不 ssh
    monkeypatch.setattr(sys, "stdin", io.StringIO(FAKE_OWNER_TOKEN + "\n"))
    rc = enroll_deliver.main(["--role", "executor", "--instance", "exec.f113.fake", "--target-workspace", "/remote/ws",
                              "--target-harness", "/remote/harness", "--gate-url", "http://gate.invalid:7118",
                              "--owner-policy-ref", "pol_fixture", "--ssh", "tester@fake-host", "--owner-token-stdin"])
    cap = capsys.readouterr()
    _show("④旧入口", cap.err)
    assert rc == 2 and "lybra roles enroll-deliver --role executor" in cap.err and "--confirm" in cap.err
    assert FAKE_OWNER_TOKEN not in cap.out + cap.err
    assert FakeGateClient.calls == [] and rig.ssh_log() == []


def test_item4_help_has_no_plaintext_owner_token_option():
    out = subprocess.run([sys.executable, "-m", "tools.aipos_cli.enroll_deliver", "--help"], capture_output=True, text=True,
                         cwd=str(REPO_ROOT)).stdout
    assert "--owner-token-stdin" in out and "--owner-token " not in out.replace("--owner-token-stdin", "")
