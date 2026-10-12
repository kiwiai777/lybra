"""AIPOS-F165(10-12 chris 岚图 LANTU-UI3-S1R 实撞) — 运行证据命令派发前可执行核查 + 拒收文案引导 BLOCK + 拉起 pi 禁提权。

依据: 顾问补审声明 `cd {worktree} && /usr/bin/node tools/lantu/test-pages.js`, 审计主机上 /usr/bin/node 不存在; 审计用实际 node 跑通后
被 F162 逐字判据拒收(RUNTIME_EVIDENCE_MISSING), 于是 sudo 建 /usr/bin/node 系统符号链接再逐字重跑——越过写权限边界改系统。

各件并入点(唯一实现, 禁第二份):
  件① 可执行核查 = 声明校验唯一处 artifact_ingest.check_runtime_evidence_declaration(出卡 draft_validator 与补审派发
      board_adapter._build_audit_dispatch_preview 同调); 声明 transitions runtime_evidence.executable_check; 逐字判据不变。
  件② 拒因逐条差异 + 固定指引 = runtime_evidence_check 拒因(artifact_ingest.runtime_evidence_command_diff / runtime_evidence_guidance);
      固定指引唯一文本源 = roles.schema write_boundary.hard_rules[no_privilege_escalation](执行/审计章程「写权限边界」节同源下发)。
  件③ 禁提权 = 唯一拉起口 harness_launch.LaunchedHarness(exec 前 prctl PR_SET_NO_NEW_PRIVS); 判定唯一实现
      harness_launch.privilege_guard_state(plan_launch 与 check-workstation 同读); 开关 project.json launch_guard
      (workspace_config.project_launch_guard 唯一读取口)。

靶场: F90/F95 临时治理根 + 临时产品仓 + 临时工位 + 进程内门; 拉起用最小命令(读 /proc/self/status 后 exec 假 harness), 不起真实 pi;
不执行 sudo(禁提权效果以内核 NoNewPrivs 标志为证: 该标志置位后 setuid 程序 exec 不再提权, sudo 无从得到 root)。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 靶场唯一来源
import test_aipos_f95_harness_launch as f95  # noqa: E402  — 拉起靶场唯一来源
import test_aipos_f162_runtime_evidence_supplementary_audit as f162  # noqa: E402  — 运行证据/补审靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — pytest fixture(拉起靶场 + 收尾无残留断言)
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.artifact_ingest import validate_task_artifact  # noqa: E402

TASK, AUDIT, SUP1 = f162.TASK, f162.AUDIT, f162.SUP1
MISSING_EXE = "/usr/bin/lybra-f165-no-such-node"
CMD_MISSING = f"cd {{worktree}} && {MISSING_EXE} tools/lantu/test-pages.js"
CMD_PRESENT = f"cd {{worktree}} && env -i PATH=/usr/bin:/bin {sys.executable} tools/lantu/test-pages.py"
CMD_PRIVILEGED = "cd {worktree} && sudo -n ln -s /opt/node /usr/bin/node"
GUIDANCE = "声明命令在本机不可执行时给 BLOCK 并写原因, 由顾问改卡/重派;严禁 sudo、严禁修改系统路径或工作树外文件来凑命令"


def _show(text: str) -> None:
    print(text, flush=True)


def _nnp(status_text: str) -> str:
    return next((line.split(":", 1)[1].strip() for line in status_text.splitlines() if line.startswith("NoNewPrivs:")), "?")


# ===========================================================================
# 件① 派发前可执行核查(出卡 draft create/publish + 补审派发 audit dispatch --supplementary, 同一校验处)
# ===========================================================================

def _draft(tmp_path: Path, task_id: str, commands: list[str]) -> Path:
    payload = {"frontmatter": {"task_id": task_id, "title": f"{task_id} t", "project": "lybra", "context_bundle": "t",
                               "task_mode": "code", "priority": "high", "status": "pending", "needs_owner": False,
                               "assigned_to": f90.EXEC, "agent_instance": f90.EXEC, "audit": "required", "audit_by": AUDITOR,
                               "harness": "claude-code", "output_target": "tests/", "artifact_policy": "formal_write",
                               "audit_runtime_evidence": {"required": True, "commands": commands}},
               "body": f"# {task_id}\n\n## Goal\n做 {task_id}。\n\n## 验收\n- 夹具绿\n"}
    path = tmp_path / f"{task_id.lower()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _create_publish(rig, tmp_path: Path, task_id: str, commands: list[str], *, confirm: bool) -> tuple[str, str, str]:
    """draft create(预演 / 写)→ draft publish(预演 / 写)原文。"""
    tail = [] if confirm else ["--dry-run"]
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "draft", "create", "--from-json", str(_draft(tmp_path, task_id, commands)),
                         "--actor", DRIVER, *tail])
    create = f"rc={rc}\n{out}{err}"
    rc2, out2, err2 = _cli(["--workspace-root", str(rig.gov), "draft", "publish", "--path", f"5_tasks/drafts/{task_id.lower()}.md",
                            "--actor", DRIVER, *tail])
    return create, f"rc={rc2}\n{out2}{err2}", nr._find_task_in_queue(rig.gov, task_id)[1] or "(不在队列)"


def test_item1_draft_preview_reports_missing_executable_confirm_refused_present_passes(rig, tmp_path):
    init_governance_repo(rig.gov)
    # 不存在的绝对路径可执行: 预演即报错(给修法), --confirm 写入 = 拒(不落草稿 / 不发布)
    create, publish, queue = _create_publish(rig, tmp_path, "PROBE-F165-D1", [CMD_MISSING], confirm=False)
    _show(f"[件①·出卡预演·不存在可执行] create {create}")
    assert "RUNTIME_EVIDENCE_COMMAND_NOT_EXECUTABLE" in create and f"`{MISSING_EXE}`" in create
    assert "command -v lybra-f165-no-such-node" in create and "严禁指望审计体 sudo" in create
    create, publish, queue = _create_publish(rig, tmp_path, "PROBE-F165-D1", [CMD_MISSING], confirm=True)
    _show(f"[件①·出卡 --confirm·不存在可执行] create {create}\n  publish {publish}\n  queue={queue}")
    assert create.startswith("rc=1") and "RUNTIME_EVIDENCE_COMMAND_NOT_EXECUTABLE" in create and queue == "(不在队列)"
    # 草稿已在(绕过 create 手放): publish 同一校验拒
    from tools.aipos_cli.draft_writer import render_markdown_task_card

    payload = json.loads(_draft(tmp_path, "PROBE-F165-D2", [CMD_MISSING]).read_text(encoding="utf-8"))
    draft_md = rig.gov / "5_tasks" / "drafts" / "probe-f165-d2.md"
    draft_md.parent.mkdir(parents=True, exist_ok=True)
    draft_md.write_text(render_markdown_task_card(payload["frontmatter"], payload["body"]), encoding="utf-8")
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "draft", "publish", "--path", "5_tasks/drafts/probe-f165-d2.md", "--actor", DRIVER])
    _show(f"[件①·draft publish(--confirm 写)·不存在可执行] rc={rc}\n{out}{err}")
    assert rc == 1 and "RUNTIME_EVIDENCE_COMMAND_NOT_EXECUTABLE" in out + err and nr._find_task_in_queue(rig.gov, "PROBE-F165-D2")[1] is None
    # 提权命令声明: 同一校验拒(不要求审计体提权)
    create, _p, _q = _create_publish(rig, tmp_path, "PROBE-F165-D3", [CMD_PRIVILEGED], confirm=False)
    _show(f"[件①·出卡预演·声明含提权] create {create}")
    assert "RUNTIME_EVIDENCE_COMMAND_PRIVILEGED" in create and "`sudo`" in create
    # 存在的可执行(本机实测绝对路径)+ PATH 名: 通过, 发布入 pending
    create, publish, queue = _create_publish(rig, tmp_path, "PROBE-F165-D4", [CMD_PRESENT, "python3 -m pytest -q tests"], confirm=True)
    _show(f"[件①·出卡 --confirm·存在可执行] create {create}\n  publish {publish}\n  queue={queue}")
    assert create.startswith("rc=0") and publish.startswith("rc=0") and queue == "pending"
    assert "RUNTIME_EVIDENCE_COMMAND" not in create + publish


def test_item1_remote_auditor_marked_unverified_not_refused(rig, tmp_path):
    init_governance_repo(rig.gov)
    f95._land(rig.gov, EXEC, "executor", rig.home / "workstations" / "probe-executor")  # 有接入登记即核身份: 执行者亦须接入
    f95._land(rig.gov, AUDITOR, "auditor", rig.home / "workstations" / "probe-auditor", host="remote-f165-host")
    create, publish, queue = _create_publish(rig, tmp_path, "PROBE-F165-R1", [CMD_MISSING], confirm=True)
    _show(f"[件①·跨机审计工位·未核] create {create}\n  publish {publish}\n  queue={queue}")
    assert create.startswith("rc=0") and publish.startswith("rc=0") and queue == "pending", publish
    assert f"未核: 审计工位 {AUDITOR} 在跨机主机 remote-f165-host" in create and MISSING_EXE in create


def test_item1_supplementary_dispatch_preview_and_confirm_refused_present_passes(rig):
    f162._close_original(rig)
    rc, text = f162._dispatch(rig, "--runtime-evidence-command", CMD_MISSING)
    preview = json.loads(text)
    _show(f"[件①·补审派发预演·不存在可执行] rc={rc} verdict={preview['verdict']} blocking={preview['blocking_reasons']}")
    assert preview["verdict"] == "BLOCK" and any(r.startswith("RUNTIME_EVIDENCE_COMMAND_NOT_EXECUTABLE: ") and MISSING_EXE in r
                                                 for r in preview["blocking_reasons"])
    rc, text = f162._dispatch(rig, "--runtime-evidence-command", CMD_MISSING, "--confirm")
    confirm = json.loads(text)
    _show(f"[件①·补审派发 --confirm·不存在可执行] rc={rc} dry_run={confirm.get('dry_run')} blocking={confirm['blocking_reasons']}")
    assert rc == 1 and any("RUNTIME_EVIDENCE_COMMAND_NOT_EXECUTABLE" in r for r in confirm["blocking_reasons"])
    assert nr._find_task_in_queue(rig.gov, SUP1)[1] is None
    rc, text = f162._dispatch(rig, "--runtime-evidence-command", CMD_PRESENT, "--confirm")
    ok = json.loads(text)
    _show(f"[件①·补审派发 --confirm·存在可执行] rc={rc} verdict={ok['verdict']} blocking={ok['blocking_reasons']}")
    assert rc == 0 and ok["dry_run"] is False and nr._find_task_in_queue(rig.gov, SUP1)[1] == "pending"


# ===========================================================================
# 件② 拒因逐条差异 + 固定指引; 章程渲染含新硬条款
# ===========================================================================

def test_item2_rejection_lists_per_command_diff_and_block_guidance(rig):
    """逐字判据不放松: 实跑把声明的 /usr/bin/node 换成实际 node 路径 = 照旧拒收; 拒因附声明 vs 实跑差异与 BLOCK 指引原文。"""
    declared = "cd {worktree} && /usr/bin/node tools/lantu/test-pages.js"
    f90._card(rig.gov, TASK, "pending", audit_runtime_evidence={"required": True, "commands": [declared]})
    init_governance_repo(rig.gov)
    outcome: dict = {}

    def watch(args, expect_ready, deploy_guard=None):
        if f"/{AUDIT}/" in " ".join(args.expect):
            vals = f162._rendered(rig, AUDIT)
            ran = {"command": f"cd {vals['worktree']} && /home/u/.local/bin/node tools/lantu/test-pages.js", "worktree": vals["worktree"],
                   "exit_code": 0, "log_excerpt": "PASS: actual JavaScript requests"}
            f162._report(rig, AUDIT, "PASS", evidence=[ran])
            outcome.update(validate_task_artifact(AUDIT, rig.gov))
            return 2
        f90._executor_work(rig)
        return 0 if expect_ready([]) else 3

    f162._loop(rig, TASK, watch, max_wait=2)
    _show(f"[件②·拒收原文] category={outcome.get('category')}\n  " + "\n  ".join(outcome.get("reasons") or []))
    assert outcome["category"] == "RUNTIME_EVIDENCE_MISSING" and not outcome["ok"]
    text = "\n".join(outcome["reasons"])
    assert "逐条差异: 声明" in text and "声明 `/usr/bin/node` ≠ 实跑 `/home/u/.local/bin/node`" in text
    assert "固定指引(硬条款 no_privilege_escalation)" in text and GUIDANCE in text


@pytest.mark.parametrize("shape,project", [("lybra", "lybra"), ("chris", "chris-huibojin")])
def test_item2_executor_and_auditor_charters_render_same_hard_clause(tmp_path, monkeypatch, shape, project):
    from test_aipos_f80_zero_gate_charter import FOREIGN_TO_CHRIS, _identity, _make_gov
    from tools.aipos_cli.charter_render import charter_render_context, render_charter

    gov = _make_gov(tmp_path, monkeypatch, shape=shape)
    for role, prefix in (("executor", "exec"), ("auditor", "audit")):
        harness = tmp_path / f"pi-{shape}" / f"{project}-{role}"
        harness.mkdir(parents=True)
        master = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        ctx = charter_render_context(gov, identity=_identity(harness, role, f"{prefix}.{project}.hostx", project), product_commit="fixture")
        text = render_charter(master, ctx)
        line = next(l for l in text.splitlines() if l.startswith("- **no_privilege_escalation**"))
        _show(f"[件②·章程渲染·{project}·{role}] {line}")
        assert GUIDANCE in line and "{{" not in text and "AIPOS-F165 硬条款" in text
        if shape == "chris":
            assert FOREIGN_TO_CHRIS.findall(text) == []


# ===========================================================================
# 件③ loop 拉起 pi 禁提权(no_new_privs); 最小命令模拟拉起(读 /proc/self/status 后 exec 假 harness), 不起真实 pi、不执行 sudo
# ===========================================================================

_PROBE = r"""
import os, sys
log = os.environ["FAKE_HARNESS_LOG"]
os.makedirs(log, exist_ok=True)
kickoff = sys.argv[2]
card = next((l.split("已认领任务卡 ", 1)[1].split("。", 1)[0] for l in kickoff.splitlines() if "已认领任务卡 " in l), "unknown")
with open(os.path.join(log, card + ".status"), "w") as fh:
    fh.write(open("/proc/self/status").read())
os.execv(sys.executable, [sys.executable, sys.argv[1], kickoff])
"""


def _probe_harness(lrig, monkeypatch) -> None:
    from tools.aipos_cli import autonomy_policy

    probe = lrig.home / "nnp_probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    monkeypatch.setattr(autonomy_policy, "launchable_harnesses",
                        lambda: {"pi": {"argv": [sys.executable, str(probe), str(f95.FAKE), "{kickoff}"], "events": "pi-json"}})


def _run(lrig, card: str) -> tuple[object, str]:
    from tools.aipos_cli.loop_driver import run_loop

    out = io.StringIO()
    res = run_loop(card, lrig.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05, max_wait=60, max_steps=30)
    return res, out.getvalue()


def _set_guard(gov: Path, guard: dict | None) -> None:
    pj = gov / "project.json"
    data = json.loads(pj.read_text(encoding="utf-8"))
    if guard is None:
        data.pop("launch_guard", None)
    else:
        data["launch_guard"] = guard
    pj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_record_launches(gov: Path, card: str) -> list[dict]:
    out: list[dict] = []
    for path in sorted((gov / "5_tasks" / "records" / "loop_runs" / card).glob("looprun_*.md")):
        fm = nr._read_frontmatter(path)
        out += list(fm.get("launches") or [])
    return out


def test_item3_default_no_new_privs_disabled_and_invalid_declaration(lrig, monkeypatch):
    from tools.aipos_cli.harness_launch import no_new_privs_supported

    assert no_new_privs_supported(), "夹具主机须为 Linux(prctl 可用)"
    parent = _nnp(Path("/proc/self/status").read_text())
    _probe_harness(lrig, monkeypatch)
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, f95.AUDIT_CLAIMER, "auditor", lrig.ws_audit)
    # (a) 缺省(无 launch_guard): 执行体与审计体都以 no_new_privs 拉起
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    res, text = _run(lrig, TASK)
    _show(f"---- 件③(a) 缺省开 lybra loop 原文 ----\n{text}")
    assert res.exit_code == 0 and res.outcome == "completed", text
    for card in (TASK, AUDIT):
        child = _nnp((lrig.log / f"{card}.status").read_text())
        _show(f"[件③(a)] {card} 拉起进程 /proc/self/status NoNewPrivs: {child}(loop 进程自身 {parent})")
        assert child == "1"
    assert "禁提权 applied: no_new_privs 已施加(机制 prctl PR_SET_NO_NEW_PRIVS)" in text
    events = [e for c in (TASK, AUDIT) for e in f95._events(lrig.gov, c) if "harness_launch" in e]
    _show("[件③(a)] session Events: " + " | ".join(events))
    assert len(events) == 2 and all("privilege_guard=applied(" in e for e in events)
    launches = _run_record_launches(lrig.gov, TASK)
    _show(f"[件③(a)] 运行记录 launches[].privilege_guard: {[l.get('privilege_guard') for l in launches]}")
    assert launches and all((l.get("privilege_guard") or {}).get("state") == "applied" for l in launches)
    # (b) Owner 授权关闭: 不施加, 拉起进程 NoNewPrivs = 继承 loop 进程自身(沙箱外为 0)
    task_b = "PROBE-F165-OFF"
    _set_guard(lrig.gov, {"no_new_privs": False, "owner_approval_ref": "decision_2026-10-12_f165_fixture"})
    f90._card(lrig.gov, task_b, "pending", harness="pi")
    res, text = _run(lrig, task_b)
    _show(f"---- 件③(b) 关闭声明 lybra loop 原文 ----\n{text}")
    assert res.exit_code == 0 and res.outcome == "completed", text
    child = _nnp((lrig.log / f"{task_b}.status").read_text())
    _show(f"[件③(b)] {task_b} 拉起进程 NoNewPrivs: {child}(loop 进程自身 {parent}; 关闭 = 不施加, 只继承)")
    assert child == parent
    assert "禁提权 disabled: project.json launch_guard.no_new_privs=false 关闭(Owner 授权 decision_2026-10-12_f165_fixture)" in text
    # (c) 关闭而缺 Owner 授权 = 声明不合, 拒拉起(fail-closed, 退回手工)
    from tools.aipos_cli import loop_driver
    from tools.aipos_cli.autonomy_policy import load_policy

    _set_guard(lrig.gov, {"no_new_privs": False})
    task_c = "PROBE-F165-BAD"
    f90._card(lrig.gov, task_c, "pending", harness="pi")
    plan = loop_driver.plan_launch(lrig.gov, task_c, policy=load_policy(lrig.gov, f95.POLICY_LAUNCH), no_launch=False,
                                   decl=loop_driver.launch_declaration(loop_driver.load_loop_contract()), already_launched=set())
    _show(f"[件③(c) 关闭缺授权] refusal={plan.refusal}\n  manual_hint={plan.manual_hint}")
    assert plan.refusal.startswith("拉起禁提权声明不可用(fail-closed, 不拉起): LAUNCH_GUARD_INVALID: ") and "缺 owner_approval_ref" in plan.refusal
    assert not plan.argv


def test_item3_check_workstation_shows_guard_state(lrig, monkeypatch):
    f95._land(lrig.gov, f95.AUDIT_CLAIMER, "auditor", lrig.ws_audit)
    argv = ["project", "check-workstation", lrig.gov.name, "--home-root", str(lrig.gov.parent), "--instance", f95.AUDIT_CLAIMER, "--harness", "pi"]
    for label, guard in (("缺省", None), ("关闭", {"no_new_privs": False, "owner_approval_ref": "decision_fixture"}),
                         ("声明不合", {"no_new_privs": "off"})):
        _set_guard(lrig.gov, guard)
        rc, out, err = _cli(argv)
        _show(f"---- 件③ check-workstation({label}) rc={rc} ----\n{out}{err}")
        if label == "缺省":
            assert rc == 0 and "拉起禁提权(no_new_privs): applied — no_new_privs 已施加(机制 prctl PR_SET_NO_NEW_PRIVS)" in out
        elif label == "关闭":
            assert rc == 0 and "拉起禁提权(no_new_privs): disabled — project.json launch_guard.no_new_privs=false 关闭(Owner 授权 decision_fixture)" in out
        else:
            assert rc == 1 and "✗ 拉起禁提权声明: LAUNCH_GUARD_INVALID:" in out and "(loop 将拒拉起)" in out
    # 跨机工位: 如实降级(远端不施加)
    from tools.aipos_cli.harness_launch import privilege_guard_state
    from tools.aipos_cli.loop_driver import launch_declaration, load_loop_contract

    _set_guard(lrig.gov, None)
    remote = privilege_guard_state(launch_declaration(load_loop_contract()), lrig.gov, "remote")
    _show(f"[件③ 跨机降级] {remote}")
    assert remote["state"] == "degraded" and "远端机制未实现" in remote["detail"]


def test_item3_single_launch_entry_and_declarations():
    """禁提权只挂唯一拉起口; 新键只进既有 schema 并带缺省(缺段 = 开)。"""
    import re
    import subprocess

    from tools.schema_loader import load_schema

    hits = subprocess.run(["git", "grep", "-n", "-E", r"preexec_fn|PR_SET_NO_NEW_PRIVS|no_new_privs_preexec", "--", "tools/"],
                          cwd=REPO_ROOT, capture_output=True, text=True).stdout.splitlines()
    _show("[件③ 唯一施加处] git grep preexec_fn|PR_SET_NO_NEW_PRIVS -- tools/:\n" + "\n".join(hits))
    # runall_isolation = run-all 测试执行沙箱的 Landlock 启动器(测试执行层, 非 harness 拉起层), 显式列出
    assert {h.split(":", 1)[0] for h in hits} == {"tools/aipos_cli/harness_launch.py", "tools/aipos_cli/runall_isolation.py"}
    assert all("preexec_fn" not in h for h in hits if h.startswith("tools/aipos_cli/runall_isolation.py"))
    src = (REPO_ROOT / "tools" / "aipos_cli" / "harness_launch.py").read_text(encoding="utf-8")
    assert len(re.findall(r"subprocess\.Popen\(", src)) == 1 and "preexec_fn=preexec" in src
    guard = load_schema("config")["configuration_sources"]["project_json"]["schema"]["launch_guard"]
    assert guard["schema"]["no_new_privs"]["default"] is True and "LAUNCH_GUARD_INVALID" in guard["reject_codes"]
    assert set(load_schema("verbs")["verbs"]["lybra_loop"]["launch"]["privilege_guard"]["states"]) == {"applied", "disabled", "degraded"}
