"""AIPOS-358 — Auditor runtime utilities (migrated from auditor_loop).

launch_auditor_runtime: blocking agent launch (execution only, no verdict judgment).
claim_preauthorized: PreAuthorized one-shot claim (reusable by 340 dispatch step).

These are execution tools (hands), not decision-makers (brains).
All "what to do next" logic lives in turn_advancer rules/state_reader.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.aipos_cli.confirm_client import GateClient, GateError


def log(msg: str) -> None:
    """Timestamped log to stderr."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[auditor-runtime {timestamp}] {msg}", file=sys.stderr)


def claim_preauthorized(
    gate_client: GateClient,
    auditor_instance: str,
    envelope: str,
    audit_task_id: str,
) -> dict[str, Any]:
    """PreAuthorized one-shot claim (auto-release when structure matches).

    Returns: {auto_released, verdict, reason, claim_id, session_id}
    Raises GateError for transient gate failures (caller retries).
    """
    resp = gate_client.call_tool("lybra_queue_claim_dry_run", {
        "actor": auditor_instance,
        "agent_instance": auditor_instance,
        "autonomy_mode": "PreAuthorized",
        "owner_policy_ref": envelope,
        "task_id": audit_task_id,
    })
    auto_released = (
        bool(resp.get("preauthorized_release"))
        and resp.get("autonomy_mode") == "PreAuthorized"
    )
    return {
        "auto_released": auto_released,
        "verdict": resp.get("verdict", ""),
        "reason": json.dumps(
            resp.get("blocking_reasons")
            or resp.get("owner_confirmation_reasons")
            or resp,
            ensure_ascii=False,
        )[:400],
        "claim_id": resp.get("claim_id", ""),
        "session_id": (
            resp.get("active_session_id") or resp.get("last_session_id") or ""
        ),
    }


def launch_auditor_runtime(
    runtime_cmd_template: str,
    audit_task_id: str,
    reviewed_task_id: str,
    audit_card_path: str,
    product_repo: Path,
    workspace_root: Path,
    envelope: str,
) -> dict[str, Any]:
    """Launch the auditor runtime (blocking, foreground).

    AIPOS-358: simplified — execution only, no verdict checking.
    Verdict verification is now done by turn-advancer scan on next cycle
    (state_reader reads verdict records; rules decide next action).

    Returns: {exit_code: int}
    """
    from tools.aipos_cli.verb_contract import get_verb_contract, validate_kickoff_verbs

    dir_name = reviewed_task_id or audit_task_id.replace("R", "")
    report_path = (
        product_repo / "task_cards" / dir_name / f"AUDIT-REPORT-{audit_task_id}.md"
    )
    verdict_verb_name = "lybra_audit_verdict_dry_run"

    # AIPOS-357: absolute workspace path for event files
    events_abs_glob = (
        str(
            (
                workspace_root / "5_tasks" / "records" / "events" / audit_task_id
            ).resolve()
        )
        + "/blocked_*.md"
    )

    kickoff = (
        f"冷启动 (auditor daemon AIPOS-358 薄壳拉起, 全程无人)。"
        f"审计卡 {audit_task_id} 已由薄壳经预授权信封 {envelope} 一发式认领 "
        f"(autonomy_mode=PreAuthorized), 你【无需再 /claim】——卡已在 claimed 态。"
        f"审计准绳=原执行卡 (审计卡 {audit_card_path} 的 reviewed_task_id 指向原卡, "
        f"以原卡为唯一真相)。请按你的 skills 独立只读取证, "
        f"逐项 PASS/FAIL+证据, 给结论。报告出口唯一化: 写到 {report_path}。"
        f"审完按你的 write-return 流程如实记录裁决与自报模型/token; 遇护栏拦截即停。"
        f"\n\n## 裁决提交失败规范动作"
        f"\n如果 {verdict_verb_name} 返回错误:"
        f"\n1. 将完整错误 JSON 贴到报告末尾"
        f"\n2. 写 blocked 事件到 {events_abs_glob}"
        f"\n3. 禁止手写 records"
        f"\n4. 如实退出 (非零 exit)"
    )

    verb_errors = validate_kickoff_verbs(kickoff)
    if verb_errors:
        raise ValueError(f"Kickoff contains invalid verb names: {'; '.join(verb_errors)}")

    kickoff_fd, kickoff_temp_path = tempfile.mkstemp(
        suffix=".txt", prefix="lybra_kickoff_audit_"
    )
    try:
        with os.fdopen(kickoff_fd, "w", encoding="utf-8") as f:
            f.write(kickoff)
    except Exception as exc:
        os.close(kickoff_fd)
        raise

    cmd = runtime_cmd_template.replace("{kickoff}", f"@{kickoff_temp_path}")
    log(f"拉起 auditor: {audit_task_id} → 报告={report_path}")

    try:
        result = subprocess.run(cmd, shell=True, cwd=str(product_repo))
    finally:
        try:
            os.unlink(kickoff_temp_path)
        except OSError:
            pass

    log(f"auditor 结束 exit={result.returncode} ({audit_task_id})")
    return {"exit_code": result.returncode}
