"""AIPOS-R4B-2: Audit helpers — 审计裁决自助落库.

AIPOS-R6C: Renamed from audit_verdict_helper.py to audit_helpers.py for neutral naming.

审计 pi 自发现身份（从 LoopContext/自发现）→ dry_run → confirm → verdict record 落库。
参数从 LoopContext 出，审计 pi 不再要 GateClient snippet。

设计权威: DESIGN v2 §2 N4 (审计自助, 收编FND-15)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.loop_context import ConnectionResolver, LoopContext


def derive_audit_task_id(reviewed_task_id: str, repo_root: Path | None = None) -> str | None:
    """AIPOS-SMOKE-LOOP-1 FIX (坑①): 由被审任务 ID 派生审计 R 卡 task_id。

    CLI `--audit-task-id` 标可选但 gate 动词必填 (LEDGER 08-12 行11)。
    约定:审计 R 卡 task_id 形如 ``{reviewed}R`` / ``{reviewed}R1`` 等,且
    frontmatter ``derived_from == reviewed_task_id`` (gate 派生时盖章)。
    本函数查治理队列找唯一匹配的 R 卡;拿不准(0 或 >1 命中)返回 None,让 CLI 响亮报错。
    """
    from tools.aipos_cli.task_loader import load_all_tasks

    if not reviewed_task_id:
        return None
    candidates = {f"{reviewed_task_id}R", f"{reviewed_task_id}R1"}
    # primary: R 卡的 derived_from 指回被审任务 (gate 派生时盖章,最可靠)
    matches: list[str] = []
    try:
        for task in load_all_tasks(repo_root):
            meta = task.get("metadata", {}) if isinstance(task, dict) else {}
            tid = str(meta.get("task_id") or "")
            if not tid:
                continue
            if str(meta.get("derived_from") or "") == reviewed_task_id:
                matches.append(tid)
                continue
            # fallback: 命名约定 {reviewed}R / {reviewed}R1 且 task_id 以被审 ID 为前缀
            if tid in candidates or (tid.startswith(reviewed_task_id) and tid[len(reviewed_task_id):].rstrip("0123456789") == "R"):
                matches.append(tid)
    except Exception:
        return None
    # 去重;唯一命中才信,否则让 CLI 显式问
    uniq = sorted(set(matches))
    return uniq[0] if len(uniq) == 1 else None


def resolve_audit_context(
    *,
    workspace_root: Path | None = None,
    role: str = "auditor",
    gate_url: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """从 LoopContext 自发现审计身份参数。
    
    AIPOS-R4B-2 N4: 审计 pi 不再手写 GateClient snippet，所有参数从
    Context/自发现出。
    
    Args:
        workspace_root: 工作区根路径（用于 .lybra/ 自发现）
        role: 角色名（默认 auditor）
        gate_url: 显式指定 gate URL（可选，优先级最高）
        token: 显式指定 token（可选，优先级最高）
    
    Returns:
        {
            "gate_url": str,
            "token": str,
            "role": str,
            "agent_instance": str | None,
            "actor": str | None,
            "owner_policy_ref": str | None,
            "source": str,  # "explicit" | "auto_discovery" | "env"
        }
    
    Raises:
        ValueError: 无法解析必要参数
    """
    # Resolve workspace_root
    if workspace_root is None:
        from tools.aipos_cli.workspace_config import resolve_workspace_root
        try:
            workspace_root = resolve_workspace_root()
        except FileNotFoundError:
            # Fallback to current directory
            workspace_root = Path.cwd()
    
    # Resolve gate_url
    resolved_gate_url = ConnectionResolver.resolve_gate_url(
        workspace_root=workspace_root,
        explicit_url=gate_url,
    )
    
    # Resolve token
    resolved_token = ConnectionResolver.resolve_token(
        workspace_root=workspace_root,
        role=role,
        explicit_token=token,
    )
    
    # Try to load connection.json for additional metadata
    agent_instance = None
    actor = None
    owner_policy_ref = None
    source = "auto_discovery"
    
    try:
        lybra_dir = ConnectionResolver.discover_lybra_dir(workspace_root)
        if lybra_dir:
            connection_config = ConnectionResolver.load_connection_config(lybra_dir)
            
            # Find token entry for this role
            tokens = connection_config.get("tokens", [])
            for token_entry in tokens:
                if token_entry.get("role") == role:
                    agent_instance = token_entry.get("agent_instance")
                    actor = token_entry.get("actor") or agent_instance
                    break
            
            # AIPOS-R6C ⑩: policy_ref 自发现全序 (policy_resolver → env → 显式)
            from tools.aipos_cli.policy_resolver import find_active_policy
            owner_policy_ref = find_active_policy(workspace_root, role=role, policy_type="dev")
            
            # Env override if set
            if not owner_policy_ref:
                import os
                owner_policy_ref = os.environ.get("LYBRA_OWNER_POLICY_REF")
    except Exception:
        # Discovery failed, use fallback
        pass
    
    if token and gate_url:
        source = "explicit"
    
    return {
        "gate_url": resolved_gate_url,
        "token": resolved_token,
        "role": role,
        "agent_instance": agent_instance,
        "actor": actor,
        "owner_policy_ref": owner_policy_ref,
        "source": source,
    }


def build_audit_verdict_dry_run_args(
    *,
    reviewed_task_id: str,
    verdict: str,
    context: dict[str, Any],
    audit_task_id: str | None = None,
    findings_summary: str | None = None,
    evidence_refs: list[str] | None = None,
    audit_claim_id: str | None = None,
    audit_session_id: str | None = None,
    audit_dispatch_record_ref: str | None = None,
    reviewed_return_record_ref: str | None = None,
    recommended_next_action: str | None = None,
    owner_waiver_ref: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """构建 audit_verdict dry_run 参数（从 context 填充身份字段）。
    
    Args:
        reviewed_task_id: 被审计任务 ID
        verdict: 裁决（PASS/PASS_WITH_NOTES/FAIL/BLOCK/WARN，从 enums.schema 读取）
        context: 从 resolve_audit_context 得到的上下文
        其他参数: 可选的审计元数据
    
    Returns:
        准备传给 lybra_audit_verdict_dry_run 的参数字典
    """
    args = {
        "reviewed_task_id": reviewed_task_id,
        "actor": context.get("actor") or context.get("agent_instance") or "unknown-auditor",
        "agent_instance": context.get("agent_instance") or context.get("actor") or "unknown-auditor",
        "owner_policy_ref": context.get("owner_policy_ref") or "unknown-policy",
        "autonomy_mode": "Supervised",
        "verdict": verdict,
    }

    # AIPOS-SMOKE-LOOP-1 FIX (坑①): audit_task_id gate 必填但 CLI 标可选 ——
    # 缺省时由 reviewed_task_id 自动派生 (查队列 R 卡),拿不准则不填让 CLI 响亮报错。
    if not audit_task_id:
        audit_task_id = derive_audit_task_id(reviewed_task_id, repo_root)
    # audit_task_id 是 gate 动词必填 (verbs.schema lybra_audit_verdict.task_id: required),
    # 这里始终带上 (派生成功 / 调用者显式给);派生为 None 时 gate 会 BLOCK 并给出明确提示。
    args["audit_task_id"] = audit_task_id or ""
    
    # 添加可选参数 (audit_task_id 已在上面处理)
    if findings_summary:
        args["findings_summary"] = findings_summary
    if evidence_refs:
        args["evidence_refs"] = evidence_refs
    if audit_claim_id:
        args["audit_claim_id"] = audit_claim_id
    if audit_session_id:
        args["audit_session_id"] = audit_session_id
    if audit_dispatch_record_ref:
        args["audit_dispatch_record_ref"] = audit_dispatch_record_ref
    if reviewed_return_record_ref:
        args["reviewed_return_record_ref"] = reviewed_return_record_ref
    if recommended_next_action:
        args["recommended_next_action"] = recommended_next_action
    if owner_waiver_ref:
        args["owner_waiver_ref"] = owner_waiver_ref
    
    return args


def build_audit_verdict_confirm_args(
    *,
    dry_run_token: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """构建 audit_verdict confirm 参数。
    
    Args:
        dry_run_token: dry_run 返回的 token
        context: 从 resolve_audit_context 得到的上下文
    
    Returns:
        准备传给 lybra_audit_verdict_confirm 的参数字典
    """
    return {
        "dry_run_token": dry_run_token,
        "actor": context.get("actor") or context.get("agent_instance") or "unknown-auditor",
        "agent_instance": context.get("agent_instance") or context.get("actor") or "unknown-auditor",
        "owner_policy_ref": context.get("owner_policy_ref") or "unknown-policy",
        "owner_confirmation_token": "OWNER_CONFIRMED",
    }


# ---------------------------------------------------------------------------
# AIPOS-F2: 裁决存在性单源——门生特征校验共享函数
# ---------------------------------------------------------------------------

def is_gate_born_verdict_metadata(metadata: dict) -> bool:
    """AIPOS-F2: 判断裁决 frontmatter 是否具备门生标记(单一声明)。

    门生标记定义来自 schema/transitions.schema.json 的 record_authenticity:
      - record_type: 以 'audit_verdict' 开头
      - verdict_id: 非空且以 'verdict_' 开头
      - verdict_at: 非空(ISO8601 时间戳)

    手写文件(缺少以上任一标记)返回 False。
    此函数是全系统唯一判定:"某条裁决记录是否门生"。
    终态锁(audit_verdict 拒重复提交)、真相选取(sweep/finalize/close 选裁决)、
    依赖校验、派审 ALREADY_PASSED 判定——全部调此函数,不各自实现。

    Args:
        metadata: 已解析的 frontmatter dict(可以是 record dict 的 metadata 字段,
                  也可以直接是从文件解析出的 frontmatter)

    Returns:
        True = 门生记录, False = 手写文件或缺少必要标记
    """
    if not isinstance(metadata, dict):
        return False
    record_type = str(metadata.get("record_type") or "").strip()
    verdict_id = str(metadata.get("verdict_id") or "").strip()
    verdict_at_raw = metadata.get("verdict_at") or metadata.get("timestamp") or ""
    verdict_at = str(verdict_at_raw).strip()

    if not record_type.startswith("audit_verdict"):
        return False
    if not verdict_id or not verdict_id.startswith("verdict_"):
        return False
    if not verdict_at:
        return False
    return True


def is_gate_born_verdict_record(record: dict) -> bool:
    """AIPOS-F2: 判断 load_records() 返回的 verdict record dict 是否门生。

    record 可能是两种结构:
      1. 顶层就有 record_type/verdict_id/verdict_at(records.py _build_record 的 else 分支)
      2. metadata 子 dict 里有这些字段

    本函数两种都查,任一命中即门生。
    """
    if not isinstance(record, dict):
        return False
    # 结构 1: 顶层字段(_build_record else 分支把 verdict_id/verdict_at 放顶层)
    if is_gate_born_verdict_metadata(record):
        return True
    # 结构 2: metadata 子 dict
    nested = record.get("metadata")
    if isinstance(nested, dict) and is_gate_born_verdict_metadata(nested):
        return True
    return False


def detect_hand_written_verdicts(verdicts_dir: Path) -> list[dict]:
    """AIPOS-F2 ③立墙带路: 扫描裁决目录,返回手写文件列表(含原因)。

    用于 audit_verdict dry_run 应答中附加提示:
    "检测到非门生裁决文件已忽略;裁决只经门产生,勿手写落盘"

    Args:
        verdicts_dir: 5_tasks/records/audit_verdicts/<task_id>/ 路径

    Returns:
        [{"file": str, "reason": str}, ...]
    """
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    rejected: list[dict] = []
    if not verdicts_dir.is_dir():
        return rejected
    for vf in sorted(verdicts_dir.glob("*.md")):
        try:
            text = vf.read_text(encoding="utf-8")
            fm, _, _ = parse_markdown_frontmatter(text)
        except Exception:
            rejected.append({"file": vf.name, "reason": "解析失败"})
            continue
        if not is_gate_born_verdict_metadata(fm):
            # 推断原因
            rt = str(fm.get("record_type") or "").strip() if isinstance(fm, dict) else ""
            vid = str(fm.get("verdict_id") or "").strip() if isinstance(fm, dict) else ""
            vat = str(fm.get("verdict_at") or fm.get("timestamp") or "").strip() if isinstance(fm, dict) else ""
            reasons = []
            if not rt.startswith("audit_verdict"):
                reasons.append(f"record_type='{rt}'")
            if not vid or not vid.startswith("verdict_"):
                reasons.append(f"verdict_id='{vid}'")
            if not vat:
                reasons.append("verdict_at 缺失")
            rejected.append({"file": vf.name, "reason": "; ".join(reasons) if reasons else "缺少门生标记"})
    return rejected


HAND_WRITTEN_VERDICT_NOTICE = (
    "检测到非门生裁决文件已忽略;裁决只经门产生,勿手写落盘"
)


# ---------------------------------------------------------------------------
# AIPOS-F72: 派审链有效性判据——manual dispatch 与 auto derivation 同源
# ---------------------------------------------------------------------------

def is_dispatch_chain_valid(
    source_metadata: dict[str, Any],
    existing_verdicts: list[dict[str, Any]],
    repo_root: Path,
) -> tuple[bool, str | None]:
    """AIPOS-F72: 判断派审链是否有效(阻止 re-dispatch)。

    **链有效 ⇔ 以下任一成立**:
    1. 链指向的审计卡状态 ∈ {pending, claimed}(审计在途)
    2. 源卡已有正式裁决(不论审计卡当前状态)

    **链失效 → 放行 re-dispatch**:
    - 审计卡已 concluded/withdrawn **且** 源卡零裁决(如 F63R 废卡场景)

    **Fail-closed**: 审计卡状态不可读/不确定 → 返回 True(拒绝,出声)

    Args:
        source_metadata: 源任务 frontmatter
        existing_verdicts: records['task_audit_verdicts'][source_task_id]
        repo_root: 治理仓根目录

    Returns:
        (is_valid, superseded_audit_ref | None)
        - is_valid=True: 链有效,应 BLOCK re-dispatch
        - is_valid=False: 链失效,放行 re-dispatch;superseded_audit_ref 为旧审计卡引用
    """
    from tools.aipos_cli.task_loader import find_task_by_id

    # 提取链指向的审计卡引用
    audit_ref = str(source_metadata.get("related_audit_task_ref") or "").strip()
    if not audit_ref:
        audit_ref = str(source_metadata.get("audit_dispatch_record_ref") or "").strip()
        # dispatch_record_ref 形如 "5_tasks/records/audit_dispatches/<task>/dispatch_*.md"
        # 从中提取审计卡 ID(需读 dispatch record)
        if audit_ref and "/audit_dispatches/" in audit_ref:
            try:
                dispatch_file = repo_root / audit_ref
                if dispatch_file.exists():
                    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
                    disp_text = dispatch_file.read_text(encoding="utf-8")
                    disp_meta, _, _ = parse_markdown_frontmatter(disp_text)
                    audit_ref = str(disp_meta.get("audit_task_id") or "").strip()
            except Exception:
                # Fail-closed: 不能确定审计卡 ID → 保守拒绝
                return (True, None)

    if not audit_ref:
        # 无链引用 = 未派审,不阻塞
        return (False, None)

    # 检查源卡是否已有裁决
    if existing_verdicts:
        # 已有裁决 → 链有效(不论审计卡状态)
        return (True, None)

    # 零裁决场景:查审计卡实际状态
    try:
        audit_task, matches = find_task_by_id(audit_ref, repo_root)
        if not audit_task:
            # Fail-closed: 审计卡不可读 → 保守拒绝
            return (True, None)

        audit_status = str(audit_task.get("metadata", {}).get("status") or "").strip().lower()
        audit_queue_state = str(audit_task.get("queue_state") or "").strip().lower()

        # 审计卡在途(pending/claimed) → 链有效
        if audit_status in {"pending", "claimed"} or audit_queue_state in {"pending", "claimed"}:
            return (True, None)

        # 审计卡已终结(concluded/withdrawn/completed)且零裁决 → 链失效,放行
        if audit_status in {"concluded", "withdrawn", "completed"} or audit_queue_state in {"concluded", "withdrawn", "completed"}:
            return (False, audit_ref)

        # 其他状态(如 blocked):保守拒绝
        return (True, None)

    except Exception:
        # Fail-closed: 查询失败 → 保守拒绝
        return (True, None)


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
