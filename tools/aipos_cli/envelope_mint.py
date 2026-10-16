"""AIPOS-F172 — `lybra envelope mint` 唯一实现(CLI 与看板信封页同调; 原 aipos_cli 内联逻辑原样收口于此, 行为不变)。

- build_mint_payload: 信封 payload 唯一构造(--dry-run 本地预演与 --confirm 门路径同读; 门 envelope 路径只要 decision_id + autonomy_policy)。
  看板调用方另带来源字段(capture_surface / captured_by / decision_rationale, 门 owner_decision_writer 授权记录合成同读), CLI 缺省不带(行为不变)。
- mint_preview: --dry-run = 本地同一 writer(board_adapter.record_owner_decision dry_run=True), 零写入。
- mint_via_gate: --confirm = 逐张经门两阶段签(lybra_owner_decision_record_dry_run → _confirm, OWNER_CONFIRMED), 门生记录经 F158 同一提交口
  governance_commit.land_written_paths 落账。凭据只经 load_role_token(F157 唯一取凭据口)从 connection.json 读, 报告只出指纹。
  不打印: 返回报告(exit_code / error / signed / landing / landing_line), 出声由调用方(CLI 文本 / JSON; 看板应答)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.aipos_cli.clock import iso_z
from tools.schema_constants import Verdict


def build_mint_payload(
    *,
    policy_id: str,
    agent_or_role: str,
    max_tasks: int,
    task_mode: str | None,
    expires_at: str,
    decision_summary: str,
    actor: str,
    launch_harnesses: list[str] | None = None,
    lane_repo: str | list[str] | None = None,
    provenance: dict[str, str] | None = None,
) -> dict[str, Any]:
    """信封 payload 唯一构造。provenance = 可选来源字段 {capture_surface, captured_by, decision_rationale}(看板签发写明来源; 缺省不带)。"""
    from tools.aipos_cli.workspace_config import repo_name_set

    task_selector: dict[str, Any] = {}
    if task_mode:
        task_selector["task_mode"] = task_mode
    lane_repos = repo_name_set(lane_repo)  # AIPOS-F139: 仓集合唯一解析(--lane-repo 可重复)
    if lane_repos:
        # AIPOS-F134 件③: lane 选择器(门 owner_decision_writer._normalize_autonomy_policy 按 resolve_card_repo 逐个校验); 缺省 = 不限 lane
        task_selector["lane_repo"] = lane_repos
    payload: dict[str, Any] = {
        "decision_id": f"envelope-{policy_id}",
        "actor": actor,
        "decided_by_ref": actor,
        "decision_summary": decision_summary,
        "autonomy_policy": {
            "policy_id": policy_id,
            "agent_or_role": agent_or_role,
            "active_from": iso_z(),
            "expires_at": expires_at,
            "max_tasks": max_tasks,
            "task_selector": task_selector,
            # AIPOS-F95 件②(b): 拉起授权(缺省 [] = 只手工 /go); 门 owner_decision_writer 按 enums.schema harness.launch 校验
            "launch_harnesses": list(launch_harnesses or []),
        },
    }
    for key in ("capture_surface", "captured_by", "decision_rationale"):
        value = str((provenance or {}).get(key) or "").strip()
        if value:
            payload[key] = value
    return payload


def mint_preview(payloads: list[dict[str, Any]], *, governance_root: Path, actor: str) -> list[dict[str, Any]]:
    """--dry-run: 每张经本地同一 writer 预演(零写入)。异常(FileNotFoundError / OSError / ValueError)照抛, 调用方出声。"""
    from tools.aipos_cli.board_adapter import record_owner_decision

    return [record_owner_decision(payload, dry_run=True, repo_root=governance_root, actor=actor) for payload in payloads]


def mint_via_gate(
    payloads: list[dict[str, Any]],
    *,
    governance_root: Path,
    actor: str,
    connection_json: str | None,
    token_role: str,
) -> dict[str, Any]:
    """逐张经门两阶段签信封: lybra_owner_decision_record_dry_run(workspace_root=目标治理根, 门按凭据 projects 校验项目范围)
    → _confirm(owner_confirmation_token=OWNER_CONFIRMED; 门另要 owner_confirm scope)。凭据只从 connection.json 读, 只出指纹。
    任一张门拒 = 停在该张(已落的照实列出), exit 1。输出以门生记录为准(performed_writes)。

    返回 report: {ok, exit_code, error, early(凭据 / 门地址未就绪 = True: 未触门, 调用方只出 Error 行), signed, landing, landing_line, ...}。"""
    from tools.aipos_cli.confirm_client import (
        GateAddressError,
        GateClient,
        GateError,
        resolve_gate_base_url,
        token_fingerprint,
    )
    from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN
    from tools.aipos_cli.service_mode import connection_path as workspace_connection_path
    from tools.aipos_cli.two_phase_shell_factory import load_role_token  # AIPOS-F157 件①: 取凭据唯一口(同角色多实例即拒)

    def _early(message: str) -> dict[str, Any]:
        return {"ok": False, "exit_code": 1, "early": True, "error": message, "signed": [], "landing": None, "landing_line": None}

    conn_path = workspace_connection_path(governance_root, connection_target=Path(connection_json) if connection_json else None)
    if not conn_path.is_file():
        return _early(f"凭据文件不存在: {conn_path}(--connection-json 指向持 {token_role} 凭据的 connection.json, 如门的中央凭据库)")
    try:
        token = load_role_token(connection_json_path=str(conn_path), role=token_role)
    except (OSError, ValueError) as exc:
        return _early(f"读不到 {token_role} 凭据({conn_path}): {exc}")
    try:
        # AIPOS-F106 件①: 门基址唯一推导口(委托 ConnectionResolver.resolve_gate_url), 凭据文件须声明 mcp.rpc_url
        base_url = resolve_gate_base_url(connection_json=conn_path, require_declared=True)
    except GateAddressError as exc:
        return _early(str(exc))
    signed: list[dict[str, Any]] = []
    report: dict[str, Any] = {"ok": False, "operation": "envelope_mint", "via": "gate owner_decision_record envelope path",
                              "gate_url": base_url, "credential_role": token_role, "credential_fingerprint": token_fingerprint(token),
                              "governance_root": str(governance_root), "signed": signed, "landing": None,
                              "exit_code": 1, "early": False, "error": None, "landing_line": None}

    def _finish(rc: int, error: str | None = None) -> dict[str, Any]:
        if error:
            report["error"] = error
        # AIPOS-F158 件①: 已签各张的门生记录(performed_writes: 决策记录 / 信封 / decision_log 指针)经 N6 同一提交口落账;
        # 中途门拒 = 已签的照落。落账失败不影响信封生效: 出拒因 + 补账命令, 签发成功时以声明退出码退出(post_write_landing)
        if signed:
            from tools.aipos_cli.governance_commit import land_written_paths
            from tools.schema_loader import SchemaLoadError

            written_all = [p for item in signed for p in item["written"]]
            try:
                landing = land_written_paths(governance_root, written_all, actor,
                                             command="envelope_mint", policy_ids=" ".join(item["policy_id"] for item in signed))
            except SchemaLoadError as exc:  # 落账声明不可读 = 不落账(fail-closed), 信封已生效: 拒因出声, exit 1
                landing = {"landed": False, "exit_code": 1, "reason": str(exc), "paths": written_all,
                           "line": f"⚠ 落账未完成(写入已生效, 勿重复执行写命令): {exc}"}
            report["landing"] = {k: v for k, v in landing.items() if k != "line"}
            report["landing_line"] = landing["line"]
            if rc == 0 and not landing["landed"]:
                rc = landing["exit_code"]
        report["exit_code"] = rc
        return report

    try:
        client = GateClient(base_url, token)
        client.initialize()
        for payload in payloads:
            policy = payload["autonomy_policy"]
            dry = client.call_tool("lybra_owner_decision_record_dry_run", {**payload, "workspace_root": str(governance_root)})
            if dry.get("verdict") == Verdict.BLOCK or not dry.get("ok", False) or not dry.get("dry_run_token"):
                reasons = dry.get("blocking_reasons") or dry.get("errors") or dry.get("message") or dry
                return _finish(1, f"门拒 {policy['policy_id']} 预演: {json.dumps(reasons, ensure_ascii=False)[:800]}")
            done = client.call_tool("lybra_owner_decision_record_confirm", {
                "dry_run_token": dry["dry_run_token"],
                "actor": actor,
                "owner_confirmation_token": OWNER_CONFIRMATION_TOKEN,
                "workspace_root": str(governance_root),
            })
            if not done.get("ok", False):
                reasons = done.get("errors") or done.get("message") or done.get("error_code") or done
                return _finish(1, f"门拒 {policy['policy_id']} 确认: {json.dumps(reasons, ensure_ascii=False)[:800]}")
            writes = [str(w.get("path")) for w in (done.get("performed_writes") or []) if isinstance(w, dict) and w.get("path")]
            written_files = [governance_root / w for w in writes]
            missing = [str(f) for f in written_files if not f.is_file()]
            if not writes or missing:
                return _finish(1, f"门应答成功但记录未见落盘({policy['policy_id']}): writes={writes} missing={missing}")
            signed.append({"policy_id": policy["policy_id"], "agent_or_role": policy["agent_or_role"],
                           "decision_id": payload["decision_id"], "written": writes})
    except GateError as exc:
        return _finish(1, f"门调用失败: {exc}(已签 {len(signed)} 张, 以门生记录为准; 未签的重跑本命令, 已落的 policy_id 会被门拒「already exists」)")
    report["ok"] = True
    return _finish(0)


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
