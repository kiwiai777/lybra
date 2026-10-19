"""AIPOS-F192 — 信封额度按「业务卡」计(chris 理想 HBLX-22 实撞: max_tasks=1 信封放行一张卡, 认领用掉 1/1 后 return 步被门拒「额度已用尽」,
自动续签建议又照抄 --max-tasks 1, 照做还会再撞)。

件① 已在本信封放行认领的卡, 其交回 / 审计派生与审计认领 / 返工重交 / 复审 / 合入 / 结案不再因额度用尽被拒(只受时间窗与撤销约束);
件② 审计卡、复审卡、补审卡、返工派生卡、FINALIZE 卡的认领归属根卡不另扣; 存量用量按新口径只读重算;
件③ 续签建议与看板签发建议额度 = 业务卡张数(本卡已放行 = 0 / 新卡 = 1), 文案说清「额度 = 业务卡张数(含其审计与返工)」。

唯一实现(禁第二份): 用量 = autonomy_policy.count_preauthorized_claims(根卡去重); 「卡已在本信封放行」= autonomy_policy.card_released_by_envelope
(envelope_quota_state 同一趟读出); 派生归属 = autonomy_policy.envelope_root_task_id(只读卡面字段, 声明 verbs.schema lybra_loop.envelope.quota.parent_links);
放行判定 = match_claim_envelope 谓词 count_bound(card_released 入参); 续签 / 授权建议额度 = autonomy_policy.envelope_card_need。

靶场一律临时根: F90 进程内真门动词处理器 + 临时治理根 / 临时产品仓 / 临时 HOME(禁真门 / 真治理根 / 真工位 / pi)。人肉期样本在临时根自造。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 靶场唯一来源
import test_aipos_f155_loop_chain_gaps as f155  # noqa: E402  — FAIL → 返工 → R2 靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, EXEC, _fm, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from tools.aipos_cli import autonomy_policy as ap  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402

TASK, POLICY = f90.TASK, f90.POLICY
TASK2 = "PROBE-F90-2"
FIN = f"{TASK}-FINALIZE"


def _show(text: str) -> None:
    print(text, flush=True)


def _quiet(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.startswith("[ENVELOPE"))


def _policy(gov: Path, pid: str, *, max_tasks: int, status: str = "active", expires_at: str = "2999-01-01T00:00:00Z",
            active_from: str = "2026-01-01T00:00:00Z", agent: str = "advisor") -> None:
    _write(gov / "5_tasks" / "policies" / f"{pid}.md", ap.build_autonomy_policy_markdown(
        policy_id=pid, agent_or_role=agent, active_from=active_from, expires_at=expires_at, max_tasks=max_tasks,
        owner_approval_ref=f"fixture-{pid}", task_selector_task_mode="code", status=status, launch_harnesses=["pi"]))


def _claim_refs(gov: Path, card: str) -> list[tuple[str, str]]:
    return [(str(nr._read_frontmatter(p).get("autonomy_mode")), str(nr._read_frontmatter(p).get("owner_policy_ref")))
            for p in f90._records(gov, "claims", card)]


def _usage_line(gov: Path, pid: str) -> str:
    u = ap.envelope_usage(gov, pid)
    return (f"{pid}: state={u['state']} used={u['used']}/{u['max_tasks']} 张业务卡 released_cards={u['released_cards']} "
            f"claim_records(旧口径认领次数)={u['claim_records']}")


def _gate_claim(rig, task_id: str, policy: str) -> tuple[int, str]:
    """驱动方同一认领命令(loop 派生形), 经进程内真门。"""
    conn = rig.gov / ".lybra" / "connection.json"
    rc, out, err = _cli(["queue", "claim", "--task-id", task_id, "--actor", EXEC, "--confirm", "--connection-json", str(conn),
                         "--agent-instance", EXEC, "--autonomy-mode", "PreAuthorized", "--owner-policy-ref", policy])
    return rc, _quiet(out + err)


# ===========================================================================
# 验收① max_tasks=1 信封放行一张卡 → 交回 / 审计派生与认领 / FAIL 返工重交 / R2 / 合入 / 结案 全程不被额度拒; 用量始终 1/1; 第二张新卡被拒
# ===========================================================================

def test_item1_max1_envelope_carries_one_card_end_to_end_then_second_card_refused(rig):
    _policy(rig.gov, POLICY, max_tasks=1)
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    usages: list[str] = []

    seen: list[str] = []
    res, text = f155._loop(rig, f155._watch(rig, {f"{TASK}R": "FAIL"}, seen))
    usages.append("首跑(认领 → 交回 → 派审 → 审计认领 → 裁决 FAIL)后 " + _usage_line(rig.gov, POLICY))
    _show(f"---- ① 首跑原文(审计 FAIL)exit {res.exit_code} outcome={res.outcome} ----\n{_quiet(text)}")
    assert seen == ["executor", f"auditor:{TASK}R:FAIL"], seen
    assert res.outcome == "not_derivable" and "处理裁决 FAIL" in res.message  # 未登记返工 = 交顾问决定(与额度无关)
    assert "额度已用尽" not in text and "ENVELOPE_QUOTA_EXHAUSTED" not in text and "count bound reached" not in text
    for step in ("run claim @", "run return @", f"run claim @ publish/pending ({TASK}R)", "run verdict @"):
        assert step in text, step
    assert f90._records(rig.gov, "audit_dispatches", f"{TASK}R"), "派审(审计派生)已落"

    verdict_id = str(nr._read_task_records(rig.gov, TASK)["latest_verdict"]["verdict_id"])
    rc, out, err = _cli(["queue", "rework", "--task-id", TASK, "--actor", DRIVER, "--verdict-ref", verdict_id,
                         "--focus-items", json.dumps(f155.FOCUS, ensure_ascii=False),
                         "--acceptance-criteria", json.dumps(f155.CRITERIA, ensure_ascii=False), "--confirm"])
    _show(f"---- ① 返工登记 lybra queue rework --confirm 原文 exit {rc} ----\n{_quiet(out + err)}")
    assert rc == 0, out + err
    usages.append("返工登记后 " + _usage_line(rig.gov, POLICY))

    seen2: list[str] = []
    res2, text2 = f155._loop(rig, f155._watch(rig, {}, seen2, executor=lambda r: f155._rework_work(r)))
    usages.append("续跑(返工重交 → R2 → 合入 → 结案)后 " + _usage_line(rig.gov, POLICY))
    _show(f"---- ① 续跑原文(返工重交 → R2 → 合入 → 结案)exit {res2.exit_code} ----\n{_quiet(text2)}")
    assert res2.exit_code == 0 and res2.outcome == "completed", _quiet(text2)
    assert seen2 == ["executor", f"auditor:{TASK}R2:PASS"]
    assert "额度已用尽" not in text2 and "ENVELOPE_QUOTA_EXHAUSTED" not in text2
    for step in (f"run return @ verdict_stale/claimed ({TASK})", f"run claim @ publish/pending ({TASK}R2)", "run finalize @",
                 f"run close @ finalize/claimed ({TASK})"):
        assert step in text2, step
    assert (rig.gov / "5_tasks" / "queue" / "completed" / f"{TASK.lower()}.md").is_file()
    claims = {card: _claim_refs(rig.gov, card) for card in (TASK, f"{TASK}R", f"{TASK}R2")}
    _show("[① 三张卡的认领记录(autonomy_mode, owner_policy_ref)] " + json.dumps(claims, ensure_ascii=False))
    assert all(refs == [("PreAuthorized", POLICY)] for refs in claims.values())

    # 外部 FINALIZE 卡(卡面 parent_task_id = 被审卡, 被审卡按 FINALIZE 卡号规则推出的卡号恰为本卡)认领: 归属根卡, 不另扣、不被额度拒
    f90._card(rig.gov, FIN, "pending", parent_task_id=TASK)
    rc, out = _gate_claim(rig, FIN, POLICY)
    usages.append("FINALIZE 卡认领后 " + _usage_line(rig.gov, POLICY))
    _show(f"---- ① FINALIZE 卡 {FIN} 认领(额度已 1/1)原文 exit {rc} ----\n{out}")
    assert rc == 0 and _claim_refs(rig.gov, FIN) == [("PreAuthorized", POLICY)], out

    usage = ap.envelope_usage(rig.gov, POLICY)
    _show("---- ① 用量全程(新口径 = 业务卡张数)----\n" + "\n".join(usages))
    assert all("used=1/1 张业务卡" in line for line in usages), usages
    assert usage["released_cards"] == [TASK] and usage["claim_records"] == 4 and usage["state"] == "exhausted"

    # 第二张新卡: 门认领被拒(回落 Supervised, 带额度拒因)+ loop 启动即「额度已用尽」专门出口
    f90._card(rig.gov, TASK2, "pending")
    rc, out = _gate_claim(rig, TASK2, POLICY)
    _show(f"---- ① 第二张新卡 {TASK2} 门认领原文 exit {rc} ----\n{out}")
    assert rc != 0 and "ENVELOPE_QUOTA_EXHAUSTED" in out and f90._records(rig.gov, "claims", TASK2) == []
    out3 = io.StringIO()
    res3 = run_loop(TASK2, rig.gov, actor=DRIVER, policy_id=POLICY, out=out3, watch=f90._agent_watch(rig, []), interval=0.05,
                    max_wait=5, max_steps=10)
    _show(f"---- ① 第二张新卡 loop 原文 exit {res3.exit_code} reason={res3.reason} ----\n{_quiet(out3.getvalue())}")
    assert res3.exit_code == exit_code_for(load_loop_contract(), "no_envelope") and res3.reason == "envelope_exhausted"
    assert res3.message.startswith(f"信封 {POLICY} 额度已用尽(已放行 1/1 张业务卡, 到期 2999-01-01T00:00:00Z)")
    assert f"本卡 {TASK2} 是一张新业务卡, 需 1 张" in res3.message and "--max-tasks 1 " in res3.suggested_action


# ===========================================================================
# 验收② 信封撤销 / 过期后, 已放行卡的后续步骤照样被拒(时间窗与撤销仍有效)
# ===========================================================================

def _revoke_after_work(rig, pid: str, **policy_kw):
    def watch(args, expect_ready, deploy_guard=None):
        f90._executor_work(rig)
        _policy(rig.gov, pid, max_tasks=1, **policy_kw)  # 执行体干活期间 Owner 撤销 / 信封到期
        return 0 if expect_ready([]) else 3

    return watch


def test_item2_revoked_envelope_blocks_later_steps_of_released_card(rig):
    pid = "pol_probe_rev_1"
    _policy(rig.gov, pid, max_tasks=1)
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=pid, out=out, watch=_revoke_after_work(rig, pid, status="revoked"),
                   interval=0.05, max_wait=5, max_steps=10)
    text = _quiet(out.getvalue())
    _show(f"---- ② 撤销后交回步原文 exit {res.exit_code} outcome={res.outcome} ----\n{text}")
    assert ap.card_released_by_envelope(rig.gov, pid, TASK) is True  # 本卡确由该信封放行
    assert res.exit_code == exit_code_for(load_loop_contract(), "gate_rejected") and res.outcome == "gate_rejected"
    assert "门拒 @ return" in res.message and "无覆盖驱动方的有效 autonomy 信封" in res.message, res.message
    usage = ap.envelope_usage(rig.gov, pid)
    assert usage["state"] == "inactive" and usage["used"] == 1  # 撤销 = 不再有效(不是额度问题)
    assert f90._records(rig.gov, "returns", TASK) == []


def test_item2_expired_envelope_blocks_later_steps_and_renew_carries_card(rig):
    pid = "pol_probe_exp_1"
    _policy(rig.gov, pid, max_tasks=1)
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=pid, out=out,
                   watch=_revoke_after_work(rig, pid, expires_at="2026-01-02T00:00:00Z"), interval=0.05, max_wait=5, max_steps=10)
    text = _quiet(out.getvalue())
    _show(f"---- ② 过期后交回步原文 exit {res.exit_code} reason={res.reason} ----\n{text}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "no_envelope") and res.reason == "envelope_expired"
    assert res.message.startswith(f"信封 {pid} 已过期(到期 2026-01-02T00:00:00Z; 已放行 1/1 张业务卡): return 步({TASK})被拒, 未执行")
    assert f"本卡 {TASK} 曾由 {pid} 放行, 但该信封已过期, 新信封承接本卡 1 张" in res.message
    assert "门拒原文: 门拒 @ return" in res.message and f90._records(rig.gov, "returns", TASK) == []


# ===========================================================================
# 验收③ 存量用量重算前后对照(临时根自造人肉期样本; 只读推导, 治理根逐字节不变)
# ===========================================================================

def _legacy_claim(gov: Path, task_id: str, pid: str, n: int = 1) -> None:
    for i in range(n):
        _write(gov / "5_tasks" / "records" / "claims" / task_id / f"claim_{task_id}_{i}.md",
               _fm({"record_type": "claim_record", "task_id": task_id, "autonomy_mode": "PreAuthorized", "owner_policy_ref": pid,
                    "claimed_at": f"2026-10-0{i + 1}T00:00:00Z"}, "legacy\n"))


def _digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def test_item3_legacy_usage_recount_before_after_read_only(rig):
    gov = rig.gov
    _policy(gov, "pol_legacy_a_1", max_tasks=2)
    _policy(gov, "pol_legacy_b_1", max_tasks=2)
    # 人肉期形: 业务卡 X 经 A 认领两次(返工重认领)、审计 XR / 复审 XR2 / 返工派生修复卡 X-fix1 / 补审 XS1 也经 A 认领
    f90._card(gov, "LEG-X", "completed")
    f90._card(gov, "LEG-XR", "completed", task_mode="audit", reviewed_task_id="LEG-X")
    f90._card(gov, "LEG-XR2", "completed", task_mode="audit", reviewed_task_id="LEG-X")
    f90._card(gov, "LEG-X-fix1", "completed", derived_from_audit_task_id="LEG-XR")
    f90._card(gov, "LEG-XS1", "completed", task_mode="audit", supplementary_of="LEG-X", reviewed_task_id="LEG-X")
    # 业务卡 Y 经 A 认领, 其审计 YR 经 B(续签信封)认领: B 承接的是 Y 这张业务卡(计 1)
    f90._card(gov, "LEG-Y", "completed")
    f90._card(gov, "LEG-YR", "completed", task_mode="audit", reviewed_task_id="LEG-Y")
    # 人肉期手工认领的 Z(无信封)+ 外部 FINALIZE 卡 Z-FINALIZE 经 B 认领
    f90._card(gov, "LEG-Z", "completed")
    f90._card(gov, "LEG-Z-FINALIZE", "completed", parent_task_id="LEG-Z")
    # 卡号像审计卡但卡面无派生字段(code 卡 LEG-WR): 不按卡号字符串猜, 自成一张业务卡
    f90._card(gov, "LEG-W", "completed")
    f90._card(gov, "LEG-WR", "completed")
    for task_id, pid, n in (("LEG-X", "pol_legacy_a_1", 2), ("LEG-XR", "pol_legacy_a_1", 1), ("LEG-XR2", "pol_legacy_a_1", 1),
                            ("LEG-X-fix1", "pol_legacy_a_1", 1), ("LEG-XS1", "pol_legacy_a_1", 1), ("LEG-Y", "pol_legacy_a_1", 1),
                            ("LEG-YR", "pol_legacy_b_1", 1), ("LEG-Z-FINALIZE", "pol_legacy_b_1", 1), ("LEG-WR", "pol_legacy_b_1", 1)):
        _legacy_claim(gov, task_id, pid, n)
    before = _digest(gov)
    rows = []
    for pid in ("pol_legacy_a_1", "pol_legacy_b_1"):
        u = ap.envelope_usage(gov, pid)
        old_state = "exhausted" if u["claim_records"] >= u["max_tasks"] else "active"
        rows.append(f"{pid}: 旧口径(认领次数)= {u['claim_records']}/{u['max_tasks']} 次 → {old_state}; "
                    f"新口径(业务卡)= {u['used']}/{u['max_tasks']} 张 {u['released_cards']} → {u['state']}")
    overview = {r["policy_id"]: (r["used"], r["status"]) for r in ap.envelope_overview(gov)["envelopes"]}
    _show("---- ③ 存量用量重算前后对照(临时根人肉期样本, 只读推导)----\n" + "\n".join(rows) + f"\n看板 / 收件箱同读 envelope_overview: {overview}")
    a, b = ap.envelope_usage(gov, "pol_legacy_a_1"), ap.envelope_usage(gov, "pol_legacy_b_1")
    assert (a["claim_records"], a["used"], a["released_cards"], a["state"]) == (7, 2, ["LEG-X", "LEG-Y"], "exhausted")
    assert (b["claim_records"], b["used"], b["released_cards"], b["state"]) == (3, 3, ["LEG-WR", "LEG-Y", "LEG-Z"], "exhausted")
    assert ap.released_business_cards(gov, "pol_legacy_a_1")["LEG-X"] == ["LEG-X", "LEG-X-fix1", "LEG-XR", "LEG-XR2", "LEG-XS1"]
    assert overview["pol_legacy_a_1"][0] == 2 and overview["pol_legacy_b_1"][0] == 3
    assert _digest(gov) == before, "只读推导: 治理根逐字节不变(不改历史记录)"


# ===========================================================================
# 件② 派生归属判据(只读卡面字段, 禁按卡号字符串猜; 成环 / 链长 / FINALIZE 卡号不符 = fail-safe)
# ===========================================================================

def test_item2_root_resolution_declared_fields_only(rig):
    gov = rig.gov
    f90._card(gov, "ROOT-A", "claimed")
    f90._card(gov, "ROOT-AR", "pending", task_mode="audit", reviewed_task_id="ROOT-A")
    f90._card(gov, "ROOT-AR2", "pending", task_mode="audit", derived_from="ROOT-A")  # 存量审计卡只写 derived_from
    f90._card(gov, "ROOT-A-fix1", "pending", derived_from_audit_task_id="ROOT-AR")
    f90._card(gov, "ROOT-A-fix2", "pending", supplementary_of="ROOT-A")  # 补审 FAIL 修复卡
    f90._card(gov, "ROOT-A-FINALIZE", "pending", parent_task_id="ROOT-A")
    f90._card(gov, "ROOT-B", "claimed", finalize_task_id="ROOT-B-FIN-CUSTOM")
    f90._card(gov, "ROOT-B-FINALIZE", "pending", parent_task_id="ROOT-B")  # 被审卡声明了别的 FINALIZE 卡号 = 不认
    f90._card(gov, "ROOT-B-FIN-CUSTOM", "pending", parent_task_id="ROOT-B")
    f90._card(gov, "ROOT-CR", "pending")  # 卡号形似审计卡但无派生字段 = 自成根
    f90._card(gov, "ROOT-C", "claimed", parent_task_id="ROOT-C")  # 指向自身 = 无上级
    f90._card(gov, "CYC-1R", "pending", task_mode="audit", reviewed_task_id="CYC-2R")
    f90._card(gov, "CYC-2R", "pending", task_mode="audit", reviewed_task_id="CYC-1R")
    f90._card(gov, "ORPH-R", "pending", task_mode="audit", reviewed_task_id="GONE-1")  # 上级不在队列 = 字段值即根
    got = {tid: ap.envelope_root_task_id(gov, tid) for tid in (
        "ROOT-A", "ROOT-AR", "ROOT-AR2", "ROOT-A-fix1", "ROOT-A-fix2", "ROOT-A-FINALIZE", "ROOT-B-FINALIZE", "ROOT-B-FIN-CUSTOM",
        "ROOT-CR", "ROOT-C", "CYC-1R", "ORPH-R", "NOT-A-CARD")}
    _show("[件② 根卡判定] " + json.dumps(got, ensure_ascii=False))
    assert got == {"ROOT-A": "ROOT-A", "ROOT-AR": "ROOT-A", "ROOT-AR2": "ROOT-A", "ROOT-A-fix1": "ROOT-A", "ROOT-A-fix2": "ROOT-A",
                   "ROOT-A-FINALIZE": "ROOT-A", "ROOT-B-FINALIZE": "ROOT-B-FINALIZE", "ROOT-B-FIN-CUSTOM": "ROOT-B",
                   "ROOT-CR": "ROOT-CR", "ROOT-C": "ROOT-C", "CYC-1R": "CYC-1R", "ORPH-R": "GONE-1", "NOT-A-CARD": "NOT-A-CARD"}


def test_item2_matcher_count_bound_only_waived_for_released_chain():
    from datetime import datetime, timezone

    policy = ap.normalize_policy(ap.parse_markdown_frontmatter(ap.build_autonomy_policy_markdown(
        policy_id="pol_unit_1", agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=1, owner_approval_ref="fixture", task_selector_task_mode="code"))[0])
    kw = dict(policy=policy, task_id="T-1", task_mode="code", project="p", agent_instance="advisor", actor="advisor",
              now=datetime(2026, 10, 19, tzinfo=timezone.utc), released_count=1)
    rows = {"new card": ap.match_claim_envelope(**kw), "released chain": ap.match_claim_envelope(**kw, card_released=True),
            "released chain, expired": ap.match_claim_envelope(**{**kw, "now": datetime(3000, 1, 1, tzinfo=timezone.utc)}, card_released=True)}
    revoked = dict(policy, status="revoked")
    rows["released chain, revoked"] = ap.match_claim_envelope(**{**kw, "policy": revoked}, card_released=True)
    _show("[件① matcher 原文] " + json.dumps(rows, ensure_ascii=False))
    assert rows["new card"] == (False, "policy count bound reached (1/1 business cards)", "ENVELOPE_QUOTA_EXHAUSTED")
    assert rows["released chain"][0] is True and "card chain already released by this envelope" in rows["released chain"][1]
    assert rows["released chain, expired"][2] == "ENVELOPE_EXPIRED" and rows["released chain, revoked"][2] == "ENVELOPE_STATUS_NOT_ACTIVE"


# ===========================================================================
# 验收④ 续签建议与看板签发建议(同一推导 envelope_card_need)
# ===========================================================================

def test_item4_renew_and_grant_suggestions_count_business_cards(rig):
    from tools.aipos_cli.owner_inbox import grant_line, suggest_grant

    gov = rig.gov
    _policy(gov, "pol_probe_loop_3", max_tasks=1)
    f90._card(gov, TASK, "claimed")
    f90._card(gov, TASK2, "pending")
    _legacy_claim(gov, TASK, "pol_probe_loop_3")
    usage = ap.envelope_usage(gov, "pol_probe_loop_3")
    ident = {"advisor"}
    plans = {"本卡已放行(信封用尽但仍在时间窗内)": ap.renew_plan(gov, usage, identities=ident, task_id=TASK),
             "新卡": ap.renew_plan(gov, usage, identities=ident, task_id=TASK2),
             "无在推卡(信封页 / brief 批量)": ap.renew_plan(gov, usage, identities=ident, task_id="<在推卡>")}
    _policy(gov, "pol_probe_loop_3", max_tasks=1, expires_at="2026-01-02T00:00:00Z")
    expired = ap.envelope_usage(gov, "pol_probe_loop_3")
    plans["本卡已放行但信封已过期"] = ap.renew_plan(gov, expired, identities=ident, task_id=TASK)
    _show("---- ④ 续签建议原文 ----\n" + "\n".join(f"[{k}] card_need={v['card_need']} max_tasks={v['max_tasks']}\n{v['quota_line']}\n  {v['renew_command']}"
                                                for k, v in plans.items()))
    assert [p["card_need"] for p in plans.values()] == [0, 1, None, 1]
    assert [p["max_tasks"] for p in plans.values()] == [1, 1, 1, 1]
    assert plans["本卡已放行(信封用尽但仍在时间窗内)"]["quota_line"] == (
        "  额度: 1 张业务卡(额度 = 业务卡张数, 每张含其审计、返工、合入、结案, 不另扣): "
        f"本卡 {TASK} 已由 pol_probe_loop_3 放行, 后续步骤不受额度限制, 本卡无需续签; 新信封额度只为之后的新卡")
    assert all("--max-tasks 1 " in p["renew_command"] for p in plans.values())

    _policy(gov, "pol_probe_loop_4", max_tasks=40)  # 同驱动方上一信封 40 张: 不再照抄
    fm = nr._read_frontmatter(nr._find_task_in_queue(gov, TASK2)[0])
    g = suggest_grant(gov, "lybra", task_id=TASK2, card_fm={**fm, "task_id": TASK2}, driver="advisor")
    line = grant_line({"grant": g})
    _show(f"---- ④ 收件箱授权请求建议(看板「按建议签发」同读)----\n{json.dumps({k: g[k] for k in ('max_tasks', 'sources', 'quota_basis', 'previous_policy_id')}, ensure_ascii=False)}\n{line}")
    assert g["max_tasks"] == 1 and g["sources"]["max_tasks"] == "card_need" and g["previous_policy_id"] == "pol_probe_loop_4"
    assert "1 张业务卡(每张含其审计与返工)" in line

    from web.board.app import _I18N_TEMPLATES as _TEXTS  # board i18n (zh / en same keys)

    texts = {lang: {k: _TEXTS[lang][k] for k in ("gr_sentence", "gr_edit_max", "gr_confirm_count")} for lang in ("zh", "en")}
    _show("---- ④ 看板签发确认页计数口径文案 ----\n" + json.dumps(texts, ensure_ascii=False, indent=1))
    assert "张业务卡" in texts["zh"]["gr_sentence"] and "不另扣" in texts["zh"]["gr_confirm_count"]
    assert "business card" in texts["en"]["gr_sentence"] and "not counted again" in texts["en"]["gr_confirm_count"]
    from tools.aipos_cli.aipos_cli import build_parser

    help_text = subprocess.run([sys.executable, "-m", "tools.aipos_cli", "envelope", "mint", "--help"], cwd=str(REPO_ROOT),
                               capture_output=True, text=True, timeout=120).stdout
    _show(f"---- ④ lybra envelope mint --help(--max-tasks 节选)----\n{re.search(r'--max-tasks.*?(?=\n  --)', help_text, re.S).group(0)}")
    assert build_parser() is not None and "Quota in business cards" in " ".join(help_text.split())


# ===========================================================================
# 防碎片化: 计数 / 放行判据 / 根卡判定 / 续签额度各一处; 声明齐; 无静默吞错
# ===========================================================================

def test_single_implementation_and_declaration():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "autonomy_policy.py").read_text(encoding="utf-8")
    gate = (REPO_ROOT / "tools" / "mcp_server" / "tools.py").read_text(encoding="utf-8")
    for name in ("count_preauthorized_claims", "envelope_root_task_id", "card_released_by_envelope", "envelope_quota_state",
                 "envelope_card_need", "_preauthorized_claim_roots"):
        assert src.count(f"def {name}(") == 1, name
    proc = subprocess.run(["git", "grep", "-l", "-e", "def envelope_root_task_id(", "-e", "def envelope_card_need(", "-e",
                           "def card_released_by_envelope(", "--", "tools", "web"], cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.stdout.split() == ["tools/aipos_cli/autonomy_policy.py"], proc.stdout
    # 门侧: 用量与放行判据一次读出(envelope_quota_state), 两条门路径都把 card_released 交给唯一 matcher
    assert "count_preauthorized_claims(" not in gate and gate.count("envelope_quota_state(repo_root, ") == 2
    assert gate.count('card_released=bool(quota["card_released"]),\n    )') == 2  # 门两条路径的 matcher 调用
    assert "card_released=released_card.get(pid, False)" in src  # loop / 收件箱挑选(select_envelope)同一判据
    decl = ap.envelope_quota_declaration()
    assert [l["field"] for l in decl["parent_links"]] == ["reviewed_task_id", "derived_from", "supplementary_of",
                                                          "derived_from_audit_task_id", "supplementary_of", "parent_task_id"]
    body = src.split("def count_preauthorized_claims(")[1].split("def envelope_card_need(")[0]
    assert "except Exception" not in body and "except Exception" not in src.split("def envelope_card_need(")[1].split("\ndef ")[0]
    guard = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))["envelope_guards"]["guards"]
    assert "业务卡" in guard["quota_exhausted"]["error_message"]
