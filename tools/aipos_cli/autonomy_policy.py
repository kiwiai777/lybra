"""AIPOS-250 — owner_autonomy_policy artifact: render + gate reader + envelope matcher.

The FIRST autonomy tier ("PreAuthorized envelope"). An Owner hand-confirms ONE bounded
autonomy envelope (a policy artifact under 5_tasks/policies/); at runtime the gate does a
STRUCTURAL match — it never re-decides. Matching is strict AND (task_selector ∧ agent/role
∧ time window ∧ released_count < max_tasks ∧ status==active); any doubt falls back to
Supervised (fail-safe,偏窄). The envelope is bounded on TWO axes: time (expires_at) and
count (max_tasks); reaching either bound drops the claim back to Supervised.

This module is pure/low-level (task_loader + record_writer only) so both the gate handlers
and the owner_decision writer can share it without an import cycle.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.record_writer import CLAIMS_ROOT, render_markdown




POLICIES_DIR = Path("5_tasks/policies")
AUTONOMY_MODE_SUPERVISED = "Supervised"
AUTONOMY_MODE_PREAUTHORIZED = "PreAuthorized"
POLICY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
POLICY_STATUS_ACTIVE = "active"
POLICY_STATUSES = {"active", "expired", "revoked"}


# AIPOS-PRERELEASE-1: permanent gate-side audit trace for PreAuthorized envelope decisions.
# Emits one JSON line to stderr (→ journald under lybra-dev-gate.service) so an envelope match
# or a Supervised fallback is observable from gate evidence — not agent self-report. Low
# volume: fires only on PreAuthorized claim attempts (the envelope path).
_ENVELOPE_LOGGER = logging.getLogger("lybra.envelope")
if not _ENVELOPE_LOGGER.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _ENVELOPE_LOGGER.addHandler(_h)
_ENVELOPE_LOGGER.setLevel(logging.INFO)
_ENVELOPE_LOGGER.propagate = False


def trace_envelope(payload: dict[str, Any]) -> None:
    """Emit one structured envelope-decision trace line (JSON). Best-effort: never raises."""
    try:
        _ENVELOPE_LOGGER.info("[ENVELOPE_TRACE] " + json.dumps(payload, default=str, sort_keys=True))
    except Exception:
        pass

# FLAT bounded-map frontmatter (AIPOS-219 P5 idiom: depth-1, readable on bare python).
# task_selector is flattened into three explicit fields; task_ids is a YAML list.
POLICY_FRONTMATTER_ORDER = [
    "record_type",
    "policy_id",
    "mode",
    "status",
    "approved_by_owner",
    "owner_approval_ref",
    "active_from",
    "expires_at",
    "agent_or_role",
    "task_selector_task_mode",
    "task_selector_project",
    "task_selector_task_ids",
    "max_tasks",
]


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_autonomy_policy_markdown(
    *,
    policy_id: str,
    agent_or_role: str,
    active_from: str,
    expires_at: str,
    max_tasks: int,
    owner_approval_ref: str,
    task_selector_task_mode: str | None = None,
    task_selector_project: str | None = None,
    task_selector_task_ids: list[str] | None = None,
    status: str = POLICY_STATUS_ACTIVE,
    approved_by_owner: bool = True,
) -> str:
    """Render an owner_autonomy_policy artifact. Written only through the owner_confirm-gated
    owner_decision_record grant path — the presence of this on-disk artifact IS the Owner's
    one hand-confirmation (red line 1: pre-authorization is not delegation)."""
    metadata = {
        "record_type": "owner_autonomy_policy",
        "policy_id": policy_id,
        "mode": AUTONOMY_MODE_PREAUTHORIZED,
        "status": status,
        "approved_by_owner": bool(approved_by_owner),
        "owner_approval_ref": owner_approval_ref,
        "active_from": active_from,
        "expires_at": expires_at,
        "agent_or_role": agent_or_role,
        "task_selector_task_mode": str(task_selector_task_mode or "") or "",
        "task_selector_project": str(task_selector_project or "") or "",
        "task_selector_task_ids": list(task_selector_task_ids or []),
        "max_tasks": int(max_tasks),
    }
    body = "\n".join(
        [
            f"# Owner Autonomy Policy: {policy_id}",
            "",
            "## Envelope",
            "",
            f"- Mode: {AUTONOMY_MODE_PREAUTHORIZED} (claim auto-release for the matched envelope only).",
            f"- Covers: `{agent_or_role}`.",
            f"- Active from `{active_from}` until `{expires_at}` (time bound).",
            f"- Max auto-released claims: {max_tasks} (count bound).",
            f"- Owner approval: `{owner_approval_ref}`.",
            "",
            "## Boundary",
            "",
            "This artifact records a bounded, revocable Owner pre-authorization for CLAIM only. "
            "It does not authorize return, publish, audit, finalize, credential access, or any "
            "runtime agent-side confirmation. Reaching the time or count bound, or status "
            "revoked/expired, drops matching claims back to Supervised (per-task owner_confirm).",
            "",
        ]
    )
    return render_markdown(metadata, body, POLICY_FRONTMATTER_ORDER)


def normalize_policy(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Coerce a parsed policy artifact frontmatter into a normalized policy dict, or None if
    it is not a well-formed owner_autonomy_policy."""
    if not isinstance(metadata, dict):
        return None
    if str(metadata.get("record_type") or "").strip() != "owner_autonomy_policy":
        return None
    policy_id = str(metadata.get("policy_id") or "").strip()
    if not policy_id or not POLICY_ID_PATTERN.fullmatch(policy_id):
        return None
    task_ids = metadata.get("task_selector_task_ids")
    if not isinstance(task_ids, list):
        task_ids = []
    try:
        max_tasks = int(metadata.get("max_tasks"))
    except (TypeError, ValueError):
        max_tasks = 0
    return {
        "policy_id": policy_id,
        "mode": str(metadata.get("mode") or "").strip(),
        "status": str(metadata.get("status") or "").strip(),
        "approved_by_owner": bool(metadata.get("approved_by_owner")),
        "owner_approval_ref": str(metadata.get("owner_approval_ref") or "").strip(),
        "active_from": str(metadata.get("active_from") or "").strip(),
        "expires_at": str(metadata.get("expires_at") or "").strip(),
        "agent_or_role": str(metadata.get("agent_or_role") or "").strip(),
        "task_selector_task_mode": str(metadata.get("task_selector_task_mode") or "").strip(),
        "task_selector_project": str(metadata.get("task_selector_project") or "").strip(),
        "task_selector_task_ids": [str(item).strip() for item in task_ids if str(item).strip()],
        "max_tasks": max_tasks,
    }


def load_policy(repo_root: Path, policy_id: str) -> dict[str, Any] | None:
    """Read a single policy artifact by id. Returns None on a missing/malformed/forged ref —
    the caller falls back to Supervised (★A1 anti-forgery: a ref to a nonexistent policy grants
    nothing)."""
    pid = str(policy_id or "").strip()
    if not pid or not POLICY_ID_PATTERN.fullmatch(pid):
        return None
    path = (repo_root / POLICIES_DIR / f"{pid}.md").resolve()
    try:
        path.relative_to(repo_root.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    metadata, _body, warnings = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
    if warnings:
        return None
    policy = normalize_policy(metadata)
    if policy is None or policy["policy_id"] != pid:
        return None
    return policy


def count_preauthorized_claims(repo_root: Path, policy_id: str) -> int:
    """Count already-landed PreAuthorized claim records attributed to this policy. Stateless and
    auditable (R-1 count bound): the gate re-derives the count from truth every match."""
    pid = str(policy_id or "").strip()
    if not pid:
        return 0
    claims_root = (repo_root / CLAIMS_ROOT).resolve()
    if not claims_root.is_dir():
        return 0
    count = 0
    for path in claims_root.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            metadata, _body, _warn = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("autonomy_mode") or "").strip() != AUTONOMY_MODE_PREAUTHORIZED:
            continue
        if str(metadata.get("owner_policy_ref") or "").strip() == pid:
            count += 1
    return count


def match_claim_envelope(
    *,
    policy: dict[str, Any],
    task_id: str,
    task_mode: str,
    project: str,
    agent_instance: str,
    actor: str,
    now: datetime,
    released_count: int,
    claiming_role: str | None = None,
) -> tuple[bool, str]:
    """Strict-AND envelope match for a claim. Returns (matched, reason). Every predicate must
    hold; any miss returns matched=False with a human reason (偏窄 fail-safe). The caller uses
    matched=True to auto-release (one-stage direct write) and matched=False to fall back to
    Supervised (per-task owner_confirm).

    AIPOS-PRERELEASE-1: every predicate's input and judgment is traced to stderr (→ journald)
    as gate-side evidence, so a match or fallback is observable without agent self-report.
    Behavior is unchanged — only observability is added.
    """
    # ── evaluate every predicate (full picture for the trace) ─────────────
    is_dict = isinstance(policy, dict)
    covered = str(policy.get("agent_or_role") or "").strip() if is_dict else ""
    identity = {str(agent_instance or "").strip(), str(actor or "").strip()}
    if claiming_role:
        identity.add(str(claiming_role).strip())
    identity.discard("")

    sel_mode = str(policy.get("task_selector_task_mode") or "").strip() if is_dict else ""
    sel_project = str(policy.get("task_selector_project") or "").strip() if is_dict else ""
    sel_ids = list(policy.get("task_selector_task_ids") or []) if is_dict else []

    active_from = _parse_iso(policy.get("active_from")) if is_dict else None
    expires_at = _parse_iso(policy.get("expires_at")) if is_dict else None
    tw_parseable = active_from is not None and expires_at is not None

    try:
        max_tasks = int(policy.get("max_tasks") or 0) if is_dict else 0
    except (TypeError, ValueError):
        max_tasks = 0

    predicates = {
        "policy_is_dict": is_dict,
        "mode": is_dict and policy.get("mode") == AUTONOMY_MODE_PREAUTHORIZED,
        "status_active": is_dict and policy.get("status") == POLICY_STATUS_ACTIVE,
        "approved_by_owner": bool(policy.get("approved_by_owner")) if is_dict else False,
        "time_window_parseable": tw_parseable,
        "time_window_active": tw_parseable and (active_from <= now < expires_at),
        "agent_or_role": bool(covered) and covered in identity,
        "task_selector_present": bool(sel_mode or sel_project or sel_ids),
        "task_id_in_ids": (not sel_ids) or (str(task_id or "").strip() in sel_ids),
        "task_mode": (not sel_mode) or (str(task_mode or "").strip() == sel_mode),
        "project": (not sel_project) or (str(project or "").strip() == sel_project),
        "max_tasks_positive": max_tasks > 0,
        "count_bound": released_count < max_tasks,
    }

    # ── first-failing reason (preserves original short-circuit order & strings) ──
    if not is_dict:
        reason = "no policy artifact resolved for owner_policy_ref"
    elif policy.get("mode") != AUTONOMY_MODE_PREAUTHORIZED:
        reason = f"policy mode is not {AUTONOMY_MODE_PREAUTHORIZED}"
    elif policy.get("status") != POLICY_STATUS_ACTIVE:
        reason = f"policy status is {policy.get('status') or 'unset'}, not active"
    elif not policy.get("approved_by_owner"):
        reason = "policy is not approved_by_owner"
    elif active_from is None or expires_at is None:
        reason = "policy time window is missing or unparseable"
    elif now < active_from:
        reason = "policy is not yet active (active_from in the future)"
    elif now >= expires_at:
        reason = "policy has expired (expires_at reached)"
    elif not covered or covered not in identity:
        reason = "claiming agent/role is not covered by policy.agent_or_role"
    elif not (sel_mode or sel_project or sel_ids):
        reason = "policy task_selector is empty (no wildcard auto-release)"
    elif sel_ids and str(task_id or "").strip() not in sel_ids:
        reason = "task_id is not in policy task_selector.task_ids"
    elif sel_mode and str(task_mode or "").strip() != sel_mode:
        reason = "task_mode does not match policy task_selector.task_mode"
    elif sel_project and str(project or "").strip() != sel_project:
        reason = "project does not match policy task_selector.project"
    elif max_tasks <= 0:
        reason = "policy max_tasks is not a positive bound"
    elif released_count >= max_tasks:
        reason = f"policy count bound reached ({released_count}/{max_tasks})"
    else:
        reason = f"matched policy {policy.get('policy_id')} (released {released_count}/{max_tasks})"

    matched = all(predicates.values())
    trace_envelope({
        "phase": "match_claim_envelope",
        "policy_id": policy.get("policy_id") if is_dict else None,
        "task_id": task_id,
        "inputs": {
            "task_mode": task_mode,
            "project": project,
            "agent_instance": agent_instance,
            "actor": actor,
            "claiming_role": claiming_role,
            "now": now.isoformat(),
            "released_count": released_count,
            "policy_agent_or_role": covered,
            "policy_task_selector": {"task_mode": sel_mode, "project": sel_project, "task_ids": sel_ids},
            "policy_active_from": policy.get("active_from") if is_dict else None,
            "policy_expires_at": policy.get("expires_at") if is_dict else None,
            "policy_max_tasks": max_tasks,
        },
        "predicates": predicates,
        "matched": matched,
        "reason": reason,
    })
    return matched, reason
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
