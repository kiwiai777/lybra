"""AIPOS-338 S1/S6 — 过门契约节 renderer (single-source, zero hand-written verbs).

Renders the card-bound 「认领与交回」section that manual mode needs. It is the
STATIC, card-bound complement to AIPOS-322's RUNTIME delivery (gate_guidance):
the Owner sees the contract on the card BEFORE pasting it; the agent gets the
live verb via the gate at claim time. Both draw from the same single sources.

Single sources (no parallel truth in the publisher):
  - BRANCH determination  -> flow_description.resolve_gate_chain
                             (collaboration_profile × task_fields -> GateChain)
  - VERB names/params     -> verb_contract.resolve_gate_verbs (the live registry)
                             The publisher (draft_writer) carries ZERO lybra_* literals.

The publisher appends this section to every NEW publish (old cards are not
backfilled). Branch-aware per AIPOS-338 S6:
  - code (no deploy)      -> claim/progress/return; independent audit R card derived
  - code + deploy         -> same + deploy-gate reminder (prod-grade only)
  - non-code              -> bench audit path (no R card); explicit degradation note
                             while bench verbs are unimplemented (AIPOS-336 pending)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.aipos_cli.flow_description import resolve_gate_chain
from tools.aipos_cli.verb_contract import resolve_gate_verbs


_DEFAULT_GATE_URL = "http://127.0.0.1:7118"


def workspace_gate_url(repo_root: str | Path) -> str:
    """Read the gate base URL from <repo_root>/.lybra/connection.json (defensive)."""
    conn = Path(repo_root) / ".lybra" / "connection.json"
    if not conn.is_file():
        return _DEFAULT_GATE_URL
    try:
        data = json.loads(conn.read_text(encoding="utf-8"))
        mcp = data.get("mcp") if isinstance(data, dict) else None
        if isinstance(mcp, dict):
            rpc_url = str(mcp.get("rpc_url") or "").strip()
            if rpc_url:
                return rpc_url[:-4] if rpc_url.endswith("/mcp") else rpc_url
    except Exception:
        pass
    return _DEFAULT_GATE_URL


def workspace_connection_info(repo_root: str | Path) -> dict[str, str]:
    """Return {gate_url, connection_json_rel, workspace_display} for a workspace."""
    root = str(repo_root)
    return {
        "gate_url": workspace_gate_url(root),
        "connection_json_rel": ".lybra/connection.json",
        "workspace_display": root,
    }


# Acceptance-facing params that are NOT in the verb schema but the contract
# section always names (envelope / autonomy). These are structural, not a verb
# list: they describe HOW the agent fills the envelope, not which verb exists.
_CLAIM_AUTONOMY = "PreAuthorized"
_RETURN_AUTONOMY = "Supervised"


def _verb_name(verbs: dict[str, Any | None], role_key: str) -> str:
    """Return the live registry verb name for a logical role, or a stale marker.

    A None (unimplemented, e.g. bench verbs) is reported by the caller explicitly;
    a registered verb always returns its live name so registry renames auto-flow.
    """
    contract = verbs.get(role_key)
    if contract is None:
        return ""
    return str(contract.get("name") or "")


def _param_list(verbs: dict[str, Any | None], role_key: str) -> list[str]:
    """Required params for a logical role, straight from the live registry."""
    contract = verbs.get(role_key)
    if contract is None:
        return []
    return list(contract.get("required_params") or [])


def _params_inline(verbs: dict[str, Any | None], role_key: str, *, extras: list[tuple[str, str]] | None = None) -> str:
    """Render `key=value` fragments for the section, registry params first."""
    fragments: list[str] = []
    for p in _param_list(verbs, role_key):
        fragments.append(f"`{p}=<…>`")
    for key, val in (extras or []):
        fragments.append(f"`{key}={val}`")
    return ", ".join(fragments)


def _branch_summary_lines(chain: Any, role: str) -> list[str]:
    """Human gate-chain summary (branch label + ordered step descriptions)."""
    lines = [f"- **分支**:`{getattr(chain, 'branch_id', '')}` — {getattr(chain, 'branch_label', '')}"]
    step_descs = []
    for step in getattr(chain, "steps", ()):
        marker = " ⚠️(该动词尚未实现)" if getattr(step, "not_implemented", False) else ""
        step_descs.append(f"{step.description}{marker}")
    if step_descs:
        lines.append("- **门链**:" + " → ".join(step_descs))
    return lines


def _executor_section(
    chain: Any,
    verbs: dict[str, Any | None],
    *,
    gate_url: str,
    connection_json_rel: str,
    workspace_display: str,
    claim_envelope: str,
    return_envelope: str,
    task_id: str | None,
    branch_id: str,
) -> list[str]:
    """Render the executor-facing contract body for the resolved branch."""
    lines: list[str] = []
    lines.append("## 【认领与交回】(执行体必读 —— 卡内 MCP 连接信息)")
    lines.append("")
    lines.append(
        f"- **gate**:`{gate_url}`;**connection.json**:`{connection_json_rel}`"
        f"  (`<workspace>` = `{workspace_display}`)"
    )
    lines.extend(_branch_summary_lines(chain, "executor"))

    claim_name = _verb_name(verbs, "claim_dry_run")
    progress_name = _verb_name(verbs, "task_progress")
    return_name = _verb_name(verbs, "return_dry_run")

    tid = task_id or "<task_id>"
    # Claim
    lines.append(
        f"- **认领**:调 `{claim_name}`(全名带 `_dry_run`;无 `_dry_run` 后缀的认领动词不存在,不要写错),"
        f"`task_id={tid}`,`actor`/`agent_instance` 均为你的实例名,"
        f"`autonomy_mode={_CLAIM_AUTONOMY}`,`owner_policy_ref={claim_envelope}`。"
        "判据:claims 记录落地(`<workspace>/5_tasks/records/claims/<ID>/claim_*.md`);"
        "未落地即停,BLOCK 报告写工作区 events"
        "(`<workspace>/5_tasks/records/events/<ID>/blocked_*.md`,兼容 S10)。"
    )
    # Progress
    lines.append(
        f"- 认领后 `{progress_name}` 报 `started`,收尾报 `completed`。"
    )
    # Return
    lines.append(
        f"- **交回**:`{return_name}`,`autonomy_mode={_RETURN_AUTONOMY}`,"
        f"`owner_policy_ref={return_envelope}`;若 328 已生效(executor 自确认 return)则自行 confirm,"
        "否则留顾问代按并说明。"
    )

    # Branch-specific notes (S6). No second branch table: branch_id came from
    # flow_description.resolve_gate_chain (the single source).
    if branch_id == "code_with_deploy":
        lines.append(
            "- **部署门提醒**:本卡 `deploy: true`。仅当为**生产级部署**(异机/对外/难回滚)时,"
            "交回后审计 PASS ≠ 可部署 —— 部署确认属 Owner"
            "(`owner_verify: required` 的不可逆确认,判断在 Owner)。"
            "开发环回部署(lybra-deploy 类快照+回滚)**不触发**部署门提醒。"
        )
    elif branch_id == "noncode_bench_audit":
        bench_submit = verbs.get("bench_audit_submit")
        if bench_submit is None:
            lines.append(
                "- ⚠️ **bench 动词尚未实现**:非代码任务走验证台 bench 审计,"
                "但 `lybra_bench_audit_submit/confirm` 尚未实现 —— 暂走 **Owner 眼验 + 记录**;"
                "bench 落地(AIPOS-336)后零改动自动启用。"
            )
        else:
            lines.append(
                f"- 非代码任务走验证台 bench 审计:交回后 `{_verb_name(verbs,'bench_audit_submit')}` "
                "提交证据,Owner 眼验后结案。"
            )
        lines.append(
            "- **本分支不派生独立审计 R 卡**;证据要求(交付时附,按任务类型取相关项):"
            "部署健康 / 配置 diff / 内容产出 / 调研结论。"
        )
    lines.append("- 动词全名与参数派生自 gate 注册表(verb_contract),改名自动跟随。")
    return lines


def _auditor_section(
    chain: Any,
    verbs: dict[str, Any | None],
    *,
    gate_url: str,
    connection_json_rel: str,
    workspace_display: str,
    audit_envelope: str,
    task_id: str | None,
) -> list[str]:
    """Render the auditor-facing contract body for a derived R card.

    Auditors claim the R card, run the independent audit, and return a verdict.
    """
    lines: list[str] = []
    lines.append("## 【认领与交回】(审计体必读 —— 卡内 MCP 连接信息)")
    lines.append("")
    lines.append(
        f"- **gate**:`{gate_url}`;**connection.json**:`{connection_json_rel}`"
        f"  (`<workspace>` = `{workspace_display}`)"
    )

    claim_name = _verb_name(verbs, "claim_dry_run")
    progress_name = _verb_name(verbs, "task_progress")
    verdict_name = _verb_name(verbs, "audit_verdict_dry_run")

    tid = task_id or "<task_id>"
    lines.append(
        f"- **认领**:调 `{claim_name}`,`task_id={tid}`,`actor`/`agent_instance` 均为审计实例名,"
        f"`autonomy_mode={_CLAIM_AUTONOMY}`,`owner_policy_ref={audit_envelope}`。"
        "判据:claims 记录落地;未落地即停,BLOCK 写工作区 events。"
    )
    lines.append(f"- 认领后 `{progress_name}` 报 `started`,裁决后报 `completed`。")
    lines.append(
        f"- **裁决**:`{verdict_name}`,准绳 = 原执行卡全文(自述只作线索);"
        "独立取证,落 `<workspace>/5_tasks/records/audit_verdicts/<被审卡ID>/verdict_*.md`。"
    )
    lines.append("- 动词全名与参数派生自 gate 注册表(verb_contract),改名自动跟随。")
    return lines


def render_gate_contract_section(
    collaboration_profile: dict[str, Any],
    task_fields: dict[str, Any],
    *,
    role: str,
    gate_url: str,
    connection_json_rel: str,
    workspace_display: str,
    claim_envelope: str | None = None,
    return_envelope: str | None = None,
    audit_envelope: str | None = None,
    task_id: str | None = None,
    workspace_root: Path | None = None,
) -> str:
    """Render the card-bound contract section. Single-source, branch-aware.

    Args:
        collaboration_profile: project.json collaboration_profile (branch input).
        task_fields: task frontmatter (task_mode/deploy/audit/output_target/...).
        role: "executor" (execution card) or "auditor" (derived R card).
        gate_url / connection_json_rel / workspace_display: workspace connection.
        *_envelope: owner_policy_ref values. If None, resolved from workspace policies.
        task_id: optional, substituted into claim/return examples.
        workspace_root: governance repo root for policy resolution.

    Returns the markdown section (no leading/trailing blank lines beyond internal).
    Raises ValueError if envelopes cannot be resolved.
    """
    # AIPOS-340F2: resolve envelopes from workspace policies; NO hardcoded fallback.
    # If an envelope is not explicitly passed and cannot be resolved → raise immediately.
    # Tests that need fixed envelopes MUST pass them explicitly (claim_envelope=..., etc.).
    needs_resolution = (
        claim_envelope is None
        or return_envelope is None
        or (role == "auditor" and audit_envelope is None)
    )
    if needs_resolution:
        from tools.aipos_cli.policy_resolver import find_active_policy

        if workspace_root is None:
            raise ValueError(
                "render_gate_contract_section: workspace_root is required to resolve policy envelopes. "
                "Production callers must pass workspace_root; tests must pass explicit envelope params."
            )

        if claim_envelope is None:
            claim_envelope = find_active_policy(workspace_root, role="exec", policy_type="dev")
        if return_envelope is None:
            return_envelope = find_active_policy(workspace_root, role="exec", policy_type="dev")
        if audit_envelope is None and role == "auditor":
            audit_envelope = find_active_policy(workspace_root, role="audit", policy_type="audit")

    # After resolution: any still-None envelope is an error (no silent baking).
    missing = []
    if claim_envelope is None:
        missing.append("claim_envelope (exec/dev)")
    if return_envelope is None:
        missing.append("return_envelope (exec/dev)")
    if role == "auditor" and audit_envelope is None:
        missing.append("audit_envelope (audit/audit)")
    if missing:
        raise ValueError(
            f"render_gate_contract_section: cannot resolve policy envelope(s) from "
            f"workspace_root={workspace_root}: {', '.join(missing)}. "
            f"Ensure active, non-expired policies exist under "
            f"<workspace>/5_tasks/policies/, or pass explicit envelope params for tests."
        )
    chain = resolve_gate_chain(collaboration_profile, task_fields)
    verbs = resolve_gate_verbs()
    branch_id = getattr(chain, "branch_id", "")
    if role == "auditor":
        body = _auditor_section(
            chain, verbs,
            gate_url=gate_url, connection_json_rel=connection_json_rel,
            workspace_display=workspace_display, audit_envelope=audit_envelope,
            task_id=task_id,
        )
    else:
        body = _executor_section(
            chain, verbs,
            gate_url=gate_url, connection_json_rel=connection_json_rel,
            workspace_display=workspace_display, claim_envelope=claim_envelope,
            return_envelope=return_envelope, task_id=task_id, branch_id=branch_id,
        )
    return "\n".join(body)
