"""AIPOS-292 — lybra auditor loop: the production auditor daemon (idle_agent).

Migrated from ~/bin/lybra-dev-auditor-daemon (AIPOS-269). The auditor loop is a
client-side FS pump + PreAuthorized claim + agent runtime launcher — the only "running
non-product" gap in the Lybra deployment (the old script was a bash while-loop with
hardcoded ~/bin paths). This module is the product command that systemd will call.

Behavioral contract (aligned with the AIPOS-269 script):
1. Watch queue/records for changes (FS pump, candidate ⑫, AIPOS-268).
2. Scan pending audit cards (task_mode=audit, status=pending, assigned to this auditor instance).
3. Claim via PreAuthorized envelope (one-shot, dry_run=gate auto-releases when structure matches).
4. Launch the auditor runtime (blocking, foreground) when a claim succeeds.
5. Block and exit 75 when the envelope is exhausted / claim is not auto-released (RestartPreventExitStatus).

Red lines:
- Loop = agent-side pump. Gate has ZERO push / ZERO clock.
- No gate write-face calls except claim confirm (the auditor loop is read-only + claim).
- Model default: claude-sonnet-5 (configurable via runtime-cmd template).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.aipos_cli.confirm_client import GateClient, GateError

BLOCK_EXIT_CODE = 75  # systemd RestartPreventExitStatus=75


def log(msg: str) -> None:
    """Timestamped log to stderr."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[auditor-loop {timestamp}] {msg}", file=sys.stderr)


def find_pending_audit_cards(workspace_root: Path, auditor_instance: str) -> list[dict[str, Any]]:
    """Scan pending audit cards assigned to this auditor instance.
    
    Returns list of dicts: {task_id, reviewed_task_id, path}.
    """
    pending_dir = workspace_root / "5_tasks" / "queue" / "pending"
    if not pending_dir.is_dir():
        return []
    
    results = []
    for card_file in sorted(pending_dir.glob("*.md")):
        try:
            content = card_file.read_text(encoding="utf-8")
        except OSError:
            continue
        
        # Parse frontmatter
        if not content.startswith("---"):
            continue
        end = content.find("\n---", 3)
        if end < 0:
            continue
        
        fm = {}
        for line in content[3:end].splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                fm[key.strip()] = value.strip().strip("'\"")
        
        if (fm.get("task_mode") == "audit" 
            and fm.get("status") == "pending"
            and fm.get("agent_instance") == auditor_instance
            and fm.get("task_id")):
            results.append({
                "task_id": fm["task_id"],
                "reviewed_task_id": fm.get("reviewed_task_id", ""),
                "path": str(card_file),
            })
    
    return results


def check_reviewed_task_has_final_verdict(
    workspace_root: Path,
    reviewed_task_id: str,
) -> dict[str, Any]:
    """AIPOS-351: Check if the reviewed task already has a final verdict.
    
    This prevents the guardian from repeatedly claiming stale R-cards whose
    reviewed task has already been audited (manually or automatically) and
    whose fix chain has closed.
    
    Returns: {has_final_verdict: bool, verdict_result: str, reason: str, verdict_files: list[Path]}
    """
    if not reviewed_task_id:
        return {
            "has_final_verdict": False,
            "verdict_result": "",
            "reason": "no reviewed_task_id specified",
            "verdict_files": [],
        }
    
    verdicts_dir = workspace_root / "5_tasks" / "records" / "audit_verdicts" / reviewed_task_id
    if not verdicts_dir.is_dir():
        return {
            "has_final_verdict": False,
            "verdict_result": "",
            "reason": f"no verdict records directory for {reviewed_task_id}",
            "verdict_files": [],
        }
    
    verdict_files = sorted(verdicts_dir.glob("verdict_*.md"))
    if not verdict_files:
        return {
            "has_final_verdict": False,
            "verdict_result": "",
            "reason": f"verdict directory exists but no verdict files for {reviewed_task_id}",
            "verdict_files": [],
        }
    
    # Read the latest verdict to determine if it's final
    latest = verdict_files[-1]  # sorted, so last is latest
    verdict_result = ""
    try:
        content = latest.read_text(encoding="utf-8")
        # Parse frontmatter for verdict result
        # AIPOS-351: check both 'verdict:' and 'verdict_result:' field names
        # (different audit paths use different field names)
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end > 0:
                for line in content[3:end].splitlines():
                    if line.startswith("verdict_result:"):
                        verdict_result = line.split(":", 1)[1].strip().strip("'\"")
                        break  # verdict_result takes priority
                    elif line.startswith("verdict:") and not line.startswith("verdict_id:"):
                        verdict_result = line.split(":", 1)[1].strip().strip("'\"")
                        # Don't break - prefer verdict_result if found later
    except OSError:
        pass
    
    # A verdict is "final" if it exists (PASS, FAIL, PASS_WITH_NOTES, etc.)
    # The guardian should not re-audit a task that already has a verdict record.
    # The fix chain closure is a separate concern (handled by the task lifecycle).
    has_final = bool(verdict_result)
    
    return {
        "has_final_verdict": has_final,
        "verdict_result": verdict_result,
        "reason": f"verdict already landed for {reviewed_task_id}: {verdict_result} ({len(verdict_files)} record(s))" if has_final else f"verdict files exist but no verdict_result parsed for {reviewed_task_id}",
        "verdict_files": verdict_files,
    }


def resolve_reviewed_task_id(audit_task_id: str, reviewed_task_id_fm: str) -> str:
    """AIPOS-357: Resolve the audited (reviewed) task ID from an audit card.

    Two sources, in priority order:
    1. Explicit frontmatter ``reviewed_task_id`` (preferred, set by audit derivation).
    2. Derived by stripping the trailing ``R`` from a ``<ID>R`` audit task ID.

    Returns ``""`` when unresolvable — special non-R audit cards (e.g. AIPOS-346A, a
    re-audit card with no R suffix and no reviewed_task_id) that cannot be mapped to a
    reviewed task. The caller must skip_unresolvable (do not claim, do not chew),
    because the verdict-criterion path
    ``5_tasks/records/audit_verdicts/<被审卡>/verdict_*.md`` cannot be assembled
    without a reviewed ID.
    """
    rid = (reviewed_task_id_fm or "").strip()
    if rid:
        return rid
    tid = (audit_task_id or "").strip()
    if tid.endswith("R") and len(tid) > 1:
        return tid[:-1]
    return ""


def _has_existing_event_kind(events_dir: Path, event_kind: str) -> bool:
    """AIPOS-357: dedup — return True if an event file with the given ``event_kind``
    already exists under ``events_dir``.

    Parsed from frontmatter ``event_kind:`` (robust); falls back to the
    ``<kind>_<YYYYMMDD>_<HHMMSS>.md`` filename prefix when frontmatter is absent or
    unparsable. Used so a repeatedly-polled card (e.g. 320R class) yields zero new
    skip events after the first one lands.
    """
    if not events_dir.is_dir():
        return False
    for ef in events_dir.glob("*.md"):
        try:
            text = ef.read_text(encoding="utf-8")
        except OSError:
            continue
        kind = ""
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                for line in text[3:end].splitlines():
                    if line.startswith("event_kind:"):
                        kind = line.split(":", 1)[1].strip().strip("'\"")
                        break
        if not kind:
            match = re.match(r"^(.+?)_\d{8}_\d{6}", ef.name)
            kind = match.group(1) if match else ""
        if kind == event_kind:
            return True
    return False


def write_skip_unresolvable_event(
    workspace_root: Path,
    audit_task_id: str,
    reviewed_task_id_raw: str,
) -> None:
    """AIPOS-357: Write a (deduped) skip event when the guardian cannot resolve the
    audited task ID — a special non-R audit card (e.g. AIPOS-346A re-audit card) that
    has no reviewed_task_id and no ``<ID>R`` suffix.

    The guardian does NOT claim and does NOT chew such cards; it skips them once and
    only once (same-kind event dedup via :func:`_has_existing_event_kind`).
    """
    events_dir = workspace_root / "5_tasks" / "records" / "events" / audit_task_id

    if _has_existing_event_kind(events_dir, "skip_unresolvable"):
        log(f"skip_unresolvable 事件已存在, 不重复写: {audit_task_id}")
        return

    events_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    event_file = events_dir / f"skip_unresolvable_{timestamp}.md"

    content = f"""---
record_type: audit_event
event_kind: skip_unresolvable
task_id: {audit_task_id}
reviewed_task_id: {reviewed_task_id_raw or "(空)"}
timestamp: {datetime.now(timezone.utc).isoformat()}
reason: reviewed_task_id_unresolvable
---
# Skip Unresolvable Event: {audit_task_id}

## AIPOS-357 守护队列卫生(特型卡不啃)

守护在认卡前解析被审卡 ID 失败: 审计卡 {audit_task_id} 既无 reviewed_task_id,
也非 `<ID>R` 形态(如 AIPOS-346A 特型补审卡), 无法映射出被审卡 → verdict 判据路径
`5_tasks/records/audit_verdicts/<被审卡>/verdict_*.md` 拼不完整。

守护【不 claim、不啃】, 跳过此卡。若为误判, 顾问可补 `reviewed_task_id` 或规整为
`<ID>R` 形态后重新入队。

此事件由 AIPOS-357 守护自动写入(去重: 同卡同类事件仅一次)。
"""
    event_file.write_text(content, encoding="utf-8")
    log(f"写入 skip_unresolvable 事件: {event_file}")


def write_skip_stale_card_event(
    workspace_root: Path,
    audit_task_id: str,
    reviewed_task_id: str,
    reason: str,
) -> None:
    """AIPOS-351: Write a (deduped, AIPOS-357) skip event when the guardian skips a
    stale R-card whose reviewed task already has a final verdict.

    Dedup (AIPOS-357): if a ``skip_stale_card`` event already exists for this card, the
    guardian is re-polling a card it has already skipped — do not rewrite (fixes the
    320R-class noise of one skip event per poll round).
    """
    events_dir = workspace_root / "5_tasks" / "records" / "events" / audit_task_id

    if _has_existing_event_kind(events_dir, "skip_stale_card"):
        log(f"skip_stale_card 事件已存在, 不重复写: {audit_task_id}")
        return

    events_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    event_file = events_dir / f"skip_stale_card_{timestamp}.md"
    
    content = f"""---
record_type: audit_event
event_kind: skip_stale_card
task_id: {audit_task_id}
reviewed_task_id: {reviewed_task_id}
timestamp: {datetime.now(timezone.utc).isoformat()}
reason: already_has_final_verdict
---
# Skip Stale Card Event: {audit_task_id}

## AIPOS-351 守护队列卫生

守护在认卡前核对发现被审卡 {reviewed_task_id} 已有终态裁决, 不再自领。

原因: {reason}

此事件由 AIPOS-351 守护自动写入, 标记 skip_stale_card (陈卡不啃)。
"""
    
    event_file.write_text(content, encoding="utf-8")
    log(f"写入 skip_stale_card 事件: {event_file}")


def claim_preauthorized(
    gate_client: GateClient,
    auditor_instance: str,
    envelope: str,
    audit_task_id: str,
) -> dict[str, Any]:
    """Attempt a PreAuthorized claim (one-shot, auto-release when structure matches).
    
    Returns: {auto_released: bool, verdict: str, reason: str, claim_id: str, session_id: str}
    Raises GateError for transient gate failures (caller should retry with backoff).
    """
    try:
        resp = gate_client.call_tool("lybra_queue_claim_dry_run", {
            "actor": auditor_instance,
            "agent_instance": auditor_instance,
            "autonomy_mode": "PreAuthorized",
            "owner_policy_ref": envelope,
            "task_id": audit_task_id,
        })
    except GateError as exc:
        # Transient gate errors should be retried by caller
        raise
    
    auto_released = bool(resp.get("preauthorized_release")) and resp.get("autonomy_mode") == "PreAuthorized"
    
    return {
        "auto_released": auto_released,
        "verdict": resp.get("verdict", ""),
        "reason": json.dumps(
            resp.get("blocking_reasons") or resp.get("owner_confirmation_reasons") or resp,
            ensure_ascii=False
        )[:400],
        "claim_id": resp.get("claim_id", ""),
        "session_id": resp.get("active_session_id") or resp.get("last_session_id") or "",
    }


def write_block_file(
    product_repo: Path,
    card_id: str,
    reason: str,
    audit_task_id: str,
    envelope: str,
    auditor_instance: str,
    claim_dump: str,
) -> Path:
    """Write a BLOCK file when the daemon must stop (envelope exhausted / not auto-released).
    
    Returns the BLOCK file path.
    """
    block_dir = product_repo / "task_cards" / card_id
    block_dir.mkdir(parents=True, exist_ok=True)
    
    n = 1
    while (block_dir / f"BLOCK-{n}.md").exists():
        n += 1
    
    block_file = block_dir / f"BLOCK-{n}.md"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    content = f"""# BLOCK — {card_id} auditor loop

- 时间: {timestamp}
- audit 卡: {audit_task_id}  信封: {envelope}  实例: {auditor_instance}
- 触发: {reason}

## 现象 (claim 应答原文)

```
{claim_dump}
```

## 停因分类

PreAuthorized 一发式 claim 未自动放行。可能原因:
- 信封 {envelope} 已达 max_tasks 上限
- 过期 (active_from/expires_at)
- 被撤销
- 任务 selector 不匹配 (task_mode=audit)
- 身份不匹配 (token 绑定实例 ≠ actor)

daemon 不回落 Supervised (无 owner_confirm scope, 宁停勿猜)。

daemon 已 exit {BLOCK_EXIT_CODE} → systemd RestartPreventExitStatus={BLOCK_EXIT_CODE} 生效 → 不自动重启 (防空转)。

## 需要谁的决定

顾问/Owner: 核对信封状态 (5_tasks/policies/{envelope}.md 的 max_tasks / 有效期 / status)。

续跑前: 重新武装或扩展信封, 然后:
  systemctl --user restart lybra-auditor.service

下一棒: 顾问复核 → cat {block_file} && systemctl --user status lybra-auditor.service
"""
    
    block_file.write_text(content, encoding="utf-8")
    log(f"BLOCK 落盘: {block_file}")
    return block_file


def check_verdict_landed(
    workspace_root: Path,
    audit_task_id: str,
    reviewed_task_id: str,
) -> dict[str, Any]:
    """Check if the verdict has actually landed in gate records.

    AIPOS-306 + AIPOS-354: Verify that verdict record exists (单一判据).
    R 卡闭合由 AIPOS-354 S1 机制(verdict 落地即自动闭卡)承担,
    不再作为守护成败条件 — 修掉『每成功一单必自判失败自尽』的根因。

    Returns: {landed: bool, verdict_files: list[Path], card_status: str, reason: str}
    """
    # ① Check verdict record files — 唯一判据
    verdicts_dir = workspace_root / "5_tasks" / "records" / "audit_verdicts" / reviewed_task_id
    verdict_files = []
    if verdicts_dir.is_dir():
        verdict_files = sorted(verdicts_dir.glob("verdict_*.md"))

    has_verdict_record = len(verdict_files) > 0

    # ② Card status — 仅 informational, 不影响 landed 判定
    card_status = "unknown"
    claimed_path = workspace_root / "5_tasks" / "queue" / "claimed" / f"{audit_task_id.lower()}.md"
    completed_path = workspace_root / "5_tasks" / "queue" / "completed" / f"{audit_task_id.lower()}.md"

    if claimed_path.exists():
        card_status = "claimed"
    elif completed_path.exists():
        card_status = "completed"
    else:
        card_status = "not_in_claimed"

    # AIPOS-354: landed = verdict record exists (单一判据, 不再要求卡离开 claimed)
    landed = has_verdict_record

    if not landed:
        reason = f"verdict 记录缺失: {verdicts_dir}/verdict_*.md 不存在"
    else:
        reason = f"verdict 已落地: {len(verdict_files)} 个记录, 卡状态={card_status}(informational)"

    return {
        "landed": landed,
        "verdict_files": verdict_files,
        "card_status": card_status,
        "reason": reason,
    }


def launch_auditor_runtime(
    runtime_cmd_template: str,
    audit_task_id: str,
    reviewed_task_id: str,
    audit_card_path: str,
    product_repo: Path,
    workspace_root: Path,
    envelope: str,
    is_retry: bool = False,
) -> dict[str, Any]:
    """Launch the auditor runtime (blocking, foreground).
    
    AIPOS-306: After agent exit, verify verdict landed. If missing, allow one bounded retry.
    
    Returns: {exit_code: int, verdict_check: dict, retry_exhausted: bool}
    """
    dir_name = reviewed_task_id or audit_task_id.replace("R", "")
    report_path = product_repo / "task_cards" / dir_name / f"AUDIT-REPORT-{audit_task_id}.md"
    
    # AIPOS-330 S2: verb names come from the gate's verb contract registry, never hand-written.
    from tools.aipos_cli.verb_contract import get_verb_contract, validate_kickoff_verbs

    verdict_dry_run = get_verb_contract("lybra_audit_verdict_dry_run")
    verdict_confirm = get_verb_contract("lybra_audit_verdict_confirm")
    verdict_verb_name = "lybra_audit_verdict_dry_run" if verdict_dry_run else "lybra_audit_verdict_dry_run"

    # AIPOS-357: event write root guard — hand the agent an ABSOLUTE workspace path for
    # event files so it never resolves a relative "5_tasks/..." against its product-repo
    # cwd (the misdirect that wrote blocked_verdict_submit into the product repo).
    events_abs_glob = (
        str((workspace_root / "5_tasks" / "records" / "events" / audit_task_id).resolve())
        + "/blocked_*.md"
    )

    if is_retry:
        # Tight kickoff for retry: report exists, only submit verdict
        kickoff = (
            f"有界补跑 (AIPOS-306 守护检测到 verdict_missing, 自动补提交裁决, 全程无人)。"
            f"审计卡 {audit_task_id} 已由 daemon 持有 (信封 {envelope})。"
            f"报告已存在: {report_path}, 结论已定。你【只需补提交裁决】(调用 gate 的 "
            f"{verdict_verb_name} 步骤), 【禁止重做分析】。读取既有报告, 提取结论, 提交裁决即可。"
            f"遇护栏拦截即说明并停。"
            f"\n\n## AIPOS-351 裁决提交失败规范动作"
            f"\n如果 {verdict_verb_name} 返回错误 (STALE_DRY_RUN/DRY_RUN_REQUIRED/SCOPE_DENIED 等):"
            f"\n1. 将完整错误 JSON 贴到报告末尾的 `## 裁决提交失败` 节"
            f"\n2. 写 blocked 事件到 {events_abs_glob} (绝对工作区路径, 勿写入产品仓根)"
            f"\n3. 禁止手写 records (不伪造裁决记录)"
            f"\n4. 如实退出 (非零 exit), 守护会检测到 verdict_missing 并升级"
        )
    else:
        # Normal kickoff
        kickoff = (
            f"冷启动 (auditor daemon {os.getenv('AIPOS_CARD_ID', 'AIPOS-292')} 自动拉起, 全程无人)。"
            f"审计卡 {audit_task_id} 已由 daemon 经预授权信封 {envelope} 一发式认领 "
            f"(autonomy_mode=PreAuthorized), 你【无需再 /claim】——卡已在 claimed 态、由你持有。"
            f"审计准绳=原执行卡 (审计卡 {audit_card_path} 的 reviewed_task_path / reviewed_task_id 指向原卡, "
            f"以原卡为唯一真相)。请按你的 skills (audit-independent-evidence) 独立只读取证, "
            f"逐项 PASS/FAIL+证据, 给结论。报告出口唯一化 (不得自选): 写到 {report_path}。"
            f"审完按你的 write-return 流程如实记录裁决与自报模型/token; 遇护栏拦截即说明并停。"
            f"\n\n## AIPOS-351 裁决提交失败规范动作"
            f"\n如果 {verdict_verb_name} 返回错误 (STALE_DRY_RUN/DRY_RUN_REQUIRED/SCOPE_DENIED 等):"
            f"\n1. 将完整错误 JSON 贴到报告末尾的 `## 裁决提交失败` 节"
            f"\n2. 写 blocked 事件到 {events_abs_glob} (绝对工作区路径, 勿写入产品仓根)"
            f"\n3. 禁止手写 records (不伪造裁决记录)"
            f"\n4. 如实退出 (非零 exit), 守护会检测到 verdict_missing 并升级"
        )

    # AIPOS-330 S2: validate that all lybra_* verbs in kickoff exist in the registry.
    verb_errors = validate_kickoff_verbs(kickoff)
    if verb_errors:
        error_msg = "\n".join(verb_errors)
        log(f"KICKOFF VERB VALIDATION FAILED:\n{error_msg}")
        raise ValueError(f"Kickoff contains invalid verb names: {error_msg}")
    
    # AIPOS-327 S1: Safe kickoff transmission (write to temp file, use @file syntax)
    import tempfile
    
    kickoff_fd, kickoff_temp_path = tempfile.mkstemp(suffix=".txt", prefix="lybra_kickoff_audit_")
    try:
        with os.fdopen(kickoff_fd, "w", encoding="utf-8") as f:
            f.write(kickoff)
    except Exception as exc:
        os.close(kickoff_fd)
        log(f"ERROR: Failed to write kickoff to temp file: {exc}")
        raise
    
    # Replace {kickoff} with @file syntax to avoid shell interpretation
    cmd = runtime_cmd_template.replace("{kickoff}", f"@{kickoff_temp_path}")
    
    retry_label = "[补跑] " if is_retry else ""
    log(f"{retry_label}拉起 auditor: {audit_task_id} → 报告={report_path}")
    log(f"Kickoff written to {kickoff_temp_path} for safe transmission")
    log(f"运行命令: {cmd[:200]}...")
    
    # Execute the runtime command (blocking)
    try:
        result = subprocess.run(cmd, shell=True, cwd=str(product_repo))
    finally:
        # Clean up temp file
        try:
            os.unlink(kickoff_temp_path)
        except OSError:
            pass
    
    log(f"{retry_label}auditor 结束 exit={result.returncode} ({audit_task_id})")
    
    # AIPOS-306: Check if verdict actually landed
    verdict_check = check_verdict_landed(workspace_root, audit_task_id, reviewed_task_id)
    
    return {
        "exit_code": result.returncode,
        "verdict_check": verdict_check,
        "retry_exhausted": is_retry,  # If this was already a retry, we've exhausted our attempts
    }


def write_audit_incomplete_event(
    workspace_root: Path,
    audit_task_id: str,
    reviewed_task_id: str,
    reason: str,
    agent_exit_code: int,
) -> None:
    """Write an audit_incomplete event (AIPOS-306: do not silently report success)."""
    events_dir = workspace_root / "5_tasks" / "records" / "events" / audit_task_id
    events_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    event_file = events_dir / f"audit_incomplete_{timestamp}.md"
    
    content = f"""---
record_type: audit_event
event_kind: audit_incomplete
task_id: {audit_task_id}
reviewed_task_id: {reviewed_task_id}
timestamp: {datetime.now(timezone.utc).isoformat()}
agent_exit_code: {agent_exit_code}
reason: verdict_missing
---
# Audit Incomplete Event: {audit_task_id}

## 现象 (AIPOS-306 守护落地校验)

auditor agent 退出 (exit={agent_exit_code}), 但 verdict 记录未落地:

{reason}

## 后续

守护将执行有界自愈 (补跑一次, tight kickoff 只补提交裁决)。
若补跑仍失败, 将升级为 blocked。

此事件由 AIPOS-306 守护自动写入, 标记 audit_incomplete (禁止谎报 exit=0 成功)。
"""
    
    event_file.write_text(content, encoding="utf-8")
    log(f"写入 audit_incomplete 事件: {event_file}")


def write_verdict_missing_block(
    product_repo: Path,
    audit_task_id: str,
    reviewed_task_id: str,
    first_reason: str,
    retry_reason: str,
    first_exit_code: int,
    retry_exit_code: int | None,
) -> None:
    """Write a BLOCK file when verdict is missing after bounded retry (AIPOS-306)."""
    block_dir = product_repo / "task_cards" / (reviewed_task_id or audit_task_id.replace("R", ""))
    block_dir.mkdir(parents=True, exist_ok=True)
    
    n = 1
    while (block_dir / f"BLOCK-{n}.md").exists():
        n += 1
    
    block_file = block_dir / f"BLOCK-{n}.md"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    retry_section = ""
    if retry_exit_code is not None:
        retry_section = f"""

### 补跑 (有界自愈)

守护执行了一次补跑 (tight kickoff, 只补提交裁决):
- exit={retry_exit_code}
- 校验结果: {retry_reason}

二次仍失败 → 升级为 blocked。
"""
    
    content = f"""# BLOCK — {audit_task_id} verdict_missing

- 时间: {timestamp}
- 审计卡: {audit_task_id}
- 被审卡: {reviewed_task_id}
- 触发: AIPOS-306 守护落地校验

## 现象 (verdict 记录未落地)

### 首次运行

- agent exit={first_exit_code}
- 校验结果: {first_reason}{retry_section}

## 停因分类 (AIPOS-306)

auditor agent 退出后, 守护校验发现:
- `5_tasks/records/audit_verdicts/{reviewed_task_id}/verdict_*.md` 缺失, **或**
- 审计卡 {audit_task_id} 仍在 claimed 态 (未离开)

守护已执行有界自愈 (补跑一次), 仍失败。

**停止原因**: 不无限重试, 升级为 blocked (可见、不静默)。

daemon 已写入 kind:blocked 事件, 已停止处理后续审计卡。

## 需要谁的决定

顾问/Owner: 
1. 检查 agent 为何未完成 write-return 的 lybra_audit_verdict 步骤
2. 手工补提交裁决 (如果报告已产出), 或重新审计
3. 复核守护逻辑 (AIPOS-306 是否有误判)

续跑前: 解决根因, 然后:
  systemctl --user restart lybra-auditor.service

下一棒: 顾问复核 → cat {block_file}
"""
    
    block_file.write_text(content, encoding="utf-8")
    log(f"BLOCK 落盘 (verdict_missing): {block_file}")
    
    # Also write a kind:blocked event
    write_blocked_event(
        Path(str(product_repo).replace("projects/lybra", "ai-project-os/2_projects/lybra")),
        audit_task_id,
        reviewed_task_id,
        first_reason,
        retry_reason,
    )


def write_blocked_event(
    workspace_root: Path,
    audit_task_id: str,
    reviewed_task_id: str,
    first_reason: str,
    retry_reason: str,
) -> None:
    """Write a kind:blocked event (AIPOS-306: visible, not silent)."""
    events_dir = workspace_root / "5_tasks" / "records" / "events" / audit_task_id
    events_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    event_file = events_dir / f"blocked_{timestamp}.md"
    
    content = f"""---
record_type: audit_event
event_kind: blocked
task_id: {audit_task_id}
reviewed_task_id: {reviewed_task_id}
timestamp: {datetime.now(timezone.utc).isoformat()}
reason: verdict_missing_after_retry
---
# Blocked Event: {audit_task_id}

## AIPOS-306 守护升级为 blocked

verdict 记录未落地, 有界自愈 (补跑一次) 仍失败:

- 首次: {first_reason}
- 补跑: {retry_reason}

守护已停止 (exit={BLOCK_EXIT_CODE}), systemd 不会自动重启。

需顾问/Owner 介入。
"""
    
    event_file.write_text(content, encoding="utf-8")
    log(f"写入 blocked 事件: {event_file}")


def run_auditor_loop(
    workspace_root: Path,
    product_repo: Path,
    gate_url: str,
    connection_json: Path,
    auditor_instance: str,
    envelope: str,
    runtime_cmd: str,
    watch_interval: float,
    watch_timeout: float,
    claim_transient_tries: int = 20,
) -> int:
    """Main auditor loop.
    
    Returns exit code: 0=clean exit, 75=BLOCK (envelope exhausted), 1=error.
    """
    # Load auditor token
    try:
        conn_data = json.loads(connection_json.read_text(encoding="utf-8"))
        auditor_token = None
        for item in conn_data.get("tokens", []):
            if isinstance(item, dict) and item.get("role") == "auditor":
                auditor_token = item.get("token", "").strip()
                break
        if not auditor_token:
            log("ERROR: auditor token not found in connection.json")
            return 1
    except (OSError, json.JSONDecodeError) as exc:
        log(f"ERROR: failed to read connection.json: {exc}")
        return 1
    
    gate_client = GateClient(gate_url, auditor_token)
    try:
        gate_client.initialize()
    except GateError as exc:
        log(f"ERROR: failed to initialize gate client: {exc}")
        return 1
    
    log(f"start gate={gate_url} ws={workspace_root} envelope={envelope} instance={auditor_instance}")
    
    # Initial scan (catch any pending audit cards missed during daemon downtime)
    log("启动期首扫 pending audit 卡...")
    rc = process_pending_audits(
        workspace_root,
        product_repo,
        gate_client,
        auditor_instance,
        envelope,
        runtime_cmd,
        claim_transient_tries,
    )
    if rc != 0:
        return rc
    
    # Main loop: watch for changes, then scan and process pending audits
    while True:
        # Watch for queue/records changes (blocking)
        log(f"watch 等待变化 (interval={watch_interval}s, timeout={watch_timeout}s)...")
        watch_rc = run_fs_watch(workspace_root, watch_interval, watch_timeout)
        
        if watch_rc == 130:  # Signal
            log("watch 收到信号, 干净退出")
            return 0
        if watch_rc == 2:  # Timeout
            log("watch 超时, 继续轮询")
            continue
        if watch_rc != 0:
            log(f"watch 异常 rc={watch_rc}, 继续轮询")
            continue
        
        # Change detected, scan and process pending audits
        rc = process_pending_audits(
            workspace_root,
            product_repo,
            gate_client,
            auditor_instance,
            envelope,
            runtime_cmd,
            claim_transient_tries,
        )
        if rc != 0:
            return rc


def process_pending_audits(
    workspace_root: Path,
    product_repo: Path,
    gate_client: GateClient,
    auditor_instance: str,
    envelope: str,
    runtime_cmd: str,
    claim_transient_tries: int,
) -> int:
    """Process all pending audit cards (concurrency limit = 1: sequential blocking).
    
    Returns: 0=success, 75=BLOCK, 1=error.
    """
    pending = find_pending_audit_cards(workspace_root, auditor_instance)
    if not pending:
        return 0
    
    for card in pending:
        audit_task_id = card["task_id"]
        reviewed_task_id_raw = card["reviewed_task_id"]
        audit_card_path = card["path"]

        # AIPOS-357: robustly resolve the audited (reviewed) task ID BEFORE claiming.
        # Special non-R audit cards (e.g. AIPOS-346A re-audit card: no R suffix and no
        # reviewed_task_id) cannot be mapped → the verdict-criterion path
        # (5_tasks/records/audit_verdicts/<被审卡>/verdict_*.md) cannot be assembled.
        # The guardian must SKIP such cards (skip_unresolvable), not claim-and-chew
        # (which previously spun until exit 75 on the unparseable card).
        reviewed_task_id = resolve_reviewed_task_id(audit_task_id, reviewed_task_id_raw)
        if not reviewed_task_id:
            log(
                f"⏭️ 跳过无法解析的特型审计卡 {audit_task_id}: "
                f"reviewed_task_id 为空且非 <ID>R 形态 → skip_unresolvable (不 claim、不啃)"
            )
            write_skip_unresolvable_event(workspace_root, audit_task_id, reviewed_task_id_raw)
            continue  # Skip this card, move to next

        log(f"发现 pending audit 卡: {audit_task_id} (reviewed={reviewed_task_id}) → 先核对队列卫生")

        # AIPOS-351: Check if the reviewed task already has a final verdict
        # before attempting to claim. This prevents the guardian from repeatedly
        # chewing on stale R-cards whose fix chain has already closed.
        verdict_check = check_reviewed_task_has_final_verdict(workspace_root, reviewed_task_id)
        if verdict_check["has_final_verdict"]:
            log(f"⏭️ 跳过陈卡 {audit_task_id}: {verdict_check['reason']}")
            write_skip_stale_card_event(
                workspace_root,
                audit_task_id,
                reviewed_task_id,
                verdict_check["reason"],
            )
            continue  # Skip this card, move to next
        
        log(f"队列卫生通过 → claim")
        
        # Attempt PreAuthorized claim with transient retry
        backoff = 1
        for attempt in range(1, claim_transient_tries + 1):
            try:
                claim_result = claim_preauthorized(gate_client, auditor_instance, envelope, audit_task_id)
                break
            except GateError as exc:
                if attempt >= claim_transient_tries:
                    log(f"claim 暂态重试 {attempt} 次仍失败, exit 1 交 systemd 自愈: {exc}")
                    return 1
                sleep_time = min(backoff * 5, 60)
                log(f"claim 暂态 (attempt={attempt}), 退避 {sleep_time}s 重试: {exc}")
                time.sleep(sleep_time)
                backoff += 1
        
        if not claim_result["auto_released"]:
            # Envelope exhausted or not auto-released → BLOCK
            write_block_file(
                product_repo,
                os.getenv("AIPOS_CARD_ID", "AIPOS-292"),
                "PreAuthorized 一发式 claim 未自动放行 (信封耗尽/不匹配/回落 Supervised)",
                audit_task_id,
                envelope,
                auditor_instance,
                claim_result["reason"],
            )
            return BLOCK_EXIT_CODE
        
        # Claim succeeded, launch auditor runtime (blocking)
        launch_result = launch_auditor_runtime(
            runtime_cmd,
            audit_task_id,
            reviewed_task_id,
            audit_card_path,
            product_repo,
            workspace_root,
            envelope,
            is_retry=False,
        )
        
        verdict_check = launch_result["verdict_check"]
        
        # AIPOS-306: Check if verdict landed
        if not verdict_check["landed"]:
            log(f"⚠️ verdict_missing: {verdict_check['reason']}")
            log(f"发 kind:audit_incomplete 事件 (禁止谎报 exit=0 成功)")
            
            # Write audit_incomplete event
            write_audit_incomplete_event(
                workspace_root,
                audit_task_id,
                reviewed_task_id,
                verdict_check["reason"],
                launch_result["exit_code"],
            )
            
            # AIPOS-306: Bounded self-healing - allow ONE retry (tight kickoff)
            if not launch_result["retry_exhausted"]:
                log(f"有界自愈: 补跑一次 (tight kickoff, 只补提交裁决)")
                retry_result = launch_auditor_runtime(
                    runtime_cmd,
                    audit_task_id,
                    reviewed_task_id,
                    audit_card_path,
                    product_repo,
                    workspace_root,
                    envelope,
                    is_retry=True,
                )
                
                retry_verdict_check = retry_result["verdict_check"]
                
                if retry_verdict_check["landed"]:
                    log(f"✓ 补跑成功: {retry_verdict_check['reason']}")
                    log(f"launch_auditor 返回, 继续扫下一张")
                else:
                    # Second failure: escalate to BLOCK
                    log(f"✗ 补跑仍失败: {retry_verdict_check['reason']}")
                    log(f"升级为 blocked (可见、不静默、不无限重试)")
                    
                    write_verdict_missing_block(
                        product_repo,
                        audit_task_id,
                        reviewed_task_id,
                        verdict_check["reason"],
                        retry_verdict_check["reason"],
                        launch_result["exit_code"],
                        retry_result["exit_code"],
                    )
                    
                    # Exit with BLOCK code (systemd will not auto-restart)
                    return BLOCK_EXIT_CODE
            else:
                # This should not happen in normal flow (retry_exhausted=True only when is_retry=True)
                log(f"✗ verdict_missing 且已是补跑, 升级为 blocked")
                write_verdict_missing_block(
                    product_repo,
                    audit_task_id,
                    reviewed_task_id,
                    verdict_check["reason"],
                    "N/A (已是补跑)",
                    launch_result["exit_code"],
                    None,
                )
                return BLOCK_EXIT_CODE
        else:
            # Normal path: verdict landed successfully
            log(f"✓ verdict 已落地: {verdict_check['reason']}")
            log(f"launch_auditor 返回, 继续扫下一张")
    
    return 0


def run_fs_watch(workspace_root: Path, interval: float, timeout: float) -> int:
    """Run the filesystem watch pump (blocking).
    
    Returns: 0=change detected, 2=timeout, 130=signal, other=error.
    """
    import subprocess
    
    cmd = [
        sys.executable, "-m", "tools.aipos_cli.aipos_cli",
        "agent", "watch",
        "--workspace-root", str(workspace_root),
        "--interval", str(interval),
        "--timeout", str(timeout),
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=False)
        return result.returncode
    except Exception as exc:
        log(f"ERROR: fs watch failed: {exc}")
        return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for `lybra auditor loop`."""
    import argparse
    
    parser = argparse.ArgumentParser(
        prog="lybra auditor loop",
        description="AIPOS-292: Production auditor daemon (idle_agent). "
                    "Watches for pending audit cards, claims via PreAuthorized envelope, "
                    "and launches auditor runtime. Exits 75 when envelope is exhausted (RestartPreventExitStatus)."
    )
    parser.add_argument("--workspace-root", required=True, type=Path, help="Lybra workspace root (治理仓)")
    parser.add_argument("--product-repo", type=Path, help="Product repo root (default: ~/projects/lybra)")
    parser.add_argument("--gate-url", default="http://127.0.0.1:7118", help="Gate URL (default: http://127.0.0.1:7118)")
    parser.add_argument("--connection-json", type=Path, help="Path to connection.json (default: <workspace>/.lybra/connection.json)")
    parser.add_argument("--auditor-instance", default="audit.lybra.kiwiai-dev", help="Auditor instance name")
    parser.add_argument("--policy", "--envelope", dest="envelope", default="pol_lybra_audit_1", help="PreAuthorized envelope/policy ref")
    parser.add_argument(
        "--runtime-cmd",
        default="pi --model anthropic/claude-3-5-sonnet-20241022 --prompt '{kickoff}'",
        help="Auditor runtime command template. Use {kickoff} for the prompt. Default: pi + claude-sonnet-5"
    )
    parser.add_argument("--interval", type=float, default=20.0, help="FS pump watch interval seconds (default: 20)")
    parser.add_argument("--timeout", type=float, default=1800.0, help="FS pump watch timeout seconds (default: 1800)")
    parser.add_argument("--claim-transient-tries", type=int, default=20, help="Transient claim retry attempts (default: 20)")
    
    args = parser.parse_args(argv)
    
    workspace_root = args.workspace_root.expanduser().resolve()
    product_repo = (args.product_repo or Path.home() / "projects" / "lybra").expanduser().resolve()
    connection_json = (args.connection_json or workspace_root / ".lybra" / "connection.json").expanduser().resolve()
    
    if not workspace_root.is_dir():
        print(f"ERROR: workspace-root does not exist: {workspace_root}", file=sys.stderr)
        return 1
    if not product_repo.is_dir():
        print(f"ERROR: product-repo does not exist: {product_repo}", file=sys.stderr)
        return 1
    if not connection_json.is_file():
        print(f"ERROR: connection-json does not exist: {connection_json}", file=sys.stderr)
        return 1
    
    try:
        return run_auditor_loop(
            workspace_root=workspace_root,
            product_repo=product_repo,
            gate_url=args.gate_url,
            connection_json=connection_json,
            auditor_instance=args.auditor_instance,
            envelope=args.envelope,
            runtime_cmd=args.runtime_cmd,
            watch_interval=args.interval,
            watch_timeout=args.timeout,
            claim_transient_tries=args.claim_transient_tries,
        )
    except KeyboardInterrupt:
        log("收到中断信号, 退出")
        return 130
    except Exception as exc:
        log(f"FATAL: {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
