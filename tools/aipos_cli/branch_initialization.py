"""Owner-confirmed local branch creation through the existing decision gate."""
from __future__ import annotations
import hashlib
import json
import re
import subprocess
from datetime import timedelta
from pathlib import Path
from tools.schema_loader import SchemaLoadError
from typing import Any, Callable


def branch_plan(root: Path, block: dict[str, Any]) -> dict[str, Any]:
    from tools.aipos_cli.target_branch import validate_branch_name
    from tools.aipos_cli.workspace_config import resolve_card_repo
    from tools.aipos_cli.autonomy_policy import _parse_iso as parse_iso

    from tools.schema_loader import load_schema
    declared = load_schema("verbs")["verbs"]["lybra_owner_decision_record_dry_run"]["branch_initialization"]
    if not declared["owner_confirm_required"]:
        raise ValueError("BRANCH_INIT_DECLARATION: Owner confirmation is required")
    if not isinstance(block, dict) or set(block) != set(declared["required_fields"]):
        raise ValueError("BRANCH_INIT_SHAPE: repo, branch, start_commit, requested_at are required")
    name = validate_branch_name(block["branch"])
    sha = block["start_commit"]
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise ValueError("BRANCH_INIT_SHA: full lowercase 40-character commit SHA required")
    if not isinstance(block["repo"], str) or not block["repo"].strip():
        raise ValueError("BRANCH_INIT_REPO: registered repo name required")
    repo = resolve_card_repo(root, {"lane": {"repo": block["repo"]}})
    kind = subprocess.run(["git", "-C", str(repo), "cat-file", "-t", sha], capture_output=True, text=True)
    if kind.returncode or kind.stdout.strip() != "commit":
        raise ValueError("BRANCH_INIT_COMMIT: commit does not exist in the selected repository")
    old = subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "--hash", "refs/heads/" + name],
                         capture_output=True, text=True)
    if old.returncode not in (0, 1, 128):
        raise ValueError("BRANCH_INIT_READ_FAILED: cannot inspect local refs")
    previous = old.stdout.strip() if old.returncode == 0 else None
    if previous and previous != sha:
        raise ValueError("BRANCH_INIT_EXISTS_DIFFERENT: existing branch will never be moved")
    if parse_iso(block["requested_at"]) is None:
        raise ValueError("BRANCH_INIT_TIME: requested_at must be an ISO timestamp")
    return {"repo": str(repo), "repo_name": block["repo"], "branch": name, "start_commit": sha,
            "existing_commit": previous, "action": "unchanged" if previous else "create"}


def decision_payload(root: Path, payload: dict[str, Any], plan: dict[str, Any], actor: str | None) -> dict[str, Any]:
    from tools.aipos_cli.autonomy_policy import client_tag
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.autonomy_policy import _parse_iso as parse_iso
    from tools.aipos_cli.workspace_config import declared_project_id

    block = payload["branch_initialization"]
    project = declared_project_id(root)
    tag = client_tag(project)
    digest = hashlib.sha256(json.dumps(block, sort_keys=True).encode()).hexdigest()
    ref = "branch_initialization:" + digest
    at = block["requested_at"]
    who = str(actor or "owner")
    return {"decision_id": payload["decision_id"], "decision_type": "branch_initialization", "decision_status": "approved",
            "decided_at": at, "decided_by_ref": who, "captured_by": who, "capture_surface": "cli",
            "decision_summary": "Create local integration branch " + plan["branch"],
            "decision_rationale": json.dumps(plan, sort_keys=True),
            "applies_to": {"project": project if tag == project else None, "external_ref": ref},
            "approval_scope": {"operation": "owner_decision_record", "authority_boundary": "One local branch at one exact commit; no reset or push",
                               "allowed_next_action": "branch_initialization"},
            "owner_approval_evidence": {"evidence_id": ref, "source_tag": "owner_console", "client_tag": tag,
                "external_ref": ref, "approval_actor_ref": who, "approval_timestamp": at, "approval_intent": "initialize_local_branch",
                "evidence_hash": digest, "evidence_ref": "harness:owner_confirm", "captured_by": who,
                "capture_method": "harness_owner_confirm", "redaction_status": "not_applicable", "refs": [plan["repo"]]},
            "refs": [plan["repo"]], "capability_scope": {"token_ref": "owner_console", "operations": ["owner_decision_record"],
                "projects": [project], "expires_at": iso_z(parse_iso(at) + timedelta(days=1))},
            **{k: payload[k] for k in ("credential_project", "target_project") if payload.get(k)}}


def build_branch_decision(root: Path, payload: dict[str, Any], *, actor: str | None, dry_run: bool,
                          backend: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    from tools.schema_constants import Verdict

    try:
        plan = branch_plan(root, payload.get("branch_initialization"))
        base = decision_payload(root, payload, plan, actor)
    except (ValueError, OSError, TypeError, KeyError, SchemaLoadError) as exc:
        return {"verdict": Verdict.BLOCK, "blocking_reasons": [str(exc)], "wrote": False, "would_write": False,
                "planned_writes": [], "original_payload": payload, "branch_initialization": True}
    result = dict(backend(root, base, actor=actor, dry_run=True))
    result.update({"original_payload": payload, "branch_initialization": True, "branch_plan": plan})
    if dry_run or result.get("blocking_reasons"):
        return result
    # This function is dispatched only by controlled_execute after the Owner-only gate check.
    # The decision is the durable authorization receipt, including exact repository/ref/SHA.
    written = backend(root, base, actor=actor, dry_run=False)
    result.update(written)
    if not result.get("wrote"):
        return result
    if plan["action"] == "create":
        created = subprocess.run(["git", "-C", plan["repo"], "branch", "--", plan["branch"], plan["start_commit"]],
                                 capture_output=True, text=True)
        if created.returncode:
            result.update({"verdict": Verdict.BLOCK, "blocking_reasons": ["BRANCH_INIT_CREATE_FAILED: " + created.stderr.strip()],
                           "branch_created": False})
            return result
    actual = subprocess.run(["git", "-C", plan["repo"], "show-ref", "--verify", "--hash", "refs/heads/" + plan["branch"]],
                            capture_output=True, text=True)
    if actual.returncode or actual.stdout.strip() != plan["start_commit"]:
        result.update({"verdict": Verdict.BLOCK, "blocking_reasons": ["BRANCH_INIT_VERIFY_FAILED"], "branch_created": False})
    else:
        result["branch_created"] = plan["action"] == "create"
    return result


def run_branch_init_cli(args: Any) -> int:
    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args
    from tools.aipos_cli.board_adapter import record_owner_decision
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.envelope_mint import mint_via_gate

    root = Path(_find_repo_root_for_args(args))
    block = {"repo": args.repo, "branch": args.branch, "start_commit": args.start_commit, "requested_at": iso_z()}
    try:
        plan = branch_plan(root, block)
    except (ValueError, OSError, SchemaLoadError) as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}))
        return 1
    if plan["action"] == "unchanged":
        print(json.dumps({"ok": True, "dry_run": not args.confirm, "branch_plan": plan, "writes": []}))
        return 0
    payload = {"decision_id": "branch-init-" + hashlib.sha256(json.dumps(block, sort_keys=True).encode()).hexdigest()[:24],
               "branch_initialization": block, "actor": args.actor}
    if args.confirm:
        report = mint_via_gate([payload], governance_root=root, actor=args.actor, connection_json=args.connection_json,
                               token_role=args.token_role)
        print(json.dumps(report, ensure_ascii=False, default=str))
        return int(report["exit_code"])
    preview = record_owner_decision(payload, dry_run=True, repo_root=root, actor=args.actor)
    print(json.dumps(preview, ensure_ascii=False, default=str))
    return 1 if preview.get("blocking_reasons") or not preview.get("ok") else 0
