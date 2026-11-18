"""Task integration targets. Source/fix bases never override this binding."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def validate_branch_name(name: Any) -> str:
    from tools.schema_loader import SchemaLoadError, get_branch_integration

    prefix = str(get_branch_integration()["branch_pattern"]).split("{task_id}")[0]
    if (not isinstance(name, str) or not name or name != name.strip()
            or name.startswith(("-", "refs/")) or name in {
                "HEAD", "FETCH_HEAD", "ORIG_HEAD", "MERGE_HEAD", "CHERRY_PICK_HEAD",
                "REVERT_HEAD", "BISECT_HEAD", "AUTO_MERGE"}
            or (len(name) == 40 and all(c in "0123456789abcdefABCDEF" for c in name))
            or (prefix and name.startswith(prefix))
            or subprocess.run(["git", "check-ref-format", "refs/heads/" + name], capture_output=True).returncode):
        raise SchemaLoadError("TARGET_BRANCH_INVALID: expected a local integration branch name")
    return name


def card_target_branch(metadata: dict[str, Any] | None = None,
                       branch_integration: dict[str, Any] | None = None) -> str:
    from tools.aipos_cli.next_resolver import card_base_branch
    from tools.schema_loader import SchemaLoadError

    fm = metadata or {}
    target = validate_branch_name(fm["target_branch"]) if "target_branch" in fm else card_base_branch(branch_integration)
    if "claimed_target_branch" in fm and fm["claimed_target_branch"] != target:
        raise SchemaLoadError("TARGET_BRANCH_CHANGED_AFTER_CLAIM: create a new card instead of changing its integration target")
    return target


def target_branch_problem(governance_root: Path, metadata: dict[str, Any] | None, *,
                          require_binding: bool = False) -> str | None:
    from tools.aipos_cli.workspace_config import resolve_card_repo, CardRepoUnresolved
    from tools.schema_loader import SchemaLoadError
    from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter

    fm = metadata or {}
    if "target_branch" not in fm and "claimed_target_branch" not in fm:
        return None  # Historical cards retain the original default policy.
    try:
        target = card_target_branch(fm)
        repo = resolve_card_repo(governance_root, fm)
        result = subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "--hash", "refs/heads/" + target],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            return f"TARGET_BRANCH_NOT_LOCAL: {target} in {repo}"
        resolved = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", target + "^{commit}"],
                                  capture_output=True, text=True, timeout=10)
        if resolved.returncode or resolved.stdout.strip() != result.stdout.strip():
            return f"TARGET_BRANCH_AMBIGUOUS: {target} does not resolve uniquely to its local branch commit"
        carried = fm.get("carried_from") or {}
        if not isinstance(carried, dict):
            return "TARGET_BRANCH_SOURCE_INVALID: carried_from must be an object"
        for source in (fm.get("reviewed_task_id"), fm.get("derived_from_audit_task_id"),
                       fm.get("supplementary_of"), carried.get("task_id")):
            if not source:
                continue
            path, _ = _find_task_in_queue(governance_root, str(source))
            if not path:
                return f"TARGET_BRANCH_SOURCE_MISSING: {source}"
            source_fm = _read_frontmatter(path)
            if card_target_branch(source_fm) != target or resolve_card_repo(governance_root, source_fm).resolve() != repo.resolve():
                return f"TARGET_BRANCH_SOURCE_MISMATCH: {source}"
        if require_binding and fm.get("claim_id"):
            from tools.aipos_cli.record_writer import claim_record_paths
            claim_path, _ = claim_record_paths(governance_root, str(fm.get("task_id")), str(fm["claim_id"]),
                                               str(fm.get("active_session_id") or ""))
            if not claim_path.is_file() or _read_frontmatter(claim_path).get("target_branch") != target:
                return "TARGET_BRANCH_CLAIM_BINDING_MISMATCH: claim record does not bind this target"
    except (SchemaLoadError, CardRepoUnresolved, ValueError, OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return None


def verdict_target_problem(governance_root: Path, task_id: str, verdict: dict[str, Any]) -> str | None:
    from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter, return_record_subject

    path, _ = _find_task_in_queue(governance_root, task_id)
    if not path:
        return None
    fm = _read_frontmatter(path)
    if "target_branch" not in fm and "claimed_target_branch" not in fm:
        return None
    problem = target_branch_problem(governance_root, fm, require_binding=True)
    if problem:
        return problem
    subject = return_record_subject(governance_root, task_id, str(verdict.get("reviewed_return_record_ref") or ""))
    if not subject or subject.get("target_branch") != card_target_branch(fm):
        return "TARGET_BRANCH_VERDICT_MISMATCH: verdict must reference a return bound to this integration target"
    artifact = verdict.get("artifact_subject") or {}
    if subject.get("commit_sha") != artifact.get("commit_sha"):
        return "TARGET_BRANCH_VERDICT_MISMATCH: return and verdict must bind the same candidate"
    return None
