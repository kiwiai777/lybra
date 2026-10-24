"""AIPOS-F225 — Owner 介入分级: 第一档「必须你定」封闭清单声明; loop 下一动作按档归类(范围不变的返工自动追加至硬上限 4 轮、
环境 / 产品修复后自动重新派审、技术故障归顾问); 顾问升 Owner 必带第一档事由; 收件箱只列第一档。

设计稿 governance/research/2026-10-24-owner-intervention-tiers-design.md(§3 三档、§8 默认值)。验收逐条:
① schema 映射守卫: 每个可判 owner_needed 的事由与每个收件箱类别都有档位, 删一个映射 = 加载即拒;
② 快车道卡与审计裁决型卡各自返工到 2 轮后自动追加第 3、4 轮(记 auto_extension_ref), 第 5 轮升第一档; 改了验收的卡到上限不自动追加;
③ audit_blocked_repeated: 部署变化后自动重新派审 / 无变化判 advisor_fix;
④ 技术事由改判 advisor_fix / investigate 的 loop status 原文;
⑤ loop note 无第一档事由拒写原文;
⑥ 收件箱改前 / 改后件数对比(靶场自造 chris 类别分布);
⑦ Owner 撤回自动放行后卡停住原文。

靶场 = F90 进程内门替身 + 临时治理根 / 产品仓 / 临时 HOME(禁真门 / 真治理根 / 真工位 / 起 pi); 复用 F202 / F221 / F222 构件(禁第二份)。
验收原文经 _show 打到 stdout(RETURN 证据)。项目名 / 实例名 / 卡号均为靶场值(产品不写死)。
"""
from __future__ import annotations

import copy
import hashlib
import json
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f202_owner_reaudit as f202  # noqa: E402  — 连续 BLOCK 构件(禁第二份)
import test_aipos_f218_candidate_rework_recovery as f218  # noqa: E402
import test_aipos_f221_root_ladder_fast_lane_rework as f221  # noqa: E402  — 快车道构件
import test_aipos_f222_rework_limit_arbitration as f222  # noqa: E402  — 返工上限构件
import test_aipos_f78_engine_agnostic as f78  # noqa: E402
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, _fm, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f179_long_run_resources_block import _block_watch, _quiet, _status  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli import owner_inbox as oi  # noqa: E402

_show, _pick = f218._show, f218._pick
CARD = f221.CARD


def _publish_baseline(gov: Path, task_id: str, card_path: Path) -> str:
    """门侧发布记录 + 发布时草稿(草稿 sha256 = 发布记录 source_sha256 = 发布后未改, 门可核)。返回草稿相对路径。"""
    rel = f"5_tasks/drafts/{task_id.lower()}.md"
    _write(gov / rel, card_path.read_text(encoding="utf-8"))
    sha = hashlib.sha256((gov / rel).read_bytes()).hexdigest()
    pid = f"publish_{task_id.lower()}"
    _write(gov / "5_tasks" / "records" / "publishes" / task_id / f"{pid}.md", _fm({
        "record_type": "publish_record", "task_id": task_id, "publish_id": pid, "actor": DRIVER, "published_by": DRIVER,
        "source_draft_ref": rel, "source_sha256": sha, "published_sha256": sha, "published_at": "'2026-10-01T00:00:00Z'"},
        "## Publish Provenance\n"))
    return rel


def _auto_records(gov: Path, task_id: str) -> list[dict]:
    return oi.auto_releases(gov, task_id)


# ===========================================================================
# ① 档位映射守卫
# ===========================================================================

def test_item1_tier_mapping_guard_rejects_unmapped(monkeypatch):
    import tools.schema_loader as sl
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import next_action_declaration, run_record_declaration, status_contract
    from tools.schema_loader import SchemaLoadError

    decl = oi.inbox_declaration()
    tiers = oi.tier_declaration(decl)
    na = status_contract()["next_action"]
    owner_needed = {r for r, a in na["by_end_reason"].items() if a == "owner_needed"} | set(na["owner_reasons"])
    _show("---- ① 档位映射(loop 事由 → 档位)----\n" + "\n".join(f"  {k}: {json.dumps(v, ensure_ascii=False)[:120]}"
                                                       for k, v in sorted(tiers["loop_reasons"].items())))
    _show("---- ① 档位映射(收件箱类别 → 档位)----\n" + "\n".join(f"  {k}: {v}" for k, v in sorted(tiers["categories"].items())))
    assert owner_needed <= set(tiers["loop_reasons"]) and set(tiers["categories"]) == set(oi.CATEGORIES)
    assert set(tiers["tier1"]["reasons"]) == {"direction_fork", "rescope", "beyond_authority", "over_budget", "withdraw",
                                              "rework_cap_exceeded", "advisor_audit_conflict", "owner_check"}
    for key in ("gate_rejected", "landing_rejected", "verb_not_authorized", "manual_required", "wait_timeout", "runtime_evidence_exhausted",
                "card_owner_mismatch", "consecutive_failures", "routine_fix_exhausted"):
        assert tiers["loop_reasons"][key]["tier"] == 3 and tiers["loop_reasons"][key]["action"] in ("advisor_fix", "investigate")
        assert tiers["loop_reasons"][key]["why"].strip(), key
    assert tiers["loop_reasons"]["audit_blocked_repeated"] == {**tiers["loop_reasons"]["audit_blocked_repeated"], "tier": 2, "kind": "auto_reaudit"}

    real = sl.load_schema

    def _patched(mutate):
        def load(name, *a, **k):
            data = real(name, *a, **k)
            if name != "verbs":
                return data
            data = copy.deepcopy(data)
            mutate(data["verbs"])
            return data
        return load

    cases = {
        "删 loop_reasons.gate_rejected": lambda v: v["lybra_owner_inbox"]["tiers"]["loop_reasons"].pop("gate_rejected"),
        "删 categories.roadmap_drift": lambda v: v["lybra_owner_inbox"]["tiers"]["categories"].pop("roadmap_drift"),
        "新增结束原因未归档": lambda v: v["lybra_loop_status"]["next_action"]["owner_reasons"].__setitem__("brand_new_reason", "x"),
        "第三档缺理由": lambda v: v["lybra_owner_inbox"]["tiers"]["loop_reasons"]["wait_timeout"].pop("why"),
        "第一档事由不在清单": lambda v: v["lybra_owner_inbox"]["tiers"]["loop_reasons"]["no_envelope"].__setitem__("owner_reason", "nope"),
    }
    for label, mutate in cases.items():
        with monkeypatch.context() as m:
            m.setattr(sl, "load_schema", _patched(mutate))
            with pytest.raises(SchemaLoadError) as exc:
                oi.inbox_declaration()
            _show(f"[① 加载即拒: {label}] {exc.value}")
            with pytest.raises(SchemaLoadError):
                next_action_declaration(run_record_declaration(load_loop_contract()))


# ===========================================================================
# ④⑤ 技术事由归顾问(loop status 原文)/ 升 Owner 无第一档事由拒写
# ===========================================================================

def test_item4_5_technical_reasons_to_advisor_and_note_requires_tier1_reason(rig):  # noqa: F811
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    task = "PROBE-F225-T"
    f78._card(rig.gov, task, "claimed", extra={"claimed_by": "exec.lybra.test", "claimed_at": "'2026-10-01T00:00:00Z'"})
    init_governance_repo(rig.gov)
    rec = LoopRunRecorder(rig.gov, task, driver=DRIVER, contract=load_loop_contract(), warn=_show)
    rec.end(outcome="gate_rejected", exit_code=2, reason="gate_rejected", message="门拒: SOME_GATE_CODE 交回自检未过")
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "loop", "status", "--task-id", task])
    _, js, _ = _cli(["--workspace-root", str(rig.gov), "loop", "status", "--task-id", task, "--json"])
    na = json.loads(js)["next_action"]
    _show(f"---- ④ loop status(gate_rejected, 原判 owner_needed)rc={rc} ----\n{out[-1400:]}")
    assert na["action"] == "investigate" and na["owner_tier"] == 3 and na["tier_from"] == {"action": "owner_needed", "reason": "gate_rejected"}
    assert "第三档(归顾问, AIPOS-F225)" in out and "--owner-reason <" in out
    rc, out, err = _cli(["owner", "inbox", "--home-root", str(rig.gov.parent), "--json"])
    assert not [i for i in json.loads(out)["items"] if i.get("task_id") == task], "第三档不进 Owner 收件箱"

    # 例行修法仍连败(routine_fix_exhausted)/ 等待超时同样归顾问
    for reason, message in (("wait_timeout", "手工模式等待产物超时"),):
        rec = LoopRunRecorder(rig.gov, task, driver=DRIVER, contract=load_loop_contract(), warn=_show)
        rec.end(outcome=reason, exit_code=3, reason=reason, message=message)
        rc, out, err = _cli(["--workspace-root", str(rig.gov), "loop", "status", "--task-id", task])
        _show(f"---- ④ loop status({reason})----\n{out[-900:]}")
        assert f"顾问下一动作: advisor_fix({reason}) — 第三档(归顾问, AIPOS-F225)" in out

    # ⑤ loop note: 无第一档事由 / 事由不在清单 = 拒写(零写入); 带事由 = 进收件箱且显示事由
    before = rec.path.read_text(encoding="utf-8")
    base = ["--workspace-root", str(rig.gov), "loop", "note", "--task-id", task, "--ask", "这卡要不要换方案", "--actor", DRIVER]
    rc, out, err = _cli([*base, "--confirm"])
    _show(f"---- ⑤ loop note 无 --owner-reason rc={rc} ----\n{out}{err}")
    assert rc == 6 and "OWNER_REASON_REQUIRED" in err and "技术故障" in err and rec.path.read_text(encoding="utf-8") == before
    rc, out, err = _cli([*base, "--owner-reason", "gate_rejected", "--confirm"])
    _show(f"---- ⑤ loop note --owner-reason gate_rejected(不在第一档清单)rc={rc} ----\n{out}{err}")
    assert rc == 6 and "给的 'gate_rejected' 不在清单" in err and rec.path.read_text(encoding="utf-8") == before
    rc, out, err = _cli([*base, "--owner-reason", "direction_fork", "--confirm"])
    _show(f"---- ⑤ loop note --owner-reason direction_fork rc={rc} ----\n{out}{err}")
    assert rc == 0 and "第一档事由: 方向 / 设计分叉(direction_fork)" in out and "已进 Owner 收件箱: OI-" in out
    rc, out, err = _cli(["owner", "inbox", "--home-root", str(rig.gov.parent)])
    _show(f"---- ⑤ 收件箱(顾问带第一档事由升 Owner 后)----\n{out}")
    assert "(第一档事由: 方向 / 设计分叉 direction_fork)" in out


# ===========================================================================
# ② 快车道卡: 返工到 2 轮后自动追加第 3、4 轮, 第 5 轮升第一档; ⑦ 撤回后卡停住
# ===========================================================================

def _setup_fast_lane_published(rig) -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(rig.gov / "5_tasks" / "policies" / f"{f221.POLICY}.md", build_autonomy_policy_markdown(
        policy_id=f221.POLICY, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-f225", task_selector_task_mode="config", launch_harnesses=["pi"]))
    path = f78._card(rig.gov, CARD, "pending", task_mode="config",
                     extra={"harness": "claude-code", "audit": "none", "output_target": f"task_cards/{CARD}/artifacts/",
                            "lane": {"repo": "governance_root", "paths": [f"task_cards/{CARD}/artifacts/"], "roles": ["executor"]}})
    _publish_baseline(rig.gov, CARD, path)
    init_governance_repo(rig.gov)


def test_item2_fast_lane_auto_extension_rounds_3_4_then_tier1_and_item7_revoke_stops(rig, monkeypatch):  # noqa: F811
    home = rig.gov.parent
    _setup_fast_lane_published(rig)
    f221._loop(rig, [], lambda r: f221._report(r, "v1"))
    offsets = [(500, 490), (400, 390), (300, 290), (200, 190)]
    for n, (review_ago, round_ago) in enumerate(offsets, 1):
        with f222._past(monkeypatch, review_ago, round_ago):
            rc, payload, err = f221._rework(rig, [f"第 {n} 轮: 超时清理"], f221.CRITERIA1 if n == 1 else None)
        data = payload.get("data") or {}
        _show(f"---- ② 第 {n} 轮打回 rc={rc} ----\n" + _pick(data, "round_added", "auto_release", "owner_extension_ref"))
        assert rc == 0 and data["round_added"] == n, f221._reason(payload, err)
        rounds = f221._rounds(rig)
        if n <= 2:
            assert "auto_extension_ref" not in rounds[-1] and not data.get("auto_release")
        else:
            recs = _auto_records(rig.gov, CARD)
            assert rounds[-1]["auto_extension_ref"] == recs[-1]["auto_release_id"] == data["auto_release"]["record_id"]
            assert recs[-1]["round"] == n and recs[-1]["kind"] == "rework_extension" and recs[-1]["tier"] == 2
            _show(f"[② 第 {n} 轮自动放行记录] {Path(recs[-1]['_path']).relative_to(rig.gov)}\n"
                  + Path(recs[-1]["_path"]).read_text(encoding="utf-8"))
        f222._rewrite_and_reloop(rig, f"v{n + 1}", 2 * n)
    assert len(_auto_records(rig.gov, CARD)) == 2

    # 第 5 轮: 超硬上限 = 拒, 升第一档(rework_cap_exceeded)
    rc, payload, err = f221._rework(rig, ["第 5 轮"], dry_run=True)
    why = f221._reason(payload, err)
    _show(f"---- ② 第 5 轮打回 --dry-run rc={rc} ----\n{why}")
    assert rc == 1 and "自动追加已到硬上限(4 轮, AIPOS-F225)" in why
    rc, payload, err = f221._rework(rig, ["第 5 轮"])
    assert rc == 1 and "REVIEW_REWORK_ESCALATED" in f221._reason(payload, err)
    rc, out, err = _cli(["owner", "inbox", "--home-root", str(home)])
    _, js, _ = _cli(["owner", "inbox", "--home-root", str(home), "--json"])
    data = json.loads(js)
    items = f222._card_items(data, CARD)
    _show(f"---- ② 收件箱(第 5 轮被拒后)----\n{out}")
    assert len(items) == 1 and items[0]["category"] == "rework_limit" and items[0]["owner_reason"]["key"] == "rework_cap_exceeded"
    assert "已返工 4/4 轮" in items[0]["ask"]
    notices = [n for n in data["notices"] if n.get("task_id") == CARD and n["category"] == "auto_released"]
    assert len(notices) == 2 and "第二档 · 已自动放行 / 告知" in out, "自动放行记录可见(不计待定数)"

    # ⑦ Owner 撤回第 4 轮的自动放行 → 卡停住(推导核硬停 / loop 以 auto_release_revoked 结束 / 第一档事项)
    target = next(n for n in notices if n["facts"]["round"] == 4)
    rc, out, err = _cli(["owner", "decide", "--item", target["item_id"], "--choice", "revoke_auto", "--relayed-by", DRIVER,
                         "--owner-words", "第 4 轮先别自动放, 我要看看", "--home-root", str(home), "--confirm"])
    _show(f"---- ⑦ owner decide revoke_auto --confirm rc={rc} ----\n{out}{err}")
    assert rc == 0, out + err
    d = nr.derive_next_step(CARD, rig.gov)
    _show("---- ⑦ lybra next 推导(撤回后)----\n" + _pick(d, "derivable", "current_state", "missing_records", "suggested_action"))
    assert not d["derivable"] and d["action"]["type"] == "auto_release_revoked" and "本卡停住" in d["suggested_action"]
    res, text = f221._loop(rig, [], lambda r: None)
    _show(f"---- ⑦ loop(撤回后)exit {res.exit_code} reason {res.reason} ----\n{_quiet(text)}")
    assert res.reason == "auto_release_revoked"
    rc, st, err = _cli(["--workspace-root", str(rig.gov), "loop", "status", "--task-id", CARD])
    _show(f"---- ⑦ loop status(撤回后)----\n{st[-1000:]}")
    assert "owner_needed(auto_release_revoked)" in st and "第一档事由: 撤卡 / 放弃 withdraw" in st
    _, js, _ = _cli(["owner", "inbox", "--home-root", str(home), "--json"])
    stopped = [i for i in json.loads(js)["items"] if i.get("task_id") == CARD and i["category"] == "loop_owner_needed"]
    assert len(stopped) == 1 and [o["key"] for o in stopped[0]["options"]][:2] == ["continue", "rescope"]
    # Owner 另定「恢复推进」= 撤回之后有新裁决 → 停住解除
    rc, out, err = _cli(["owner", "decide", "--item", stopped[0]["item_id"], "--choice", "continue", "--relayed-by", DRIVER,
                         "--owner-words", "看过了, 继续", "--home-root", str(home), "--confirm"])
    assert rc == 0, out + err
    assert oi.auto_release_stop(rig.gov, CARD) is None
    assert (nr.derive_next_step(CARD, rig.gov).get("action") or {}).get("type") != "auto_release_revoked"


# ===========================================================================
# ② 审计裁决型卡: 同一判据; 改了验收的卡到上限不自动追加
# ===========================================================================

def _audit_card(rig, task: str) -> Path:
    rounds = [{"round": n, "verdict_ref": f"verdict_old_{n}", "created_at": f"2026-10-0{n}T00:00:00Z", "created_by": DRIVER,
               "focus_items": ["x"]} for n in (1, 2)]
    pending = f78._card(rig.gov, task, "pending")
    _publish_baseline(rig.gov, task, pending)
    pending.unlink()
    return f78._card(rig.gov, task, "claimed", extra={"claimed_by": "exec.lybra.test", "claimed_at": "'2026-10-01T00:00:00Z'",
                                                      "rework_rounds": rounds})


def test_item2_audit_verdict_card_auto_rounds_and_changed_acceptance_not_auto(rig):  # noqa: F811
    home = rig.gov.parent
    task, changed = "PROBE-F225-A", "PROBE-F225-B"
    _audit_card(rig, task)
    path_b = _audit_card(rig, changed)
    text = path_b.read_text(encoding="utf-8").replace("output_target: tools/aipos_cli/, tests/", "output_target: tools/aipos_cli/")
    path_b.write_text(text.replace("- 夹具绿", "- 夹具绿(验收已降为只跑定向测试)"), encoding="utf-8")  # 发布后改了范围与验收
    init_governance_repo(rig.gov)
    now = datetime.now(timezone.utc)
    for n in (3, 4):
        vid = f222._verdict(rig.gov, task, n, f221._iso(now + timedelta(seconds=5 * (n - 2))))
        res = f222._queue_rework(rig.gov, task, vid, dry_run=False)
        card = f218._card_fm(rig.gov, task)
        _show(f"---- ② 审计裁决型卡 queue rework 第 {n} 轮 ----\n" + json.dumps(
            {"ok": res.get("ok"), "auto_release": (res.get("data") or {}).get("auto_release"), "round": card["rework_rounds"][-1]},
            ensure_ascii=False))
        assert res.get("ok") and card["rework_rounds"][-1]["round"] == n and card["rework_rounds"][-1]["auto_extension_ref"]
        assert any(w.get("type") == "auto_release_record" for w in res.get("performed_writes") or [])
    v5 = f222._verdict(rig.gov, task, 5, f221._iso(now + timedelta(seconds=20)))
    res = f222._queue_rework(rig.gov, task, v5, dry_run=True)
    _show(f"---- ② 审计裁决型卡第 5 轮(硬上限)----\n{res.get('blocking_reasons')}")
    assert res["verdict"] == "BLOCK" and any("自动追加已到硬上限(4 轮" in r for r in res["blocking_reasons"])

    v3 = f222._verdict(rig.gov, changed, 3, f221._iso(now + timedelta(seconds=5)))
    res = f222._queue_rework(rig.gov, changed, v3, dry_run=True)
    _show(f"---- ② 改了验收 / 范围的卡第 3 轮 ----\n{res.get('blocking_reasons')}")
    assert res["verdict"] == "BLOCK" and any("卡面候选 / 验收 / 范围自发布起已变" in r and "output_target" in r and "__body__" in r
                                            for r in res["blocking_reasons"])
    _, js, _ = _cli(["owner", "inbox", "--home-root", str(home), "--json"])
    items = {i["task_id"]: i for i in json.loads(js)["items"] if i["category"] == "rework_limit"}
    assert set(items) == {task, changed} and all(i["owner_reason"]["key"] == "rework_cap_exceeded" for i in items.values())


# ===========================================================================
# ③ audit_blocked_repeated: 部署变化后自动重新派审 / 无变化 = advisor_fix
# ===========================================================================

def test_item3_audit_blocked_repeated_auto_reaudit(rig):  # noqa: F811
    f202._two_blocks(rig)
    status_text, status = _status(rig.gov, f202.TASK)
    na = status["next_action"]
    _show("---- ③ loop status(连续 BLOCK, 部署 / 验证根未变)----\n" + status_text[-1500:])
    assert na["action"] == "advisor_fix" and na["owner_tier"] == 2 and not na["auto_reaudit"]["eligible"]
    assert "部署提交与验证根声明都未变化" in na["detail"]
    _, js, _ = _cli(["owner", "inbox", "--home-root", str(rig.gov.parent), "--json"])
    assert not [i for i in json.loads(js)["items"] if i.get("task_id") == f202.TASK], "第二档不进 Owner 收件箱"

    # 部署更新(修复上线)后原命令重跑 loop = 自动重新派审(落自动放行记录; F202 同一派审出口, R3)
    res, text = f202._loop(rig, _block_watch(rig, {f"{f202.TASK}R3": "BLOCK"}), f202.COMMIT_NEW)
    _show(f"---- ③ 部署更新后续跑 loop: exit {res.exit_code} reason={res.reason} ----\n{_quiet(text)}")
    assert "auto_reaudit: 第二档自动重新派审 auto_release_" in text and f"--audit-task-id {f202.TASK}R3" in text, text
    assert res.reason == "audit_blocked", text  # R3 再 BLOCK 1 次 = 界后第 1 次, 例行修环境
    recs = _auto_records(rig.gov, f202.TASK)
    assert len(recs) == 1 and recs[0]["kind"] == "auto_reaudit" and f202.COMMIT_NEW[:12] in recs[0]["basis"]
    r3_path, _q = nr._find_task_in_queue(rig.gov, f"{f202.TASK}R3")
    assert "auto_reaudit: 第二档自动重新派审" in str(nr._read_frontmatter(r3_path).get("dispatch_reason"))
    _show("[③ 自动放行记录] " + Path(recs[0]["_path"]).read_text(encoding="utf-8"))

    # R4 再 BLOCK(界后连续 2 次), 部署未再变 = 不自动(advisor_fix 原文)
    reaudit = next(s for s in _status(rig.gov, f202.TASK)[1]["next_action"]["fix"] if s.startswith("修好后重新派审计下一轮: "))
    rc, out, err = _cli(shlex.split(reaudit.split(": ", 1)[1])[1:])
    assert rc == 0, out + err
    res, text = f202._loop(rig, _block_watch(rig, {f"{f202.TASK}R4": "BLOCK"}), f202.COMMIT_NEW)
    assert res.reason == "audit_blocked_repeated" and "auto_reaudit: 不自动重新派审" in text, text
    status_text, status = _status(rig.gov, f202.TASK)
    _show("---- ③ loop status(R4 再 BLOCK, 部署未再变)----\n" + status_text[-1200:])
    assert status["next_action"]["action"] == "advisor_fix" and "归顾问修环境 / 产品" in status["next_action"]["detail"]


# ===========================================================================
# ⑥ 收件箱改前 / 改后件数(靶场自造 chris 10-24 类别分布)
# ===========================================================================

def test_item6_inbox_counts_before_after_with_chris_like_distribution(rig, monkeypatch):  # noqa: F811
    """chris 10-24: 路线图偏差 9、顾问请求 3、审计连续 BLOCK 3、验收抽查 3、连续失败 2 = 20 件。靶场按同类别自造, 改前计数
    = 分级前口径(全部类别都进 items、loop 事由全按 owner_needed), 改后 = 产品现行口径; 顾问请求按 F225 须带第一档事由。"""
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    def run(task: str, reason: str) -> None:
        f78._card(rig.gov, task, "claimed", extra={"claimed_by": "exec.lybra.test", "claimed_at": "'2026-10-01T00:00:00Z'"})
        for _ in range(2 if reason == "max_steps_exhausted" else 1):
            rec = LoopRunRecorder(rig.gov, task, driver=DRIVER, contract=load_loop_contract(), warn=lambda _t: None)
            rec.end(outcome=reason, exit_code=4, reason=reason, message=f"fixture {reason}")

    for n in range(3):
        run(f"PROBE-F225-ASK{n}", "manual_required")
    for n in range(2):
        run(f"PROBE-F225-CF{n}", "max_steps_exhausted")  # 连续 2 次 investigate 类 = 原 consecutive_failures(owner_needed)
    for n in range(3):
        run(f"PROBE-F225-AB{n}", "audit_blocked_repeated")
    init_governance_repo(rig.gov)
    for n in range(3):  # 顾问请求 3 件: 顾问按 F225 带第一档事由升 Owner
        rc, out, err = _cli(["--workspace-root", str(rig.gov), "loop", "note", "--task-id", f"PROBE-F225-ASK{n}", "--ask", "请 Owner 定方案",
                             "--owner-reason", "direction_fork", "--actor", DRIVER, "--confirm"])
        assert rc == 0, out + err
    roadmap_signals = [{"severity": "inbox", "kind": "stalled", "milestone": f"M{n}", "criterion": None, "lane": "x", "label": "停滞",
                        "reason": "停滞超阈值", "subject": f"里程碑 M{n} 停滞", "anchor": f"M{n}|2026-10-01", "advisor": DRIVER,
                        "advice": "补卡", "facts": {"met": 0, "total": 1, "last_at": "2026-10-01"}} for n in range(9)]
    criteria = [{"id": f"C{n}", "state": "owner_pending", "statement": "x", "evidence": [], "qualified_cards": [f"PROBE-F225-ASK{n}"],
                 "verifications": [], "verify_target_by": DRIVER, "verify_target": f"PROBE-F225-ASK{n}"} for n in range(3)]
    view = {"errors": [], "signals": roadmap_signals,
            "milestones": [{"id": "M0", "status": "active", "lane": "x", "progress": {"met": 0, "total": 3}, "criteria": criteria}]}
    import tools.aipos_cli.roadmap_drift as rd
    from tools.aipos_cli import board_pages

    monkeypatch.setattr(rd, "roadmap_status", lambda root, project=None: view)
    monkeypatch.setattr(board_pages, "roadmap_url", lambda *a, **k: "/roadmap")
    after = oi.collect_inbox(rig.gov.parent, include_paused=True)
    by_cat: dict[str, int] = {}
    for item in [*after["items"], *after["notices"]]:
        key = f"{item['category']}/{(item.get('facts') or {}).get('reason') or ''}".rstrip("/")
        by_cat[key] = by_cat.get(key, 0) + 1
    # 改前口径: loop 事由(含第二 / 三档)全为 owner_needed 进 items, 第二档类别也进 items
    from tools.aipos_cli.loop_run_record import loop_status

    before = len(after["items"]) + len(after["notices"])
    for n in range(2):
        before += 1 if loop_status(rig.gov, f"PROBE-F225-CF{n}")["next_action"].get("tier_from") else 0
    for n in range(3):
        before += 1 if loop_status(rig.gov, f"PROBE-F225-AB{n}")["next_action"].get("tier_from") else 0
    _show(f"---- ⑥ 收件箱件数: 改前 {before} 件(分级前口径) → 改后 {after['count']} 件第一档 + {after['notice_count']} 件第二档告知 ----\n"
          f"[分布] {json.dumps(by_cat, ensure_ascii=False)}\n" + oi.render_inbox_text(after))
    assert before == 20 and after["count"] == 6 and after["notice_count"] == 9
    assert [i["category"] for i in after["items"]][-3:] == ["owner_check"] * 3, "验收抽查排末尾"


# ===========================================================================
# 声明 / 单一实现 / 夹具发现
# ===========================================================================

def test_declarations_single_source_and_discovery():
    from tools.aipos_cli import queue_mutation
    from tools.aipos_cli.verb_contract import declared_exit_codes
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    auto = queue_mutation.auto_extension_declaration()
    assert auto["hard_cap"] == 4 and auto["round_field"] == "auto_extension_ref"
    card = json.loads((REPO_ROOT / "schema" / "card.schema.json").read_text(encoding="utf-8"))
    assert auto["round_field"] in card["fields"]["rework_rounds"]["items"]["properties"] and "owner_reason" in card["fields"]
    assert declared_exit_codes("lybra_loop_note")["owner_reason_refused"] == 6
    spec = oi.auto_release_declaration()
    assert spec["revoke_option"] in oi.inbox_declaration()["options"] and spec["revoked_end_reason"] in oi.tier_declaration()["loop_reasons"]
    qsrc = (REPO_ROOT / "tools" / "aipos_cli" / "queue_mutation.py").read_text(encoding="utf-8")
    body = qsrc.split("def build_rework_round(", 1)[1].split("\ndef ", 1)[0]
    assert "rework_round_budget(" in body and "auto_extension_check(" in body
    isrc = (REPO_ROOT / "tools" / "aipos_cli" / "owner_inbox.py").read_text(encoding="utf-8")
    assert isrc.count("write_records_atomic(") == 1, "自动放行记录只经既有事件记录写口"
    for rel in ("tools/aipos_cli/loop_driver.py", "tools/aipos_cli/board_adapter.py"):
        assert "write_auto_release(" in (REPO_ROOT / rel).read_text(encoding="utf-8")
    import inspect

    for fn in (oi.apply_owner_tier, oi._check_tiers, oi.auto_release_stop, oi.write_auto_release, nr.auto_reaudit_check,
               queue_mutation.auto_extension_check, queue_mutation.publish_scope_baseline):
        assert "except Exception" not in inspect.getsource(fn), fn.__name__
    assert is_test_file("tests/test_aipos_f225_owner_intervention_tiers.py", default_test_contract())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
