"""AIPOS-363 S1/S2 — connector materialization: the ONLY adaptation layer between a
cross-machine executor and the gate.

Flow (card architecture, one sentence):
  claim → pull body via 319 (include_body) → drop LOCAL material → launch ANY agent that
  reads LOCAL files → agent writes LOCAL RETURN → connector pushes back via 320 (return_body).
The agent stays a DUMB executor: it reads/writes LOCAL files only and learns ZERO gate verbs
(card S3: harness-agnostic baseline — pi / Claude Code / codex / any agent that can read a file).

Why this is a module APART from agent_connector.py:
  agent_connector.py is red-line-governed READ-ONLY ("fetch/watch NEVER claim"; its only gate
  call is the read tool). Materialization MUST claim and MUST write — those red lines do not
  apply here, so conflating the two would muddy the connector's governance. This module IS the
  "only adaptation layer" the card names; claim+materialize and read+pushback live here by design.

Material area (one card = one directory; card S1 "一卡一目录"):
  <material_root>/<task_id>/
    card.md         — the task card body (from 319 include_body), the agent's only input surface
    MANIFEST.json   — non-secret provenance: task_id, claim_id, active_session_id, gate_url,
                      actor, role, materialized_at, gate_workspace (traceability; never re-read)
    RETURN.md       — the agent writes its return here; pushback reads + relays it (320)

  material_root defaults to ~/.lybra/work (overridable via --material-root / LYBRA_MATERIAL_ROOT).
  The directory is RETAINED after pushback as the local RETURN copy (card S2: "本地 RETURN 保留为副本").

Red lines (card):
  - agent 主动 pull, gate 被动 (248): materialize/pushback are agent-side stateless gate calls.
  - 本卡不碰凭据搬运: the token is read internally from connection.json by role (confirm_client);
    the material area MUST NOT contain credentials — MANIFEST records only non-secret provenance.
  - 正文经认证信道,不落日志: card.md travels the authenticated MCP channel; it is never logged
    by this module (only written to the local material area the operator chose).
  - 交回失败不静默 (S2): on any pushback failure, emit a blocked progress event (323) and surface
    the error loudly; never swallow.

S5 (MCP into session = optional enhancement): an MCP-capable harness MAY instead call
lybra_task_preview(include_body) / lybra_queue_return_dry_run(return_body) directly. That path is
NOT implemented here — materialization is the harness-agnostic BASELINE. The config port is the
connection.json + gate_url this module already consumes; a harness that speaks MCP simply skips the
local file hop. (Documented, not implemented — card S5.)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.confirm_client import GateClient, GateError, load_owner_token

DEFAULT_MATERIAL_ROOT = Path("~/.lybra/work").expanduser()
CLAIM_VERB = "lybra_queue_claim_dry_run"
PREVIEW_VERB = "lybra_task_preview"
RETURN_DRY_RUN_VERB = "lybra_queue_return_dry_run"
RETURN_CONFIRM_VERB = "lybra_queue_return_confirm"
PROGRESS_VERB = "lybra_task_progress"
OWNER_CONFIRM_LITERAL = "OWNER_CONFIRMED"  # public confirm-intent ceremony (328); gates nothing Owner-side


def material_root(override: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the material area root: explicit override > LYBRA_MATERIAL_ROOT > ~/.lybra/work."""
    if override:
        return Path(override).expanduser()
    env = os.environ.get("LYBRA_MATERIAL_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    return DEFAULT_MATERIAL_ROOT


def task_material_dir(task_id: str, root: Path | None = None) -> Path:
    """One card = one directory (card S1). task_id is sanitized to a single path segment
    (no separators / traversal) so a forged id can never escape the material root."""
    clean = str(task_id or "").strip()
    if not clean or "/" in clean or "\\" in clean or ".." in clean:
        raise ValueError(f"invalid task_id for material path: {task_id!r}")
    return (root or material_root()) / clean


def render_materialized_kickoff(task_id: str, material_dir: Path, *, harness_hint: str = "") -> str:
    """The MATERIALIZED kickoff: zero gate verbs, points only at LOCAL files (card S3:
    "agent 侧零 gate 知识"). Distinct from advisor_pump.generate_kickoff BY DESIGN — that one
    tells a local-workspace agent to ask the gate; this one tells a cross-machine agent to read
    a local file the connector already dropped. Shared concern (kickoff content), different lane.

    The agent is told exactly two local paths (input card, output RETURN) and nothing else —
    where the material came from or how the return reaches the gate is the connector's job."""
    card_path = material_dir / "card.md"
    return_path = material_dir / "RETURN.md"
    hint = f"\n{harness_hint}" if harness_hint else ""
    return "\n".join(
        [
            f"冷启动(材料化)。任务卡 {task_id} 的正文已由连接器拉取并落在本机:",
            f"  {card_path}",
            "请先读这张卡(它是你的唯一任务输入),按卡内要求在本车道内独立完成实现。",
            "",
            "完成后,把你的归还正文如实写入本地文件(连接器会读取并代你推回 gate):",
            f"  {return_path}",
            "RETURN 写法:做了什么、改了哪些文件、测试结果原文、实际使用的模型与自报 token 用量。",
            "失败/部分完成也如实写,不粉饰。",
            "",
            "你不需要、也不应调用任何 gate 动词——取正文/交回由连接器代办;你只见本地文件。",
            "遇护栏拦截或卡内信息不足:说明并停,不绕过。",
            f"材料目录(可留作本地副本):{material_dir}{hint}",
        ]
    )


def _write_card_and_manifest(
    material_dir: Path,
    *,
    task_id: str,
    body_markdown: str,
    manifest: dict[str, Any],
) -> None:
    """Write card.md + MANIFEST.json atomically-enough (dir first, then files). MANIFEST holds
    only NON-SECRET provenance — never a token, never the body (the body is in card.md)."""
    material_dir.mkdir(parents=True, exist_ok=True)
    (material_dir / "card.md").write_text(body_markdown, encoding="utf-8")
    (material_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def read_return(material_dir: Path) -> str:
    """Read the local RETURN.md the agent wrote. Missing file is a loud error (card S2: the
    connector does not fabricate a return)."""
    path = material_dir / "RETURN.md"
    if not path.is_file():
        raise FileNotFoundError(f"no local RETURN.md to push back at {path}")
    return path.read_text(encoding="utf-8")


def load_manifest(material_dir: Path) -> dict[str, Any]:
    """Read the materialized MANIFEST (claim_id / active_session_id provenance for pushback)."""
    path = material_dir / "MANIFEST.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def materialize(
    client: GateClient,
    *,
    task_id: str,
    actor: str,
    owner_policy_ref: str,
    root: Path | None = None,
    autonomy_mode: str = "PreAuthorized",
    claim_reason: str = "",
    actual_model: str = "",
    gate_workspace: str = "",
) -> dict[str, Any]:
    """S1: claim the task via the gate, then pull its body (319 include_body) and drop it into
    the local material area. Returns a result dict with ok/claimed/material_dir/kickoff.

    Claim handling honors the gate's two outcomes:
      - PreAuthorized auto-release (performed_moves, no dry_run_token) → claim landed, proceed.
      - Supervised fallback (owner_confirmation_required + dry_run_token) → the connector CANNOT
        owner-confirm (executor scope); we report 'needs_owner_confirm' and STOP without
        materializing (card red line: 判断留人; the connector never self-confirms a claim).
    """
    claim_args: dict[str, Any] = {
        "task_id": task_id,
        "actor": actor,
        "agent_instance": actor,
        "autonomy_mode": autonomy_mode,
        "owner_policy_ref": owner_policy_ref,
        "active_session_id": f"session_{task_id}",
        "context_bundle_ack": "ack",
        "with_records": True,
        "claim_reason": claim_reason or f"materialize claim ({task_id})",
    }
    if actual_model:
        claim_args["actual_model"] = actual_model
    claim = client.call_tool(CLAIM_VERB, claim_args)
    if not claim.get("ok"):
        return {"ok": False, "phase": "claim", "error": "claim verb returned not-ok", "detail": claim}
    # PreAuthorized auto-release landed?
    released = bool(claim.get("preauthorized_release")) or bool(
        (claim.get("data") or {}).get("moved") or (claim.get("data") or {}).get("wrote")
    )
    if claim.get("owner_confirmation_required") and not released:
        return {
            "ok": False,
            "phase": "claim",
            "needs_owner_confirm": True,
            "autonomy_mode": claim.get("autonomy_mode"),
            "dry_run_token": claim.get("dry_run_token"),
            "message": (
                "claim fell back to Supervised (outside the PreAuthorized envelope / count-bound / "
                "expired). The connector cannot owner-confirm a claim (executor scope). Resolve the "
                "envelope (Owner) or confirm via the supervised chain, then re-run materialize."
            ),
            "detail": claim,
        }
    # pull the body (319 include_body)
    preview = client.call_tool(PREVIEW_VERB, {"task_id": task_id, "include_body": True})
    if not preview.get("ok"):
        return {"ok": False, "phase": "preview", "error": "task_preview include_body failed", "detail": preview}
    data = preview.get("data") if isinstance(preview.get("data"), dict) else {}
    body_markdown = str(data.get("body_markdown") or "")
    if not body_markdown:
        return {"ok": False, "phase": "preview", "error": "body_markdown empty (gate returned no body)", "detail": preview}
    claim_data = claim.get("data") if isinstance(claim.get("data"), dict) else {}
    frontmatter = claim_data.get("updated_frontmatter") if isinstance(claim_data.get("updated_frontmatter"), dict) else {}
    # the gate REWRITES active_session_id on claim (generates a canonical one); read it back from
    # the response so pushback's return_dry_run passes the SESSION_MISMATCH check. Sources, in
    # preference order: updated_frontmatter (claim/autorelease) -> data top-level -> our request.
    gate_session_id = (
        str(frontmatter.get("active_session_id") or "").strip()
        or str(claim_data.get("active_session_id") or "").strip()
        or claim_args["active_session_id"]
    )
    manifest = {
        "task_id": task_id,
        "claim_id": str(frontmatter.get("claim_id") or claim_data.get("claim_id") or claim.get("claim_id") or ""),
        "active_session_id": gate_session_id,
        "actor": actor,
        "owner_policy_ref": owner_policy_ref,
        "autonomy_mode": claim.get("autonomy_mode") or autonomy_mode,
        "materialized_at": _now_iso(),
        "gate_url": getattr(client, "_base_url", ""),
        "gate_workspace": gate_workspace,
    }
    mdir = task_material_dir(task_id, root)
    _write_card_and_manifest(mdir, task_id=task_id, body_markdown=body_markdown, manifest=manifest)
    return {
        "ok": True,
        "phase": "materialized",
        "task_id": task_id,
        "claim_id": manifest["claim_id"],
        "material_dir": str(mdir),
        "card_path": str(mdir / "card.md"),
        "return_path": str(mdir / "RETURN.md"),
        "kickoff": render_materialized_kickoff(task_id, mdir),
        "autonomy_mode": manifest["autonomy_mode"],
    }


def _emit_blocked_event(
    client: GateClient,
    *,
    task_id: str,
    actor: str,
    summary: str,
    actual_model: str = "",
) -> dict[str, Any]:
    """Card S2: 交回失败不静默 — surface a blocked progress event (323 channel). Best-effort: if
    the gate is unreachable we still RETURN the error to the caller; we never swallow it."""
    args: dict[str, Any] = {
        "task_id": task_id,
        "actor": actor,
        "event_type": "blocked",
        "stage": "pushback",
        "summary": summary,
    }
    if actual_model:
        args["model_self_reported"] = actual_model
    try:
        return client.call_tool(PROGRESS_VERB, args)
    except GateError as exc:
        return {"ok": False, "event_emitted": False, "error": f"progress event also failed: {exc}"}


def pushback(
    client: GateClient,
    *,
    task_id: str,
    actor: str,
    owner_policy_ref: str,
    root: Path | None = None,
    actual_model: str = "",
) -> dict[str, Any]:
    """S2: read the local RETURN.md and push it back via 320 (return_body), then self-confirm
    (328). Any failure emits a blocked progress event (323) — never silent. The local RETURN.md
    is retained as a copy (card S2)."""
    mdir = task_material_dir(task_id, root)
    try:
        return_body = read_return(mdir)
    except FileNotFoundError as exc:
        # no RETURN to push — emit blocked so the loop/owner sees it, then surface loudly
        ev = _emit_blocked_event(client, task_id=task_id, actor=actor, summary=f"pushback: {exc}", actual_model=actual_model)
        return {"ok": False, "phase": "read_return", "error": str(exc), "event": ev}
    manifest = load_manifest(mdir)
    session_id = str(manifest.get("active_session_id") or f"session_{task_id}")
    dry_args: dict[str, Any] = {
        "task_id": task_id,
        "actor": actor,
        "agent_instance": actor,
        "autonomy_mode": "Supervised",
        "owner_policy_ref": owner_policy_ref,
        "active_session_id": session_id,
        "result_summary": f"materialized pushback for {task_id} (body from local RETURN.md)",
        "executor_status": "completed",
        "audit_readiness": "ready",
        "return_reason": "work complete (materialized), request audit",
        "return_body": return_body,
    }
    try:
        dry = client.call_tool(RETURN_DRY_RUN_VERB, dry_args)
    except GateError as exc:
        ev = _emit_blocked_event(client, task_id=task_id, actor=actor, summary=f"return_dry_run gate error: {exc}", actual_model=actual_model)
        return {"ok": False, "phase": "return_dry_run", "error": str(exc), "event": ev}
    if not dry.get("ok") or not dry.get("dry_run_token"):
        ev = _emit_blocked_event(client, task_id=task_id, actor=actor, summary=f"return_dry_run not-ok: {dry.get('error_code') or dry.get('message') or 'no token'}", actual_model=actual_model)
        return {"ok": False, "phase": "return_dry_run", "error": "return dry-run rejected", "event": ev, "detail": dry}
    # 328: executor self-confirms its own return (OWNER_CONFIRMED is a public intent ceremony;
    # the real gates are downstream audit_verdict -> owner_verify -> close — see tools.py return_confirm).
    confirm_args = {
        "dry_run_token": dry["dry_run_token"],
        "owner_confirmation_token": OWNER_CONFIRM_LITERAL,
        "actor": actor,
        "agent_instance": actor,
        "owner_policy_ref": owner_policy_ref,
    }
    try:
        confirmed = client.call_tool(RETURN_CONFIRM_VERB, confirm_args)
    except GateError as exc:
        ev = _emit_blocked_event(client, task_id=task_id, actor=actor, summary=f"return_confirm gate error: {exc}", actual_model=actual_model)
        return {"ok": False, "phase": "return_confirm", "error": str(exc), "event": ev}
    if not confirmed.get("ok"):
        ev = _emit_blocked_event(client, task_id=task_id, actor=actor, summary=f"return_confirm not-ok: {confirmed.get('error_code') or confirmed.get('message')}", actual_model=actual_model)
        return {"ok": False, "phase": "return_confirm", "error": "return confirm rejected", "event": ev, "detail": confirmed}
    # success — local RETURN retained as copy by design (not deleted)
    return {
        "ok": True,
        "phase": "pushed_back",
        "task_id": task_id,
        "return_body_size": len(return_body),
        "local_return_retained": str(mdir / "RETURN.md"),
        "detail": confirmed,
    }


def _connect(args: Any) -> GateClient:
    """Connect a GateClient with the role token read internally from connection.json (card red
    line: 凭据只按名引用 — the token is never taken on argv and never printed)."""
    token = load_owner_token(connection_json=args.connection_json, role=args.role, token_env=args.token_env)
    client = GateClient(args.gate_url, token)
    client.initialize()
    return client


def _print_result(result: dict[str, Any], *, json_out: bool) -> int:
    if json_out:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        ok = result.get("ok")
        phase = result.get("phase", "")
        if ok:
            print(f"[materialize] OK ({phase}) task={result.get('task_id')}")
            if result.get("kickoff"):
                print("\n----- materialized kickoff (pipe to your harness) -----")
                print(result["kickoff"])
            if result.get("local_return_retained"):
                print(f"\n[pushback] local RETURN retained: {result['local_return_retained']}")
        else:
            print(f"[materialize] NOT OK ({phase}): {result.get('error') or result.get('message')}")
            if result.get("needs_owner_confirm"):
                print(result.get("message", ""))
    return 0 if result.get("ok") else 2


def run_materialize(args: Any) -> int:
    """`lybra agent materialize` — claim + pull body + drop local material + print kickoff."""
    try:
        client = _connect(args)
        result = materialize(
            client,
            task_id=args.task_id,
            actor=args.actor,
            owner_policy_ref=args.owner_policy_ref,
            root=Path(args.material_root) if getattr(args, "material_root", None) else None,
            autonomy_mode=getattr(args, "autonomy_mode", "PreAuthorized"),
            actual_model=getattr(args, "actual_model", "") or "",
            gate_workspace=getattr(args, "gate_workspace", "") or "",
        )
    except (GateError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"lybra agent materialize: {exc}")
        return 2
    return _print_result(result, json_out=getattr(args, "json", False))


def run_pushback(args: Any) -> int:
    """`lybra agent pushback` — read local RETURN + push via 320 + self-confirm (328)."""
    try:
        client = _connect(args)
        result = pushback(
            client,
            task_id=args.task_id,
            actor=args.actor,
            owner_policy_ref=args.owner_policy_ref,
            root=Path(args.material_root) if getattr(args, "material_root", None) else None,
            actual_model=getattr(args, "actual_model", "") or "",
        )
    except (GateError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"lybra agent pushback: {exc}")
        return 2
    return _print_result(result, json_out=getattr(args, "json", False))

# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
