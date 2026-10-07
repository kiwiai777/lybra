"""AIPOS-F110 — 跨机拉起(chris 拓扑: 执行 pi 在 Mac, 门与治理根在 dev): remote transport 经 ssh 拉起并清理, 远端工位身份与开工材料。

靶场 = AIPOS-F95 同一靶场(F90 进程内门 + tmp 治理根/产品仓/HOME + 假 harness tests/fake_harness.py; 禁真门/真治理根/真工位/真 pi)
+ ssh 执行器替身 tests/fake_ssh.py(声明式: 以 `ssh` 之名放 PATH 首位, 产品按 verbs.schema lybra_loop.launch.remote.ssh_argv[0]
经 PATH 解析, 拉起/探测/清理/反向检查全部走同一 transport 代码路径; 「远端」= 本机另一目录, 远端登录 shell = sh -c)。
禁 ssh 到任何真实主机: 替身只认声明里的 host, 未声明一律 exit 255。

验收(卡面 ★件④/★验收):
 ① 拉起(remote 执行体 + local 审计体共用分层走到 completed)、kickoff 经 stdin 逐字节到达(含 ` $( ${ 换行)、就绪清理(远端组 TERM)
 ② 超时清理(远端组 TERM→KILL, 孙进程同清)、SIGHUP 清理(loop 收 SIGHUP → 经 ssh 清远端组后按信号退出)
 ③ ssh 不可达 → 不拉起, 退回手工并提示原因; material_access 未声明 / 含凭据 → 拒拉起并提示补声明
 ④ remote 与 local 同一 render_kickoff(本机开工提示与 F110 前逐字节相同); git grep 无第二拉起实现
 ⑤ 技能步骤命令过 argparse; check-workstation 双向可达检查(同一 ssh 代码路径)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 靶场唯一来源(禁第二份)
import test_aipos_f95_harness_launch as f95  # noqa: E402  — 拉起靶场唯一来源
from test_aipos_f78_engine_agnostic import DRIVER, EXEC, _write  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — pytest fixture(F95 拉起靶场)
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402

FAKE_SSH = REPO_ROOT / "tests" / "fake_ssh.py"
TASK, AUDIT = f90.TASK, f90.AUDIT
REMOTE_HOST = "mac-fake"     # 远端工位 ssh 目标(land 事件 host; 替身声明可达)
GATE_ALIAS = "gate-fake"     # 远端视角的门机别名(替身声明可达)
# 材料访问说明故意含 shell 危险字符(` $( ${), 证 kickoff 全程不经任何 shell 展开
MATERIAL = "经 ssh gate-fake 读写门机材料; 代码提交到卡分支并推回门机产品仓; 字面 `whoami` $(id) ${HOME} 不得被展开"


def _show(line: str) -> None:
    print(line, flush=True)


def setup_remote(r: SimpleNamespace, mp: pytest.MonkeyPatch, *, reachable: dict[str, bool] | None = None) -> SimpleNamespace:
    """ssh 替身上 PATH(以 `ssh` 之名, 包一层 sh 调本解释器跑 fake_ssh.py)+ 声明可达 host + 远端工位目录(无 .lybra/role)。"""
    bin_dir = r.home / "fake-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "ssh"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE_SSH} "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    mp.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    log = r.home / "fake-ssh.jsonl"
    decl = r.home / "fake-ssh.json"
    hosts = {REMOTE_HOST: True, GATE_ALIAS: True, **(reachable or {})}
    decl.write_text(json.dumps({"hosts": {h: {"reachable": ok} for h, ok in hosts.items()}, "log": str(log)}), encoding="utf-8")
    mp.setenv("FAKE_SSH_DECL", str(decl))
    remote_ws = r.home / "remote-mac" / "exec-ws"
    remote_ws.mkdir(parents=True, exist_ok=True)  # 远端工位: 门机读不到其 .lybra/role(此处故意不写), 身份 = land 事件实例
    return SimpleNamespace(ssh_log=log, ssh_decl=decl, remote_ws=remote_ws, bin_dir=bin_dir)


def _declare_material(gov: Path, instance: str = EXEC, material: str = MATERIAL) -> None:
    from tools.aipos_cli.workspace_config import set_project_workstation

    set_project_workstation(gov, instance, gate_ssh_alias=GATE_ALIAS, material_access=material)


def _ssh_calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.is_file() else []


def _remote_rig(lrig, monkeypatch, **kw) -> SimpleNamespace:  # noqa: F811
    extra = setup_remote(lrig, monkeypatch, **kw)
    for k, v in vars(extra).items():
        setattr(lrig, k, v)
    assert Path(subprocess.run(["sh", "-c", "command -v ssh"], capture_output=True, text=True).stdout.strip()) == lrig.bin_dir / "ssh"
    return lrig


# ===========================================================================
# ① 拉起 + kickoff 经 stdin 逐字节 + 就绪清理; ④ remote/local 同一 render_kickoff
# ===========================================================================

def test_item1_remote_launch_stdin_kickoff_ready_cleanup_and_single_render(lrig, monkeypatch):
    r = _remote_rig(lrig, monkeypatch)
    f90._card(r.gov, TASK, "pending", harness="pi")
    init_governance_repo(r.gov)
    _declare_material(r.gov)
    f95._land(r.gov, EXEC, "executor", r.remote_ws, host=REMOTE_HOST)       # 执行体: 跨机(远端目录无 .lybra/role)
    f95._land(r.gov, f95.AUDIT_CLAIMER, "auditor", r.ws_audit)              # 审计体: 本机(同一分层)
    monkeypatch.setenv("FAKE_EXEC_MODE", "linger")  # 写完产物不退 → 宽限期满经 ssh 终止远端组
    renders: list[tuple[str, dict | None, str, dict]] = []
    real_render = nr.render_kickoff

    def spy(next_card, *, remote=None):
        text = real_render(next_card, remote=remote)
        renders.append((str(next_card.get("task_id")), remote, text, dict(next_card)))
        return text

    monkeypatch.setattr(nr, "render_kickoff", spy)
    out = io.StringIO()
    res = run_loop(TASK, r.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05, max_wait=60, max_steps=30)
    text = out.getvalue()
    _show("---- ① 跨机拉起全链 lybra loop 输出原文 ----\n" + text + "------------------------------")
    assert res.exit_code == 0 and res.outcome == "completed", text
    launches = [s.launch for s in res.steps if s.launch and s.launch.get("launched")]
    assert [l["card"] for l in launches] == [TASK, AUDIT], launches
    remote, local = launches
    _show(f"[①] remote launch: {json.dumps(remote, ensure_ascii=False)}\n[①] local launch: {json.dumps(local, ensure_ascii=False)}")
    assert remote["transport"] == "remote" and remote["host"] == REMOTE_HOST and remote["workstation"] == str(r.remote_ws)
    assert remote["outcome"] == "artifact_ready" and remote["termination"] == "sigterm", remote
    assert local["transport"] == "local" and local["remote_pgid"] is None and local["outcome"] == "artifact_ready"
    # 远端组号回传 = 假 harness 自报的进程组; 清理后组内无进程
    pgid = int((r.log / f"{TASK}.pgid").read_text())
    assert remote["remote_pgid"] == pgid and not f95._group_alive(pgid)
    assert (r.log / f"{TASK}.cwd").read_text() == str(r.remote_ws)  # 远端在工位目录起
    assert re.search(rf"launch: pi @ {REMOTE_HOST}:{re.escape(str(r.remote_ws))} pid=\d+ pgid=\d+ remote_pgid={pgid}", text), text
    assert re.search(rf"\[pi {TASK} {REMOTE_HOST} pid=\d+\] 工具 read: AGENTS.md", text), text  # 远端事件流照常汇总

    # kickoff 经 stdin 逐字节: 远端 harness 收到的 argv[kickoff] == 产品渲染结果(含换行与 ` $( ${ 原样)
    received = (r.log / f"{TASK}.kickoff").read_bytes()
    remote_renders = [t for card, ctx, t, _nc in renders if card == TASK and ctx is not None]
    assert remote_renders and received == remote_renders[-1].encode("utf-8")
    for hazard in ("`whoami`", "$(id)", "${HOME}", "\n"):
        assert hazard in received.decode("utf-8"), hazard
    _show(f"[①] 远端收到的 kickoff({len(received)} bytes, 与 render_kickoff 逐字节相同):\n{received.decode('utf-8')}")
    calls = _ssh_calls(r.ssh_log)
    _show("[①] ssh 替身调用(host / 远端命令首行):\n" + "\n".join(f"  {c['host']}: {c['command'].splitlines()[0][:90]}" for c in calls))
    assert all("已认领任务卡" not in c["command"] and "whoami" not in c["command"] for c in calls), "kickoff 不得进 ssh 命令行"
    assert [c["host"] for c in calls] == [REMOTE_HOST] * len(calls)
    signals = [re.search(r"lybra-signal (\w+) (\d+)$", c["command"]) for c in calls]
    assert [(m.group(1), int(m.group(2))) for m in signals if m] == [("TERM", pgid), ("KILL", pgid)], calls
    assert sum("lybra-probe" in c["command"] for c in calls) == 1 and sum("lybra-launch" in c["command"] for c in calls) == 1

    # ④ kickoff 单源: remote 与 local 同一 render_kickoff; remote = 本机提示 + 门机材料段(本机部分逐字节相同)
    _card_id, ctx, _text, next_card = [x for x in renders if x[0] == TASK and x[1] is not None][-1]
    assert ctx == {"gate_host": socket.gethostname(), "governance_root": str(r.gov.resolve()), "workstation_host": REMOTE_HOST,
                   "gate_ssh_alias": GATE_ALIAS, "material_access": MATERIAL}, ctx
    assert not [x for x in renders if x[0] == TASK and x[1] is None], "跨机卡只经 my-tasks --remote-workstation 渲染一次"
    local_same_card = real_render(next_card)  # 同一 next_card 按本机口径(remote=None)渲染
    block = nr.kickoff_declaration()["remote_material"]
    for key, value in ctx.items():
        block = block.replace("{" + key + "}", value)
    card_line = re.search(r"^任务卡路径: .+$", local_same_card, re.M).group(0)
    assert received.decode("utf-8") == local_same_card.replace(card_line, card_line + block, 1)  # 本机部分逐字节相同 + 门机段
    assert any(x[0] == AUDIT and x[1] is None for x in renders), "本机审计体走同一 render_kickoff(remote=None)"
    audit_kickoff = (r.log / f"{AUDIT}.kickoff").read_text(encoding="utf-8")
    assert "门机" not in audit_kickoff and "material" not in audit_kickoff  # 本机工位提示不带门机材料段
    events = f95._events(r.gov, TASK)
    _show("[①] session record 拉起事件:\n" + "\n".join(events))
    assert any("transport=remote" in e and f"remote_pgid={pgid}" in e for e in events)
    assert any("outcome=artifact_ready" in e and "termination=sigterm" in e for e in events)


def test_item4_render_kickoff_local_bytes_unchanged_remote_block_fail_closed():
    card = {"task_id": "AIPOS-Z", "worktree_path": "/r/.worktrees/AIPOS-Z", "report_path": "/g/task_cards/AIPOS-Z/RETURN.md",
            "card_path": "/g/5_tasks/queue/claimed/aipos-z.md",
            "report_required_frontmatter": [{"key": "branch", "hint": "分支", "value": "card/AIPOS-Z"}]}
    local = nr.render_kickoff(card)
    pre_f110 = ("已认领任务卡 AIPOS-Z。\n\n工作树路径: /r/.worktrees/AIPOS-Z\n报告落点: /g/task_cards/AIPOS-Z/RETURN.md\n"
                "任务卡路径: /g/5_tasks/queue/claimed/aipos-z.md\n报告 frontmatter 必填(产品给出; 缺任一项或仍为占位, 报告被拒收):\n"
                "- branch: card/AIPOS-Z(分支)\n\n按你的 AGENTS.md 执行，完成后写报告到报告落点。")
    assert local == pre_f110  # 本机工位开工提示与 F110 前逐字节相同
    ctx = {"gate_host": "gate", "governance_root": "/g", "workstation_host": "mac", "gate_ssh_alias": "dev", "material_access": "经 ssh dev 读写 {x}"}
    remote = nr.render_kickoff(card, remote=ctx)
    _show("[④] remote render_kickoff 原文:\n" + remote)
    assert remote.startswith(local.split("\n报告 frontmatter")[0] + "\n以上工作树、报告落点、任务卡均在门机 gate(治理根 /g)")
    assert "材料访问方式(项目声明): 经 ssh dev 读写 {x}\n报告 frontmatter 必填" in remote  # 值内花括号不再被解析
    assert remote.replace(remote.split("任务卡路径: /g/5_tasks/queue/claimed/aipos-z.md")[1].split("\n报告 frontmatter")[0], "") == local
    for broken in ({**ctx, "material_access": ""}, {k: v for k, v in ctx.items() if k != "gate_ssh_alias"}):
        with pytest.raises(nr.KickoffRenderError):
            nr.render_kickoff(card, remote=broken)


# ===========================================================================
# ② 超时清理 / SIGHUP 清理
# ===========================================================================

def test_item2_remote_hang_timeout_kills_remote_group_term_then_kill(lrig, monkeypatch):
    r = _remote_rig(lrig, monkeypatch)
    f90._card(r.gov, TASK, "pending", harness="pi")
    _declare_material(r.gov)
    f95._land(r.gov, EXEC, "executor", r.remote_ws, host=REMOTE_HOST)
    monkeypatch.setenv("FAKE_EXEC_MODE", "hang")  # 吐事件后挂起 + 孙进程; 忽略 SIGTERM → 升级 KILL
    out = io.StringIO()
    res = run_loop(TASK, r.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05, max_wait=3, max_steps=5)
    text = out.getvalue()
    _show("---- ② 跨机挂起→超时 输出原文 ----\n" + text)
    assert res.exit_code == 3 and "等待产物超时" in res.message and "已终止拉起的 pi 进程组" in res.message, res.message
    launch = [s.launch for s in res.steps if s.launch and s.launch.get("launched")][0]
    pgid = int((r.log / f"{TASK}.pgid").read_text())
    assert launch["outcome"] == "timeout" and launch["termination"] == "sigkill" and launch["remote_pgid"] == pgid, launch
    assert not f95._group_alive(pgid)
    calls = [c["command"] for c in _ssh_calls(r.ssh_log)]
    assert [re.search(r"lybra-signal (\w+) ", c).group(1) for c in calls if "lybra-signal" in c] == ["TERM", "KILL"], calls
    _show(f"[② 超时] 远端组 {pgid} 组内进程: {'有' if f95._group_alive(pgid) else '无'}; pgrep -af fake_harness: {f95._pgrep_fake()!r}")
    assert any("outcome=timeout" in e and "termination=sigkill" in e for e in f95._events(r.gov, TASK))


_SIGHUP_SCRIPT = r"""
import sys, pytest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, {repo!r}); sys.path.insert(0, {tests!r})
import test_aipos_f90_loop_one_stage as f90
import test_aipos_f95_harness_launch as f95
import test_aipos_f110_remote_launch as f110
from test_aipos_f78_engine_agnostic import DRIVER, EXEC
from tools.aipos_cli.loop_driver import run_loop_cli
mp = pytest.MonkeyPatch()
tmp = Path({tmp!r})
r = f90.rig.__wrapped__(tmp, mp)
extra = f95.setup_launch(r, mp)
rem = f110.setup_remote(r, mp)
mp.setenv("FAKE_EXEC_MODE", "hang")
f90._card(r.gov, f90.TASK, "pending", harness="pi")
f110._declare_material(r.gov)
f95._land(r.gov, EXEC, "executor", rem.remote_ws, host=f110.REMOTE_HOST)
print("LOG", extra.log, flush=True)
print("SSHLOG", rem.ssh_log, flush=True)
sys.exit(run_loop_cli(SimpleNamespace(task_id=f90.TASK, workspace_root=r.gov, envelope=f95.POLICY_LAUNCH, actor=DRIVER,
                                      connection_json=None, max_steps=5, max_wait=120, interval=0.05, json=False, no_launch=False)))
"""


def test_item2_sighup_to_loop_kills_remote_group_via_ssh(tmp_path):
    script = _SIGHUP_SCRIPT.format(repo=str(REPO_ROOT), tests=str(REPO_ROOT / "tests"), tmp=str(tmp_path))
    proc = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=str(REPO_ROOT))
    seen: list[str] = []
    log = ssh_log = None
    for line in proc.stdout:  # 同步阻塞读 loop 输出(非轮询): 等到第一行远端进度(假 harness 已落 pgid 文件后才吐事件)
        seen.append(line.rstrip("\n"))
        if line.startswith("LOG "):
            log = Path(line.split(" ", 1)[1].strip())
        if line.startswith("SSHLOG "):
            ssh_log = Path(line.split(" ", 1)[1].strip())
        if re.search(rf"\[pi \S+ {REMOTE_HOST} pid=\d+\] 工具 read", line):
            break
    assert log is not None and ssh_log is not None, seen
    pgid = int((log / f"{TASK}.pgid").read_text())
    assert f95._group_alive(pgid), "远端进程组应在跑"
    before = f95._pgrep_fake()
    proc.send_signal(signal.SIGHUP)  # ssh 断线 = SIGHUP
    rest, err = proc.communicate(timeout=90)
    text = "\n".join(seen) + "\n" + rest
    _show("---- ② SIGHUP loop 输出原文 ----\n" + text + "\n---- stderr ----\n" + err)
    _show(f"[② SIGHUP] 发信号前 pgrep -af fake_harness:\n{before}")
    assert proc.returncode == -signal.SIGHUP, (proc.returncode, err)
    assert "收到信号 1, 已终止拉起的 harness 进程组" in err and "interrupted — loop 收到信号 1" in text
    assert not f95._group_alive(pgid)
    commands = [c["command"] for c in _ssh_calls(ssh_log)]
    assert [re.search(r"lybra-signal (\w+) ", c).group(1) for c in commands if "lybra-signal" in c] == ["TERM", "KILL"], commands
    after = f95._pgrep_fake()
    _show(f"[② SIGHUP] 之后 pgrep -af fake_harness: {after!r}")
    assert after == ""


# ===========================================================================
# ③ ssh 不可达 → 退回手工; material_access 未声明 / 含凭据 → 拒拉起
# ===========================================================================

def test_item3_ssh_unreachable_falls_back_to_manual_with_reason(lrig, monkeypatch):
    r = _remote_rig(lrig, monkeypatch, reachable={REMOTE_HOST: False})
    f90._card(r.gov, TASK, "pending", harness="pi")
    _declare_material(r.gov)
    f95._land(r.gov, EXEC, "executor", r.remote_ws, host=REMOTE_HOST)
    calls: list[dict] = []
    out = io.StringIO()
    res = run_loop(TASK, r.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05, max_wait=1, max_steps=5,
                   watch=f95._watch_recorder(calls))
    text = out.getvalue()
    _show("---- ③ ssh 不可达 输出原文 ----\n" + text)
    assert res.exit_code == 3 and calls == [{}]  # 只经 watch 等(未拉起 = 手工模式)
    wait = [s for s in res.steps if s.kind == "wait"][0]
    assert wait.launch["launched"] is False and "ssh 不可达 mac-fake(exit 255): ssh: connect to host mac-fake port 22: Connection refused" in wait.launch["refusal"]
    assert f"manual: 请在 {REMOTE_HOST}:{r.remote_ws} 的 pi 会话敲 /go(跨机工位)(未拉起: ssh 不可达 {REMOTE_HOST}" in text, text
    assert not r.log.exists() and f95._events(r.gov, TASK) == []
    assert [("lybra-probe" in c["command"], c["reachable"]) for c in _ssh_calls(r.ssh_log)] == [(True, False)]


@pytest.mark.parametrize("variant", ["undeclared", "credential"])
def test_item3_material_access_undeclared_or_credential_refuses_launch(lrig, monkeypatch, variant):
    from tools.aipos_cli.workspace_config import WorkstationDeclarationError, set_project_workstation

    r = _remote_rig(lrig, monkeypatch)
    f90._card(r.gov, TASK, "pending", harness="pi")
    f95._land(r.gov, EXEC, "executor", r.remote_ws, host=REMOTE_HOST)
    if variant == "credential":
        with pytest.raises(WorkstationDeclarationError) as exc:  # 写入口拒(不落盘)
            set_project_workstation(r.gov, EXEC, gate_ssh_alias=GATE_ALIAS, material_access="经 ssh 读写, token=abc")
        assert exc.value.code == "WORKSTATION_MATERIAL_INVALID"
        data = json.loads((r.gov / "project.json").read_text(encoding="utf-8"))
        assert "workstations" not in data
        data["workstations"] = {EXEC: {"gate_ssh_alias": GATE_ALIAS, "material_access": "经 ssh 读写, password 见群"}}  # 手改绕写入口
        (r.gov / "project.json").write_text(json.dumps(data), encoding="utf-8")
    calls: list[dict] = []
    out = io.StringIO()
    res = run_loop(TASK, r.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05, max_wait=1, max_steps=5,
                   watch=f95._watch_recorder(calls))
    text = out.getvalue()
    _show(f"---- ③ material_access {variant} 输出原文 ----\n" + text)
    wait = [s for s in res.steps if s.kind == "wait"][0]
    code = "WORKSTATION_MATERIAL_UNDECLARED" if variant == "undeclared" else "WORKSTATION_MATERIAL_INVALID"
    assert wait.launch["launched"] is False and code in wait.launch["refusal"], wait.launch
    assert "lybra project set-workstation <项目名> --instance " + EXEC in wait.launch["refusal"]
    assert "password 见群" not in text  # 拒因不回显声明值
    assert res.exit_code == 3 and calls == [{}] and not r.log.exists()
    assert _ssh_calls(r.ssh_log) == []  # 声明不齐先拒, 不发任何 ssh
    # 产品开工提示口(my-tasks --remote-workstation)同一判据: 材料不可用 = 不出开工提示(KICKOFF_UNRESOLVED, 原因原样)
    from tools.aipos_cli.loop_driver import workstation_kickoff

    kickoff, why = workstation_kickoff(r.gov, str(r.remote_ws), TASK, remote=True, instance=EXEC)
    _show(f"[③ {variant}] my-tasks --remote-workstation 拒因: {why}")
    assert kickoff == "" and "KICKOFF_UNRESOLVED" in why and code in why and "password 见群" not in why


# ===========================================================================
# ④ 单一分层(git grep 无第二拉起实现)
# ===========================================================================

def test_item4_single_launch_layer_git_grep():
    def grep(pattern: str) -> list[str]:
        out = subprocess.run(["git", "grep", "-n", "-E", pattern, "--", "tools/"], cwd=REPO_ROOT, capture_output=True, text=True)
        assert out.returncode in (0, 1), out.stderr
        return out.stdout.splitlines()

    sessions = grep(r"start_new_session=True")
    _show("[④] git grep start_new_session=True -- tools/:\n" + "\n".join(sessions))
    # AIPOS-F109 件④: run-all 自动发现执行器给每个测试文件独立进程组以便收尾清孤儿(测试执行, 非 harness 拉起层), 显式列出
    # AIPOS-F118 件①: finalize 合并后回归跑测试清单 / async 后台检查(测试执行, 非 harness 拉起层; 唯一拉起口 _popen_group), 显式列出
    assert [line.split(":", 1)[0] for line in sessions] == [
        "tools/aipos_cli/harness_launch.py", "tools/aipos_cli/post_merge_regression.py", "tools/aipos_cli/runall_discovery.py"], sessions
    popen = [line for line in grep(r"subprocess\.Popen\(") if "/harness_launch.py" in line or "/loop_driver.py" in line]
    assert [line.split(":", 1)[0] for line in popen] == ["tools/aipos_cli/harness_launch.py"], popen
    ssh_literals = grep(r"\[\"ssh\"")
    _show("[④] git grep '[\"ssh\"' -- tools/:\n" + "\n".join(ssh_literals))
    assert all(line.startswith("tools/aipos_cli/enroll_deliver.py:") for line in ssh_literals), ssh_literals  # loop 侧 ssh 只读声明
    src = (REPO_ROOT / "tools" / "aipos_cli" / "harness_launch.py").read_text(encoding="utf-8")
    loop_src = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    for text in (src, loop_src):
        assert "sleep(" not in text and "except Exception" not in text and "shell=True" not in text and "except:" not in text
    assert len(re.findall(r"=\s*LaunchedHarness\(", loop_src)) == 1 and "LaunchedHarness(plan, decl, say)" in loop_src  # local/remote 同一构造点
    enums = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))["enums"]
    assert {v["value"]: v["supported"] for v in enums["workstation_transport"]["values"]} == {"local": True, "remote": True}


# ===========================================================================
# ⑤ 技能步骤命令过 argparse; check-workstation 双向可达检查
# ===========================================================================

def test_item5_skill_remote_steps_parse():
    import shlex

    import test_aipos_f84_advisor_skills_generic as f84
    from tools.aipos_cli.aipos_cli import build_parser
    from tools.aipos_cli.enroll_deliver import build_parser as enroll_parser

    skill = (REPO_ROOT / "agents" / "skills" / "advisor-commands" / "SKILL.md").read_text(encoding="utf-8")
    section = skill.split("#### 跨机工位(AIPOS-F110", 1)[1].split("\n#### ", 1)[0]
    blocks = [re.sub(r"\\\n\s*", " ", b).strip() for b in re.findall(r"```bash\n(.*?)```", section, re.S)]
    assert len(blocks) == 3, blocks
    parsed: list[str] = []
    for block in blocks:
        line = f84._substitute(block.replace("<ssh目标>", "kiwi@remote-host"))
        argv = shlex.split(line)
        if argv[0] == "python3":
            assert argv[1:3] == ["-m", "tools.aipos_cli.enroll_deliver"], argv
            args = enroll_parser().parse_args(argv[3:])
            # AIPOS-F113 新形: Owner 凭据从 connection.json 读(不上命令行), 无 --owner-token / --code 参数
            assert args.ssh == "kiwi@remote-host" and args.connection_json and not hasattr(args, "owner_token")
            assert "--owner-token " not in line and "--code" not in line
        else:
            args = build_parser().parse_args(argv[1:])
            assert args.command == "project" and args.project_command in ("set-workstation", "check-workstation"), argv
        parsed.append(line)
    _show("[⑤] 技能跨机步骤(占位代换后)均过 argparse:\n" + "\n".join(f"  {p}" for p in parsed))
    for must in ("BatchMode=yes", "SIGHUP", "stdin", "render_kickoff"):
        assert must in section, must


def test_item5_check_workstation_bidirectional_and_set_workstation_cli(tmp_path, monkeypatch):
    from tools.aipos_cli.aipos_cli import main
    from tools.aipos_cli.enrollment import _append_enrollment_trail

    home = tmp_path / "home-root"
    proj = home / "probe"
    (proj / "5_tasks" / "queue").mkdir(parents=True)
    _write(proj / "project.json", json.dumps({"project": "probe", "config_version": 1}))
    r = SimpleNamespace(home=tmp_path / "h")
    rem = setup_remote(r, monkeypatch)
    _append_enrollment_trail(proj, action="land", code_id="enroll_fixture", role="executor", instance="exec.probe.mac",
                             by="(agent-enroll)", reason=f"host={REMOTE_HOST} workstation={rem.remote_ws} files=['role']")

    def cli(*argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    check = ("project", "check-workstation", "probe", "--home-root", str(home), "--instance", "exec.probe.mac", "--harness", "pi", "--json")
    monkeypatch.setattr("tools.aipos_cli.autonomy_policy.launchable_harnesses",
                        lambda: {"pi": {"argv": [sys.executable, "-c", "pass", "{kickoff}"], "events": "pi-json"}})
    rc, out, err = cli(*check)
    _show(f"[⑤] check-workstation(未声明材料) exit {rc}:\n{out}{err}")
    assert rc == 1 and any(c["check"] == "材料声明" and not c["ok"] for c in json.loads(out)["checks"])
    rc, out, err = cli("project", "set-workstation", "probe", "--home-root", str(home), "--instance", "exec.probe.mac",
                       "--gate-ssh-alias", GATE_ALIAS, "--material-access", "经 ssh gate-fake 读写", "--json", "--confirm")
    _show(f"[⑤] set-workstation exit {rc}: {out.strip()}")
    assert rc == 0 and json.loads((proj / "project.json").read_text())["workstations"]["exec.probe.mac"] == {
        "gate_ssh_alias": GATE_ALIAS, "material_access": "经 ssh gate-fake 读写"}
    rc, out, err = cli("project", "set-workstation", "probe", "--home-root", str(home), "--instance", "exec.probe.mac",
                       "--gate-ssh-alias", GATE_ALIAS, "--material-access", "Bearer xyz")
    assert rc == 1 and "WORKSTATION_MATERIAL_INVALID" in err and "project.json 未改动" in err
    rc, out, err = cli(*check)
    report = json.loads(out)
    _show(f"[⑤] check-workstation(双向可达) exit {rc}:\n{json.dumps(report, ensure_ascii=False, indent=1)}")
    assert rc == 0 and report["ok"] and [c["check"].split(" ")[0] for c in report["checks"]] == [
        "land", "transport", "harness", "材料声明", "门机→工位", "工位→门机"], report
    # 反向不可达(工位上门机别名连不上)= 工位→门机 ✗; 外层到工位仍可达
    rem.ssh_decl.write_text(json.dumps({"hosts": {REMOTE_HOST: {"reachable": True}, GATE_ALIAS: {"reachable": False}},
                                        "log": str(rem.ssh_log)}), encoding="utf-8")
    rc, out, err = cli(*check)
    report = json.loads(out)
    _show(f"[⑤] check-workstation(反向不可达) exit {rc}: {report['checks'][-1]}")
    assert rc == 1 and not report["checks"][-1]["ok"] and f"经别名 {GATE_ALIAS} 连门机不可达" in report["checks"][-1]["detail"]
    nested = [c for c in _ssh_calls(rem.ssh_log) if c["host"] == GATE_ALIAS]
    assert nested and all("lybra-gate-check" in c["command"] for c in nested)  # 反向检查经工位上的同一 ssh 前缀发起
