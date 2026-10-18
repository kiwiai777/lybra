"""AIPOS-F156 — 子 agent 模式等交回超时归例行(件①, gap #139) + 共用仓卡归属: 取下一张只取本子项目的卡、loop 拒跑别人的卡(件②, gap #140)
+ 发布记录归因(件③, gap #141)。

各件唯一并入点(禁第二路径):
  件① 执行方式 = workspace_config.card_execution_mode(只读 F143 声明唯一读取口 project_execution; 起草缺省 draft_writer.apply_execution_defaults
      同一谓词 execution_wants_pi); 下一动作映射仍只在 verbs.schema lybra_loop_status.next_action(by_execution_mode 与 by_end_reason 同节,
      修法在 routine_rejections.end_reasons.wait_timeout, 判定唯一 loop_run_record.next_action); 开工提示 = loop_driver.plan_launch 的
      manual_hint.subagent(声明 verbs.schema lybra_loop.launch.manual_hint)。
  件② 卡归属 = machine_zone.card_owner(门侧发布记录签发实例; 存量回退卡面 created_by 标「自报」); 归属视角 = machine_zone.owner_viewer;
      四视图(next / brief / needs-owner / loop status 结案取下一张)经唯一可见卡入口 machine_zone.visible_cards(viewer=)同一判定;
      loop 启动核归属 = machine_zone.card_owner_refusal(结束原因 card_owner_mismatch → owner_needed)。声明 verbs.schema lane_view.owner_view。
  件③ 签发实例 = draft_writer.resolve_publish_signer(核验走 F154 唯一口 two_phase_shell_factory.signer_role_for_actor; 推断只读
      driver_token_instances 条目字段)。

靶场: F134 多顾问靶场 `two`(临时治理根 + 进程内真门 + 双仓 ota / lantu)加第三个顾问实例 ADV_LT(信封限 lane {lantu, ota}; ota = 共用仓,
同 chris web6); 件① 用 F90 靶场 rig。无真实凭据、不连真门、不读真实治理根。
"""
from __future__ import annotations

import ast
import io
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f134_multi_advisor_lane_envelope as f134  # noqa: E402  — 多顾问靶场唯一来源
import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 进程内真门 + loop 全链靶场
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import POLICY, TASK, rig  # noqa: E402,F401  — pytest 夹具
from test_aipos_f134_multi_advisor_lane_envelope import ADV_MAIN, ADV_OTA, POL_MAIN, POL_OTA, two  # noqa: E402,F401
from test_aipos_f143_execution_mode import _cli  # noqa: E402  — 进程内 CLI 唯一来源
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

ADV_LT = "advisor-lantu.f134probe.h1"
TOK_LT = "fixture-adv-lantu"
POL_LT = "pol_f156_lantu"
OTA_PUB, LT_PUB, LEGACY, LT_DONE = "PROBE-OTA-52", "PROBE-LT-4", "PROBE-OTA-OLD", "PROBE-LT-3"


def _show(text: str) -> None:
    print(text, flush=True)


def _stop_watch(args, expect_ready, **_kw):  # 认领后不模拟子 agent: 等待即超时(声明退出码 3)
    return 3


# ===========================================================================
# 件① 执行方式分流: 子 agent 模式 wait_timeout = advisor_fix(例行), pi 模式仍 owner_needed
# ===========================================================================

def _declare_subagent(gov: Path) -> None:
    decl = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    decl["execution"] = {"default_mode": "subagent", "subagent_executor": EXEC, "auditor": AUDITOR}
    _write(gov / "project.json", json.dumps(decl))


def _run_until_wait_timeout(gov: Path, seen: dict) -> object:
    def watch(args, expect_ready, **_kw):
        # 等待中(手工模式 = 不拉起)顾问下一动作原文: 同一 CLI `lybra loop status`
        seen["kickoff"] = _cli("loop", "status", "--task-id", TASK, "--workspace-root", str(gov))
        return 3

    return run_loop(TASK, gov, actor=DRIVER, policy_id=POLICY, out=io.StringIO(), watch=watch, interval=0.05, max_wait=1,
                    max_steps=10)


def test_item1_subagent_wait_timeout_is_routine_advisor_fix(rig):
    gov = rig.gov
    _declare_subagent(gov)
    f90._card(gov, TASK, "pending")  # harness=claude-code, assigned_to=EXEC = 子 agent 执行者
    init_governance_repo(gov)
    seen: dict = {}
    res = _run_until_wait_timeout(gov, seen)
    assert res.outcome == "wait_timeout" and res.reason == "wait_timeout", res.message
    rc, out, err = seen["kickoff"]
    _show(f"---- 件①·子 agent 模式等待中 lybra loop status 原文 exit {rc} ----\n{out}{err}")
    assert "顾问下一动作: continue_wait(manual_kickoff)" in out
    assert "子 agent 模式(本卡执行方式 = 顾问派子 agent; 不找 Owner、不敲 /go)" in out and "敲 /go(" not in out
    assert "请顾问在 claude-code 会话派子 agent" in out  # 开工提示 = manual_hint.subagent(声明)
    rc, out, err = _cli("loop", "status", "--task-id", TASK, "--workspace-root", str(gov), "--wait", "5")
    _show(f"---- 件①·子 agent 模式等交回超时 lybra loop status --wait 原文 exit {rc} ----\n{out}{err}")
    assert rc == declared_exit_code("lybra_loop_status", "advisor_fix") == 7, out + err
    assert "顾问下一动作: advisor_fix(wait_timeout)" in out and "例行结束 wait_timeout 第 1/3 次" in out
    assert "不找 Owner: 本卡执行方式 = 顾问派子 agent" in out and "执行体可能仍在做 → 用原命令重跑 loop 续等" in out  # AIPOS-F185 件①: 超时修法人话
    assert "子 agent 已终止或未派 → 按开工材料派本卡子 agent" in out and "加大 --max-wait" in out
    assert f"lybra loop --task-id {TASK} --workspace-root {gov} --actor {DRIVER} --envelope {POLICY}" in out
    report = json.loads(_cli("loop", "status", "--task-id", TASK, "--workspace-root", str(gov), "--json")[1])
    assert report["next_action"]["action"] == "advisor_fix" and report["next_action"]["execution_mode"] == "subagent"
    # 同一例行结束原因连续 3 次 = 升 owner_needed(routine_fix_exhausted), 与其他例行结束原因同一计数实现
    for _ in range(2):
        _run_until_wait_timeout(gov, {})
    na = lrr.loop_status(gov, TASK)["next_action"]
    _show(f"[件①·第 3 次连续 wait_timeout] {na['action']}({na['reason']}): {na['detail']}")
    assert na["action"] == "owner_needed" and na["reason"] == "routine_fix_exhausted"


def test_item1_pi_mode_wait_timeout_still_owner_needed(rig):
    gov = rig.gov
    _declare_subagent(gov)
    f90._card(gov, TASK, "pending", harness="pi")  # 卡要 pi 执行工位 = card_execution_mode "pi"
    init_governance_repo(gov)
    seen: dict = {}
    res = _run_until_wait_timeout(gov, seen)
    assert res.outcome == "wait_timeout", res.message
    rc, out, err = seen["kickoff"]
    _show(f"---- 件①·pi 模式等待中原文 ----\n{out}{err}")
    assert "顾问下一动作: continue_wait(manual_kickoff)" in out and "子 agent 模式" not in out and "敲 /go" in out
    rc, out, err = _cli("loop", "status", "--task-id", TASK, "--workspace-root", str(gov), "--wait", "5")
    _show(f"---- 件①·pi 模式等交回超时原文 exit {rc} ----\n{out}{err}")
    assert rc == declared_exit_code("lybra_loop_status", "owner_needed") == 5
    assert "顾问下一动作: owner_needed(wait_timeout)" in out and "请 Owner 在工位敲 /go" in out


def test_item1_undeclared_execution_unchanged(rig):
    """未声明 execution = 行为不变(owner_needed); 等审计等别的卡的等待不按执行方式覆盖。"""
    gov = rig.gov
    f90._card(gov, TASK, "pending")
    init_governance_repo(gov)
    _run_until_wait_timeout(gov, {})
    na = lrr.loop_status(gov, TASK)["next_action"]
    assert na["action"] == "owner_needed" and na["reason"] == "wait_timeout" and "execution_mode" not in na
    view = {"task_id": TASK, "wait_card": f"{TASK}R"}
    _declare_subagent(gov)
    assert lrr.run_execution_mode(gov, view) == (None, "")  # 等的是审计卡: 不适用
    assert lrr.run_execution_mode(gov, {**view, "wait_card": TASK})[0] == "subagent"


def test_item1_declaration_fail_closed():
    import copy

    from tools.schema_loader import SchemaLoadError

    decl = lrr.run_record_declaration(load_loop_contract())
    rules = lrr.next_action_declaration(decl)
    assert rules["by_execution_mode"]["modes"]["subagent"]["by_end_reason"] == {"wait_timeout": "advisor_fix"}
    assert rules["by_end_reason"]["wait_timeout"] == "owner_needed"  # pi / 手工工位原口径不动
    broken = copy.deepcopy(lrr.status_contract())
    del broken["next_action"]["routine_rejections"]["end_reasons"]["wait_timeout"]
    with pytest.raises(SchemaLoadError):
        lrr.next_action_declaration(decl, broken)  # 覆盖到 advisor_fix 而无修法 = 拒
    broken = copy.deepcopy(lrr.status_contract())
    broken["next_action"]["by_execution_mode"]["modes"]["ghost-mode"] = {"by_end_reason": {}, "kickoff_detail": "{hint}"}
    with pytest.raises(SchemaLoadError):
        lrr.next_action_declaration(decl, broken)  # 执行方式不在 config.schema execution.default_mode 值域 = 拒


# ===========================================================================
# 件② 共用仓卡归属 + 件③ 发布归因(同一靶场: 两个限 lane 的分顾问 + 不限 lane 的总顾问)
# ===========================================================================

@pytest.fixture
def three(two, monkeypatch):
    gov = two.gov
    conn = json.loads((gov / ".lybra" / "connection.json").read_text(encoding="utf-8"))
    conn["tokens"].append({"role": "advisor", "role_class": "advisor", "agent_instance": ADV_LT, "token": TOK_LT, "scopes": []})
    _write(gov / ".lybra" / "connection.json", json.dumps(conn))
    monkeypatch.setitem(f134.CAPS, TOK_LT, {"role": "advisor", "role_class": "advisor", "agent_instance": ADV_LT})
    f134._policy(gov, POL_LT, ADV_LT, lane=["lantu", "ota"])  # 共用仓 ota 同在两个分顾问的 lane 集合里
    return two


def _draft(gov: Path, task_id: str, priority: str, tmp: Path) -> Path:
    payload = {"frontmatter": {"task_id": task_id, "title": f"{task_id} t", "project": "lybra", "context_bundle": "t",
                               "task_mode": "code", "priority": priority, "status": "pending", "needs_owner": False,
                               "assigned_to": EXEC, "agent_instance": EXEC, "audit": "required", "audit_by": AUDITOR,
                               "harness": "claude-code", "output_target": "tests/", "artifact_policy": "formal_write",
                               "lane": {"repo": "ota", "paths": ["tests/"], "roles": ["executor"]}},
               "body": f"# {task_id}\n\n## Goal\n做 {task_id}。\n\n## 验收\n- 夹具绿\n"}
    path = tmp / f"{task_id.lower()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _publish(gov: Path, task_id: str, priority: str, actor: str, tmp: Path) -> str:
    rc, out, err = _cli("--workspace-root", str(gov), "draft", "create", "--from-json", str(_draft(gov, task_id, priority, tmp)),
                        "--actor", actor)
    assert rc == 0, out + err
    assert f"签发实例: {actor}(来源 explicit)" in out
    rc, out, err = _cli("--workspace-root", str(gov), "draft", "publish", "--path", f"5_tasks/drafts/{task_id.lower()}.md",
                        "--actor", actor)
    assert rc == 0, out + err
    return out


def _seed(three, tmp_path: Path) -> None:
    gov = three.gov
    _publish(gov, OTA_PUB, "critical", ADV_OTA, tmp_path)  # OTA 顾问出的卡(优先级更高: 不按归属分时它当选)
    _publish(gov, LT_PUB, "low", ADV_LT, tmp_path)
    f90._card(gov, LEGACY, "pending", created_by=ADV_OTA, lane={"repo": "ota", "paths": ["tests/"], "roles": ["executor"]})


def test_item3_publish_records_actor_and_multi_instance_requires_actor(three, tmp_path):
    gov = three.gov
    out = _publish(gov, OTA_PUB, "high", ADV_OTA, tmp_path)
    _show(f"---- 件③·draft publish --actor {ADV_OTA} 原文 ----\n{out}")
    record = next((gov / "5_tasks" / "records" / "publishes" / OTA_PUB).glob("publish_*.md")).read_text(encoding="utf-8")
    head = record.split("\n---", 1)[0]
    _show(f"---- 件③·发布记录 frontmatter 原文 ----\n{head}")
    assert f"\nactor: {ADV_OTA}\n" in head and f"\npublished_by: {ADV_OTA}\n" in head
    card = (gov / "5_tasks" / "queue" / "pending" / f"{OTA_PUB.lower()}.md").read_text(encoding="utf-8")
    assert f"\ncreated_by: {ADV_OTA}\n" in card  # create --actor 填卡稿 created_by
    # 多实例缺 --actor = 拒(不落任何文件)
    _cli("--workspace-root", str(gov), "draft", "create", "--from-json", str(_draft(gov, LT_PUB, "low", tmp_path)), "--actor", ADV_LT)
    rc, out, err = _cli("--workspace-root", str(gov), "draft", "publish", "--path", f"5_tasks/drafts/{LT_PUB.lower()}.md")
    _show(f"---- 件③·多实例缺 --actor 原文 exit {rc} ----\n{out}{err}")
    assert rc == 1 and "PUBLISH_ACTOR_REQUIRED: 本治理根接入了 3 个顾问实例" in err and ADV_LT in err and ADV_OTA in err
    assert not (gov / "5_tasks" / "queue" / "pending" / f"{LT_PUB.lower()}.md").exists()
    assert not (gov / "5_tasks" / "records" / "publishes" / LT_PUB).exists()
    # 签发实例无本人凭据 = 拒(F154 按实例选凭据唯一口)
    rc, out, err = _cli("--workspace-root", str(gov), "draft", "publish", "--path", f"5_tasks/drafts/{LT_PUB.lower()}.md",
                        "--actor", "advisor-ghost.f134probe.h1")
    _show(f"---- 件③·--actor 未接入实例原文 exit {rc} ----\n{err}")
    assert rc == 1 and "PUBLISH_ACTOR_UNVERIFIED: 签发实例 advisor-ghost.f134probe.h1 核不过" in err
    # 卡稿 created_by 与 --actor 不符 = 拒
    path = _draft(gov, "PROBE-MIS-1", "low", tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["frontmatter"]["created_by"] = ADV_OTA
    path.write_text(json.dumps(data), encoding="utf-8")
    rc, out, err = _cli("--workspace-root", str(gov), "draft", "create", "--from-json", str(path), "--actor", ADV_LT)
    _show(f"---- 件③·created_by 与 --actor 不符原文 exit {rc} ----\n{err}")
    assert rc == 1 and f"PUBLISH_ACTOR_MISMATCH: 卡稿 created_by={ADV_OTA} 与签发实例 --actor {ADV_LT} 不一致" in err


def test_item3_single_instance_infers_actor(rig, tmp_path):
    """单实例治理根(连接文件只绑一个顾问实例)缺 --actor = 推断该实例(再经同一核验)。"""
    gov = rig.gov
    rc, out, err = _cli("--workspace-root", str(gov), "draft", "create", "--from-json", str(_draft(gov, "PROBE-ONE-1", "low", tmp_path)))
    _show(f"---- 件③·单实例缺 --actor 原文 exit {rc} ----\n{out}{err}")
    assert rc == 0 and f"签发实例: {DRIVER}(来源 inferred)" in out


def test_item2_next_views_split_by_owner(three, tmp_path):
    gov = three.gov
    _seed(three, tmp_path)
    rc, out, err = _cli("next", "--workspace-root", str(gov))
    assert rc == 0 and f"下一张可推进卡: {OTA_PUB}" in out, out + err  # 不按归属分(行为不变): 高优先级的别人的卡当选
    rc, out, err = _cli("next", "--workspace-root", str(gov), "--actor", ADV_LT)
    _show(f"---- 件②·lybra next --actor {ADV_LT}(分顾问 B)原文 ----\n{out}{err}")
    assert rc == 0 and f"下一张可推进卡: {LT_PUB} lane=ota priority=low [归属 {ADV_LT}]" in out
    assert f"归属视角 {ADV_LT}(来源 actor): 只取归属本实例或归属不明的卡" in out
    assert (f"其他子项目(不取) 2 张(归属其他实例, 不推荐、不取): {OTA_PUB}(归属 {ADV_OTA}), {LEGACY}(归属 {ADV_OTA}(自报))" in out
            or f"其他子项目(不取) 2 张(归属其他实例, 不推荐、不取): {LEGACY}(归属 {ADV_OTA}(自报)), {OTA_PUB}(归属 {ADV_OTA})" in out)
    rows_text = out.split("其他子项目", 1)[0]
    assert f"[pending/claim] {OTA_PUB}" not in rows_text and f"[pending/claim] {LEGACY}" not in rows_text
    js = json.loads(_cli("next", "--workspace-root", str(gov), "--actor", ADV_LT, "--json")[1])
    assert [r["task_id"] for r in js if r.get("owner_other")] and all(not r.get("next_card") for r in js if r.get("owner_other"))
    # --lane 且信封恰属一个实例 = 该实例视角(B 的信封 lane {lantu, ota})
    rc, out, err = _cli("next", "--workspace-root", str(gov), "--lane", "lantu", "--lane", "ota")
    _show(f"---- 件②·lybra next --lane lantu --lane ota(信封归属 {ADV_LT})原文 ----\n{out}{err}")
    assert f"下一张可推进卡: {LT_PUB}" in out and f"归属视角 {ADV_LT}(来源 lane_envelope)" in out
    # 只给共用仓 lane: 两个分顾问的信封都覆盖 = 不按归属分(行为不变)
    rc, out, err = _cli("next", "--workspace-root", str(gov), "--lane", "ota")
    assert f"下一张可推进卡: {OTA_PUB}" in out and "归属视角" not in out
    # 总顾问(信封不限 lane): 全量, 只标归属(存量卡标「自报」)
    rc, out, err = _cli("next", "--workspace-root", str(gov), "--actor", ADV_MAIN)
    _show(f"---- 件②·lybra next --actor {ADV_MAIN}(总顾问)原文 ----\n{out}{err}")
    assert rc == 0 and f"归属视角 {ADV_MAIN}: 总顾问(有效信封不限 lane), 看全量" in out and "其他子项目" not in out
    assert f"下一张可推进卡: {OTA_PUB} lane=ota priority=critical [归属 {ADV_OTA}]" in out
    assert f"{LEGACY} [lane ota] [归属 {ADV_OTA}(自报)]" in out and f"{LT_PUB} [lane ota] [归属 {ADV_LT}]" in out
    # brief / needs-owner 同一判定(经 visible_cards)
    (gov / "governance" / "decision_log").mkdir(parents=True, exist_ok=True)  # brief 读决策日志(空)
    rc, out, err = _cli("brief", "--workspace-root", str(gov), "--actor", ADV_LT, "--json")
    assert rc == 0, out + err
    queue = json.loads(out)["queue"]
    assert sorted(o["task_id"] for o in queue["owner_other"]) == sorted([OTA_PUB, LEGACY]) and queue["pending"] == 1
    payload = json.loads(_cli("--workspace-root", str(gov), "needs-owner", "--actor", ADV_LT, "--json")[1])
    assert payload["owner_view"]["instance"] == ADV_LT and not payload["owner_view"]["chief"]


def test_item2_loop_refuses_other_owners_card(three, tmp_path):
    gov = three.gov
    _seed(three, tmp_path)
    init_governance_repo(gov)
    out = io.StringIO()
    res = run_loop(OTA_PUB, gov, actor=ADV_LT, out=out, watch=_stop_watch, interval=0.05, max_wait=1, max_steps=5)
    text = out.getvalue()
    _show(f"---- 件②·B({ADV_LT})用 loop 跑 A 的卡原文 ----\n{text}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "not_derivable") == 4 and res.reason == "card_owner_mismatch"
    assert (f"卡归属不符, 拒跑(防跑走别人的卡): {OTA_PUB} 归属 {ADV_OTA}(门侧发布记录签发实例), 驱动方 {ADV_LT} "
            f"本次信封 {POL_LT} 限 lane ['lantu', 'ota'](非总顾问)") in text
    assert not f134.TokenGate.calls  # 拒在任何门动词之前(未认领)
    rc, status, err = _cli("loop", "status", "--task-id", OTA_PUB, "--workspace-root", str(gov), "--wait", "5")
    _show(f"---- 件②·被拒运行的顾问下一动作原文 exit {rc} ----\n{status}{err}")
    assert rc == declared_exit_code("lybra_loop_status", "owner_needed") and "顾问下一动作: owner_needed(card_owner_mismatch)" in status
    # 自报归属不作拒据; 总顾问不受限; 归属本实例照常
    from tools.aipos_cli.autonomy_policy import load_policy
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.machine_zone import card_owner_refusal

    legacy_fm, _ = require_frontmatter(gov / "5_tasks" / "queue" / "pending" / f"{LEGACY.lower()}.md")
    assert card_owner_refusal(gov, LEGACY, legacy_fm, driver_actor=ADV_LT, policy=load_policy(gov, POL_LT)) == ""
    assert card_owner_refusal(gov, OTA_PUB, None, driver_actor=ADV_MAIN, policy=load_policy(gov, POL_MAIN)) == ""
    assert card_owner_refusal(gov, OTA_PUB, None, driver_actor=ADV_OTA, policy=load_policy(gov, POL_OTA)) == ""


def test_item2_card_done_take_next_only_own_cards(three, tmp_path):
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    gov = three.gov
    _seed(three, tmp_path)
    f90._card(gov, LT_DONE, "completed", lane={"repo": "ota", "paths": ["tests/"], "roles": ["executor"]})
    rec = LoopRunRecorder(gov, LT_DONE, driver=ADV_LT, contract=load_loop_contract(), warn=lambda _m: None)
    rec.set_envelope(POL_LT)
    rec.end(outcome="completed", exit_code=0, reason="completed", message=f"{LT_DONE} 已结案, 治理已落账")
    rc, out, err = _cli("loop", "status", "--task-id", LT_DONE, "--workspace-root", str(gov), "--wait", "5")
    _show(f"---- 件②·分顾问 B 结案取下一张原文 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "顾问下一动作: card_done_take_next(completed)" in out
    assert f"下一张可推进卡: {LT_PUB} lane=ota priority=low" in out and "其他子项目(不取) 2 张" in out
    assert f"lybra loop --task-id {LT_PUB} --workspace-root {gov}" in out
    na = lrr.loop_status(gov, LT_DONE)["next_action"]
    assert na["next_card"]["task_id"] == LT_PUB and na["owner_view"]["chief_reason"] == f"本次信封 {POL_LT} 限 lane ['lantu', 'ota']"


# ===========================================================================
# 不变量: 防碎片化(唯一口)
# ===========================================================================

def _defs(rel: str) -> set[str]:
    tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_invariants_single_sources():
    product = [p for p in (REPO_ROOT / "tools" / "aipos_cli").glob("*.py")]
    owners = {p.name for p in product for name in ("card_owner", "owner_viewer", "card_execution_mode", "resolve_publish_signer")
              if f"def {name}(" in p.read_text(encoding="utf-8")}
    assert owners == {"machine_zone.py", "workspace_config.py", "draft_writer.py"}, owners
    assert {"card_owner", "owner_viewer", "card_owner_refusal", "visible_cards"} <= _defs("tools/aipos_cli/machine_zone.py")
    # 下一动作映射只在 verbs.schema: next_action 实现里不出现结束原因 → 动作的字面映射
    src = (REPO_ROOT / "tools" / "aipos_cli" / "loop_run_record.py").read_text(encoding="utf-8")
    assert '"wait_timeout"' not in src and "'wait_timeout'" not in src
    # 执行方式只读 F143 唯一读取口: 执行方式判定处只经 project_execution, 不直读 project.json execution 键
    for rel in ("tools/aipos_cli/loop_run_record.py", "tools/aipos_cli/loop_driver.py", "tools/aipos_cli/machine_zone.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"""\[\s*["']execution["']\s*\]|\.get\(\s*["']execution["']""", text), rel
    # 签发核验只走 F154 唯一口; 起草缺省与卡执行方式同一谓词
    draft = (REPO_ROOT / "tools" / "aipos_cli" / "draft_writer.py").read_text(encoding="utf-8")
    assert "signer_role_for_actor(" in draft
    defaults = draft.split("def apply_execution_defaults(", 1)[1].split("\ndef ", 1)[0]
    assert "if execution_wants_pi(execution, metadata):" in defaults and "wants_pi = " not in defaults
