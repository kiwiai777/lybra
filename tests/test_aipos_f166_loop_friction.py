"""AIPOS-F166(gap #151/#152/#153) — loop 摩擦三连: 等待步遇部署自动续等 / 信封用尽专门出口与预警 / 合并后回归丢失可见可补跑。

靶场一律临时根: 治理根 / 产品仓 / 部署目录 / HOME 皆 tmp_path(禁真门 / 真治理根 / 真工位 / pi); 门 = F90 进程内真门动词处理器;
部署目录布局同 tools/lybra-deploy(F152 夹具); 合并后回归 = F118 靶场(真 run-all 执行器)。夹具只杀本用例自起的进程(精确 pid / 进程组)。

验收(卡面 ★验收):
 ① 等待步中部署更新 → 自动续等(运行记录一行 + 新运行 continues_run 关联, 已落步不重复); 执行步之间部署更新仍 deploy_changed;
    真进程 re-exec(F143 lybra_cli_invocation 换代码树根)落到新快照代码(调用方 cwd 里同名包不遮蔽)
 ② 信封额度用尽 → envelope_exhausted + 续签文案(policy_id = 该驱动方已有最大序号 +1); 过期 → envelope_expired;
    loop status 下一动作 owner_needed(envelope_renew); 剩余额度预警 / 到期预警原文(loop status 与 brief)
 ③ 异步回归启动即落进行中记录(sha / pid / 日志在治理根运行记录旁); 进程被夹具杀掉后 loop status / next / brief 报「合并后回归未完成」
    + 补跑命令; `regression run` 预演零写入, --confirm 补跑落同格式结论(F149 baseline 可复用)
"""
from __future__ import annotations

import io
import json
import os
import shutil
import signal
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 靶场唯一来源
import test_aipos_f118_post_merge_regression as f118  # noqa: E402  — 合并后回归靶场唯一来源
import test_aipos_f152_deploy_consistency as f152  # noqa: E402  — 部署目录夹具唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f118_post_merge_regression import _isolated_tmp  # noqa: E402,F401  — autouse: 回归临时 worktree 落本用例 tmp
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f152_deploy_consistency import product  # noqa: E402,F401  — pytest fixture
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import post_merge_regression as pmr  # noqa: E402
from tools.aipos_cli.agent_watch_fs import run_fs_watch  # noqa: E402
from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown, envelope_usage  # noqa: E402
from tools.aipos_cli.clock import utc_now  # noqa: E402
from tools.aipos_cli.loop_driver import WaitReexec, exit_code_for, load_loop_contract, run_loop  # noqa: E402

TASK, POLICY = f90.TASK, f90.POLICY
COMMIT_A, COMMIT_B = f152.COMMIT_A, f152.COMMIT_B


def _show(line: str) -> None:
    print(line, flush=True)


def _runs(gov: Path, card: str = TASK) -> list[dict]:
    decl = lrr.run_record_declaration(load_loop_contract())
    metas = [lrr.read_run(p, decl) for p in sorted((gov / "5_tasks" / "records" / "loop_runs" / card).glob("looprun_*.md"))]
    return sorted(metas, key=lambda m: (str(m.get("started_at")), str(m.get("run_id"))))


def _deploy_with_code_stub(base: Path) -> Path:
    """F152 只含 VERSION 的两份假快照 + 产品代码标记文件(续等目标快照须是含产品代码的快照, DeployGuard.current_snapshot 核)。"""
    deploy = f152._fake_deploy(base)
    for name in ("r-a", "r-b"):
        stub = deploy / "releases" / name / "tools" / "aipos_cli" / "deploy_runtime.py"
        stub.parent.mkdir(parents=True)
        stub.write_text("# fixture stub\n", encoding="utf-8")
    return deploy


class _Replaced(BaseException):
    """靶场: 进程已被新快照替换(真 exec 不返回; 靶场在 launcher 里进程内跑「新进程」后以此跳出旧进程栈)。"""

    def __init__(self, result):
        super().__init__("replaced")
        self.result = result


# ===========================================================================
# ① 等待步遇部署更新 → 自动续等
# ===========================================================================

def test_item1_wait_step_deploy_switch_reexecs_to_new_snapshot_and_continues(rig, tmp_path):
    deploy = _deploy_with_code_stub(tmp_path / "product")
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    sleeps: list[float] = []

    def sleeper(seconds: float) -> None:  # 手工等待第一轮后他卡部署了新版本(执行体尚未交回)
        sleeps.append(seconds)
        if len(sleeps) == 1:
            f152._switch(deploy, "r-b")

    def waiting_watch(args, expect_ready, deploy_guard=None):
        assert deploy_guard is not None
        return run_fs_watch(args, expect_ready=expect_ready, deploy_guard=deploy_guard, sleeper=sleeper)

    launched: dict = {}

    def launcher(args, target):
        """替身 = 「新进程」: 产品 CLI 解析器解析续等参数原文, 以新快照(r-b)的运行代码身份进程内跑 run_loop(真 exec 由下一用例实证)。"""
        from tools.aipos_cli.aipos_cli import build_parser

        launched.update(args=list(args), target=Path(target))
        ns = build_parser().parse_args(list(args))
        out2 = io.StringIO()
        res2 = run_loop(ns.task_id, Path(ns.workspace_root), actor=ns.actor, policy_id=ns.envelope, out=out2,
                        watch=f152._guarded(f90._agent_watch(rig, [])), interval=ns.interval, max_wait=ns.max_wait,
                        max_steps=ns.max_steps, deploy_guard=f152._guard(deploy, Path(target).name), continues_run=ns.continues_run)
        raise _Replaced((res2, out2.getvalue(), ns))

    out = io.StringIO()
    with pytest.raises(_Replaced) as replaced:
        run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=waiting_watch, interval=0.05, max_wait=600,
                 max_steps=30, deploy_guard=f152._guard(deploy, "r-a"), wait_reexec=WaitReexec(launcher=launcher, extra_args=["--json"]))
    res2, text2, ns = replaced.value.result
    text = out.getvalue()
    _show(f"---- ① 旧进程(快照 r-a)loop 原文(等待步中部署切到 r-b)----\n{text}")
    _show(f"---- ① 续等参数原文 ----\nlybra {' '.join(launched['args'])}\n目标快照 {launched['target']}")
    _show(f"---- ① 新进程(快照 r-b, continues_run)loop 原文 exit {res2.exit_code} ----\n"
          + "\n".join(l for l in text2.splitlines() if not l.startswith("[ENVELOPE")))
    assert len(sleeps) == 1  # 第一轮等待后即续等(非等满 600s)
    assert "本步是等待步(无副作用) → re-exec 到新部署快照 r-b" in text and "从同一等待步续等" in text
    assert launched["target"] == Path(os.path.realpath(deploy / "releases" / "r-b"))
    assert ns.continues_run and "--json" in launched["args"] and ns.max_steps == 30 - 2  # 认领 1 轮 + 被打断的等待 1 轮
    assert res2.exit_code == exit_code_for(load_loop_contract(), "completed") == 0, text2
    assert f"续等(AIPOS-F166): 接上一运行 {ns.continues_run}" in text2 and "运行部署快照 r-b" in text2
    assert f"({TASK}): lybra queue claim" not in text2 and "[1] wait: " in text2, "已落的步不重复(本卡认领不重做, 首步即回到同一等待步)"
    assert len(f90._records(rig.gov, "claims", TASK)) == 1

    old, new = _runs(rig.gov)[:2]
    _show("---- ① 运行记录: 旧运行(deploy_reexec)与新运行(continues_run)----\n"
          + json.dumps({k: old.get(k) for k in ("run_id", "status", "outcome", "exit_code", "end_reason", "end_message", "reexec")},
                       ensure_ascii=False, indent=1)
          + "\n" + json.dumps({k: new.get(k) for k in ("run_id", "continues_run", "end_reason", "exit_code")}, ensure_ascii=False, indent=1)
          + f"\n新运行 deploy 段: {new['deploy']}")
    assert old["end_reason"] == "deploy_reexec" and old["outcome"] == "continued" and old["exit_code"] is None
    assert old["reexec"]["to_release"] == "r-b" and old["reexec"]["to_commit"] == COMMIT_B and old["deploy"]["changed_to"] == COMMIT_B
    assert new["continues_run"] == old["run_id"] == ns.continues_run and new["deploy"]["commit"] == COMMIT_B
    assert new["end_reason"] == "completed"

    rc, status, _ = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov)])
    _show(f"---- ① lybra loop status 原文 exit {rc} ----\n{status}")
    assert f"续等: 接上一运行 {old['run_id']}" in status


def test_item1_deploy_switch_between_execute_steps_still_deploy_changed(rig, tmp_path):
    deploy = _deploy_with_code_stub(tmp_path / "product")
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    base = f90._agent_watch(rig, [])

    def executor_then_deploy(args, expect_ready, deploy_guard=None):
        rc = base(args, expect_ready)  # 等待就绪(交回已落)后才部署: 下一步是执行步(return)
        f152._switch(deploy, "r-b")
        return rc

    calls: list = []
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=executor_then_deploy, interval=0.05, max_wait=5,
                   max_steps=30, deploy_guard=f152._guard(deploy, "r-a"),
                   wait_reexec=WaitReexec(launcher=lambda args, target: calls.append(args)))
    _show(f"---- ① 执行步之间部署更新 原文 exit {res.exit_code} ----\n{out.getvalue()}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "deploy_changed") == 6 and res.reason == "deploy_changed"
    assert res.message.startswith("部署已更新(本进程运行快照 commit") and "已在步间停下(未执行下一步)" in res.message
    assert calls == [], "执行步之间不续等"
    assert _runs(rig.gov)[-1]["end_reason"] == "deploy_changed"


def test_item1_real_exec_into_new_snapshot_loads_new_code_not_cwd(product):
    """真进程: 跑在快照 r-a 的进程经 exec_into_snapshot 换成 r-b 的代码(F143 lybra_cli_invocation 构造, os.execve 同一 pid);
    调用方 cwd = 主检出且其 aipos_cli 被改坏(中间态)也不遮蔽快照代码。"""
    rel_a = Path(os.path.realpath(product.deploy / "releases" / "r-a"))
    rel_b = product.deploy / "releases" / "r-b"
    shutil.copytree(rel_a, rel_b, copy_function=os.link)
    (rel_b / "VERSION").unlink()  # 硬链接副本: 先断链再写
    (rel_b / "VERSION").write_text(f152._version_text(COMMIT_B, "r-b"), encoding="utf-8")
    f152._switch(product.deploy, "r-b")
    (product.repo / "tools" / "aipos_cli" / "aipos_cli.py").write_text('raise SystemExit("主检出中间态代码被加载")\n', encoding="utf-8")
    code = (f"import os, sys; sys.path.insert(0, {str(rel_a)!r})\n"
            "from tools.aipos_cli.deploy_runtime import DeployGuard\n"
            "from tools.aipos_cli.loop_driver import exec_into_snapshot\n"
            "guard = DeployGuard.for_running_process()\n"
            "print('before-exec pid', os.getpid(), guard.running.mode, guard.running.release, flush=True)\n"
            "target = guard.current_snapshot()\n"
            "exec_into_snapshot(['--version'], target)\n")
    res = subprocess.run([sys.executable, "-c", code], cwd=str(product.repo), env=f152._env(), capture_output=True, text=True, timeout=120)
    _show(f"---- ① 真 re-exec(r-a → r-b, cwd = 改坏的主检出)原文 rc={res.returncode} ----\n{res.stdout}{res.stderr}")
    assert res.returncode == 0, res.stderr
    first, second = res.stdout.splitlines()[:2]
    assert first.startswith("before-exec pid ") and first.endswith(" deploy r-a")
    assert second == f"lybra 部署快照 r-b(commit {COMMIT_B}); 代码根 {os.path.realpath(rel_b)}"


def test_item1_reexec_unavailable_falls_back_to_deploy_changed(rig, tmp_path):
    """新快照不可用(current 指向不含产品代码的快照)= 不续等, 照 deploy_changed 停, 结束原文附原因。"""
    deploy = f152._fake_deploy(tmp_path / "product")  # 快照只有 VERSION, 无产品代码
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)

    def sleeper(_seconds: float) -> None:
        f152._switch(deploy, "r-b")

    def waiting_watch(args, expect_ready, deploy_guard=None):
        return run_fs_watch(args, expect_ready=expect_ready, deploy_guard=deploy_guard, sleeper=sleeper)

    calls: list = []
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=io.StringIO(), watch=waiting_watch, interval=0.05, max_wait=600,
                   max_steps=30, deploy_guard=f152._guard(deploy, "r-a"), wait_reexec=WaitReexec(launcher=lambda a, t: calls.append(a)))
    _show(f"[① 新快照不可用] exit {res.exit_code} reason={res.reason}: {res.message}")
    assert res.exit_code == 6 and res.reason == "deploy_changed" and calls == []
    assert "等待步续等未成(AIPOS-F166): 新部署快照不可用" in res.message and "不含 lybra 产品代码" in res.message


# ===========================================================================
# ② 信封用尽 / 过期: 专门出口 + 续签文案(policy_id +1)+ 预警
# ===========================================================================

def _policy(gov: Path, pid: str, *, max_tasks: int, expires_at: str = "2999-01-01T00:00:00Z", agent: str = "advisor",
            active_from: str = "2026-01-01T00:00:00Z") -> None:
    f90._write(gov / "5_tasks" / "policies" / f"{pid}.md", build_autonomy_policy_markdown(
        policy_id=pid, agent_or_role=agent, active_from=active_from, expires_at=expires_at, max_tasks=max_tasks,
        owner_approval_ref=f"fixture-{pid}", task_selector_task_mode="code", launch_harnesses=["pi"]))


def test_item2_envelope_exhausted_mid_run_gets_dedicated_exit_and_renew_command(rig):
    """gap #152 原样: 信封额度在认领时用完(1/1), 之后的交回步被拒「无覆盖驱动方的有效 autonomy 信封」→ 专门出口(不再泛化 gate_rejected)。"""
    _policy(rig.gov, POLICY, max_tasks=1)  # 认领即用尽
    _policy(rig.gov, "pol_probe_loop_7", max_tasks=5, expires_at="2026-01-02T00:00:00Z")  # 该驱动方同前缀已有最大序号 7(已过期)
    _policy(rig.gov, "pol_other_loop_9", max_tasks=5, agent="advisor.someone-else")  # 别的前缀 / 别的驱动方: 不计
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=f90._agent_watch(rig, []), interval=0.05,
                   max_wait=5, max_steps=30)
    text = "\n".join(l for l in out.getvalue().splitlines() if not l.startswith("[ENVELOPE"))
    _show(f"---- ② loop(信封中途用尽)原文 exit {res.exit_code} reason={res.reason} ----\n{text}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "no_envelope") == 5 and res.outcome == "no_envelope"
    assert res.reason == "envelope_exhausted"
    assert res.message.startswith(f"信封 {POLICY} 额度已用尽(已用 1/1 次, 到期 2999-01-01T00:00:00Z): return 步({TASK})被拒, 未执行")
    assert f"请 Owner 续签(新信封 pol_probe_loop_8 = 该驱动方同前缀已有最大序号 +1, 范围照抄 {POLICY}" in res.message
    assert res.suggested_action.startswith("lybra envelope mint --confirm --policy-id pol_probe_loop_8 --agent-or-role advisor --max-tasks 1 "
                                           "--task-mode code --launch-harness pi --expires-at ")
    assert "门拒原文: 门拒 @ return" in res.message
    meta = _runs(rig.gov)[-1]
    assert meta["end_reason"] == "envelope_exhausted" and meta["envelope_state"]["next_policy_id"] == "pol_probe_loop_8"

    rc, status, _ = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov)])
    _show(f"---- ② lybra loop status 原文 exit {rc} ----\n{status}")
    _rcj, js, _ = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov), "--json"])
    na = json.loads(js)["next_action"]
    _show("---- ② --json next_action 原文 ----\n" + json.dumps(na, ensure_ascii=False, indent=1))
    assert na["action"] == "owner_needed" and na["reason"] == "envelope_renew" and na["end_reason"] == "envelope_exhausted"
    assert f"信封 {POLICY} 已用 1/1 次, 到期 2999-01-01T00:00:00Z(额度已用尽); 续签新信封 pol_probe_loop_8" in na["detail"]
    assert na["hint"] == res.suggested_action
    assert "顾问下一动作: owner_needed(envelope_renew)" in status and "信封预警: pol_probe_loop_1 额度已用尽(已用 1/1 次)" in status

    # 续跑命令带 --envelope(用尽的那张)→ 启动即专门出口, 不给泛化申领出口
    out2 = io.StringIO()
    res2 = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out2, watch=f90._agent_watch(rig, []), interval=0.05,
                    max_wait=5, max_steps=30)
    _show(f"---- ② 续跑(信封仍用尽)原文 exit {res2.exit_code} reason={res2.reason} ----\n{out2.getvalue()}")
    assert res2.reason == "envelope_exhausted" and "loop 启动校验不过, 未推进任何步" in res2.message and "pol_probe_loop_8" in res2.message


def test_item2_envelope_expired_and_mint_hint_policy_id_plus_one(rig):
    _policy(rig.gov, "pol_probe_loop_3", max_tasks=20, expires_at="2026-01-02T00:00:00Z")
    f90._card(rig.gov, TASK, "pending")
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id="pol_probe_loop_3", out=io.StringIO(), interval=0.05, max_wait=1, max_steps=3)
    _show(f"---- ② 信封过期 原文 exit {res.exit_code} reason={res.reason} ----\n{res.message}")
    assert res.exit_code == 5 and res.reason == "envelope_expired"
    assert res.message.startswith("信封 pol_probe_loop_3 已过期(到期 2026-01-02T00:00:00Z; 已用 0/20 次): loop 启动校验不过, 未推进任何步")
    assert "--policy-id pol_probe_loop_4 " in res.suggested_action  # 同前缀已有 1 / 3 → 4

    # 无信封(泛化申领出口)的 policy_id 也不再写死 _1: 该驱动方 pol_<项目>_loop_ 前缀已有最大序号 +1
    from tools.aipos_cli.loop_driver import mint_hint

    project = json.loads((rig.gov / "project.json").read_text(encoding="utf-8")).get("project_id") or "project"
    _policy(rig.gov, f"pol_{project}_loop_5", max_tasks=1, expires_at="2026-01-02T00:00:00Z")
    hint = mint_hint(task_id=TASK, task_fm={"project": project, "task_mode": "code"}, driver_actor=DRIVER, governance_root=rig.gov)
    _show(f"[② 申领出口] {hint}")
    assert f"--policy-id pol_{project}_loop_6 --agent-or-role {DRIVER}" in hint


def test_item2_remaining_and_expiry_warnings_in_loop_status_and_brief(rig):
    soon = (utc_now() + timedelta(hours=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _policy(rig.gov, "pol_probe_warn_1", max_tasks=3)  # 剩余 3 次(≤3)
    _policy(rig.gov, "pol_probe_warn_2", max_tasks=40, expires_at=soon)  # 5 小时后到期(≤24h)
    _policy(rig.gov, "pol_probe_calm_1", max_tasks=40)  # 不预警
    f90._card(rig.gov, TASK, "pending")
    for pid in ("pol_probe_warn_1", "pol_probe_warn_2"):
        rec = lrr.LoopRunRecorder(rig.gov, TASK, driver=DRIVER, contract=load_loop_contract(), warn=_show)
        rec.set_envelope(pid)
        rec.end(outcome="completed", exit_code=0, reason="completed", message="夹具")
        rc, status, _ = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov)])
        line = next(l for l in status.splitlines() if "信封预警" in l)
        _show(f"[② loop status 预警原文 {pid}] {line.strip()}")
        if pid == "pol_probe_warn_1":
            assert "信封预警: pol_probe_warn_1 剩余 3/3 次(阈值: ≤3 次或 ≤10%)" in line
        else:
            assert f"信封预警: pol_probe_warn_2 {soon} 到期(剩 4h5" in line and "阈值 ≤24h" in line
        assert "续签: lybra envelope mint --confirm --policy-id" in line
    assert envelope_usage(rig.gov, "pol_probe_calm_1")["state"] == "active"
    (rig.gov / "governance" / "decision_log").mkdir(parents=True, exist_ok=True)  # brief 读决策日志落点(靶场补空目录)
    rc, brief, err = _cli(["brief", "--workspace-root", str(rig.gov)])
    lines = [l.strip() for l in brief.splitlines() if "信封预警" in l]
    _show(f"---- ② lybra brief 预警原文 rc={rc} ----\n" + "\n".join(lines) + (f"\n[stderr] {err}" if rc else ""))
    assert rc == 0, err
    assert [l.split()[1] for l in lines] == ["pol_probe_warn_1", "pol_probe_warn_2"], "只列达阈值的有效信封"
    rc, js, _ = _cli(["brief", "--workspace-root", str(rig.gov), "--json"])
    assert [w["policy_id"] for w in json.loads(js)["envelope_warnings"]] == ["pol_probe_warn_1", "pol_probe_warn_2"]


# ===========================================================================
# ③ 合并后回归: 进行中记录 / 未完成可见 / 补跑
# ===========================================================================

def _kill_child(child: subprocess.Popen) -> None:
    """只杀本用例 finalize 拉起的回归后台子进程(独立会话: 进程组号 = 其 pid), 精确到该组。"""
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=60)


def test_item3_async_regression_running_record_kill_unfinished_visible_then_rerun(tmp_path):
    r = f118._rig(tmp_path, {"mode": "warn", "execution": "async", "timeout_seconds": 300})
    gov = r["gov"]
    res = f118._finalize(r, f118.CARD_A)
    child = pmr.ASYNC_CHILDREN.pop()
    assert res["verdict"] == "PASS"
    line = f118._marker_lines(res)[0]
    events = f118._events(gov, f118.CARD_A)
    from tools.aipos_cli.frontmatter import require_frontmatter

    meta, body = require_frontmatter(events[0])
    running = meta["post_merge_regression"]
    _show(f"---- ③ finalize 输出一行 ----\n{line}\n---- ③ 启动即落的进行中回归事件记录 {events[0].name} ----\n"
          + json.dumps(running, ensure_ascii=False, indent=1))
    merge = f118._git(r["repo"], "rev-parse", "HEAD")
    assert len(events) == 1 and running["status"] == "running" and running["merge_commit"] == merge
    assert running["async_pid"] == child.pid and running["process_fingerprint"] and running["host"]
    log = Path(running["async_log"])
    assert log.parent == gov / "5_tasks" / "records" / "loop_runs" / f118.CARD_A and log.is_file(), "日志在治理根运行记录旁, 不在 /tmp"
    assert "进行中记录已落, 结论落同一回归事件记录 5_tasks/records/events/" in line
    fin = f118._fin_record(gov, f118.CARD_A)["post_merge_regression"]
    assert fin["status"] == "async_started" and fin["event_record"] == str(events[0].relative_to(gov))

    _kill_child(child)  # 模拟机器重启 / 进程被杀: 回归没跑完
    states = pmr.regression_states(gov, sha=merge)
    assert states[0]["state"] == "incomplete", states

    rc, status, err = _cli(["loop", "status", "--workspace-root", str(gov)])
    _show(f"---- ③ lybra loop status(进程已被杀)原文 rc={rc} ----\n{status}{err}")
    expect = (f"合并后回归未完成: {f118.CARD_A} 合并提交 {merge[:12]} — 合并后回归未完成: 进行中记录(pid {child.pid}, 日志 {log})"
              "无结论且进程已不在(判活: pid + 进程启动时刻指纹; 机器重启 / 进程被杀); "
              f"补跑: lybra regression run --sha {merge} --workspace-root {gov}(预演; 执行加 --confirm --actor <驱动方实例>)")
    assert rc == 0 and expect in status
    rc, nxt, err = _cli(["next", "--task-id", f118.CARD_A, "--workspace-root", str(gov)])
    _show(f"---- ③ lybra next --task-id 原文 rc={rc} ----\n{nxt}{err}")
    assert f"Regression: {expect}" in nxt
    (gov / "governance" / "decision_log").mkdir(parents=True, exist_ok=True)  # brief 读决策日志落点(靶场补空目录)
    rc, brief, err = _cli(["brief", "--workspace-root", str(gov)])
    _show(f"---- ③ lybra brief 回归行原文 rc={rc} ----\n" + "\n".join(l for l in brief.splitlines() if "合并后回归未完成" in l) + err)
    assert expect in brief

    before = sorted(p.relative_to(gov).as_posix() for p in gov.rglob("*") if p.is_file())
    rc, preview, err = _cli(["regression", "run", "--sha", merge[:12], "--workspace-root", str(gov), "--repo-root", str(r["repo"])])
    _show(f"---- ③ lybra regression run --sha <短 sha>(预演)原文 rc={rc} ----\n{preview}{err}")
    assert rc == 0 and "合并后回归状态: 提交 " + merge in preview and "— incomplete: 合并后回归未完成" in preview
    assert "预演(未执行、未写任何文件)" in preview
    assert sorted(p.relative_to(gov).as_posix() for p in gov.rglob("*") if p.is_file()) == before, "预演零写入"
    rc, _o, refused = _cli(["regression", "run", "--sha", merge, "--workspace-root", str(gov), "--repo-root", str(r["repo"]), "--confirm"])
    assert rc == 2 and "--confirm 须给 --actor" in refused

    rc, ran, err = _cli(["regression", "run", "--sha", merge, "--workspace-root", str(gov), "--repo-root", str(r["repo"]),
                         "--confirm", "--actor", f118.ACTOR])
    _show(f"---- ③ lybra regression run --confirm(补跑)原文 rc={rc} ----\n{ran}{err}")
    assert rc == 0, err
    assert f"{pmr.MARKER} 无新增失败" in ran
    events = f118._events(gov, f118.CARD_A)
    concluded = [require_frontmatter(p)[0]["post_merge_regression"] for p in events]
    _show("---- ③ 补跑结论记录(F149 同格式)----\n" + json.dumps(concluded[-1], ensure_ascii=False, indent=1))
    assert len(events) == 2 and concluded[-1]["status"] == "pass" and concluded[-1]["merged_complete"] is True
    assert concluded[-1]["trigger"] == "rerun" and concluded[-1]["action"] == "recorded" and concluded[-1]["merge_commit"] == merge
    assert pmr.regression_states(gov, sha=merge)[0]["state"] == "concluded"
    rc, base, _ = _cli(["--workspace-root", str(gov), "regression", "baseline", "--sha", merge])
    _show(f"---- ③ lybra regression baseline(补跑结论可作基线)原文 rc={rc} ----\n{base}")
    assert "结论: 已记录, 可复用" in base
    rc, again, refused = _cli(["regression", "run", "--sha", merge, "--workspace-root", str(gov), "--repo-root", str(r["repo"])])
    _show(f"[③ 已有完整结论再补跑] rc={rc} {again}{refused}")
    assert rc == 2 and "已有完整结论 pass" in refused
    rc, status, _ = _cli(["loop", "status", "--workspace-root", str(gov)])
    assert "合并后回归未完成" not in status


def test_item3_legacy_async_started_record_without_event_is_incomplete(tmp_path):
    """F166 前的形(10-13 实撞 6d411af): finalization 记录只有 async_started + pid + /tmp 日志, 无事件记录, 进程已不在 = 未完成(仅按 pid 判, 注明)。"""
    gov = tmp_path / "gov"
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=30)  # 本用例自起并已退出的进程号(不在)
    f90._write(gov / "5_tasks" / "records" / "finalizations" / "AIPOS-OLD1" / "finalization_AIPOS-OLD1_1.md",
               "---\nrecord_type: finalization\npost_merge_regression:\n  status: async_started\n  merge_commit: " + "c" * 40
               + f"\n  async_pid: {dead.pid}\n  async_log: /tmp/lybra-post-merge-regression-async-x.log\n---\n# 存量\n")
    rows = pmr.unfinished_regressions(gov)
    _show(f"[③ 存量记录] {rows[0]['line']}")
    assert len(rows) == 1 and rows[0]["task_id"] == "AIPOS-OLD1" and "仅按 pid" in rows[0]["line"] and "F166 前记录无进程指纹" in rows[0]["line"]


# ===========================================================================
# 不变量(防碎片化 / 声明齐)
# ===========================================================================

def test_invariants_single_implementations_and_declarations():
    contract = load_loop_contract()
    run_decl = lrr.run_record_declaration(contract)
    rules = lrr.next_action_declaration(run_decl)
    for reason in ("deploy_reexec", "envelope_exhausted", "envelope_expired"):
        assert reason in run_decl["end_reasons"] and reason in rules["by_end_reason"]
    assert contract["deploy_consistency"]["wait_reexec"]["end_reason"] == "deploy_reexec"
    assert "continues_run" in contract["parameters"]["properties"]
    assert rules["envelope_renew"]["reason"] == "envelope_renew" and rules["by_end_reason"]["envelope_exhausted"] == "owner_needed"
    src = {n: (REPO_ROOT / "tools" / "aipos_cli" / f"{n}.py").read_text(encoding="utf-8")
           for n in ("loop_driver", "post_merge_regression", "loop_run_record", "autonomy_policy")}
    assert "lybra_cli_invocation(" in src["loop_driver"] and "os.execve(" in src["loop_driver"]  # 换代码 = F143 同一构造
    assert "current_snapshot()" in src["loop_driver"] and "releases" not in src["loop_driver"]  # 判定 = F152 DeployGuard
    assert "count_preauthorized_claims(" in src["autonomy_policy"].split("def envelope_usage")[1].split("\ndef ")[0]  # 门侧同一计数
    assert "tempfile.gettempdir()" not in src["post_merge_regression"].split("def _spawn_async")[1].split("\ndef ")[0]
    assert "execute_check(" in src["post_merge_regression"].split("def rerun(")[1]  # 补跑 = F118 同一执行
    from tools.aipos_cli.finalize import _post_merge_import_closure

    closure = set(_post_merge_import_closure())
    assert {"tools.aipos_cli.loop_run_record", "tools.aipos_cli.git_exclude"} <= closure
