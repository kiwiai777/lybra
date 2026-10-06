"""AIPOS-F95 — 外部执行体/审计体自动拉起(Owner 信封授权下由 lybra loop 在工位按声明模板拉起 harness)+ 开工提示产品单源。

靶场 = AIPOS-F90 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME; 进程内门 InProcessGate 经真产品 CLI 薄壳; 禁真门/真治理根/真工位)
+ tmp 工位目录(.lybra/role + connection.json, 由测试写 enrollment_log land 事件)+ 假 harness(tests/fake_harness.py)。
测试声明 pi 的 launch 模板指向假 harness(替换 autonomy_policy.launchable_harnesses 返回值), 禁起真实 pi 会话。

验收(卡面 ★验收):
 ① 授权信封 + 假 harness: loop 从认领起拉起执行体 → Return → 入门/派审 → 拉起审计体 → 报告 → completed; 输出含一行式进度原文
 ② 无授权 / --no-launch / harness 无模板 / 工位定位不到 / 身份不符 → 不拉起, 提示含工位目录与 /go; 手工流程与 F95 前一致
 ③ 挂起 → 超时杀进程组; 早退 → exit 3 附 stderr 末尾; SIGTERM loop → 子进程组被杀; 均以 pgrep 证无残留
 ④ go.ts 发送文本与拉起所用 kickoff 逐字节相同; go.ts 缺 kickoff 拒开工(TS 夹具 f86/f87/f93)
 ⑤ 新铸信封 Boundary 与 allowed_verbs / launch_harnesses 一致
 ⑥ session record Events 有拉起事件; ⑥b land 事件带 host, host 非本机不拉起并提示 host:dir 与 /go, 缺 host 按本机
"""
from __future__ import annotations

import contextlib
import copy
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
from test_aipos_f78_engine_agnostic import DRIVER, EXEC, _write  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture(进程内门 + 进程内 CLI)
from tools.aipos_cli import loop_driver  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import LoopInterrupted, run_loop  # noqa: E402

FAKE = REPO_ROOT / "tests" / "fake_harness.py"
TASK = f90.TASK
AUDIT = f90.AUDIT
POLICY_LAUNCH = "pol_probe_launch_1"
# 审计卡的认领实例: 靶场里由交回时 audit_derivation 生成审计卡, 其 agent_instance 走产品存量缺省(不读被审卡 audit_by,
# 见 RETURN 缺口; 本卡不修)。审计工位按该实例登记, 身份核验才对得上。
AUDIT_CLAIMER = "audit.lybra.kiwiai-dev"


def _show(line: str) -> None:
    print(line, flush=True)


def _pgrep_fake() -> str:
    """`pgrep -af <假 harness 脚本绝对路径>`: 只匹配假 harness 进程(及其孙进程), 不误中命令行里含该词的 shell。"""
    out = subprocess.run(["pgrep", "-af", str(FAKE)], capture_output=True, text=True)
    return out.stdout.strip()


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _land(gov: Path, instance: str, role: str, workstation: Path, *, host: str | None = "__local__") -> None:
    """按 enroll 落地的同一 trail writer 写 land 事件(host=None = F95 前存量格式, 无 host)。"""
    from tools.aipos_cli.enrollment import _append_enrollment_trail

    host_part = "" if host is None else f"host={socket.gethostname() if host == '__local__' else host} "
    _append_enrollment_trail(gov, action="land", code_id=f"enroll_fixture_{role}", role=role, instance=instance,
                             by="(agent-enroll)", reason=f"{host_part}workstation={workstation} files=['connection.json', 'role']")


def _workstation(home: Path, gov: Path, name: str, role: str, instance: str) -> Path:
    ws = home / "workstations" / name
    _write(ws / ".lybra" / "role", json.dumps({"role": role, "instance": instance}))
    _write(ws / ".lybra" / "connection.json", json.dumps({
        "governance_root": str(gov), "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"},
        "tokens": [{"role": role, "role_class": role, "agent_instance": instance, "token": "fixture-ws-not-a-secret", "scopes": []}],
    }))
    _write(ws / "AGENTS.md", f"# {role} 工位(夹具)\n")
    return ws


def setup_launch(r: SimpleNamespace, mp: pytest.MonkeyPatch, *, grace: float = 1, terminate_wait: float = 1) -> SimpleNamespace:
    """在 F90 靶场上补: 授权拉起信封 / 两个工位 / 测试声明的 pi 模板(→ 假 harness) / 短宽限。"""
    from tools.aipos_cli import autonomy_policy
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(r.gov / "5_tasks" / "policies" / f"{POLICY_LAUNCH}.md", build_autonomy_policy_markdown(
        policy_id=POLICY_LAUNCH, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-launch", task_selector_task_mode="code", launch_harnesses=["pi"]))
    ws_exec = _workstation(r.home, r.gov, "probe-executor", "executor", EXEC)
    ws_audit = _workstation(r.home, r.gov, "probe-auditor", "auditor", AUDIT_CLAIMER)
    mp.setattr(autonomy_policy, "launchable_harnesses",
               lambda: {"pi": {"argv": [sys.executable, str(FAKE), "{kickoff}"], "events": "pi-json"}})
    real_contract = loop_driver.load_loop_contract

    def short_contract(repo_root=None):
        contract = copy.deepcopy(real_contract(repo_root))
        contract["launch"].update({"grace_seconds": grace, "terminate_wait_seconds": terminate_wait})
        return contract

    mp.setattr(loop_driver, "load_loop_contract", short_contract)
    log = r.home / "fake-log"
    mp.setenv("FAKE_HARNESS_LOG", str(log))
    mp.setenv("FAKE_EXEC_MODE", "work")
    mp.setenv("FAKE_AUDIT_MODE", "work")
    return SimpleNamespace(ws_exec=ws_exec, ws_audit=ws_audit, log=log)


@pytest.fixture
def lrig(rig, monkeypatch):  # noqa: F811
    extra = setup_launch(rig, monkeypatch)
    for k, v in vars(extra).items():
        setattr(rig, k, v)
    yield rig
    leftover = _pgrep_fake()
    assert leftover == "", f"残留假 harness 进程:\n{leftover}"


def _events(gov: Path, card: str) -> list[str]:
    lines: list[str] = []
    for path in sorted((gov / "5_tasks" / "records" / "sessions" / card).glob("*.md")):
        lines += [ln for ln in path.read_text(encoding="utf-8").splitlines() if "harness_" in ln]
    return lines


# ===========================================================================
# ① 授权信封 + 假 harness: 执行体与审计体都被拉起, 走到 completed
# ===========================================================================

def test_item1_authorized_launch_executor_and_auditor_to_completed(lrig, monkeypatch):
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    _land(lrig.gov, EXEC, "executor", lrig.ws_exec)                # 带 host(本机)
    _land(lrig.gov, AUDIT_CLAIMER, "auditor", lrig.ws_audit, host=None)  # ⑥b 存量无 host = 本机
    monkeypatch.setenv("FAKE_EXEC_MODE", "linger")  # 执行体写完产物不退 → 宽限期满终止进程组
    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=POLICY_LAUNCH, out=out, interval=0.05, max_wait=60, max_steps=30)
    text = out.getvalue()
    _show("---- ① lybra loop 输出原文 ----\n" + text + "------------------------------")
    assert res.exit_code == 0 and res.outcome == "completed", text
    launches = [s.launch for s in res.steps if s.launch and s.launch.get("launched")]
    assert [l["card"] for l in launches] == [TASK, AUDIT], launches
    assert launches[0]["outcome"] == "artifact_ready" and launches[0]["termination"] == "sigterm", launches[0]
    assert launches[1]["outcome"] == "artifact_ready" and launches[1]["termination"] == "already_exited", launches[1]
    assert launches[0]["workstation"] == str(lrig.ws_exec) and launches[1]["workstation"] == str(lrig.ws_audit)
    assert launches[0]["host"] == socket.gethostname() and launches[1]["host"] is None and launches[1]["transport"] == "local"
    # 进程在工位目录跑, 收到的 kickoff 是开工提示全文
    assert (lrig.log / f"{TASK}.cwd").read_text() == str(lrig.ws_exec)
    assert (lrig.log / f"{AUDIT}.cwd").read_text() == str(lrig.ws_audit)
    # 一行式进度: 工具调用名+首段参数 / 助手文本首行 / 错误; 未知事件只计数; 凭据字样整行隐去
    assert re.search(rf"\[pi {TASK} pid=\d+\] 工具 read: AGENTS.md", text), text
    assert re.search(rf"\[pi {TASK} pid=\d+\] 工具 bash: git -C .* commit -m work$", text, re.M), text
    assert f"助手: 开工 {TASK}: 先读卡面" in text and "第二行不应上屏" not in text and "second line" not in text
    assert "工具 read 出错: ENOENT: no such file" in text
    assert "[含凭据字样, 整行已隐去]" in text and "fixture-not-a-secret" not in text and "fixture-ws-not-a-secret" not in text
    assert "weird_future_event" in text.split("事件计数")[1] and text.count("this line is not json") == 0
    assert re.search(r"事件计数: 汇总 \d+, 静默 \d+, 非 JSON 1, 原样 0, 未知类型 \{'weird_future_event': 1\}", text), text
    # ⑥ 拉起/收尾事件进该卡既有 session record Events
    for card, ws in ((TASK, lrig.ws_exec), (AUDIT, lrig.ws_audit)):
        ev = _events(lrig.gov, card)
        _show(f"[⑥] {card} session Events: {ev}")
        assert any(f"harness_launch by {DRIVER}; harness=pi;" in e and f"workstation={ws};" in e and "pid=" in e for e in ev), ev
        assert any("harness_exit by" in e and "outcome=artifact_ready" in e for e in ev), ev
    assert (lrig.gov / "5_tasks" / "queue" / "completed" / f"{TASK.lower()}.md").is_file()
    for name in (TASK, AUDIT):
        assert not _group_alive(int((lrig.log / f"{name}.pgid").read_text())), name
    _show(f"[①/⑧] pgrep -af fake_harness: {_pgrep_fake()!r}")


# ===========================================================================
# ② 不拉起的各条件: 退回手工, 提示含工位目录与 /go
# ===========================================================================

def _watch_recorder(calls: list[dict]):
    def watch(args, expect_ready, **kwargs):
        calls.append(kwargs)
        return 2  # 手工模式下无人干活 → 等待超时(行为同 F95 前: exit 3)
    return watch


@pytest.mark.parametrize("variant", ["no_envelope_grant", "no_launch_flag", "no_template", "unlocated", "identity_mismatch"])
def test_item2_not_launched_falls_back_to_manual_go(lrig, variant):
    harness = "codex" if variant == "no_template" else "pi"
    f90._card(lrig.gov, TASK, "pending", harness=harness)
    if variant != "unlocated":
        _land(lrig.gov, EXEC, "executor", lrig.ws_audit if variant == "identity_mismatch" else lrig.ws_exec)
    calls: list[dict] = []
    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=f90.POLICY if variant == "no_envelope_grant" else POLICY_LAUNCH,
                   out=out, interval=0.05, max_wait=1, max_steps=5, watch=_watch_recorder(calls),
                   no_launch=(variant == "no_launch_flag"))
    text = out.getvalue()
    _show(f"---- ② {variant} 输出原文 ----\n{text}")
    assert res.exit_code == 3 and calls == [{}], (res.exit_code, calls)  # 只经 watch 等(无 stop_when/sleeper = 未拉起)
    wait = [s for s in res.steps if s.kind == "wait"][0]
    assert wait.launch and wait.launch["launched"] is False
    expected = {
        "no_envelope_grant": "launch_harnesses 为空",
        "no_launch_flag": "--no-launch",
        "no_template": "无 launch 模板",
        "unlocated": "工位位置定位不到",
        "identity_mismatch": f"的实例 {AUDIT_CLAIMER} ≠ 卡实例 {EXEC}",
    }[variant]
    assert expected in wait.launch["refusal"], wait.launch
    if variant == "unlocated":
        assert f"manual: 请在实例 {EXEC} 的工位(pi 会话)敲 /go" in text, text
    else:
        ws = lrig.ws_audit if variant == "identity_mismatch" else lrig.ws_exec
        assert f"manual: 请在 {ws} 的 {harness} 会话敲 /go" in text, text
    assert not lrig.log.exists(), "不得拉起任何进程"
    assert _events(lrig.gov, TASK) == []


def test_item2_manual_mode_flow_unchanged_completes(lrig):
    """手工模式(信封未授权拉起): 与 F95 前同一流程走到 completed(watch 期间由模拟 agent 落产物), 零拉起。"""
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    _land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    _land(lrig.gov, AUDIT_CLAIMER, "auditor", lrig.ws_audit)
    seen: list[str] = []
    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=f90.POLICY, out=out, watch=f90._agent_watch(lrig, seen),
                   interval=0.05, max_wait=5, max_steps=30)
    text = out.getvalue()
    _show("---- ② 手工模式全链输出原文 ----\n" + text)
    assert res.exit_code == 0 and res.outcome == "completed", text
    verbs = f90._cli_verbs(lrig)
    assert verbs[0] == f"lybra queue claim {TASK}" and verbs[-1] == f"lybra queue close {TASK}", verbs
    assert f"manual: 请在 {lrig.ws_exec} 的 pi 会话敲 /go" in text and f"manual: 请在 {lrig.ws_audit} 的 pi 会话敲 /go" in text
    assert seen[0] == "executor" and "auditor" in seen and not lrig.log.exists()


# ===========================================================================
# ③ 生命周期: 挂起超时 / 早退 / SIGTERM; ④ kickoff 逐字节同 /go
# ===========================================================================

def test_item3_hang_timeout_kills_group_and_item4_kickoff_bytes_equal_go_ts(lrig, monkeypatch, tmp_path):
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    _land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    monkeypatch.setenv("FAKE_EXEC_MODE", "hang")  # 吐事件后挂起 + 孙进程; 忽略 SIGTERM → 升级 SIGKILL
    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=POLICY_LAUNCH, out=out, interval=0.05, max_wait=3, max_steps=5)
    text = out.getvalue()
    _show("---- ③ 挂起→超时 输出原文 ----\n" + text)
    assert res.exit_code == 3 and "等待产物超时" in res.message and "已终止拉起的 pi 进程组" in res.message, res.message
    launch = [s.launch for s in res.steps if s.launch and s.launch.get("launched")][0]
    assert launch["outcome"] == "timeout" and launch["termination"] == "sigkill", launch
    pgid = int((lrig.log / f"{TASK}.pgid").read_text())
    assert pgid == launch["pgid"] and not _group_alive(pgid)
    _show(f"[③ 挂起] pgid={pgid} 组内进程: {'有' if _group_alive(pgid) else '无'}; pgrep -af fake_harness: {_pgrep_fake()!r}")
    assert any("outcome=timeout" in e for e in _events(lrig.gov, TASK))

    # ④ 卡仍 claimed(执行体未交): 工位 my-tasks 输出 → go.ts planGo 发送文本 == 拉起时传入的 kickoff(逐字节)
    buf = io.StringIO()
    from tools.aipos_cli.aipos_cli import main

    with contextlib.redirect_stdout(buf):
        assert main(["my-tasks", "--workstation", str(lrig.ws_exec), "--json"]) == 0
    my_tasks = json.loads(buf.getvalue())
    launched_kickoff = (lrig.log / f"{TASK}.kickoff").read_bytes()
    assert my_tasks["next_card"]["kickoff"].encode("utf-8") == launched_kickoff
    mt = tmp_path / "my-tasks.json"
    mt.write_text(buf.getvalue(), encoding="utf-8")
    node = subprocess.run(["node", str(REPO_ROOT / "tests" / "ts" / "f93-go-report-fields.test.ts"), str(mt)],
                          capture_output=True, text=True, check=True)
    plan = json.loads(node.stdout.strip().splitlines()[-1])
    assert plan["kind"] == "kickoff" and plan["kickoff"].encode("utf-8") == launched_kickoff
    _show(f"[④] go.ts 发送 == 拉起 kickoff(逐字节, {len(launched_kickoff)} bytes):\n{plan['kickoff']}")
    # 开工提示与 F95 前 go.ts 字面量等义(工作树/报告落点/卡路径/报告必填字段)
    nc = my_tasks["next_card"]
    for label, key in (("工作树路径", "worktree_path"), ("报告落点", "report_path"), ("任务卡路径", "card_path")):
        assert f"{label}: {nc[key]}\n" in plan["kickoff"]
    for item in nc["report_required_frontmatter"]:
        assert f"- {item['key']}: " in plan["kickoff"]


def test_item3_early_exit_reports_stderr_tail_exit3(lrig, monkeypatch):
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    _land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    monkeypatch.setenv("FAKE_EXEC_MODE", "early")
    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=POLICY_LAUNCH, out=out, interval=0.05, max_wait=30, max_steps=5)
    text = out.getvalue()
    _show("---- ③ 早退 输出原文 ----\n" + text)
    assert res.exit_code == 3 and res.outcome == "wait_timeout"
    assert "进程已退出(exit 7)而产物未就绪" in res.message and "fatal: fake harness crashed early" in res.message
    tail = res.message.split("stderr 末尾:\n", 1)[1].splitlines()
    assert len(tail) == 20 and tail[0].strip() == "fake stderr line 7" and tail[-1].strip() == "fatal: fake harness crashed early", tail
    launch = [s.launch for s in res.steps if s.launch and s.launch.get("launched")][0]
    assert launch["outcome"] == "early_exit" and launch["returncode"] == 7
    assert not _group_alive(int((lrig.log / f"{TASK}.pgid").read_text()))
    _show(f"[③ 早退] pgrep -af fake_harness: {_pgrep_fake()!r}")


_SIGTERM_SCRIPT = r"""
import sys, pytest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, {repo!r}); sys.path.insert(0, {tests!r})
import test_aipos_f90_loop_one_stage as f90
import test_aipos_f95_harness_launch as f95
from test_aipos_f78_engine_agnostic import DRIVER, EXEC
from tools.aipos_cli.loop_driver import run_loop_cli
mp = pytest.MonkeyPatch()
tmp = Path({tmp!r})
r = f90.rig.__wrapped__(tmp, mp)
extra = f95.setup_launch(r, mp)
mp.setenv("FAKE_EXEC_MODE", "hang")
f90._card(r.gov, f90.TASK, "pending", harness="pi")
f95._land(r.gov, EXEC, "executor", extra.ws_exec)
print("LOG", extra.log, flush=True)
sys.exit(run_loop_cli(SimpleNamespace(task_id=f90.TASK, workspace_root=r.gov, envelope=f95.POLICY_LAUNCH, actor=DRIVER,
                                      connection_json=None, max_steps=5, max_wait=120, interval=0.05, json=False, no_launch=False)))
"""


def test_item3_sigterm_to_loop_kills_launched_group(tmp_path):
    script = _SIGTERM_SCRIPT.format(repo=str(REPO_ROOT), tests=str(REPO_ROOT / "tests"), tmp=str(tmp_path))
    proc = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=str(REPO_ROOT))
    seen: list[str] = []
    log = None
    for line in proc.stdout:  # 同步阻塞读 loop 输出(非轮询): 等到第一行进度(假 harness 已落 pgid 文件后才吐事件)
        seen.append(line.rstrip("\n"))
        if line.startswith("LOG "):
            log = Path(line.split(" ", 1)[1].strip())
        if re.search(r"\[pi \S+ pid=\d+\] 工具 read", line):
            break
    assert log is not None, seen
    pgid = int((log / f"{TASK}.pgid").read_text())
    assert _group_alive(pgid), "拉起的进程组应在跑"
    before = _pgrep_fake()
    proc.send_signal(signal.SIGTERM)
    rest, err = proc.communicate(timeout=60)
    text = "\n".join(seen) + "\n" + rest
    _show("---- ③ SIGTERM loop 输出原文 ----\n" + text + "\n---- stderr ----\n" + err)
    _show(f"[③ SIGTERM] 发信号前 pgrep -af fake_harness:\n{before}")
    assert proc.returncode == -signal.SIGTERM, (proc.returncode, err)
    assert "收到信号 15, 已终止拉起的 harness 进程组" in err and "interrupted — loop 收到信号 15" in text
    assert not _group_alive(pgid)
    after = _pgrep_fake()
    _show(f"[③ SIGTERM] 之后 pgrep -af fake_harness: {after!r}")
    assert after == ""


# ===========================================================================
# ⑤ 信封: launch_harnesses 字段 + Boundary 由声明渲染; mint 参数
# ===========================================================================

def test_item5_minted_envelope_boundary_matches_declaration(tmp_path, monkeypatch):
    import test_aipos_f78_engine_agnostic as f78
    from tools.aipos_cli.aipos_cli import _envelope_mint_payload, build_parser
    from tools.aipos_cli.autonomy_policy import envelope_authorizes_launch, load_policy
    from tools.aipos_cli.owner_decision_writer import build_owner_decision_record

    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    allowed = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]["lybra_loop"]["envelope"]["allowed_verbs"]
    for pid, grant in (("pol_f95_launch", ["pi"]), ("pol_f95_manual", [])):
        payload = _envelope_mint_payload(policy_id=pid, agent_or_role="advisor", max_tasks=5, task_mode="code",
                                         expires_at="2999-01-01T00:00:00Z", decision_summary="f95", actor="owner",
                                         launch_harnesses=grant)
        result = build_owner_decision_record(gov, payload, actor="owner", dry_run=False)
        assert result.get("wrote") and not result["blocking_reasons"], result["blocking_reasons"]
        text = (gov / "5_tasks" / "policies" / f"{pid}.md").read_text(encoding="utf-8")
        boundary = text.split("## Boundary", 1)[1]
        _show(f"[⑤] {pid} Boundary:{boundary}")
        assert "CLAIM only" not in text and "does not authorize return" not in text
        assert f"only these verbs: {', '.join(allowed)} (" in boundary
        assert f"Harness launch authorized: {', '.join(grant) or 'none (manual /go only)'} (" in boundary
        policy = load_policy(gov, pid)
        assert policy["launch_harnesses"] == grant
        assert envelope_authorizes_launch(policy, "pi")[0] is bool(grant)
    # 门 writer 校验: 无 launch 模板的 harness / 非列表 = 拒
    for bad, why in ((["codex"], "has no launch template"), ("pi", "must be a list")):
        payload = _envelope_mint_payload(policy_id="pol_f95_bad", agent_or_role="advisor", max_tasks=5, task_mode="code",
                                         expires_at="2999-01-01T00:00:00Z", decision_summary="f95", actor="owner")
        payload["autonomy_policy"]["launch_harnesses"] = bad
        res = build_owner_decision_record(gov, payload, actor="owner", dry_run=True)
        assert any(why in b for b in res["blocking_reasons"]), res["blocking_reasons"]
    # 存量信封(无 launch_harnesses 键)= 不授权
    assert load_policy(gov, "pol_f95_manual")["launch_harnesses"] == []
    # CLI: envelope mint --launch-harness(可重复) / loop --no-launch
    args = build_parser().parse_args(["envelope", "mint", "--dry-run", "--policy-id", "p", "--agent-or-role", "a", "--max-tasks", "1",
                                      "--expires-at", "2999-01-01T00:00:00Z", "--decision-summary", "s", "--launch-harness", "pi"])
    assert args.launch_harness == ["pi"]
    assert build_parser().parse_args(["loop", "--task-id", "X", "--no-launch"]).no_launch is True
    contract = loop_driver.load_loop_contract()
    assert contract["parameters"]["properties"]["no_launch"]["default"] is False


# ===========================================================================
# ⑥b 工位位置: land 事件带 host; host 非本机 → 不拉起, 提示 host:dir 与 /go
# ===========================================================================

def test_item6b_land_event_carries_host_and_remote_falls_back_to_manual(lrig, monkeypatch):
    from unittest.mock import patch

    from tools.aipos_cli import enroll_client
    from tools.aipos_cli.enrollment import issue_self_contained_code, workstation_location

    # enroll 落地: landed_detail 带 host(本机 = 主机名; --landed-host = ssh 目标)
    root = lrig.home / "enroll-gate"
    _write(root / ".lybra" / "connection.json", json.dumps({"config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"},
                                                            "tokens": []}))
    details: list[str] = []
    for host in (None, "kiwi@far-mac"):
        sc = issue_self_contained_code(root, role="advisor", by="t")["self_contained_code"]
        exchange = {"ok": True, "token_entry": {"role": "advisor", "agent_instance": "adv.f95", "fingerprint": "fp",
                                                "scopes": ["advisor"], "token": "tok-fixture"}}
        with patch.object(enroll_client, "exchange_enrollment_code", return_value=exchange), \
                patch.object(enroll_client, "land_enrollment_code", side_effect=lambda *a, **k: details.append(k["landed_detail"]) or True):
            assert enroll_client.enroll(code=sc, gate_url="", workspace_root=root, landed_host=host)["ok"]
    _show(f"[⑥b] enroll landed_detail: {details}")
    assert details[0].startswith(f"host={socket.gethostname()} workstation={root.resolve()} ")
    assert details[1].startswith(f"host=kiwi@far-mac workstation={root.resolve()} ")
    # enroll_deliver --ssh 把 ssh 目标作 --landed-host 传给远端 enroll
    from tools.aipos_cli import enroll_deliver

    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(lrig.gov))
    sent: list[list[str]] = []

    class _GateStub:  # AIPOS-F113: --ssh 发码/回滚走门动词(本机 Owner 凭据只作 HTTP Authorization); 夹具不连真实门
        def __init__(self, *a, **k):
            pass

        def call_tool(self, name, arguments, **k):
            if name == "lybra_roles_enroll_code":
                return {"ok": True, "code_id": "enroll_f95fixture", "self_contained_code": "LYBRAENROLL1.fixture-not-a-secret",
                        "enrollment": {"fingerprint": "sha256:fixture"}}
            return {"ok": True}

    with patch.object(enroll_deliver, "GateClient", _GateStub), \
            patch.object(enroll_deliver.subprocess, "run", side_effect=lambda argv, **k: sent.append(argv) or
                         subprocess.CompletedProcess(argv, 1, "", "stop here (fixture)")):
        with pytest.raises(RuntimeError):
            enroll_deliver.enroll_deliver_ssh(role="executor", instance=EXEC, workspace_root="/remote/ws", harness_root="/remote/ws",
                                              ssh_target="kiwi@far-mac", gate_url="http://127.0.0.1:1", owner_policy_ref="p",
                                              owner_token="fixture-owner-not-a-secret")
    # AIPOS-F113: ssh argv = ssh -- <目标> <逐参数 shlex.quote 的远端命令>(远端命令在末位)
    assert "--landed-host kiwi@far-mac" in sent[0][-1], sent
    help_text = subprocess.run([sys.executable, "-m", "tools.aipos_cli.enroll_client", "--help"], capture_output=True, text=True,
                               cwd=str(REPO_ROOT)).stdout
    assert "--landed-host" in help_text

    # 读侧: 远端 host → transport=remote(AIPOS-F110 起已支持; 本靶场未声明跨机材料)→ loop 不拉起, 提示 host:dir 与 /go
    _land(lrig.gov, EXEC, "executor", lrig.ws_exec, host="kiwi@far-mac")
    loc = workstation_location(lrig.gov, EXEC)
    assert loc["found"] and loc["transport"] == "remote" and loc["supported"] is True and loc["host"] == "kiwi@far-mac"
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    calls: list[dict] = []
    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=POLICY_LAUNCH, out=out, interval=0.05, max_wait=1, max_steps=5,
                   watch=_watch_recorder(calls))
    text = out.getvalue()
    _show("---- ⑥b 远端工位 输出原文 ----\n" + text)
    assert calls == [{}] and not lrig.log.exists()
    assert f"manual: 请在 kiwi@far-mac:{lrig.ws_exec} 的 pi 会话敲 /go(跨机工位)" in text, text
    assert "WORKSTATION_MATERIAL_UNDECLARED" in text and "lybra project set-workstation" in text, text  # AIPOS-F110: 材料未声明 = 拒拉起
    # 存量无 host = 本机(缺省声明在 enums.schema workstation_transport.missing_host)
    _land(lrig.gov, EXEC, "executor", lrig.ws_exec, host=None)
    stock = workstation_location(lrig.gov, EXEC)
    declared = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))["enums"]["workstation_transport"]
    assert stock["host"] is None and stock["transport"] == declared["missing_host"] == "local" and stock["supported"] is True


# ===========================================================================
# 件① 开工提示单源 / 汇总与打码 / 纪律
# ===========================================================================

def test_item1_kickoff_rendered_from_declaration_fail_closed():
    card = {"task_id": "AIPOS-Z", "worktree_path": "/r/.worktrees/AIPOS-Z", "report_path": "/g/task_cards/AIPOS-Z/RETURN.md",
            "card_path": "/g/5_tasks/queue/claimed/aipos-z.md",
            "report_required_frontmatter": [{"key": "commit_sha", "hint": "说明 {不是占位}", "value": None},
                                            {"key": "branch", "hint": "分支", "value": "card/AIPOS-Z"}]}
    text = nr.render_kickoff(card)
    _show("[件①] render_kickoff 原文:\n" + text)
    tmpl = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]["lybra_my_tasks"]["kickoff"]
    assert text.startswith("已认领任务卡 AIPOS-Z。\n\n工作树路径: /r/.worktrees/AIPOS-Z\n") and "{" + "task_id}" in tmpl["template"]
    assert "- commit_sha: (说明 {不是占位})\n- branch: card/AIPOS-Z(分支)\n\n按你的 AGENTS.md 执行" in text
    for broken in ({**card, "report_required_frontmatter": []}, {**card, "worktree_path": None},
                   {**card, "report_required_frontmatter": [{"hint": "x", "value": None}]},
                   {**card, "report_required_frontmatter": [{"key": "k", "hint": "x", "value": 3}]}):
        with pytest.raises(nr.KickoffRenderError):
            nr.render_kickoff(broken)
    sel = nr.select_next_card([{**card, "queue_state": "claimed", "worktree_exists": True, "worktree_path": card["worktree_path"],
                                "kickoff_refusal": None, "frontmatter_warnings": [], "claimed_at": "2026-10-04T00:00:00Z",
                                "report_required_frontmatter": [{"key": "k", "hint": "x", "value": 3}]}])
    assert sel["next_card"] is None and sel["next_card_excluded"][0]["code"] == "KICKOFF_UNRESOLVED", sel


def test_progress_summary_and_redaction_units():
    from tools.aipos_cli.harness_launch import redact_progress, summarize_event

    ev = lambda d: json.dumps(d)  # noqa: E731
    assert summarize_event(ev({"type": "tool_execution_start", "toolName": "bash", "args": {"command": "ls -la\nrm"}}), "pi-json") \
        == ("工具 bash: ls -la", "summarized")
    assert summarize_event(ev({"type": "message_end", "message": {"role": "assistant", "stopReason": "error",
                                                                   "errorMessage": "rate limited\nx"}}), "pi-json")[0] == "错误: rate limited"
    assert summarize_event(ev({"type": "message_update"}), "pi-json") == (None, "quiet")
    assert summarize_event(ev({"type": "brand_new"}), "pi-json") == (None, "unknown:brand_new")
    assert summarize_event("not json", "pi-json") == (None, "non_json")
    assert summarize_event(ev({"type": "agent_end"}), "other-format") == (None, "raw")
    assert redact_progress("Authorization: Bearer abc") == "[含凭据字样, 整行已隐去]"
    assert redact_progress("cat .lybra/connection.json token") == "[含凭据字样, 整行已隐去]"
    secret = "Ab3" + "x" * 40
    assert redact_progress(f"value {secret} end") == "value [已打码] end"
    sha = "0123456789abcdef0123456789abcdef01234567"
    assert redact_progress(f"commit {sha} test_aipos_f87_fragmentation_ratchet") == f"commit {sha} test_aipos_f87_fragmentation_ratchet"


def test_discipline_single_source_no_spin_no_daemon_registered():
    go = (REPO_ROOT / "agents" / "harness" / "pi" / "_shared" / "extensions" / "go.ts").read_text(encoding="utf-8")
    for literal in ("已认领任务卡", "工作树路径:", "报告落点:", "按你的 AGENTS.md"):
        assert literal not in go, f"go.ts 文案字面量应退役: {literal}"
    assert "card.kickoff" in go
    for rel in ("tools/aipos_cli/loop_driver.py", "tools/aipos_cli/harness_launch.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "sleep(" not in src and "except Exception" not in src and "except:" not in src, rel
        assert "threading.Thread" not in src and "daemon" not in src.lower() and "shell=True" not in src, rel
    for retired in ("agent_supervise", "agent_launch_check", "pump.py"):
        assert not list((REPO_ROOT / "tools").rglob(f"{retired}*")), retired
    enums = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))["enums"]
    launch = {v["value"]: v.get("launch") for v in enums["harness"]["values"]}
    assert launch["pi"]["argv"] == ["pi", "-p", "{kickoff}", "--mode", "json"] and "--no-session" not in launch["pi"]["argv"]
    assert launch["codex"] is None and launch["claude-code"] is None
    assert {v["value"]: v["supported"] for v in enums["workstation_transport"]["values"]} == {"local": True, "remote": True}  # AIPOS-F110
    runall = (REPO_ROOT / "tests" / "run-all.sh").read_text(encoding="utf-8")
    assert "tests/test_aipos_f95_harness_launch.py" in runall
    skill = (REPO_ROOT / "agents" / "skills" / "advisor-commands" / "SKILL.md").read_text(encoding="utf-8")
    assert "--launch-harness pi" in skill and "两种开工模式" in skill and "禁 `until`/`sleep` 轮询" in skill
    onboarding = (REPO_ROOT / "agents" / "skills" / "lybra-onboarding" / "SKILL.md").read_text(encoding="utf-8")
    assert "--launch-harness pi" in onboarding and "缺省 = 手工模式" in onboarding
