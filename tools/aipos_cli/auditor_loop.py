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


def launch_auditor_runtime(
    runtime_cmd_template: str,
    audit_task_id: str,
    reviewed_task_id: str,
    audit_card_path: str,
    product_repo: Path,
    envelope: str,
) -> int:
    """Launch the auditor runtime (blocking, foreground).
    
    Returns the exit code of the runtime process.
    """
    dir_name = reviewed_task_id or audit_task_id.replace("R", "")
    report_path = product_repo / "task_cards" / dir_name / f"AUDIT-REPORT-{audit_task_id}.md"
    
    kickoff = (
        f"冷启动 (auditor daemon {os.getenv('AIPOS_CARD_ID', 'AIPOS-292')} 自动拉起, 全程无人)。"
        f"审计卡 {audit_task_id} 已由 daemon 经预授权信封 {envelope} 一发式认领 "
        f"(autonomy_mode=PreAuthorized), 你【无需再 /claim】——卡已在 claimed 态、由你持有。"
        f"审计准绳=原执行卡 (审计卡 {audit_card_path} 的 reviewed_task_path / reviewed_task_id 指向原卡, "
        f"以原卡为唯一真相)。请按你的 skills (audit-independent-evidence) 独立只读取证, "
        f"逐项 PASS/FAIL+证据, 给结论。报告出口唯一化 (不得自选): 写到 {report_path}。"
        f"审完按你的 write-return 流程如实记录裁决与自报模型/token; 遇护栏拦截即说明并停。"
    )
    
    # Expand template variables
    cmd = runtime_cmd_template.replace("{kickoff}", kickoff)
    
    log(f"拉起 auditor: {audit_task_id} → 报告={report_path}")
    log(f"运行命令: {cmd[:200]}...")
    
    # Execute the runtime command (blocking)
    result = subprocess.run(cmd, shell=True, cwd=str(product_repo))
    
    log(f"auditor 结束 exit={result.returncode} ({audit_task_id})")
    return result.returncode


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
        reviewed_task_id = card["reviewed_task_id"]
        audit_card_path = card["path"]
        
        log(f"发现 pending audit 卡: {audit_task_id} (reviewed={reviewed_task_id or '(未知)'}) → claim")
        
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
        launch_auditor_runtime(
            runtime_cmd,
            audit_task_id,
            reviewed_task_id,
            audit_card_path,
            product_repo,
            envelope,
        )
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
