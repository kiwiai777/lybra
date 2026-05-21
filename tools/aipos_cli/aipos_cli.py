from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

if __package__ in (None, ""):
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.aipos_cli.renderer import (
    render_agents_text,
    render_draft_list_text,
    render_draft_result_text,
    render_json,
    render_my_tasks_text,
    render_needs_owner_text,
    render_preview_text,
    render_queue_text,
    render_queue_mutation_text,
    render_records_text,
    render_task_detail_text,
    render_validate_text,
)
from tools.aipos_cli.agent_profiles import actor_matches_task, availability_for_actor, load_agent_profiles
from tools.aipos_cli.context_pack_builder import build_context_pack_preview
from tools.aipos_cli.adapter_response import blocked_response, derive_verdict, make_response
from tools.aipos_cli.board_adapter import execute_dry_run as execute_controlled_dry_run
from tools.aipos_cli.board_adapter import submit_external_intake
from tools.aipos_cli.controlled_execute import snapshot_hash, validate_owner_confirmation
from tools.aipos_cli.draft_validator import list_drafts, validate_draft_file
from tools.aipos_cli.draft_writer import (
    build_template_payload,
    create_draft,
    load_body_file,
    load_create_payload_from_json,
    publish_draft,
)
from tools.aipos_cli.external_intake_writer import build_external_intake_draft, load_intake_payload_from_json
from tools.aipos_cli.orchestration_event_writer import append_orchestration_event, load_event_payload_from_json
from tools.aipos_cli.orchestration_summary_preview import build_orchestration_summary_preview
from tools.aipos_cli.planner_loop_mvp import build_planner_loop_mvp_preview
from tools.aipos_cli.planner_iteration_writer import append_planner_iteration, load_iteration_payload_from_json
from tools.aipos_cli.preview import build_preview
from tools.aipos_cli.queue_mutation import mutate_queue_task
from tools.aipos_cli.records import load_records
from tools.aipos_cli.task_loader import find_repo_root, load_all_tasks, load_task_by_path
from tools.aipos_cli.validator import (
    build_records_diagnostics,
    build_records_summary,
    validate_single_task,
    validate_tasks,
)


def _filter_my_tasks(report: dict[str, Any], actor: str, profiles: dict[str, Any]) -> dict[str, Any]:
    filtered = [
        task
        for task in report["tasks"]
        if actor_matches_task(task, actor, profiles)
    ]
    availability = availability_for_actor(actor, profiles)
    return {**report, "scope": "my_tasks", "actor": actor, "tasks": filtered, **availability}


def _filter_needs_owner(report: dict[str, Any]) -> dict[str, Any]:
    filtered = [
        task
        for task in report["tasks"]
        if task["verdict"] == "NEEDS_OWNER"
        or task["metadata"].get("needs_owner") is True
        or task["metadata"].get("owner_review_required") is True
        or task["metadata"].get("approval_required") is True
        or bool(task["needs_owner_reasons"])
    ]
    return {**report, "scope": "needs_owner", "tasks": filtered}


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "path": task.get("path"),
        "queue_state": task.get("queue_state"),
        "status": task.get("status"),
        "source_tag": task.get("metadata", {}).get("source_tag"),
        "client_tag": task.get("metadata", {}).get("client_tag"),
        "external_ref": task.get("metadata", {}).get("external_ref"),
        "verdict": task.get("verdict"),
        "blocking_reasons": task.get("blocking_reasons", []),
        "warnings": task.get("warnings", []),
        "needs_owner_reasons": task.get("needs_owner_reasons", []),
        "recommended_action": task.get("recommended_action"),
        "record_ref_checks": [
            {
                "field": item.get("reference"),
                "record_type": item.get("record_type"),
                "record_id": item.get("record_id"),
                "status": item.get("status"),
                "severity": item.get("level"),
                "message": item.get("message"),
            }
            for item in task.get("record_ref_checks", [])
        ],
        "records": task.get(
            "records",
            {
                "session_records": len(task.get("record_links", {}).get("sessions", [])),
                "claim_logs": len(task.get("record_links", {}).get("claims", [])),
                "has_record_issues": False,
            },
        ),
    }


def build_validate_json_report(report: dict[str, Any], records: dict[str, Any] | None = None) -> dict[str, Any]:
    output = {"scope": report.get("scope"), "tasks": [_task_summary(task) for task in report["tasks"]]}
    if "summary" in report:
        output["summary"] = report["summary"]
    if "actor" in report:
        output["actor"] = report["actor"]
    if report.get("scope") == "queue":
        output["records_summary"] = report.get("records_summary") or build_records_summary(
            records or {}, report["tasks"]
        )
        output["records_diagnostics"] = report.get("records_diagnostics") or build_records_diagnostics(
            records or {}, report["tasks"]
        )
    return output


def _json_report(report: dict[str, Any], records: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_validate_json_report(report, records=records)


def _records_json(records: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": "records",
        "summary": records["summary"],
        "sessions": records["sessions"],
        "claims": records["claims"],
        "warnings": records.get("warnings", []),
        "parse_errors": records.get("parse_errors", []),
    }


def _agents_json(profiles: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": "agents",
        "summary": profiles["summary"],
        "profiles": profiles["profiles"],
    }


def _task_lookup_arguments(subparser: argparse.ArgumentParser) -> None:
    group = subparser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task-id", help="Task ID to locate across queue directories")
    group.add_argument("--path", help="Task path relative to repo root")


def _queue_mutation_arguments(subparser: argparse.ArgumentParser) -> None:
    _task_lookup_arguments(subparser)
    subparser.add_argument("--actor", required=True, help="Actor performing the mutation")
    subparser.add_argument("--with-records", action="store_true", help="Opt in to records writing under 5_tasks/records/")
    subparser.add_argument("--dry-run", action="store_true", help="Validate and preview without writing")
    subparser.add_argument("--json", action="store_true", help="Output JSON")


def _load_json_object(path: str) -> dict[str, Any]:
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON input must be an object")
    return data


def _is_expired_iso(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return True
    try:
        expires_at = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > expires_at


def _execute_intake_from_dry_run_envelope(
    repo_root: Any,
    envelope: dict[str, Any],
    actor: str,
    *,
    owner_confirmation_token: str | None = None,
) -> dict[str, Any]:
    operation = "controlled_execute_confirm"
    if envelope.get("operation") != "intake_submit":
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="UNSUPPORTED_OPERATION",
            message="controlled-execute confirm --from-json supports only intake_submit in AIPOS-108",
            actor={"actor": actor},
            safety_notice="AIPOS-108 local CLI controlled execute proof validation.",
        )
    if (envelope.get("actor") or {}).get("actor") != actor:
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="ACTOR_MISMATCH",
            message="confirm actor does not match dry-run actor",
            actor={"actor": actor},
            safety_notice="AIPOS-108 local CLI controlled execute proof validation.",
        )
    if _is_expired_iso(envelope.get("dry_run_expires_at")):
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="REVALIDATION_FAILED",
            message="dry-run proof expired; run dry-run again",
            actor={"actor": actor},
            safety_notice="AIPOS-108 local CLI controlled execute proof validation.",
        )

    source_data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    payload = source_data.get("original_payload")
    if not isinstance(payload, dict):
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="BACKEND_CONTRACT_MISMATCH",
            message="dry-run envelope is missing data.original_payload",
            actor={"actor": actor},
            safety_notice="AIPOS-108 local CLI controlled execute proof validation.",
        )

    current = submit_external_intake(payload, dry_run=True, repo_root=repo_root, actor=actor)
    current_hash = snapshot_hash("intake_submit", actor, current)
    expected_hash = str(envelope.get("dry_run_snapshot_hash") or "")
    if not expected_hash or current_hash != expected_hash:
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="REVALIDATION_FAILED",
            message="dry-run snapshot mismatch; run dry-run again",
            actor={"actor": actor},
            data={
                "expected_dry_run_snapshot_hash": expected_hash,
                "current_snapshot_hash": current_hash,
                "recommended_action": "run dry-run again",
            },
            safety_notice="AIPOS-108 local CLI controlled execute proof validation.",
        )

    ok_owner, owner_error = validate_owner_confirmation(
        required=bool(envelope.get("owner_confirmation_required", False)),
        owner_confirmation_token=owner_confirmation_token,
    )
    if not ok_owner:
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="OWNER_CONFIRMATION_REQUIRED",
            message=owner_error or "owner confirmation required",
            actor={"actor": actor},
            owner_confirmation_required=True,
            owner_confirmation_reasons=list(envelope.get("owner_confirmation_reasons", [])),
            safety_notice="AIPOS-108 local CLI controlled execute proof validation.",
        )

    result = build_external_intake_draft(repo_root, payload, actor=actor, dry_run=False)
    verdict = derive_verdict(
        blocking_reasons=list(result.get("blocking_reasons", [])),
        warnings=list(result.get("warnings", [])),
    )
    return make_response(
        ok=bool(result.get("wrote", False)),
        verdict=verdict,
        operation="intake_submit",
        dry_run=False,
        actor={"actor": actor},
        data=result,
        summary={
            "safe_id": result.get("safe_id"),
            "task_id": result.get("task_id"),
            "target_path": result.get("target_path"),
            "wrote": result.get("wrote", False),
        },
        planned_writes=list(result.get("planned_writes", [])),
        performed_writes=list(result.get("planned_writes", [])) if result.get("wrote") else [],
        warnings=list(result.get("warnings", [])),
        blocking_reasons=list(result.get("blocking_reasons", [])),
        safety_notice="AIPOS-108 local CLI controlled execute proof validation.",
        errors=[],
    )


def _resolve_task_selection(args: argparse.Namespace, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if args.task_id:
        matches = [task for task in tasks if task.get("task_id") == args.task_id]
        if not matches:
            raise ValueError(f"No task found for task_id: {args.task_id}")
        if len(matches) > 1:
            paths = ", ".join(task["path"] for task in matches)
            raise ValueError(f"Duplicate task_id {args.task_id} found in: {paths}")
        return matches[0]
    if args.path:
        return load_task_by_path(args.path)
    raise ValueError("Exactly one of --task-id or --path must be provided")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Project OS CLI")
    subparsers = parser.add_subparsers(dest="command")

    draft_parser = subparsers.add_parser("draft", help="Safe task draft writer")
    draft_subparsers = draft_parser.add_subparsers(dest="draft_command")

    draft_create_parser = draft_subparsers.add_parser("create", help="Create a draft task card")
    create_source_group = draft_create_parser.add_mutually_exclusive_group(required=True)
    create_source_group.add_argument("--from-json", help="Read draft payload from JSON file")
    create_source_group.add_argument("--from-template", help="Create from a built-in template")
    draft_create_parser.add_argument("--task-id", help="Draft task_id")
    draft_create_parser.add_argument("--title", help="Draft title")
    draft_create_parser.add_argument("--project", help="Project", default="ai-project-os")
    draft_create_parser.add_argument("--assigned-to", help="assigned_to value")
    draft_create_parser.add_argument("--agent-instance", help="agent_instance value")
    draft_create_parser.add_argument("--context-bundle", help="context_bundle value")
    draft_create_parser.add_argument("--task-mode", help="task_mode value")
    draft_create_parser.add_argument("--model-tier", help="model_tier value")
    draft_create_parser.add_argument("--priority", help="priority value")
    draft_create_parser.add_argument("--created-by", help="created_by value")
    draft_create_parser.add_argument("--output-target", help="output_target value")
    draft_create_parser.add_argument("--artifact-policy", help="artifact_policy value")
    draft_create_parser.add_argument("--body-file", help="Optional body markdown file")
    draft_create_parser.add_argument("--dry-run", action="store_true", help="Render and validate without writing")
    draft_create_parser.add_argument("--json", action="store_true", help="Output JSON")

    draft_validate_parser = draft_subparsers.add_parser("validate", help="Validate a draft task card")
    draft_validate_parser.add_argument("--path", required=True, help="Draft path under 5_tasks/drafts/")
    draft_validate_parser.add_argument("--json", action="store_true", help="Output JSON")

    draft_list_parser = draft_subparsers.add_parser("list", help="List draft task cards")
    draft_list_parser.add_argument("--json", action="store_true", help="Output JSON")

    draft_publish_parser = draft_subparsers.add_parser("publish", help="Publish a validated draft to pending")
    draft_publish_parser.add_argument("--path", required=True, help="Draft path under 5_tasks/drafts/")
    draft_publish_parser.add_argument("--dry-run", action="store_true", help="Validate and render without writing")
    draft_publish_parser.add_argument("--json", action="store_true", help="Output JSON")

    queue_parser = subparsers.add_parser("queue", help="Render task queue")
    queue_subparsers = queue_parser.add_subparsers(dest="queue_command")
    queue_parser.add_argument("--json", action="store_true", help="Output JSON")

    queue_claim_parser = queue_subparsers.add_parser("claim", help="Move a task from pending to claimed")
    _queue_mutation_arguments(queue_claim_parser)

    queue_block_parser = queue_subparsers.add_parser("block", help="Move a task from claimed to blocked")
    _queue_mutation_arguments(queue_block_parser)
    queue_block_parser.add_argument("--reason", required=True, help="Blocking reason")

    queue_complete_parser = queue_subparsers.add_parser("complete", help="Move a task from claimed to completed")
    _queue_mutation_arguments(queue_complete_parser)
    queue_complete_parser.add_argument("--report-link", required=True, help="Completion report link")

    queue_reopen_parser = queue_subparsers.add_parser("reopen", help="Move a task from blocked to pending")
    _queue_mutation_arguments(queue_reopen_parser)
    queue_reopen_parser.add_argument("--reason", required=True, help="Reopen reason")

    my_tasks_parser = subparsers.add_parser("my-tasks", help="Render tasks for an actor")
    my_tasks_parser.add_argument("--actor", required=True, help="Role instance or agent instance")
    my_tasks_parser.add_argument("--json", action="store_true", help="Output JSON")

    needs_owner_parser = subparsers.add_parser("needs-owner", help="Render owner review tasks")
    needs_owner_parser.add_argument("--json", action="store_true", help="Output JSON")

    validate_parser = subparsers.add_parser("validate", help="Run validator")
    validate_parser.add_argument("--json", action="store_true", help="Output JSON")

    controlled_parser = subparsers.add_parser("controlled-execute", help="Local controlled execute dry-run/confirm")
    controlled_subparsers = controlled_parser.add_subparsers(dest="controlled_command")
    controlled_dry_run_parser = controlled_subparsers.add_parser("dry-run", help="Build a controlled execute dry-run proof")
    controlled_dry_run_parser.add_argument("--operation", required=True, choices=["intake_submit"], help="Controlled execute operation")
    controlled_dry_run_parser.add_argument("--actor", required=True, help="Actor requesting the dry-run")
    controlled_dry_run_parser.add_argument("--from-json", required=True, help="Read normalized operation payload from JSON")
    controlled_dry_run_parser.add_argument("--json", action="store_true", help="Output JSON")
    controlled_confirm_parser = controlled_subparsers.add_parser("confirm", help="Confirm a controlled execute dry-run proof")
    confirm_source = controlled_confirm_parser.add_mutually_exclusive_group(required=True)
    confirm_source.add_argument("--dry-run-id", help="In-process dry-run id, for module-level integrations")
    confirm_source.add_argument("--from-json", help="Read prior dry-run JSON envelope as stateless CLI proof")
    controlled_confirm_parser.add_argument("--actor", required=True, help="Actor confirming the dry-run")
    controlled_confirm_parser.add_argument("--owner-confirmation-token", help="Owner confirmation token if required")
    controlled_confirm_parser.add_argument("--json", action="store_true", help="Output JSON")

    records_parser = subparsers.add_parser("records", help="Render records summary")
    records_parser.add_argument("--json", action="store_true", help="Output JSON")

    agents_parser = subparsers.add_parser("agents", help="Render agent profiles")
    agents_parser.add_argument("--json", action="store_true", help="Output JSON")

    context_pack_parser = subparsers.add_parser("context-pack", help="Read-only context pack preview")
    context_pack_subparsers = context_pack_parser.add_subparsers(dest="context_pack_command")
    context_pack_preview_parser = context_pack_subparsers.add_parser("preview", help="Build a read-only context pack preview")
    context_pack_source = context_pack_preview_parser.add_mutually_exclusive_group(required=True)
    context_pack_source.add_argument("--task-id", help="Task ID to build context from")
    context_pack_source.add_argument("--path", help="Task path relative to repo root")
    context_pack_source.add_argument("--orchestration-id", help="Orchestration id to build context from")
    context_pack_preview_parser.add_argument("--json", action="store_true", help="Output JSON")

    orchestration_parser = subparsers.add_parser("orchestration", help="Orchestration append-only writers")
    orchestration_subparsers = orchestration_parser.add_subparsers(dest="orchestration_command")
    event_parser = orchestration_subparsers.add_parser("event", help="Orchestration event log operations")
    event_subparsers = event_parser.add_subparsers(dest="event_command")
    event_append_parser = event_subparsers.add_parser("append", help="Append one orchestration event")
    event_append_parser.add_argument("--from-json", required=True, help="Read event payload from JSON file")
    event_append_parser.add_argument("--actor", required=True, help="Actor requesting the append; must match payload actor")
    event_append_parser.add_argument("--dry-run", action="store_true", help="Validate and preview without writing")
    event_append_parser.add_argument("--expected-hash", help="Required snapshot hash for non-dry-run writes")
    event_append_parser.add_argument("--json", action="store_true", help="Output JSON")
    iteration_parser = orchestration_subparsers.add_parser("iteration", help="Planner iteration log operations")
    iteration_subparsers = iteration_parser.add_subparsers(dest="iteration_command")
    iteration_append_parser = iteration_subparsers.add_parser("append", help="Append one planner iteration")
    iteration_append_parser.add_argument("--from-json", required=True, help="Read planner iteration payload from JSON file")
    iteration_append_parser.add_argument(
        "--actor", required=True, help="Actor requesting the append; must match planner_agent or planner_agent_instance"
    )
    iteration_append_parser.add_argument("--dry-run", action="store_true", help="Validate and preview without writing")
    iteration_append_parser.add_argument("--expected-hash", help="Required snapshot hash for non-dry-run writes")
    iteration_append_parser.add_argument("--json", action="store_true", help="Output JSON")
    summary_parser = orchestration_subparsers.add_parser("summary", help="Orchestration summary preview operations")
    summary_subparsers = summary_parser.add_subparsers(dest="summary_command")
    summary_preview_parser = summary_subparsers.add_parser("preview", help="Preview reconstructable summary state")
    summary_preview_parser.add_argument("--orchestration-id", required=True, help="Orchestration id to summarize")
    summary_preview_parser.add_argument("--json", action="store_true", help="Output JSON")
    loop_parser = orchestration_subparsers.add_parser("loop", help="Semi-automated planner loop MVP operations")
    loop_subparsers = loop_parser.add_subparsers(dest="loop_command")
    loop_preview_parser = loop_subparsers.add_parser("preview", help="Preview one safe planner loop coordinator step")
    loop_preview_parser.add_argument("--orchestration-id", required=True, help="Orchestration id to coordinate")
    loop_preview_parser.add_argument("--actor", help="Actor requesting the preview")
    loop_preview_parser.add_argument("--json", action="store_true", help="Output JSON")

    task_parser = subparsers.add_parser("task", help="Render task detail")
    _task_lookup_arguments(task_parser)
    task_parser.add_argument("--json", action="store_true", help="Output JSON")

    preview_parser = subparsers.add_parser("preview", help="Render start task session preview")
    _task_lookup_arguments(preview_parser)
    preview_parser.add_argument("--actor", required=True, help="Current actor")
    preview_parser.add_argument("--json", action="store_true", help="Output JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    if args.command == "draft":
        try:
            repo_root = find_repo_root()
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        if not args.draft_command:
            parser.print_help()
            return 2

        if args.draft_command == "create":
            try:
                if args.from_json:
                    metadata, body = load_create_payload_from_json(args.from_json)
                else:
                    body = load_body_file(args.body_file) if args.body_file else None
                    metadata, body = build_template_payload(
                        args.from_template,
                        {
                            "task_id": args.task_id,
                            "title": args.title,
                            "project": args.project,
                            "assigned_to": args.assigned_to,
                            "agent_instance": args.agent_instance,
                            "context_bundle": args.context_bundle,
                            "task_mode": args.task_mode,
                            "model_tier": args.model_tier,
                            "priority": args.priority,
                            "created_by": args.created_by,
                            "output_target": args.output_target,
                            "artifact_policy": args.artifact_policy,
                        },
                        body=body,
                    )
                result = create_draft(repo_root, metadata, body, dry_run=args.dry_run)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1

            if args.json:
                print(render_json(result))
            else:
                print(render_draft_result_text(result))
            return 1 if result.get("verdict") == "BLOCK" else 0

        if args.draft_command == "validate":
            try:
                result = validate_draft_file(repo_root, args.path)
            except (OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_draft_result_text(result))
            return 1 if result.get("verdict") == "BLOCK" else 0

        if args.draft_command == "list":
            result = list_drafts(repo_root)
            if args.json:
                print(render_json(result))
            else:
                print(render_draft_list_text(result))
            return 0

        if args.draft_command == "publish":
            try:
                result = publish_draft(repo_root, args.path, dry_run=args.dry_run)
            except (OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_draft_result_text(result))
            return 1 if result.get("verdict") == "BLOCK" else 0

        print(f"Unknown draft command: {args.draft_command}", file=sys.stderr)
        return 2

    try:
        repo_root = find_repo_root()
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.command == "controlled-execute":
        if not getattr(args, "controlled_command", None):
            parser.print_help()
            return 2
        try:
            if args.controlled_command == "dry-run":
                payload = load_intake_payload_from_json(args.from_json)
                result = submit_external_intake(payload, dry_run=True, repo_root=repo_root, actor=args.actor)
            elif args.controlled_command == "confirm":
                if getattr(args, "from_json", None):
                    envelope = _load_json_object(args.from_json)
                    result = _execute_intake_from_dry_run_envelope(
                        repo_root,
                        envelope,
                        args.actor,
                        owner_confirmation_token=args.owner_confirmation_token,
                    )
                else:
                    result = execute_controlled_dry_run(
                        args.dry_run_id,
                        args.actor,
                        owner_confirmation_token=args.owner_confirmation_token,
                        repo_root=repo_root,
                    )
            else:
                parser.print_help()
                return 2
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(render_json(result))
        return 0

    if args.command == "queue" and getattr(args, "queue_command", None) in {"claim", "block", "complete", "reopen"}:
        profiles = load_agent_profiles(repo_root)
        try:
            result = mutate_queue_task(
                repo_root,
                args.queue_command,
                task_id=getattr(args, "task_id", None),
                task_path=getattr(args, "path", None),
                actor=args.actor,
                reason=getattr(args, "reason", None),
                report_link=getattr(args, "report_link", None),
                dry_run=args.dry_run,
                profiles=profiles,
                with_records=args.with_records,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(render_queue_mutation_text(result))
        return 1 if result.get("verdict") == "BLOCK" else 0

    if args.command == "orchestration":
        if getattr(args, "orchestration_command", None) == "event" and getattr(args, "event_command", None) == "append":
            try:
                payload = load_event_payload_from_json(args.from_json)
                result = append_orchestration_event(
                    repo_root,
                    payload,
                    actor=args.actor,
                    dry_run=args.dry_run,
                    expected_hash=args.expected_hash,
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_json(result))
            return 1 if result.get("verdict") == "BLOCK" else 0
        if (
            getattr(args, "orchestration_command", None) == "iteration"
            and getattr(args, "iteration_command", None) == "append"
        ):
            try:
                payload = load_iteration_payload_from_json(args.from_json)
                result = append_planner_iteration(
                    repo_root,
                    payload,
                    actor=args.actor,
                    dry_run=args.dry_run,
                    expected_hash=args.expected_hash,
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_json(result))
            return 1 if result.get("verdict") == "BLOCK" else 0
        if (
            getattr(args, "orchestration_command", None) == "summary"
            and getattr(args, "summary_command", None) == "preview"
        ):
            try:
                tasks = load_all_tasks(repo_root)
                records = load_records(repo_root)
                result = build_orchestration_summary_preview(
                    repo_root,
                    args.orchestration_id,
                    tasks=tasks,
                    records=records,
                )
            except (OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_json(result))
            return 1 if result.get("verdict") == "BLOCK" else 0
        if (
            getattr(args, "orchestration_command", None) == "loop"
            and getattr(args, "loop_command", None) == "preview"
        ):
            try:
                result = build_planner_loop_mvp_preview(repo_root, args.orchestration_id, actor=getattr(args, "actor", None))
            except (OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_json(result))
            return 1 if result.get("verdict") == "BLOCK" else 0
        else:
            parser.print_help()
            return 2

    if args.command == "context-pack":
        if getattr(args, "context_pack_command", None) != "preview":
            parser.print_help()
            return 2
        try:
            result = build_context_pack_preview(
                repo_root,
                task_id=getattr(args, "task_id", None),
                path=getattr(args, "path", None),
                orchestration_id=getattr(args, "orchestration_id", None),
            )
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == "BLOCK" else 0

    try:
        tasks = load_all_tasks(repo_root)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    records = load_records(repo_root)
    profiles = load_agent_profiles(repo_root)
    actor = getattr(args, "actor", None)
    report = validate_tasks(tasks, current_actor=actor, records=records, profiles=profiles)

    if args.command == "queue":
        if args.json:
            print(render_json(_json_report(report, records=records)))
        else:
            print(render_queue_text(report))
        return 0

    if args.command == "my-tasks":
        actor_report = _filter_my_tasks(report, args.actor, profiles)
        if args.json:
            print(render_json(_json_report(actor_report, records=records)))
        else:
            print(render_my_tasks_text(actor_report, args.actor))
        return 0

    if args.command == "needs-owner":
        owner_report = _filter_needs_owner(report)
        if args.json:
            print(render_json(_json_report(owner_report, records=records)))
        else:
            print(render_needs_owner_text(owner_report))
        return 0

    if args.command == "validate":
        if args.json:
            print(render_json(build_validate_json_report(report, records=records)))
        else:
            print(render_validate_text(report))
        return 0

    if args.command == "records":
        if args.json:
            print(render_json(_records_json(records)))
        else:
            print(render_records_text(records))
        return 0

    if args.command == "agents":
        if args.json:
            print(render_json(_agents_json(profiles)))
        else:
            print(render_agents_text(profiles))
        return 0

    if args.command == "task":
        try:
            selected = _resolve_task_selection(args, tasks)
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        validated = validate_single_task(selected, tasks=tasks, records=records, profiles=profiles)
        if args.json:
            print(render_json(validated))
        else:
            print(render_task_detail_text(validated))
        return 0

    if args.command == "preview":
        try:
            selected = _resolve_task_selection(args, tasks)
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        validated = validate_single_task(
            selected, tasks=tasks, current_actor=args.actor, records=records, profiles=profiles
        )
        preview = build_preview(validated, actor=args.actor, records=records, profiles=profiles)
        if args.json:
            print(render_json(preview))
        else:
            print(render_preview_text(preview))
        return 0

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
