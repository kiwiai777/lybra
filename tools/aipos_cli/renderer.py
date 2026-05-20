from __future__ import annotations

import json
from typing import Any


def _task_line(task: dict[str, Any]) -> str:
    assigned = task.get("assigned_to") or "-"
    agent = task.get("agent_instance") or "-"
    mode = task.get("task_mode") or "-"
    tier = task.get("model_tier") or "-"
    task_id = task.get("task_id") or "<missing-task-id>"
    title = task.get("title") or "<missing-title>"
    return (
        f"{task_id} | {task.get('queue_state')}/{task.get('status') or '-'} | {task.get('verdict')} | "
        f"{assigned} | {agent} | {mode} | {tier} | {title} | {task.get('path')}"
    )


def render_queue_text(report: dict[str, Any]) -> str:
    lines = ["Task Queue", "task_id | queue/status | verdict | assigned_to | agent_instance | task_mode | model_tier | title | path"]
    if not report["tasks"]:
        lines.append("(no task files found)")
        return "\n".join(lines)
    for task in report["tasks"]:
        lines.append(_task_line(task))
    return "\n".join(lines)


def render_queue_mutation_text(result: dict[str, Any]) -> str:
    lines = [
        "Queue Mutation",
        f"Action: {result.get('action') or '-'}",
        f"Task ID: {result.get('task_id') or '-'}",
        f"Actor: {result.get('actor') or '-'}",
        f"Source: {result.get('source_path') or '-'}",
        f"Target: {result.get('target_path') or '-'}",
        f"From State: {result.get('from_state') or '-'}",
        f"To State: {result.get('to_state') or '-'}",
        f"Dry-run: {result.get('dry_run')}",
        f"Would Write: {result.get('would_write')}" if "would_write" in result else None,
        f"Wrote: {result.get('wrote')}" if "wrote" in result else None,
        f"Would Move: {result.get('would_move')}" if "would_move" in result else None,
        f"Moved: {result.get('moved')}" if "moved" in result else None,
        f"Records Enabled: {result.get('records_enabled')}" if "records_enabled" in result else None,
        f"Proposed Claim ID: {result.get('proposed_claim_id') or '-'}" if result.get("with_records") else None,
        f"Proposed Session ID: {result.get('proposed_session_id') or '-'}" if result.get("with_records") else None,
        f"Claim Log Path: {result.get('claim_log_path') or '-'}" if result.get("with_records") else None,
        f"Session Record Path: {result.get('session_record_path') or '-'}" if result.get("with_records") else None,
    ]
    lines = [line for line in lines if line is not None]
    if result.get("blocking_reasons"):
        lines.append("Blocking Reasons:")
        lines.extend(f"- {reason}" for reason in result["blocking_reasons"])
    if result.get("warnings"):
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in result["warnings"])
    if result.get("record_writes"):
        lines.append("Record Writes:")
        lines.extend(f"- {item.get('path')} ({item.get('record_type')})" for item in result["record_writes"])
    if result.get("record_updates"):
        lines.append("Record Updates:")
        lines.extend(f"- {item.get('path')} ({item.get('record_type')})" for item in result["record_updates"])
    if result.get("record_warnings"):
        lines.append("Record Warnings:")
        lines.extend(f"- {warning}" for warning in result["record_warnings"])
    lines.append(f"Safety Notice: {result.get('safety_notice') or '-'}")
    return "\n".join(lines)


def render_my_tasks_text(report: dict[str, Any], actor: str) -> str:
    lines = [f"My Tasks: {actor}"]
    if report.get("availability_status"):
        lines.append(f"availability: {report.get('availability_status')}")
    if report.get("availability_warning"):
        lines.append(f"warning: {report.get('availability_warning')}")
    if not report["tasks"]:
        lines.append("(no matching tasks)")
        return "\n".join(lines)
    for task in report["tasks"]:
        lines.append(_task_line(task))
        match_reason = task.get("actor_match", {}).get("reason")
        if match_reason and match_reason != "not_checked":
            lines.append(f"  actor_match: {match_reason}")
        reasons = task["blocking_reasons"] or task["warnings"] or task["needs_owner_reasons"]
        if reasons:
            lines.append(f"  notes: {', '.join(reasons[:3])}")
    return "\n".join(lines)


def render_needs_owner_text(report: dict[str, Any]) -> str:
    lines = ["Needs Owner"]
    if not report["tasks"]:
        lines.append("(no tasks currently require owner review)")
        return "\n".join(lines)
    for task in report["tasks"]:
        lines.append(_task_line(task))
        reasons = task["needs_owner_reasons"] or task["blocking_reasons"] or ["owner review requested"]
        lines.append(f"  reasons: {', '.join(reasons[:3])}")
    return "\n".join(lines)


def render_validate_text(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "Validation Summary",
        (
            f"total_tasks={summary['total_tasks']} "
            f"pass={summary['pass']} warn={summary['warn']} "
            f"block={summary['block']} needs_owner={summary['needs_owner']}"
        ),
    ]
    if not report["tasks"]:
        lines.append("(no task files found)")
        return "\n".join(lines)
    for task in report["tasks"]:
        lines.append(_task_line(task))
    return "\n".join(lines)


def render_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def render_draft_result_text(result: dict[str, Any]) -> str:
    action = result.get("action") or "draft_action"
    if action == "draft_publish":
        lines = [
            "Draft Publish",
            f"Source: {result.get('source_path') or '-'}",
            f"Target: {result.get('target_path') or '-'}",
            f"Task ID: {result.get('task_id') or '-'}",
            f"Verdict: {result.get('verdict') or '-'}",
            f"Dry-run: {result.get('dry_run')}" if "dry_run" in result else None,
            (
                f"Would Write: {result.get('would_write')}"
                if "would_write" in result
                else f"Wrote: {result.get('wrote')}"
            ),
            f"Wrote: {result.get('wrote')}" if "wrote" in result else None,
        ]
        lines = [line for line in lines if line is not None]
        blocking_reasons = result.get("blocking_reasons", [])
        warnings = result.get("warnings", [])
        if blocking_reasons:
            lines.append("Blocking Reasons:")
            lines.extend(f"- {reason}" for reason in blocking_reasons)
        if warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in warnings)
        if result.get("planned_writes"):
            lines.append("Planned Writes:")
            lines.extend(f"- {item.get('path')} ({item.get('kind')})" for item in result["planned_writes"])
        lines.append(
            "Safety Notice: AIPOS-30 publish only writes a validated draft to 5_tasks/queue/pending/. "
            "It does not claim, complete, block, write records, or run agents."
        )
        return "\n".join(lines)

    lines = [
        f"Action: {action}",
        f"task_id: {result.get('task_id') or '-'}",
        f"target_path: {result.get('target_path') or result.get('path') or '-'}",
        f"verdict: {result.get('verdict') or '-'}",
        f"dry_run: {result.get('dry_run')}" if "dry_run" in result else None,
        f"would_write: {result.get('would_write')}" if "would_write" in result else None,
        f"wrote: {result.get('wrote')}" if "wrote" in result else None,
    ]
    lines = [line for line in lines if line is not None]
    blocking_reasons = result.get("blocking_reasons", [])
    warnings = result.get("warnings", [])
    if blocking_reasons:
        lines.append("blocking_reasons:")
        lines.extend(f"- {reason}" for reason in blocking_reasons)
    if warnings:
        lines.append("warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
    if result.get("planned_writes"):
        lines.append("planned_writes:")
        lines.extend(f"- {item.get('path')} ({item.get('kind')})" for item in result["planned_writes"])
    return "\n".join(lines)


def render_draft_list_text(result: dict[str, Any]) -> str:
    lines = ["Draft List", "task_id | status | verdict | assigned_to | project | title | path"]
    drafts = result.get("drafts", [])
    if not drafts:
        lines.append("(no draft files found)")
        return "\n".join(lines)
    for draft in drafts:
        lines.append(
            f"{draft.get('task_id') or '-'} | {draft.get('status') or '-'} | {draft.get('verdict') or '-'} | "
            f"{draft.get('assigned_to') or '-'} | {draft.get('project') or '-'} | "
            f"{draft.get('title') or '-'} | {draft.get('path') or '-'}"
        )
    return "\n".join(lines)


def render_agents_text(profiles: dict[str, Any]) -> str:
    lines = [
        "Agent Profiles",
        (
            f"profiles={profiles['summary']['profiles']} "
            f"enabled_profiles={profiles['summary']['enabled_profiles']} "
            f"instances={profiles['summary']['instances']} "
            f"source={profiles['summary']['source']} "
            f"warnings={profiles['summary'].get('warnings', 0)}"
        ),
    ]
    if not profiles.get("profiles"):
        lines.append("(no profiles loaded)")
        return "\n".join(lines)
    for profile in profiles["profiles"]:
        lines.append(
            f"{profile.get('agent_id') or '-'} | enabled={profile.get('enabled')} | "
            f"availability_status={profile.get('availability_status') or 'unknown'} | "
            f"default_instance={profile.get('default_instance') or '-'} | "
            f"default_runtime_profile={profile.get('default_runtime_profile') or '-'} | "
            f"source={profile.get('source') or '-'}"
        )
        lines.append(f"  aliases: {', '.join(profile.get('aliases', [])) or '-'}")
        for instance in profile.get("instances", []):
            lines.append(
                "  instance: "
                f"{instance.get('agent_instance') or '-'} | profile={instance.get('runtime_profile') or '-'} | "
                f"availability_status={instance.get('availability_status') or 'unknown'} | "
                f"entrypoint={instance.get('runtime_entrypoint') or '-'} | "
                f"command={instance.get('runtime_command') or '-'} | "
                f"args={instance.get('runtime_args', [])!r}"
            )
            if instance.get("runtime_env"):
                lines.append(f"    runtime_env: {instance.get('runtime_env')}")
            if instance.get("launch_notes"):
                lines.append(f"    launch_notes: {instance.get('launch_notes')}")
    return "\n".join(lines)


def _record_line(record: dict[str, Any], kind: str) -> str:
    record_id = record.get(f"{kind}_id") or record.get("record_id") or "-"
    timestamp = record.get("created_at") or record.get("claimed_at") or "-"
    extras: list[str] = []
    if kind == "session" and record.get("session_status"):
        extras.append(f"status={record.get('session_status')}")
    if kind == "claim" and record.get("claimed_by"):
        extras.append(f"claimed_by={record.get('claimed_by')}")
    suffix = f" [{' '.join(extras)}]" if extras else ""
    return f"- {record_id} | {timestamp} | {record.get('path') or '-'}{suffix}"


def render_records_text(records: dict[str, Any]) -> str:
    summary = records["summary"]
    lines = [
        "Records Summary",
        f"total session records: {summary['session_records']}",
        f"total claim logs: {summary['claim_logs']}",
        (
            "tasks with records: "
            f"sessions={summary['tasks_with_session_records']} claims={summary['tasks_with_claim_logs']}"
        ),
        f"records with parse errors: {summary['parse_errors']}",
        "Session Records",
    ]
    if records["sessions"]:
        for record in records["sessions"][:10]:
            lines.append(_record_line(record, "session"))
    else:
        lines.append("(none)")
    lines.append("Claim Logs")
    if records["claims"]:
        for record in records["claims"][:10]:
            lines.append(_record_line(record, "claim"))
    else:
        lines.append("(none)")
    lines.append("Warnings / Parse Errors")
    issues = records.get("warnings", []) + records.get("parse_errors", [])
    if issues:
        lines.extend(f"- {issue}" for issue in issues[:20])
    else:
        lines.append("(none)")
    return "\n".join(lines)


def _short_body_preview(body: str, max_lines: int = 30, max_chars: int = 4000) -> tuple[str, bool]:
    trimmed = body[:max_chars]
    lines = trimmed.splitlines()
    selected = lines[:max_lines]
    preview = "\n".join(selected)
    truncated = len(body) > len(trimmed) or len(lines) > max_lines
    return preview, truncated


def render_task_detail_text(task: dict[str, Any]) -> str:
    metadata = task["metadata"]
    body_preview, truncated = _short_body_preview(task.get("body", ""))
    lines = [
        "Task Detail",
        f"task_id: {task.get('task_id') or '-'}",
        f"title: {task.get('title') or '-'}",
        f"path: {task.get('path') or '-'}",
        f"queue_state: {task.get('queue_state') or '-'}",
        f"status: {task.get('status') or '-'}",
        f"status_consistent: {task.get('status_consistent')}",
        f"verdict: {task.get('verdict') or '-'}",
        f"assigned_to: {task.get('assigned_to') or '-'}",
        f"agent_instance: {task.get('agent_instance') or '-'}",
        f"project: {metadata.get('project') or '-'}",
        f"task_mode: {task.get('task_mode') or '-'}",
        f"model_tier: {task.get('model_tier') or '-'}",
        f"context_bundle: {metadata.get('context_bundle') or '-'}",
        f"priority: {metadata.get('priority') or '-'}",
        f"needs_owner: {metadata.get('needs_owner')}",
        f"output_target: {metadata.get('output_target') or '-'}",
        f"artifact_policy: {metadata.get('artifact_policy') or '-'}",
        f"session_policy: {metadata.get('session_policy') or '-'}",
        f"context_isolation: {metadata.get('context_isolation') or '-'}",
        f"artifact_scope: {metadata.get('artifact_scope') or '-'}",
        f"memory_scope: {metadata.get('memory_scope') or '-'}",
        f"source_tag: {metadata.get('source_tag') or '-'}",
        f"client_tag: {metadata.get('client_tag') or '-'}",
        f"external_ref: {metadata.get('external_ref') or '-'}",
        f"claim_id: {metadata.get('claim_id') or '-'}",
        f"active_session_id: {metadata.get('active_session_id') or '-'}",
        f"last_session_id: {metadata.get('last_session_id') or '-'}",
        f"blocking_reasons: {', '.join(task.get('blocking_reasons', [])) or '-'}",
        f"warnings: {', '.join(task.get('warnings', [])) or '-'}",
        f"needs_owner_reasons: {', '.join(task.get('needs_owner_reasons', [])) or '-'}",
        "Records:",
        f"session_records: {len(task.get('record_links', {}).get('sessions', []))}",
        f"claim_logs: {len(task.get('record_links', {}).get('claims', []))}",
        "record_ref_checks:",
        "body_preview:",
        body_preview or "(empty body)",
    ]
    checks = task.get("record_ref_checks", [])
    if checks:
        insert_at = lines.index("body_preview:")
        detail_lines = [
            f"- {check.get('reference')}: {check.get('status')} ({check.get('level')})"
            f" | {check.get('record_id') or '-'} | {check.get('message')}"
            for check in checks
        ]
        lines[insert_at:insert_at] = detail_lines
    else:
        lines.insert(lines.index("body_preview:"), "(none)")
    if task.get("record_links", {}).get("sessions"):
        insert_at = lines.index("body_preview:")
        lines[insert_at:insert_at] = ["linked_session_records:"] + [
            _record_line(record, "session") for record in task["record_links"]["sessions"][:5]
        ]
    if task.get("record_links", {}).get("claims"):
        insert_at = lines.index("body_preview:")
        lines[insert_at:insert_at] = ["linked_claim_logs:"] + [
            _record_line(record, "claim") for record in task["record_links"]["claims"][:5]
        ]
    if truncated:
        lines.append("[body truncated]")
    return "\n".join(lines)


def render_preview_text(preview: dict[str, Any]) -> str:
    verdict_banner = {
        "PASS": "READY FOR SESSION START",
        "WARN": "WARNING ACKNOWLEDGEMENT REQUIRED",
        "BLOCK": "NOT EXECUTABLE",
        "NEEDS_OWNER": "OWNER REVIEW REQUIRED",
    }.get(preview["verdict"], preview["verdict"])
    lines = [
        f"Preview Verdict: {verdict_banner}",
        f"Task: {preview.get('task_id') or '-'} | {preview.get('title') or '-'}",
        f"Actor: {preview.get('current_actor') or '-'}",
        f"Queue / Status: {preview.get('queue_state') or '-'} / {preview.get('frontmatter_status') or '-'}",
        "Execution Envelope:",
        f"  assigned_to: {preview.get('assigned_to') or '-'}",
        f"  assigned_to_canonical: {preview.get('task_assigned_to_canonical') or '-'}",
        f"  agent_instance: {preview.get('agent_instance') or '-'}",
        f"  project: {preview.get('project') or '-'}",
        f"  task_mode: {preview.get('task_mode') or '-'}",
        f"  model_tier: {preview.get('model_tier') or '-'}",
        f"  context_bundle: {preview.get('context_bundle') or '-'}",
        f"  output_target: {preview.get('output_target') or '-'}",
        f"  artifact_policy: {preview.get('artifact_policy') or '-'}",
        f"  session_policy: {preview.get('session_policy') or '-'}",
        f"  context_isolation: {preview.get('context_isolation') or '-'}",
        f"  artifact_scope: {preview.get('artifact_scope') or '-'}",
        f"  memory_scope: {preview.get('memory_scope') or '-'}",
        "Runtime Metadata:",
        f"  claim_id: {preview.get('claim_id') or '-'}",
        f"  active_session_id: {preview.get('active_session_id') or '-'}",
        f"  last_session_id: {preview.get('last_session_id') or '-'}",
        "Actor Match:",
        f"  actor_canonical_agent: {preview.get('actor_canonical_agent') or '-'}",
        f"  actor_profile_matched: {preview.get('actor_profile_matched')}",
        f"  actor_match_reason: {preview.get('actor_match_reason') or '-'}",
        f"  actor_aliases: {', '.join(preview.get('actor_aliases', [])) or '-'}",
        "Availability:",
        f"  actor_availability_status: {preview.get('actor_availability_status') or 'unknown'}",
        f"  actor_instance_availability_status: {preview.get('actor_instance_availability_status') or 'unknown'}",
        f"  actor_agent_availability_status: {preview.get('actor_agent_availability_status') or 'unknown'}",
        f"  availability_warning: {preview.get('availability_warning') or '-'}",
        "Proposed IDs:",
        f"  proposed_session_id: {preview.get('proposed_session_id') or '-'}",
        f"  proposed_claim_id: {preview.get('proposed_claim_id') or '-'}",
        "Runtime Profile:",
        f"  runtime_profile: {preview.get('runtime_profile') or '-'}",
        f"  runtime_entrypoint: {preview.get('runtime_entrypoint') or '-'}",
        f"  runtime_command: {preview.get('runtime_command') or '-'}",
        f"  runtime_args: {preview.get('runtime_args', [])!r}",
        f"  runtime_env: {preview.get('runtime_env', {})!r}",
        f"  launch_notes: {preview.get('launch_notes') or '-'}",
        "Record Awareness:",
        f"  proposed_session_record_path: {preview.get('proposed_session_record_path') or '-'}",
        f"  proposed_claim_log_path: {preview.get('proposed_claim_log_path') or '-'}",
        "Validation:",
        f"  blocking_reasons: {', '.join(preview.get('blocking_reasons', [])) or '-'}",
        f"  warnings: {', '.join(preview.get('warnings', [])) or '-'}",
        f"  needs_owner_reasons: {', '.join(preview.get('needs_owner_reasons', [])) or '-'}",
        f"Copy Context Allowed: {preview.get('copy_context_allowed')}",
        f"Claim Allowed: {preview.get('claim_allowed')}",
        f"Run Locally Allowed: {preview.get('run_locally_allowed')}",
        f"Recommended Action: {preview.get('recommended_action') or '-'}",
    ]
    session_records = preview.get("existing_session_records", [])
    claim_logs = preview.get("existing_claim_logs", [])
    if session_records:
        lines.append("Existing Session Records:")
        lines.extend(f"  {_record_line(record, 'session')}" for record in session_records[:5])
    else:
        lines.append("Existing Session Records: (none)")
    if claim_logs:
        lines.append("Existing Claim Logs:")
        lines.extend(f"  {_record_line(record, 'claim')}" for record in claim_logs[:5])
    else:
        lines.append("Existing Claim Logs: (none)")
    checks = preview.get("record_ref_checks", [])
    if checks:
        lines.append("Record Reference Checks:")
        lines.extend(
            f"  - {check.get('reference')}: {check.get('status')} ({check.get('level')}) | {check.get('record_id') or '-'}"
            for check in checks
        )
    else:
        lines.append("Record Reference Checks: (none)")
    lines.extend(
        [
            "Safety Notice:",
            "This preview is read-only. It does not claim, move, execute, or write task/session/claim records.",
        ]
    )
    return "\n".join(lines)
