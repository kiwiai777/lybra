"""AIPOS-F180C - `lybra roadmap verify --result mismatch`: the auto-drafted fix card inherits only the artifacts declared on
the verified criterion (plus the required fields of the inherited task_class), and a refused fix draft never blocks the
Owner verification record (record first and landed, draft refusal reported with a manual drafting hint, non-zero exit).

Rig = the AIPOS-F175 rig (temp HOME + temp home root git repo with a bare remote; project name / instances / card ids are
rig values). Extra criteria and cards are added through the product roadmap command and the declared card / record
locations. No real credentials, no real governance root, no writes to a real gate, no subprocess except git (rig landing).

Acceptance (card): (1) multi-criterion source card -> fix card carries only this criterion's artifacts; filtered-empty case;
(2) drafted fix refused (rig-made validation failure) -> record still written and landed, status / inbox / manual hint;
plus declaration / single-implementation invariants.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f171_roadmap_board_readonly import _cli, _git, _tree_digest  # noqa: E402
from test_aipos_f175_verification_owner_check import (  # noqa: E402,F401  (single source of rig helpers; rig is a fixture)
    ADVISOR, AUDIT, EXEC, PNG, PROJECT, _artifact, _card, _commit, _iso, _show, _status, _verdict, _verify, rig,
)


def _add_criteria(rig, *ids: str) -> None:
    for cid in ids:
        rc, out, err = _cli("roadmap", "add-criterion", *rig["ws"], "--milestone", "M1", "--id", cid, "--statement",
                            f"{cid} page matches design", "--verify", "owner_check", "--confirm", "--actor", ADVISOR)
        assert rc == 0, f"{cid}\n{out}{err}"


@pytest.fixture
def multi(rig):
    """K-7 hangs on two owner_check criteria (C7 ui review + C8 runtime evidence), complex class, artifacts on both."""
    _add_criteria(rig, "C7", "C8")
    _card(rig["gov"], "completed", "K-7", criteria=["C8", "C7"], completed_at=_iso(-1), task_class="complex",
          planner_agent=ADVISOR, reviewer=AUDIT, audit_by=AUDIT,
          artifacts=[{"criterion": "C8", "path_glob": "evidence-manifest.json", "kind": "report"},
                     {"criterion": "C8", "path_glob": "review-guide.md", "kind": "report"},
                     {"criterion": "C7", "path_glob": "screens/*.png", "kind": "screenshot"}])
    _verdict(rig["gov"], "K-7")
    _artifact(rig["gov"], "K-7", "evidence-manifest.json", b'{"ok": true}\n')
    _artifact(rig["gov"], "K-7", "review-guide.md", "# guide\n".encode("utf-8"))
    _artifact(rig["gov"], "K-7", "screens/home.png", PNG)
    _artifact(rig["gov"], "K-7", "screens/fail.png", PNG)
    _commit(rig["home"], "fixture: K-7 multi-criterion card")
    return rig


# ===========================================================================
# (1) fix card inherits only this criterion's artifacts
# ===========================================================================

def test_item1_fix_card_keeps_only_this_criterion_artifacts(multi):
    from tools.aipos_cli.draft_validator import expected_draft_relative_path, validate_draft_file
    from tools.aipos_cli.frontmatter import require_frontmatter

    assert _status(multi["gov"])["C7"]["state"] == "owner_pending"
    argv = ("--criterion", "C7", "--result", "mismatch", "--relayed-by", ADVISOR, "--owner-words", "产品交互不通过")
    before = _tree_digest(multi["home"])
    rc, out, err = _verify(multi, *argv)
    _show(f"---- (1) verify mismatch preview (source card artifacts on C7 + C8) exit {rc} ----\n{out}{err}")
    assert rc == 0 and err == "" and "修复卡草稿(将写): K-7-oc1 → 5_tasks/drafts/k-7-oc1.md" in out
    assert _tree_digest(multi["home"]) == before, "preview writes nothing"
    rc, out, err = _verify(multi, *argv, "--confirm", "--actor", "owner")
    _show(f"---- (1) verify mismatch --confirm exit {rc} ----\n{out}{err}")
    assert rc == 0 and "修复卡草稿: K-7-oc1" in out and "落账: verdict=PASS committed=True pushed=True" in out
    draft = multi["gov"] / expected_draft_relative_path("K-7-oc1")
    text = draft.read_text(encoding="utf-8")
    _show(f"---- (1) drafted fix card ----\n{text}")
    meta, body = require_frontmatter(draft)
    assert meta["criteria"] == ["C7"]
    assert meta["artifacts"] == [{"criterion": "C7", "path_glob": "screens/*.png", "kind": "screenshot"}]
    assert meta["task_class"] == "complex" and meta["planner_agent"] == ADVISOR and meta["reviewer"] == AUDIT
    assert "产品交互不通过" in body
    assert ("本标准产物 task_cards/K-7/artifacts/screens/fail.png, task_cards/K-7/artifacts/screens/home.png)" in body
            and "evidence-manifest.json" not in body)
    assert "原卡没有挂在" not in body
    checked = validate_draft_file(multi["gov"], draft.relative_to(multi["gov"]).as_posix())
    _show(f"[(1) draft validate (same check as publish preview)] verdict={checked['verdict']} blocking={checked['blocking_reasons']}")
    assert checked["verdict"] != "BLOCK", checked


def test_item1_filtered_empty_and_form_check_not_relaxed(multi):
    """Single drafting function, called directly: nothing left after filtering -> no artifacts + body hint; a malformed entry is
    kept and still refused by the unique form check roadmap.artifact_problems (via create_draft)."""
    from tools.aipos_cli import roadmap
    from tools.aipos_cli.draft_writer import create_draft

    decl = roadmap.verify_declaration()
    source = {"task_id": "K-7", "project": PROJECT, "assigned_to": EXEC, "agent_instance": EXEC, "context_bundle": EXEC,
              "task_mode": "code", "priority": "high", "output_target": "src/", "artifact_policy": "formal_write", "audit_by": AUDIT,
              "criteria": ["C8", "C7"], "artifacts": [{"criterion": "C8", "path_glob": "evidence-manifest.json", "kind": "report"}]}
    common = {"fix_task_id": "K-7-oc9", "milestone": "M1", "criterion": "C7", "statement": "C7 page matches design",
              "record_id": "verify_K-7_x", "record_rel": "5_tasks/records/owner_verifications/K-7/verify_K-7_x.md",
              "relayed_by": ADVISOR, "owner_words": "产品交互不通过", "note": None, "decl": decl}
    meta, body = roadmap._fix_draft_payload(multi["gov"], source, **common)
    _show(f"---- (1) filtered-empty: drafted meta keys {sorted(meta)}\n{body}")
    assert "artifacts" not in meta and meta["criteria"] == ["C7"]
    assert decl["fix_card"]["artifacts_missing"].format(criterion="C7") in body
    assert "本标准产物 (原卡未声明))" in body
    res = create_draft(multi["gov"], meta, body, dry_run=True)
    assert res["verdict"] != "BLOCK", res["blocking_reasons"]

    bad = {**source, "artifacts": ["not-a-mapping", {"criterion": "C8", "path_glob": "x.json", "kind": "report"}]}
    meta, _body = roadmap._fix_draft_payload(multi["gov"], bad, **common)
    assert meta["artifacts"] == ["not-a-mapping"]
    res = create_draft(multi["gov"], meta, _body, dry_run=True)
    _show(f"[(1) malformed entry kept -> form check refuses] {res['verdict']} {res['blocking_reasons']}")
    assert res["verdict"] == "BLOCK" and any("CARD_ARTIFACTS_INVALID" in r and "artifacts[0] 须为映射" in r for r in res["blocking_reasons"])


# ===========================================================================
# (2) fix draft refused -> owner record still written and landed
# ===========================================================================

@pytest.fixture
def broken(rig):
    """K-9 on owner_check C9 is complex-class but lacks planner_agent / reviewer (rig-made): the inherited fix draft is refused
    by the draft check while the criterion itself derives to owner_pending."""
    _add_criteria(rig, "C9")
    _card(rig["gov"], "completed", "K-9", criteria=["C9"], completed_at=_iso(-1), task_class="complex", audit_by=AUDIT,
          artifacts=[{"criterion": "C9", "path_glob": "detail.png", "kind": "screenshot"}])
    _verdict(rig["gov"], "K-9")
    _artifact(rig["gov"], "K-9", "detail.png", PNG)
    _commit(rig["home"], "fixture: K-9 card whose fix draft is refused")
    return rig


def test_item2_refused_fix_draft_keeps_owner_record(broken):
    from tools.aipos_cli.draft_validator import expected_draft_relative_path
    from tools.aipos_cli.owner_inbox import collect_inbox
    from tools.aipos_cli.roadmap_drift import roadmap_status

    assert _status(broken["gov"])["C9"]["state"] == "owner_pending"
    inbox_before = sorted(i["subject"] for i in collect_inbox(broken["home"])["items"] if i["category"] == "owner_check")
    _show(f"[(2) inbox owner_check before] {inbox_before}")
    assert "标准 C9 待 Owner 抽查" in inbox_before
    argv = ("--criterion", "C9", "--result", "mismatch", "--relayed-by", ADVISOR, "--owner-words", "详情页交互不通过")
    before = _tree_digest(broken["home"])
    rc, out, err = _verify(broken, *argv)
    _show(f"---- (2) verify mismatch preview (fix draft refused) exit {rc} ----\n{out}{err}")
    assert rc == 1 and "Owner 抽查记录(将写): 5_tasks/records/owner_verifications/K-9/verify_K-9_" in out
    assert "修复卡草稿(将写): K-9-oc1 起草被拒(抽查记录照常写入, --confirm 后落账): VERIFY_FIX_DRAFT_BLOCKED" in out
    assert "Complex-class task missing planner_agent" in out and "(frontmatter 含 task_id = K-9-oc1, milestone = M1" in out
    assert "修复卡 K-9-oc1 起草被拒, Owner 抽查记录照常写入, --confirm 后落账" in err
    assert _tree_digest(broken["home"]) == before, "preview writes nothing"

    rc, out, err = _verify(broken, *argv, "--confirm", "--actor", "owner")
    _show(f"---- (2) verify mismatch --confirm (fix draft refused) exit {rc} ----\n{out}{err}")
    assert rc == 1 and "Owner 抽查记录: 5_tasks/records/owner_verifications/K-9/verify_K-9_" in out
    assert "落账: verdict=PASS committed=True pushed=True" in out
    assert "修复卡草稿: K-9-oc1 起草被拒(抽查记录已写入并落账, 不回滚)" in out
    assert "修复卡 K-9-oc1 起草被拒, Owner 抽查记录已写入并落账, 不回滚; 修复卡待出" in err
    rec = next((broken["gov"] / "5_tasks" / "records" / "owner_verifications" / "K-9").glob("verify_K-9_*.md"))
    record = rec.read_text(encoding="utf-8")
    _show(f"---- (2) owner record written ----\n{record}")
    assert "result: mismatch" in record and "fix_task_id: K-9-oc1" in record and "owner_words: 详情页交互不通过" in record
    assert f"owner_check_ref = {rec.stem}" in out, "manual hint points at the written record id"
    assert not (broken["gov"] / expected_draft_relative_path("K-9-oc1")).exists()
    log = _git(broken["home"], "log", "-1", "--name-only", "--format=%s")
    _show(f"---- (2) landing commit ----\n{log}")
    assert rec.relative_to(broken["home"]).as_posix() in log and "drafts" not in log
    assert _git(broken["home"], "status", "--porcelain") == ""

    c9 = _status(broken["gov"])["C9"]
    _show(f"[(2) C9 after] state={c9['state_label']} fix_cards={c9['fix_cards']}\n        basis={c9['basis']}")
    assert c9["state"] == "mismatch" and c9["fix_cards"][0]["pending"] is True
    assert "· 修复卡 K-9-oc1(待出: 自动起草被拒, 顾问按 roadmap verify 输出的指引手工出卡)" in c9["basis"]
    view = roadmap_status(broken["gov"], project=PROJECT)
    steps = [s for s in view["next_steps"] if "C9" in s]
    _show(f"[(2) next steps] {steps}")
    assert any("Owner 判不符 · 修复卡 K-9-oc1(待出" in s for s in steps)
    inbox_after = sorted(i["subject"] for i in collect_inbox(broken["home"])["items"] if i["category"] == "owner_check")
    _show(f"[(2) inbox owner_check after] {inbox_after}")
    assert "标准 C9 待 Owner 抽查" not in inbox_after

    rc, out, err = _cli("roadmap", "status", *broken["ws"])
    _show(f"---- (2) lybra roadmap status exit {rc} ----\n{out}{err}")
    assert rc == 0 and "· C9 [owner_check] C9 page matches design — 不符:" in out and "修复卡 K-9-oc1(待出" in out

    # follow the manual hint: a hand-written draft with the named fields (+ the class-required fields the refusal named) passes
    payload = {"task_id": "K-9-oc1", "title": "fix C9", "project": PROJECT, "assigned_to": EXEC, "agent_instance": EXEC,
               "context_bundle": EXEC, "task_mode": "code", "task_class": "complex", "priority": "high", "output_target": "src/",
               "artifact_policy": "formal_write", "audit_by": AUDIT, "planner_agent": ADVISOR, "reviewer": AUDIT,
               "milestone": "M1", "criteria": ["C9"], "owner_check_ref": rec.stem, "created_by": ADVISOR,
               "artifacts": [{"criterion": "C9", "path_glob": "detail.png", "kind": "screenshot"}]}
    src = broken["tmp"] / "k9-oc1.json"
    src.write_text(json.dumps({"frontmatter": payload, "body": "# K-9-oc1\n\n- Owner 原话: 详情页交互不通过\n"},
                              ensure_ascii=False), encoding="utf-8")
    # same JSON loader + draft check as `draft create --from-json --dry-run` (actor signing needs a credential the rig lacks)
    from tools.aipos_cli.draft_writer import create_draft, load_create_payload_from_json

    hand_meta, hand_body = load_create_payload_from_json(src)
    res = create_draft(broken["gov"], hand_meta, hand_body, dry_run=True)
    _show(f"[(2) hand-written draft per hint, draft check dry-run] verdict={res['verdict']} blocking={res['blocking_reasons']}")
    assert res["verdict"] != "BLOCK", res["blocking_reasons"]


def test_item2_json_reports_not_ok_with_manual_hint(broken):
    rc, out, err = _verify(broken, "--criterion", "C9", "--result", "mismatch", "--relayed-by", ADVISOR, "--owner-words", "不通过",
                           "--json")
    data = json.loads(out)
    _show(f"[(2) --json preview] rc={rc} ok={data['ok']} fix={data['fix']} fix_error={data['fix_error'][:80]}... "
          f"fix_manual={data['fix_manual'][:60]}...")
    assert rc == 1 and data["ok"] is False and data["fix"] is None and data["record"]["path"].startswith("5_tasks/records/")
    assert data["fix_task_id"] == "K-9-oc1" and "VERIFY_FIX_DRAFT_BLOCKED" in data["fix_error"] and data["fix_manual"].startswith("手工出卡")


def test_declarations_and_single_drafting_function():
    from tools.aipos_cli import roadmap, roadmap_drift

    decl = roadmap.verify_declaration()
    for key in ("artifacts_missing", "evidence_none", "manual_hint"):
        assert decl["fix_card"][key], key
    for key in ("{evidence}", "{artifacts_note}"):
        assert key in decl["fix_card"]["body_template"], key
    for key in ("fix_blocked", "record_kept_preview", "record_kept_written", "fix_error"):
        assert decl["texts"][key], key
    assert "零写入" not in decl["reject_codes"]["VERIFY_FIX_DRAFT_BLOCKED"]
    assert roadmap_drift.status_declaration()["verification"]["basis"]["fix_pending"]
    src = Path(roadmap.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert src.count("def _fix_draft_payload(") == 1 and src.count("_fix_draft_payload(") == 2
    assert src.count("create_draft(") == 1 and src.count("build_owner_verification_record(") == 2
    for literal in ("/home/", "2_projects", "chris", "kiwiai", "HBLX"):
        assert literal not in src, literal
