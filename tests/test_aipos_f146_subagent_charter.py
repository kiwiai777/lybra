"""AIPOS-F146 — 子 agent 执行者取章程: `lybra charter` 认 subagent 模式实例(无工位; 身份 = land 事件 role/mode/harness, 判定唯一
enrollment.instance_enrollment), 按其 harness(codex / claude-code)取执行者章程(distribution.schema executor-charter-<kind>, 母本仍是唯一的
agents/roles/executor/AGENTS.md, 按渲染上下文 executor_mode 选段); 同类读点(loop 等待提示 / check-workstation / my-tasks / 跨机开工材料)
对 subagent 实例给出正确行为或明确出口。

靶场: AIPOS-F90 rig(tmp 治理根 + 进程内门)+ AIPOS-F143 子 agent 接入夹具(_issue/_enroll: 门侧同一兑换/land 实现, 只替换 HTTP 传输);
pi 工位与 land 事件用 AIPOS-F95 夹具同一 writer。禁真门 / 真治理根 / 真工位 / 真 pi。

验收(卡面 ★验收):
 ① 临时治理根: 子 agent 执行者 `lybra charter --role executor --instance <实例>` 成功且渲染含零门规则原文; pi 工位实例行为不变原文;
    未接入 / 已作废实例拒原文; subagent 与 pi 两种渲染各一份原文对照
 ② loop 等子 agent 交回时提示文案原文
 ③ 读点(check-workstation / my-tasks / 跨机开工材料)对 subagent 实例的出口原文
"""
from __future__ import annotations

import io
import json
import re
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC  # noqa: E402
from test_aipos_f90_loop_one_stage import POLICY, TASK, rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f95_harness_launch import _land as _land_ws, _workstation  # noqa: E402  — pi 工位 / land 事件同一 writer
from test_aipos_f143_execution_mode import _cli, _enroll, _issue, _land, _meta  # noqa: E402  — 子 agent 接入夹具唯一来源
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

MASTER = REPO_ROOT / "agents" / "roles" / "executor" / "AGENTS.md"
SECRET = "fixture-subagent-exec"
ZERO_GATE = (
    "**你不接触门** — 不调用任何 `lybra_*` 动词、不读 `connection.json`/token。",
    "**职责终点=写完报告** — `RETURN.md` 写完即停;认领/交回/裁决/finalize 由产品做(产品执行 `lybra next --run` 等命令)。",
    "**认领/进度/交回全由驱动方经门完成**",
)
PI_ONLY = ("跑在 Pi 上", "**开工只走 `/go`**", "**不接受贴卡号/卡路径冷启动**", "(以 `/go` 开工提示所列为准")
SUB_ONLY = ("是顾问在其会话里派生的子 agent 执行者", "**开工 = 顾问按卡路径派活**", "你不敲 `/go`、不自己找卡、不自己认领",
            "(以卡面「报告必填」所列为准")


def _show(text: str) -> None:
    print(text, flush=True)


def _code(name: str) -> int:
    return declared_exit_code("lybra_charter", name)


def _enroll_subagent(rig, harness: str, host: str = "advhost") -> dict:
    gate = rig.gov.parent / "gate"
    return _enroll(gate, _issue(gate, rig.gov, "executor", EXEC), rig.gov, "executor", EXEC, SECRET,
                   executor_mode="subagent", harness_kind=harness, harness_host=host)


def _body(rendered: str) -> str:
    """渲染物去掉末行渲染标记(含母本/上下文指纹)后的正文。"""
    lines = rendered.rstrip("\n").splitlines()
    assert lines[-1].startswith("<!-- lybra:charter-render")
    return "\n".join(lines[:-1])


# ===========================================================================
# ① 子 agent 执行者取章程(codex / claude-code)+ 零门原文 + subagent 段
# ===========================================================================

@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_item1_subagent_executor_charter_by_land_harness(rig, harness):
    from tools.aipos_cli import charter_render as cr
    from tools.aipos_cli.enrollment import instance_enrollment

    role_before = (rig.gov / ".lybra" / "role").read_bytes()
    res = _enroll_subagent(rig, harness)
    assert res["ok"] and res["executor_mode"] == "subagent"
    view = instance_enrollment(rig.gov, EXEC)
    _show(f"[① 接入判定 {harness}] role={view['role']} mode={view['mode']} harness={view['harness']} host={view['host']} "
          f"governance_root_mode={view['governance_root_mode']} via={view['via']} workstation.found={view['workstation']['found']}")
    assert (view["role"], view["mode"], view["harness"], view["governance_root_mode"]) == ("executor", "subagent", harness, True)

    rc, out, err = _cli("charter", "--role", "executor", "--instance", EXEC, "--workspace-root", str(rig.gov))
    _show(f"---- ① `lybra charter --role executor --instance {EXEC}`(子 agent, harness={harness})exit {rc}, stdout 前 12 行 ----\n"
          + "\n".join(out.splitlines()[:12]) + f"\n... (共 {len(out.splitlines())} 行)")
    assert rc == _code("ok") == 0, err
    assert all(line.startswith("Warning: ") for line in err.splitlines()), err  # rig 连接文件无 token_ref 的注册表告警之外无拒因
    for needle in ZERO_GATE + SUB_ONLY:
        assert needle in out, f"缺原文 {needle!r}"
    for needle in PI_ONLY:
        assert needle not in out, f"子 agent 章程不应含 pi 口径 {needle!r}"
    assert "lybra:executor-mode" not in out and "{{" not in out and f"实例 `{EXEC}`" in out
    assert SECRET not in out and "fixture-not-a-secret" not in out
    zero = [ln for ln in out.splitlines() if "你不接触门" in ln or "职责终点=写完报告" in ln]
    _show("[① 零门规则原文]\n" + "\n".join(zero))

    result = cr.instance_charter(rig.gov, role_class="executor", instance=EXEC)
    _show(f"[① instance_charter] distribution_id={result['distribution_id']} harness={result['harness']} "
          f"workstation={result['workstation']} executor_mode={result['executor_mode']}")
    assert (result["distribution_id"], result["harness"], result["workstation"], result["executor_mode"]) == (
        f"executor-charter-{harness}", harness, None, "subagent")
    assert result["text"] == out
    # 子 agent 不写 .lybra/role(身份源 = land 事件), 取章程只读
    assert (rig.gov / ".lybra" / "role").read_bytes() == role_before
    # 显式 --harness pi 取 pi 条目(同一母本, 段仍按接入模式选 subagent)
    pi = cr.instance_charter(rig.gov, role_class="executor", instance=EXEC, harness="pi")
    assert pi["distribution_id"] == "executor-charter" and _body(pi["text"]) == _body(out)
    # 角色类不符 = 拒
    rc, so, se = _cli("charter", "--role", "auditor", "--instance", EXEC, "--workspace-root", str(rig.gov))
    _show(f"[① 角色类不符] exit {rc}: {se.strip().splitlines()[-1]}")
    assert rc == _code("refused") and "≠ --role 'auditor'" in se and so == ""


# ===========================================================================
# ① pi 工位实例行为不变 + 两种渲染原文对照 + 未接入 / 已作废拒原文
# ===========================================================================

def _independent_pi_master(master: str) -> str:
    """独立于产品选择实现的对照: 母本去掉 subagent 段与段标记行 = pi 工位应得的母本正文。"""
    out = re.sub(r"<!-- lybra:executor-mode subagent -->\n.*?(?=<!-- lybra:executor-mode end -->\n)", "", master, flags=re.S)
    return re.sub(r"^<!-- lybra:executor-mode \S+ -->\n", "", out, flags=re.M)


def test_item1_pi_workstation_unchanged_and_two_renders_side_by_side(rig, tmp_path):
    from tools.aipos_cli import charter_render as cr
    from tools.distribution_manifest import get_product_commit

    ws = _workstation(tmp_path / "home", rig.gov, "exec", "executor", EXEC)
    _land_ws(rig.gov, EXEC, "executor", ws)
    rc, pi_out, err = _cli("charter", "--role", "executor", "--instance", EXEC, "--workspace-root", str(rig.gov))
    assert rc == 0, err
    master = MASTER.read_text(encoding="utf-8")
    ctx = cr.charter_render_context(rig.gov, identity=cr.workstation_identity(ws), product_commit=get_product_commit())
    assert ctx["executor_mode"] == "workstation"  # 工位身份无 executor_mode = 声明缺省
    assert pi_out == cr.render_charter(master, ctx)
    assert _body(pi_out) == _body(cr.render_charter(_independent_pi_master(master), ctx))  # pi 段 = 修前母本正文
    for needle in ZERO_GATE + PI_ONLY:
        assert needle in pi_out, needle
    for needle in SUB_ONLY:
        assert needle not in pi_out, needle
    assert "lybra:executor-mode" not in pi_out
    result = cr.instance_charter(rig.gov, role_class="executor", instance=EXEC)
    assert (result["distribution_id"], result["harness"], result["workstation"], result["executor_mode"]) == (
        "executor-charter", "pi", str(ws), None)

    # 同一母本按 subagent 渲染(同一 render_charter; 身份 = subagent_identity 形)——两份原文对照
    sub_identity = {**cr.workstation_identity(ws), "harness_root": str(rig.gov), "executor_mode": "subagent"}
    sub_out = cr.render_charter(master, cr.charter_render_context(rig.gov, identity=sub_identity, product_commit=get_product_commit()))
    pi_lines, sub_lines = pi_out.splitlines(), sub_out.splitlines()
    import difflib

    diff = [ln for ln in difflib.unified_diff(pi_lines, sub_lines, "pi 工位渲染", "子 agent 渲染", n=0, lineterm="")]
    _show("---- ① 同一母本两种渲染原文对照(unified diff, 只列不同行) ----\n" + "\n".join(diff))
    assert any("开工只走 `/go`" in ln for ln in diff if ln.startswith("-"))
    assert any("开工 = 顾问按卡路径派活" in ln for ln in diff if ln.startswith("+"))


def test_item1_unenrolled_and_voided_instances_refused(rig):
    from tools.aipos_cli.enrollment import _trail_line, _write_trail_line, enrollment_trail_path

    rc, out, err = _cli("charter", "--role", "executor", "--instance", EXEC, "--workspace-root", str(rig.gov))
    _show(f"[① 未接入] exit {rc}: {err.strip().splitlines()[-1]}")
    assert rc == _code("refused") and "未在本治理根接入" in err and out == ""

    _enroll_subagent(rig, "codex")
    trail = enrollment_trail_path(rig.gov)
    lines = trail.read_text(encoding="utf-8").splitlines()
    land_no = [i for i, ln in enumerate(lines, start=1) if " land " in ln and f"instance={EXEC}" in ln]
    assert land_no
    _write_trail_line(trail, _trail_line(action="void", code_id="-", role="executor", instance=EXEC, project="lybra",
                                         by="advisor.fixture", reason="F146 夹具作废",
                                         extra=f"voids_lines={','.join(map(str, land_no))}  voids_code_ids=enroll_fixture"))
    rc, out, err = _cli("charter", "--role", "executor", "--instance", EXEC, "--workspace-root", str(rig.gov))
    _show(f"[① 已作废 land 事件] exit {rc}: {err.strip().splitlines()[-1]}")
    assert rc == _code("refused") and "未在本治理根接入" in err and out == ""


def test_item1_segment_syntax_fail_closed():
    from tools.aipos_cli.charter_render import select_mode_segments

    good = "a\n<!-- lybra:executor-mode workstation -->\nP\n<!-- lybra:executor-mode subagent -->\nS\n<!-- lybra:executor-mode end -->\nz\n"
    assert select_mode_segments(good, "workstation") == "a\nP\nz\n"
    assert select_mode_segments(good, "subagent") == "a\nS\nz\n"
    assert select_mode_segments("no markers {{x}}\n", "anything") == "no markers {{x}}\n"  # 无段标记 = 原样
    for bad, needle in (
        (good.replace("<!-- lybra:executor-mode end -->\n", ""), "未闭合"),
        (good.replace("subagent -->", "ghost -->"), "不在 roles.schema executor_modes"),
        ("<!-- lybra:executor-mode workstation -->\nP\n<!-- lybra:executor-mode end -->\n", "覆盖模式"),
        ("x\n<!-- lybra:executor-mode end -->\n", "不在任何分段组内"),
        (good.replace("<!-- lybra:executor-mode subagent -->", "<!-- lybra:executor-mode workstation -->"), "重复"),
        ("<!-- lybra:executor-mode  workstation  -->\n", "形坏"),
    ):
        with pytest.raises(ValueError, match=needle):
            select_mode_segments(bad, "workstation")
    with pytest.raises(ValueError, match="executor_mode='ghost'"):
        select_mode_segments(good, "ghost")


def test_item1_executor_charter_declarations_one_master():
    from tools.aipos_cli.distribution_sync import declared_harness_kinds, harness_distributions
    from tools.aipos_cli.workstation_wiring import declared_role_distributions

    dists = declared_role_distributions("executor", "executor")
    by_kind = {k: [d for d in harness_distributions(dists, k) if d.get("kind") == "charter"] for k in declared_harness_kinds()}
    _show(f"[① 执行者章程条目] { {k: [d['distribution_id'] for d in v] for k, v in by_kind.items()} }")
    assert all(len(v) == 1 for v in by_kind.values()), by_kind
    assert {d["source_path"] for v in by_kind.values() for d in v} == {"agents/roles/executor/AGENTS.md"}  # 母本只一份


# ===========================================================================
# ② loop 等子 agent 交回时的提示原文  ③ 读点出口
# ===========================================================================

def _declare_and_publish(rig, harness: str) -> None:
    from tools.aipos_cli.draft_writer import create_draft, publish_draft

    _enroll_subagent(rig, harness)
    _land(rig.gov, AUDITOR, "auditor", f"host={socket.gethostname()} workstation={rig.gov.parent / 'ws-audit'} files=['connection.json']")
    rc, _out, err = _cli("project", "set-execution", "--workspace-root", str(rig.gov), "--subagent-executor", EXEC, "--auditor", AUDITOR,
                         "--confirm")
    assert rc == 0, err
    created = create_draft(rig.gov, _meta(TASK), "## Goal\n\nx\n")
    published = publish_draft(rig.gov, created["target_path"])
    assert published["verdict"] != "BLOCK"


def test_item2_loop_wait_hint_for_subagent(rig):
    from tools.aipos_cli.loop_driver import launch_declaration, load_loop_contract, plan_launch, run_loop

    _declare_and_publish(rig, "codex")
    out = io.StringIO()
    run = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=lambda args, expect_ready: 3, interval=0.05, max_wait=1)
    text = out.getvalue()
    _show("---- ② lybra loop 输出原文(认领后等子 agent 交回) ----\n" + text)
    assert run.steps[0].action_type == "claim" and run.steps[0].ok
    hint_line = next(ln for ln in text.splitlines() if "manual:" in ln)
    assert f"请顾问在 codex 会话派子 agent(实例 {EXEC}, 无工位)按卡执行并写交回" in hint_line
    assert f"lybra my-tasks --actor {EXEC} --task-id {TASK}" in hint_line and f"lybra charter --role executor --instance {EXEC}" in hint_line
    assert "/go" not in hint_line and "工位位置定位不到" not in hint_line
    assert f"实例 {EXEC} 为子 agent 执行者(接入模式 subagent, 无工位), loop 不拉起" in hint_line
    plan = plan_launch(rig.gov, TASK, policy=None, no_launch=False, decl=launch_declaration(load_loop_contract()), already_launched=set())
    assert plan.refusal and plan.argv == [] and plan.manual_hint in hint_line


def test_item3_read_points_give_subagent_exits(rig):
    from tools.aipos_cli.loop_driver import check_workstation, run_loop
    from tools.aipos_cli.next_resolver import remote_kickoff_context

    _declare_and_publish(rig, "claude-code")
    run = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=io.StringIO(), watch=lambda args, expect_ready: 3, interval=0.05,
                   max_wait=1)
    assert run.steps[0].action_type == "claim" and run.steps[0].ok  # 驱动方以子 agent 执行者身份认领

    # my-tasks --actor <子 agent>: 正确行为 = 开工材料(工作树 / 报告落点 / 卡路径), 顾问据此派子 agent
    rc, out, err = _cli("my-tasks", "--actor", EXEC, "--workspace-root", str(rig.gov), "--task-id", TASK, "--json")
    assert rc == 0, err
    kickoff = json.loads(out)["next_card"]["kickoff"]
    _show(f"---- ③ my-tasks --actor {EXEC} --task-id {TASK} 的 kickoff 原文 ----\n{kickoff}")
    assert kickoff.startswith(f"已认领任务卡 {TASK}。") and "报告落点:" in kickoff and "任务卡路径:" in kickoff

    # my-tasks --workstation <治理根> --actor <子 agent>: 拒 + 明确出口
    rc, out, err = _cli("my-tasks", "--workstation", str(rig.gov), "--actor", EXEC, "--task-id", TASK, "--json")
    _show(f"[③ my-tasks --workstation <治理根> --actor 子 agent] exit {rc}: {err.strip()}")
    assert rc == 2 and f"实例 {EXEC} 为子 agent 执行者(接入模式 subagent, 无工位), 出口: lybra my-tasks --actor {EXEC}" in err

    # check-workstation: 不适用 + 等待提示出口
    rep = check_workstation(rig.gov, EXEC, "claude-code")
    _show(f"[③ check-workstation 子 agent] {json.dumps(rep, ensure_ascii=False)}")
    assert not rep["ok"] and rep["checks"][0]["check"] == "接入模式" and "loop 不拉起、无工位可检查" in rep["checks"][0]["detail"]
    assert "请顾问在 claude-code 会话派子 agent" in rep["checks"][0]["detail"]

    # 跨机开工材料(--remote-workstation): 拒 + 出口
    with pytest.raises(ValueError) as exc:
        remote_kickoff_context(rig.gov, EXEC)
    _show(f"[③ remote_kickoff_context 子 agent] {exc.value}")
    assert "不适用 --remote-workstation" in str(exc.value) and f"lybra my-tasks --actor {EXEC}" in str(exc.value)
