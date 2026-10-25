"""AIPOS-F221 — ①老治理根解析梯向上查找误命中全局 ~/.lybra/config.json(别的项目根下静默解析到活动项目);
②非代码快车道(F197 顾问复核 → mark-concluded)缺「复核不通过 → 返工轮 → 再交回 → 再复核」正式出口(chris LANTU-DEMO1 实撞)。

件① 靶场: 临时 HOME 下放全局 ~/.lybra/config.json(home_root = HOME/projects, active_project = projA)+ 两个已建项目根 projA / projB;
    cwd = projB 根跑老解析口(resolve_workspace_context / resolve_workspace_root / task_loader.find_repo_root)= projB(改前 = projA, 见 RETURN
    改前原文); 不在任何治理根内时全局视图仍走 home 模型(活动项目); 向上查找不认全局配置为工作区内配置; projB 根下 audit-verdict 自发现取
    projB 的 connection.json, 凭据缺失拒因点名所用治理根与 connection.json 路径。
件② 靶场 = F90 进程内门 + loop 全链靶场(临时治理根 + 临时 HOME; 禁真门 / 真治理根 / 真工位 / 起 pi), 卡面自造人肉期 LANTU-DEMO1 形态
    (task_mode=config, lane.repo=governance_root, 不声明独立审计 = 快车道): 交回 → 顾问复核不通过(mark-concluded --outcome rework: 复核记录 +
    返工轮, 卡留 claimed)→ next 判等新交回(早于该轮登记的旧改写不算)→ 重写交回 → 重交回 → 回到顾问复核 → 第 2 轮 → 第 3 轮超上限拒 →
    pass 结案; 同一交回重复打回拒; 顾问对快车道卡跑 audit-verdict(CLI 取凭据前 / 门侧裁决)拒因点名本出口; 信封额度同链不额外扣。
验收原文经 _show 打到 stdout(RETURN 证据)。项目名 / 实例名 / 卡号均为靶场值(产品不写死)。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f218_candidate_rework_recovery as f218  # noqa: E402  — 治理根产出靶场(禁第二份)
import test_aipos_f78_engine_agnostic as f78  # noqa: E402
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, _fm, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402

_show, _pick, _quiet = f218._show, f218._pick, f218._quiet
CARD = "PROBE-F221-C"          # LANTU-DEMO1 形态: task_mode=config, 只写治理根, 不声明独立审计
POLICY = "pol_probe_f221_config"
NEXT_KEYS = ("derivable", "current_node", "triggered_by", "verb", "missing_records", "suggested_action", "notes", "action")
FOCUS1 = ["源码 / 缓存 / JDK 挂载改只读", "候选校验失败须 BLOCK 而非 WARN"]
CRITERIA1 = ["挂载清单逐项标 ro", "校验失败路径有拒因原文"]


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ===========================================================================
# 件① 老解析口委托 F144 唯一解析梯
# ===========================================================================

@pytest.fixture
def two_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    user_home = tmp_path / "userhome"
    home = user_home / "projects"
    for name in ("projA", "projB"):
        for qs in ("pending", "claimed", "completed", "blocked"):
            (home / name / "5_tasks" / "queue" / qs).mkdir(parents=True, exist_ok=True)
        _write(home / name / "project.json", json.dumps({"project": name, "config_version": 1}))
    _write(user_home / ".lybra" / "config.json", json.dumps({"config_version": 2, "home_root": str(home), "active_project": "projA"}))
    for name, inst in (("projA", "advisor-a.projA.rig"), ("projB", "advisor-b.projB.rig")):
        _write(home / name / ".lybra" / "connection.json", json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "tokens": [
            {"role": "advisor", "role_class": "advisor", "agent_instance": inst, "token": f"fixture-{name}", "token_ref": f"tr-{name}",
             "scopes": []}]}))
    for key in [k for k in os.environ if k.startswith(("LYBRA_", "AIPOS_"))]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(user_home))
    (user_home / "elsewhere").mkdir()
    return {"user_home": user_home, "home": home, "a": home / "projA", "b": home / "projB", "outside": user_home / "elsewhere"}


def test_item1_old_resolvers_in_project_b_root_resolve_b(two_projects, monkeypatch):
    from tools.aipos_cli.task_loader import find_repo_context, find_repo_root
    from tools.aipos_cli.workspace_config import (find_workspace_config, global_config_path, resolve_governance_root,
                                                  resolve_workspace_context, resolve_workspace_root)

    b, a = two_projects["b"].resolve(), two_projects["a"].resolve()
    monkeypatch.chdir(b)
    got = {"resolve_workspace_context()": resolve_workspace_context(), "resolve_workspace_root()": resolve_workspace_root(),
           "task_loader.find_repo_root()": find_repo_root(), "task_loader.find_repo_context()": find_repo_context(),
           "resolve_governance_root()": resolve_governance_root(established=False)["source"]}
    _show(f"[件①·改后] 全局配置 {global_config_path()}(active_project=projA); cwd={b}\n"
          + "\n".join(f"  {k} -> {v}" for k, v in got.items()))
    assert got["resolve_workspace_context()"] == (b, None) and got["resolve_workspace_root()"] == b
    assert got["task_loader.find_repo_root()"] == b and got["task_loader.find_repo_context()"] == (b, None)
    # 向上查找路过全局运行时配置不当作工作区内配置
    assert find_workspace_config(b) is None and find_workspace_config(two_projects["outside"]) is None
    # 不在任何治理根内 = 全局视图仍走 home 模型(活动项目), home_root 如实带出
    monkeypatch.chdir(two_projects["outside"])
    outside = resolve_workspace_context()
    _show(f"[件①·全局视图] cwd={two_projects['outside']} resolve_workspace_context() -> {outside}")
    assert outside == (a, two_projects["home"].resolve())


def test_item1_old_resolver_callers_follow_cwd_root(two_projects, monkeypatch):
    """老解析口调用方(非测试)逐个核对: deploy_gate / finalize 治理根缺省、audit_helpers 自发现、board_adapter 无根解析都随委托落到所在治理根。"""
    from tools.aipos_cli import audit_helpers, board_adapter
    from tools.aipos_cli.workspace_config import resolve_workspace_root

    b = two_projects["b"].resolve()
    monkeypatch.chdir(b / "5_tasks" / "queue")
    assert resolve_workspace_root() == b
    assert board_adapter._resolve_repo_root(None) == b and board_adapter._resolve_repo_and_home(None) == (b, None)
    ctx = audit_helpers.resolve_audit_context(workspace_root=None, role="advisor", gate_url="http://127.0.0.1:1",
                                              agent_instance="advisor-b.projB.rig")
    _show(f"[件①·调用方] cwd=projB/5_tasks/queue: resolve_audit_context(workspace_root=None) 取到实例 {ctx['agent_instance']}")
    assert ctx["agent_instance"] == "advisor-b.projB.rig"


class _FakeGate:
    calls: list[tuple[str, str]] = []

    def __init__(self, base_url: str, token: str, **_kw) -> None:
        _FakeGate.calls.append((base_url, token))

    def initialize(self) -> None:
        raise RuntimeError("夹具门: 只记凭据不连")


def test_item1_audit_verdict_self_discovery_reads_cwd_root_connection(two_projects, monkeypatch):
    from tools.aipos_cli import confirm_client

    b = two_projects["b"].resolve()
    monkeypatch.chdir(b)
    monkeypatch.setattr(confirm_client, "GateClient", _FakeGate)
    _FakeGate.calls = []
    rc, out, err = _cli(["audit-verdict", "--reviewed-task-id", "RIG-1", "--verdict", "FAIL", "--agent-instance", "advisor-b.projB.rig",
                         "--gate-url", "http://127.0.0.1:1"])
    _show(f"[件①·audit-verdict 自发现 @ projB] rc={rc}\n{err.strip()}\n  门客户端收到的凭据 = {[t for _u, t in _FakeGate.calls]}")
    assert _FakeGate.calls == [("http://127.0.0.1:1", "fixture-projB")]
    rc, out, err = _cli(["audit-verdict", "--reviewed-task-id", "RIG-1", "--verdict", "FAIL", "--agent-instance", "ghost.projB.rig",
                         "--gate-url", "http://127.0.0.1:1"])
    _show(f"[件①·凭据缺失拒因 @ projB] rc={rc}\n{err.strip()}")
    assert rc == 1 and f"治理根 {b}" in err and str(b / ".lybra" / "connection.json") in err
    assert str(two_projects["a"]) not in err


# ===========================================================================
# 件② 快车道复核不通过 → 返工轮 → 再交回 → 再复核
# ===========================================================================

def _report(rig, version: str) -> Path:
    slot = rig.gov / "task_cards" / CARD
    _write(slot / "artifacts" / "sandbox-config.md", f"# 沙箱配置方案({version})\n")
    path = slot / "RETURN.md"
    _write(path, _fm({"outputs": [f"task_cards/{CARD}/artifacts/sandbox-config.md"], "model": f90.SELF_EXEC_MODEL},
                     f"# RETURN — {CARD}\n\n## 一句话结论\n沙箱配置方案 {version}。\n\n## 做了什么\n- 配置方案\n"))
    f90._claude_session(rig.home, path, "claude-opus-5-5", name=f"sess-{CARD}-{version}.jsonl")
    return path


def _loop(rig, seen: list[str], executor):
    from tools.aipos_cli.loop_driver import run_loop

    def watch(args, expect_ready, deploy_guard=None):
        seen.append("executor")
        executor(rig)
        return 0 if expect_ready([]) else 3

    out = io.StringIO()
    res = run_loop(CARD, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=watch, interval=0.05, max_wait=5, max_steps=40,
                   no_launch=True)
    return res, out.getvalue()


def _mark(rig, *extra: str) -> tuple[int, dict, str]:
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "mark-concluded", "--task-id", CARD, "--actor", DRIVER, "--report-path",
                         f"task_cards/{CARD}/RETURN.md", "--json", *extra])
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        payload = {}
    return rc, payload, err


def _rework(rig, focus: list[str], criteria: list[str] | None = None, *, dry_run: bool = False) -> tuple[int, dict, str]:
    extra = ["--outcome", "rework", "--focus-items", json.dumps(focus, ensure_ascii=False)]
    if criteria is not None:
        extra += ["--acceptance-criteria", json.dumps(criteria, ensure_ascii=False)]
    return _mark(rig, *extra, *(["--dry-run"] if dry_run else []))


def _reason(payload: dict, err: str) -> str:
    msgs = [str(e.get("message")) for e in payload.get("errors") or []] or [str(r) for r in payload.get("blocking_reasons") or []]
    return " ".join(msgs) + err


def _rounds(rig) -> list[dict]:
    return f218._card_fm(rig.gov, CARD).get("rework_rounds") or []


def _events(rig) -> list[Path]:
    directory = rig.gov / "5_tasks" / "records" / "events" / CARD
    return sorted(directory.glob("advisor_review_*.md")) if directory.is_dir() else []


def test_item2_fast_lane_review_rework_round_re_return_back_to_review_then_pass(rig, monkeypatch):  # noqa: F811
    from tools.aipos_cli import board_adapter
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown, envelope_quota_state

    _write(rig.gov / "5_tasks" / "policies" / f"{POLICY}.md", build_autonomy_policy_markdown(
        policy_id=POLICY, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-f221", task_selector_task_mode="config", launch_harnesses=["pi"]))
    f78._card(rig.gov, CARD, "pending", task_mode="config",
              extra={"harness": "claude-code", "audit": "none", "output_target": f"task_cards/{CARD}/artifacts/",
                     "lane": {"repo": "governance_root", "paths": [f"task_cards/{CARD}/artifacts/"], "roles": ["executor"]}})
    init_governance_repo(rig.gov)
    fm0 = f218._card_fm(rig.gov, CARD)
    assert nr.fast_lane_review(rig.gov, CARD, fm0), "LANTU-DEMO1 形态 = 快车道(只写治理根、不声明独立审计)"

    seen: list[str] = []
    res, text = _loop(rig, seen, lambda r: _report(r, "v1"))
    _show(f"---- ② loop 首跑(config 快车道卡交回)exit {res.exit_code} reason {res.reason} ----\n" + _quiet(text))
    assert seen == ["executor"]
    first_return = str(nr._read_task_records(rig.gov, CARD)["latest_return"]["return_id"])
    review = nr.derive_next_step(CARD, rig.gov)
    _show("---- ② 交回后 lybra next 原文(复核结案 + 复核打回两条出口)----\n" + _pick(review, *NEXT_KEYS[:-1], "rework_command"))
    assert review["verb"] == "lybra_mark_concluded" and "--outcome rework --focus-items" in review["rework_command"]
    assert "复核不通过打回原卡返工" in review["suggested_action"] and review["rework_command"] in review["command"]
    quota0 = envelope_quota_state(rig.gov, POLICY, CARD)

    # ⑤ 顾问对快车道卡跑审计动词 = 拒(取凭据前), 门侧裁决同判
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "audit-verdict", "--reviewed-task-id", CARD, "--verdict", "FAIL",
                         "--agent-instance", DRIVER, "--gate-url", "http://127.0.0.1:1"])
    _show(f"---- ⑤ 顾问对快车道卡 audit-verdict(CLI)rc={rc} ----\n{err.strip()}")
    assert rc == 1 and "FAST_LANE_REVIEW_ONLY" in err and "--outcome rework" in err and "Auto-discovery" not in err
    gate = board_adapter.audit_verdict_task(audit_task_id=f"{CARD}R", reviewed_task_id=CARD, actor=DRIVER, agent_instance=DRIVER,
                                            owner_policy_ref=POLICY, verdict="FAIL", dry_run=True, repo_root=rig.gov)
    _show(f"---- ⑤ 门侧 audit_verdict_task 预演 ----\n{json.dumps(gate.get('blocking_reasons'), ensure_ascii=False)}")
    assert gate["verdict"] == "BLOCK" and "FAST_LANE_REVIEW_ONLY" in gate["blocking_reasons"][0]

    # 前置拒: 缺返工要点
    rc, payload, err = _mark(rig, "--outcome", "rework", "--dry-run")
    _show(f"---- ② 复核打回缺 --focus-items rc={rc} ----\n{_reason(payload, err)}")
    assert rc == 1 and "REVIEW_REWORK_FOCUS_REQUIRED" in _reason(payload, err)

    # 预演零写入
    card_text = (rig.gov / "5_tasks" / "queue" / "claimed" / f"{CARD.lower()}.md").read_text(encoding="utf-8")
    rc, payload, err = _rework(rig, FOCUS1, CRITERIA1, dry_run=True)
    _show(f"---- ② mark-concluded --outcome rework --dry-run rc={rc} ----\n" + _pick(payload.get("data") or {}, "outcome", "review_record",
                                                                                      "rework_round", "max_rounds"))
    assert rc == 0 and payload["data"]["round_added"] == 1 and payload["data"]["review_record"]["reviewed_return_record_ref"] == first_return
    assert not _events(rig) and (rig.gov / "5_tasks" / "queue" / "claimed" / f"{CARD.lower()}.md").read_text(encoding="utf-8") == card_text

    # 正式打回(复核记录落于 t0, 返工轮登记于 t0+120s 的「旧报告不算」演示: 复核时刻取过去, 轮登记取门当前时刻)
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=120)
    with monkeypatch.context() as m:
        m.setattr(board_adapter, "iso_ms", lambda: _iso(t0))  # AIPOS-F232: 复核时刻改 iso_ms(秒级文本 = 存量形, 照读)
        rc, payload, err = _rework(rig, FOCUS1, CRITERIA1)
    review_id = payload.get("data", {}).get("review_record", {}).get("review_id")
    events = _events(rig)
    _show(f"---- ② mark-concluded --outcome rework(正式)rc={rc} ----\n" + _pick(payload.get("data") or {}, "outcome", "written",
                                                                              "rework_round", "round_added")
          + "\n[复核记录 frontmatter] " + json.dumps(nr._read_frontmatter(events[0]) if events else {}, ensure_ascii=False))
    assert rc == 0 and len(events) == 1 and review_id
    rev_fm = nr._read_frontmatter(events[0])
    assert rev_fm["reviewer_instance"] == DRIVER and rev_fm["reviewed_return_record_ref"] == first_return
    assert rev_fm["artifact_subject"]["outputs_digest"] and rev_fm["focus_items"] == FOCUS1 and rev_fm["rework_round"] == 1
    rounds = _rounds(rig)
    assert [r["round"] for r in rounds] == [1] and rounds[0]["verdict_ref"] == review_id and rounds[0]["focus_items"] == FOCUS1
    assert nr._find_task_in_queue(rig.gov, CARD)[1] == "claimed", "复核打回不结案"

    # 同一交回重复打回 = 拒
    rc, payload, err = _rework(rig, ["再打一次"], dry_run=True)
    _show(f"---- ② 同一交回重复打回 rc={rc} ----\n{_reason(payload, err)}")
    assert rc == 1 and "REVIEW_REWORK_ALREADY_PENDING" in _reason(payload, err)

    # next 判等新交回; 晚于复核但早于该轮登记的旧改写不算
    report = _report(rig, "v1.5(复核后、返工轮登记前改写)")
    os.utime(report, ((t0 + timedelta(seconds=60)).timestamp(),) * 2)
    wait = nr.derive_next_step(CARD, rig.gov)
    _show("---- ② 打回后 lybra next 原文(报告改于复核后、返工轮登记前)----\n" + _pick(wait, *NEXT_KEYS))
    assert wait["action"] == {"type": "await_rework", "card": CARD, "round": 1} and wait["current_node"] == "fast_lane_rework"
    said = wait["missing_records"][0] + wait["suggested_action"] + wait["notes"]
    # AIPOS-F232 件⑤: 登记时刻落盘为毫秒精度, 等待提示按秒显示(rework_bound_note 同一式样)
    assert f"第 1 轮于 {str(rounds[0]['created_at'])[:19]}Z 登记, 等其后的新交回" in said
    assert f"改于 {_iso(t0 + timedelta(seconds=60))}, 早于第 1 轮登记, 不算本轮交回" in wait["notes"]
    assert "派下一轮审计" not in said and "新 tip" not in said

    # 执行体按返工重写交回 → next = 重交回; loop 续跑 = 重交回后回到顾问复核
    report = _report(rig, "v2(挂载只读 + 校验 BLOCK)")
    os.utime(report, ((datetime.now(timezone.utc) + timedelta(seconds=2)).timestamp(),) * 2)
    rr = nr.derive_next_step(CARD, rig.gov)
    _show("---- ② 重写交回后 lybra next 原文 ----\n" + _pick(rr, *NEXT_KEYS[:-1]))
    assert rr["derivable"] and rr["verb"] == "lybra_queue_return_dry_run" and rr["current_node"] == "fast_lane_rework"
    seen2: list[str] = []
    res2, text2 = _loop(rig, seen2, lambda r: seen2.append("executor-unexpected"))
    _show(f"---- ② loop 续跑(重交回 → 回到顾问复核)exit {res2.exit_code} reason {res2.reason} ----\n" + _quiet(text2))
    assert seen2 == [] and f"run return @ fast_lane_rework/claimed ({CARD})" in text2
    second_return = str(nr._read_task_records(rig.gov, CARD)["latest_return"]["return_id"])
    assert second_return != first_return
    back = nr.derive_next_step(CARD, rig.gov)
    _show("---- ② 重交回后 lybra next 原文(回到顾问复核)----\n" + _pick(back, *NEXT_KEYS[:-1]))
    assert back["verb"] == "lybra_mark_concluded" and "第 1 轮返工已重交回" in back["notes"] and "回到顾问复核" in back["notes"]

    # 第 2 轮(上限 2)→ 重交回 → 第 3 轮超上限拒
    rc, payload, err = _rework(rig, ["第 2 轮: 超时清理未实现"])
    assert rc == 0 and payload["data"]["round_added"] == 2, _reason(payload, err)
    _report(rig, "v3(超时清理)")
    os.utime(rig.gov / "task_cards" / CARD / "RETURN.md", ((datetime.now(timezone.utc) + timedelta(seconds=4)).timestamp(),) * 2)
    res3, text3 = _loop(rig, [], lambda r: None)
    assert f"run return @ fast_lane_rework/claimed ({CARD})" in text3, text3
    rc, payload, err = _rework(rig, ["第 3 轮"], dry_run=True)
    _show(f"---- ② 第 3 轮打回(上限 2)rc={rc} ----\n{_reason(payload, err)}")
    assert rc == 1 and "REVIEW_REWORK_ROUND_REFUSED" in _reason(payload, err) and "返工轮次已达上限 (2 轮)" in _reason(payload, err)
    assert len(_events(rig)) == 2, "超上限拒 = 前置拒, 不落复核记录"

    # ④ 同链返工不额外扣信封额度
    quota1 = envelope_quota_state(rig.gov, POLICY, CARD)
    _show(f"[④ 信封额度] 打回前 used={quota0.get('used')} card_released={quota0.get('card_released')}; "
          f"两轮返工后 used={quota1.get('used')} card_released={quota1.get('card_released')}")
    assert quota1.get("used") == quota0.get("used") and quota1.get("card_released") is True

    # 复核通过 → pass 结案
    rc, payload, err = _mark(rig, "--conclusion-note", "顾问复核: 两轮返工后通过", "--outcome", "pass")
    _show(f"---- ② 复核通过 mark-concluded --outcome pass rc={rc} ----\n" + _pick(payload.get("data") or {}, "outcome", "target_path", "moved"))
    assert rc == 0 and payload["data"]["moved"] is True and nr._find_task_in_queue(rig.gov, CARD)[1] == "completed"


def test_item2_rework_outcome_refused_outside_fast_lane(rig):  # noqa: F811
    """非快车道(声明独立审计的治理根卡)= 复核打回不适用, 拒因指向 queue rework; 结论值域不含 rework(不结案)。"""
    from tools.aipos_cli.board_adapter import mark_concluded_outcome_choices, mark_concluded_task
    from tools.aipos_cli.task_complexity import conclusion_outcomes

    f78._card(rig.gov, "PROBE-F221-A", "claimed", task_mode="docs",
              extra={"harness": "claude-code", "audit": "required", "claimed_by": "exec.lybra.test", "claimed_at": "2026-10-23T00:00:00Z",
                     "lane": {"repo": "governance_root", "paths": ["task_cards/PROBE-F221-A/artifacts/"], "roles": ["executor"]}})
    res = mark_concluded_task(task_id="PROBE-F221-A", actor=DRIVER, report_path="task_cards/PROBE-F221-A/RETURN.md", dry_run=True,
                              repo_root=rig.gov, outcome="rework", focus_items='["x"]')
    reason = json.dumps(res, ensure_ascii=False)
    _show(f"[② 非快车道复核打回] {res.get('errors') or res.get('blocking_reasons')}")
    assert res["ok"] is False and "REVIEW_REWORK_NOT_FAST_LANE" in reason and "queue rework" in reason
    # AIPOS-F230 件④: 另加同轮补正 outcome(same_round, 不结案)
    assert "rework" not in conclusion_outcomes() and mark_concluded_outcome_choices() == (*conclusion_outcomes(), "rework", "same_round")
    res = mark_concluded_task(task_id="PROBE-F221-A", actor=DRIVER, report_path="r", dry_run=True, repo_root=rig.gov, outcome="pass",
                              focus_items='["x"]')
    assert res["ok"] is False and "--focus-items" in json.dumps(res, ensure_ascii=False)


def test_declarations_single_source():
    decl = nr.review_rework_declaration()
    fast = nr.fast_lane_rework_declaration()
    assert decl["outcome"] == "rework" and decl["event_type"] == "advisor_review" and fast["state"] == "fast_lane_rework"
    src = (REPO_ROOT / "tools" / "aipos_cli" / "board_adapter.py").read_text(encoding="utf-8")
    body = src.split("def _mark_concluded_review_rework(", 1)[1].split("\ndef ", 1)[0]
    # 返工轮走门 queue rework 同一写函数 / 同一构建; 复核记录走既有记录写口; 不另起写卡路径
    assert "queue_rework_task(" in body and "build_rework_round(" in body and "write_records_atomic(" in body
    assert "amend_task(" not in body and "write_text(" not in body
    nsrc = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    fbody = nsrc.split("def fast_lane_rework_state(", 1)[1].split("\ndef ", 1)[0]
    assert "rework_round_for_verdict(" in fbody and "rework_return_bound(" in fbody and "non_branch_re_return(" in fbody
    wsrc = (REPO_ROOT / "tools" / "aipos_cli" / "workspace_config.py").read_text(encoding="utf-8")
    ctx = wsrc.split("def resolve_workspace_context(", 1)[1].split("\ndef ", 1)[0]
    assert "resolve_governance_root(" in ctx and "find_workspace_config(" not in ctx, "老解析口无显式根分支委托 F144 唯一实现"


def test_f221_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    assert is_test_file("tests/test_aipos_f221_root_ladder_fast_lane_rework.py", default_test_contract())
