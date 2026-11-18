"""F294: real multi-repository derived-audit claim and detached worktree tests."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_aipos_f184_queue_landing_supersede import rig, _card, _git, EXEC, AUDITOR
from test_aipos_f285_concurrent_landing_claim_residue import _claim
from test_aipos_f73d_loop_driver import _fm, _write
from tools.aipos_cli.frontmatter import require_frontmatter
from tools.aipos_cli.next_resolver import _ensure_worktree, card_worktree_location, return_binding_subject
from tools.aipos_cli.target_branch import target_branch_problem
from tools.aipos_cli.task_loader import find_task_card

TASK = "PROJ-294"


def _fixture(rig, *, target="ota"):
    gov = rig["gov"]
    config = json.loads((gov / "project.json").read_text())
    business = Path(config["code_repo"])
    default = business.parent / "default-product"
    default.mkdir()
    _git(default, "init", "-q", "-b", "main")
    _git(default, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "default baseline", "--allow-empty")
    _git(business, "config", "user.name", "fixture")
    _git(business, "config", "user.email", "fixture@example.invalid")
    _git(business, "branch", "ota")
    config["code_repo"] = str(default)
    config["repos"] = {"default": "web", "items": {"web": str(default), "mobile": str(business)},
                       "no_deploy": ["web", "mobile"]}
    (gov / "project.json").write_text(json.dumps(config))
    extra = {"lane": {"repo": "mobile", "paths": ["feature.txt"]}}
    if target is not None:
        extra["target_branch"] = target
    _card(gov, TASK, "pending", extra=extra)
    claimed = _claim(gov, TASK)
    assert claimed.get("ok", claimed.get("verdict") != "BLOCK"), claimed
    source, _ = find_task_card(gov, TASK)
    sfm, _ = require_frontmatter(source)
    _, worktree = card_worktree_location(gov, TASK, sfm)
    _write(worktree / "feature.txt", "candidate\n")
    _git(worktree, "add", "--", "feature.txt")
    _git(worktree, "commit", "-qm", "business candidate")
    candidate = _git(worktree, "rev-parse", "HEAD")
    subject = return_binding_subject(gov, TASK, sfm)
    record = "return_PROJ-294_fixture"
    _write(gov / "5_tasks/records/returns" / TASK / (record + ".md"),
           _fm({"record_type": "return_record", "task_id": TASK, "return_id": record,
                "artifact_subject": subject}))
    from tools.aipos_cli.audit_derivation import build_derived_audit_task
    audit = build_derived_audit_task(source_task_id=TASK, source_metadata=sfm,
        source_path=str(source.relative_to(gov)), return_record_ref=record,
        artifact_refs=["task_cards/" + TASK + "/RETURN.md"], repo_root=gov)
    afm = audit["metadata"]
    assert "lane" not in afm
    audit_path = _card(gov, afm["task_id"], "pending", assigned=AUDITOR, extra=afm)
    return gov, default, business, source, audit_path, candidate


def _audit_claim(gov, audit_id):
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task
    return mutate_queue_task(gov, "claim", task_id=audit_id, actor=AUDITOR, dry_run=False,
                             profiles=load_agent_profiles(gov), with_records=True)


def _snapshot(gov, *repos):
    return (
        tuple((_git(repo, "show-ref"), _git(repo, "symbolic-ref", "HEAD"),
               _git(repo, "rev-parse", "HEAD"), _git(repo, "worktree", "list", "--porcelain")) for repo in repos),
        {str(p.relative_to(gov)): hashlib.sha256(p.read_bytes()).hexdigest()
         for parent in (gov / "5_tasks/queue", gov / "5_tasks/records")
         for p in parent.rglob("*") if p.is_file()},
    )


@pytest.mark.parametrize("shape", ["legacy_no_lane", "matching_lane", "derived_from_only", "supplementary"])
def test_actual_derived_pending_audit_claims_business_candidate(rig, shape):
    gov, default, business, source, audit_path, candidate = _fixture(rig)
    afm, body = require_frontmatter(audit_path)
    if shape == "matching_lane":
        afm["lane"] = {"repo": "mobile"}
    if shape == "derived_from_only":
        afm.pop("reviewed_task_id")
    if shape == "supplementary":
        previous = {**afm, "task_id": TASK + "R0", "status": "completed"}
        _card(gov, previous["task_id"], "completed", assigned=AUDITOR, extra=previous)
        afm["supplementary_of"] = previous["task_id"]
    _write(audit_path, _fm(afm, body))
    before_default = _git(default, "show-ref"), _git(default, "rev-parse", "HEAD")
    before_business = _git(business, "show-ref"), _git(business, "rev-parse", "HEAD")
    before_source = source.read_bytes()
    result = _audit_claim(gov, afm["task_id"])
    assert result.get("ok", result.get("verdict") != "BLOCK"), result
    claimed_path, state = find_task_card(gov, afm["task_id"])
    assert state == "claimed", result
    claimed_fm, _ = require_frontmatter(claimed_path)
    assert target_branch_problem(gov, claimed_fm, require_binding=True) is None
    repo, worktree = card_worktree_location(gov, afm["task_id"], claimed_fm)
    assert repo == business and worktree.parent.parent == business
    assert _git(worktree, "rev-parse", "HEAD") == candidate
    assert subprocess.run(["git", "-C", str(worktree), "symbolic-ref", "-q", "HEAD"]).returncode == 1
    before_retry = _snapshot(gov, default, business)
    again = _ensure_worktree(gov, afm["task_id"], claimed_fm)
    assert again["ok"] and again["commit"] == candidate, again
    assert _snapshot(gov, default, business) == before_retry
    assert (_git(default, "show-ref"), _git(default, "rev-parse", "HEAD")) == before_default
    assert (_git(business, "show-ref"), _git(business, "rev-parse", "HEAD")) == before_business
    assert source.read_bytes() == before_source
    assert not (default / ".worktrees" / afm["task_id"]).exists()


@pytest.mark.parametrize("problem", ["conflict", "missing", "no_target", "target_mismatch",
                                    "audit_cycle", "source_cycle"])
def test_bad_source_refused_before_queue_records_or_refs_change(rig, problem):
    gov, default, business, source, audit_path, _ = _fixture(rig)
    afm, body = require_frontmatter(audit_path)
    if problem == "conflict":
        afm["lane"] = {"repo": "web"}
    elif problem == "missing":
        afm["reviewed_task_id"] = "PROJ-999"
    elif problem == "no_target":
        _git(business, "branch", "-D", "ota")
    elif problem == "target_mismatch":
        afm["target_branch"] = "main"
    elif problem == "audit_cycle":
        afm["reviewed_task_id"] = afm["task_id"]
    elif problem == "source_cycle":
        sfm, sbody = require_frontmatter(source)
        sfm["derived_from_audit_task_id"] = afm["task_id"]
        _write(source, _fm(sfm, sbody))
    _write(audit_path, _fm(afm, body))
    before = _snapshot(gov, default, business)
    result = _audit_claim(gov, afm["task_id"])
    assert not result.get("ok", result.get("verdict") != "BLOCK"), result
    assert _snapshot(gov, default, business) == before
    assert not (business / ".worktrees" / afm["task_id"]).exists()
    assert not (default / ".worktrees" / afm["task_id"]).exists()


def test_legacy_default_main_audit_and_ordinary_card_remain_supported(rig):
    gov, default, business, _, audit_path, candidate = _fixture(rig, target=None)
    afm, _ = require_frontmatter(audit_path)
    result = _audit_claim(gov, afm["task_id"])
    assert result.get("ok", result.get("verdict") != "BLOCK"), result
    _, worktree = card_worktree_location(gov, afm["task_id"], afm)
    assert _git(worktree, "rev-parse", "HEAD") == candidate
    ordinary = {"task_id": "PROJ-295", "task_mode": "code"}
    out = _ensure_worktree(gov, ordinary["task_id"], ordinary)
    assert out["ok"], out
    assert Path(out["worktree_path"]).parent.parent == default
    assert _git(Path(out["worktree_path"]), "rev-parse", "HEAD") == _git(default, "rev-parse", "main")
