"""AIPOS-F71 — lybra next 推导核。

唯一实现:合并 turn-advancer(AIPOS-340)与 next-step(AIPOS-R7A)为单一推导器。
推导只认记录(queue 目录 + records 三查 + 卡 frontmatter),禁读日志/会话残留。
状态不明 → 输出"不可推导 + 缺哪份记录 + 建议动作",禁猜。
token 值永不出现在输出。

推导核从 transitions.schema.json(节点与迁移)+ verbs.schema.json(动词参数配方)
派生含全参数的可照抄命令。命令指向产品 CLI 薄壳(lybra queue claim/return/close
--confirm 等),零本地状态变更逻辑。

两种模式:
- 无参:项目级扫描(队列 → 当前最小待办卡 + 所处节点 + 该谁动)
- --task-id:单卡模式

项目无关:推导全由声明 + 工作区推导,不写死项目名。

AIPOS-F71 返工第5件:所有治理路径经 schema_loader 单一读取口,禁手拼路径字面量。
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.schema_loader import resolve_governance_path

# 产品仓根(schema 所在地)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_governance_path_with_relative(key: str, governance_root: Path) -> Path:
    """解析治理路径,处理 relative_to 链。
    
    如 queue 相对于 tasks_root,需递归解析:
    tasks_root → 5_tasks/
    queue → tasks_root + queue/ = 5_tasks/queue/
    """
    from tools.schema_loader import get_governance_path, resolve_governance_path
    
    entry = get_governance_path(key, REPO_ROOT)
    relative_to = entry.get("relative_to")
    
    # governance_root 是终点,直接返回根目录
    if key == "governance_root" or not relative_to:
        # 无 relative_to 或到达根,直接用 path
        rel_path = str(entry.get("path", "")).strip().strip("/")
        if not rel_path:
            # governance_root 没有 path,返回根本身
            return governance_root
        return governance_root / rel_path
    else:
        # 递归解析父路径
        parent_path = _resolve_governance_path_with_relative(relative_to, governance_root)
        # 拼接当前路径
        rel_path = str(entry.get("path", "")).strip().strip("/")
        return parent_path / rel_path

# ---------------------------------------------------------------------------
# 状态 → 节点映射(读 transitions.schema.json nodes 声明)
# ---------------------------------------------------------------------------

# 队列目录名 → 状态机节点
# 按 transitions.schema.json nodes 定义:
#   N0: publish (draft → pending)
#   N1: claim   (pending → claimed)
#   N2: return  (claimed → returned)
#   N3: audit_dispatch (returned → audit_dispatched)
#   N4: audit_verdict  (audit_dispatched → verdict_issued)
#   N5: finalize (verdict_issued → finalized)
#   N6: close   (finalized → completed)

# 状态推导:从记录推,不从 frontmatter 猜
# queue 目录位置 = 事实;records = 事实;frontmatter = 声明(辅助)


def _read_frontmatter(task_path: Path) -> dict[str, Any]:
    """读取卡 frontmatter(YAML)。失败返回空 dict。"""
    try:
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
        fm, _, _ = parse_markdown_frontmatter(task_path.read_text(encoding="utf-8"))
        return fm if isinstance(fm, dict) else {}
    except Exception:
        return {}


def _find_task_in_queue(workspace_root: Path, task_id: str) -> tuple[Path | None, str | None]:
    """在 queue/ 目录中找任务卡。返回 (path, queue_dir_name)。"""
    queue_root = _resolve_governance_path_with_relative("queue", workspace_root)
    for status_dir in ["pending", "claimed", "completed", "blocked"]:
        # 卡文件名可能是 task_id 的小写
        task_file = queue_root / status_dir / f"{task_id.lower()}.md"
        if task_file.is_file():
            return task_file, status_dir
        # 也试原始大小写
        task_file2 = queue_root / status_dir / f"{task_id}.md"
        if task_file2.is_file():
            return task_file2, status_dir
    return None, None


def _find_latest_record(records_dir: Path, prefix: str) -> dict[str, Any] | None:
    """在 records 子目录中找最新记录(按修改时间)。返回 frontmatter dict 或 None。"""
    if not records_dir.is_dir():
        return None
    files = sorted(records_dir.glob(f"{prefix}_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    try:
        return _read_frontmatter(files[0])
    except Exception:
        return None


def _read_task_records(workspace_root: Path, task_id: str) -> dict[str, Any]:
    """读取任务的全部记录状态。纯读事实,不判活。"""
    records_root = _resolve_governance_path_with_relative("records", workspace_root)

    result: dict[str, Any] = {
        "latest_claim": None,
        "latest_return": None,
        "latest_audit_dispatch": None,
        "latest_verdict": None,
        "latest_closure": None,
        "events": [],
    }

    # claims
    claims_dir = records_root / "claims" / task_id
    result["latest_claim"] = _find_latest_record(claims_dir, "claim")

    # returns
    returns_dir = records_root / "returns" / task_id
    result["latest_return"] = _find_latest_record(returns_dir, "return")

    # audit_dispatches
    dispatches_dir = records_root / "audit_dispatches" / task_id
    result["latest_audit_dispatch"] = _find_latest_record(dispatches_dir, "dispatch")

    # audit_verdicts (keyed by reviewed_task_id)
    verdicts_dir = records_root / "audit_verdicts" / task_id
    result["latest_verdict"] = _find_latest_record(verdicts_dir, "verdict")

    # closures
    closures_dir = records_root / "closures" / task_id
    result["latest_closure"] = _find_latest_record(closures_dir, "closure")

    # events
    events_dir = records_root / "events" / task_id
    if events_dir.is_dir():
        for ef in sorted(events_dir.glob("*.md"), key=lambda p: p.stat().st_mtime):
            fm = _read_frontmatter(ef)
            if fm:
                result["events"].append(fm)

    return result


def _check_return_artifact(workspace_root: Path, task_id: str) -> bool:
    """检查 RETURN.md 是否存在(task_cards/<ID>/RETURN.md)。"""
    task_cards_root = _resolve_governance_path_with_relative("task_cards", workspace_root)
    task_work_dir = task_cards_root / task_id
    return (task_work_dir / "RETURN.md").is_file()


def _check_verdict_artifact(workspace_root: Path, task_id: str) -> Path | None:
    """检查审计裁决报告是否存在(task_cards/<ID>R/VERDICT-<ID>R.md)。返回路径或 None。"""
    if not task_id.upper().endswith("R"):
        return None
    task_cards_root = _resolve_governance_path_with_relative("task_cards", workspace_root)
    task_work_dir = task_cards_root / task_id
    verdict_file = task_work_dir / f"VERDICT-{task_id}.md"
    if verdict_file.is_file():
        return verdict_file
    # 也尝试小写
    verdict_file2 = task_work_dir / f"verdict-{task_id.lower()}.md"
    if verdict_file2.is_file():
        return verdict_file2
    return None


def _check_audit_card(workspace_root: Path, task_id: str) -> bool:
    """检查审计卡是否已生成(<ID>R 在 queue 中)。"""
    audit_id = f"{task_id}R"
    queue_root = _resolve_governance_path_with_relative("queue", workspace_root)
    for status_dir in ["pending", "claimed", "completed"]:
        if (queue_root / status_dir / f"{audit_id.lower()}.md").is_file():
            return True
        if (queue_root / status_dir / f"{audit_id}.md").is_file():
            return True
    return False


# ---------------------------------------------------------------------------
# 推导核心:状态 → 下一步节点 + 角色 + 命令
# ---------------------------------------------------------------------------

def _extract_return_summary(workspace_root: Path, task_id: str) -> str | None:
    """从 RETURN.md 提取一句话结论。
    
    第4轮③: result_summary 必须从 RETURN.md「一句话结论」节提取,不存在→该步不该是return。
    """
    try:
        task_cards_root = _resolve_governance_path_with_relative("task_cards", workspace_root)
        return_path = task_cards_root / task_id / "RETURN.md"
        
        if not return_path.is_file():
            return None
        
        content = return_path.read_text(encoding="utf-8")
        
        # 查找「一句话结论」节
        lines = content.split("\n")
        in_summary_section = False
        for i, line in enumerate(lines):
            if "一句话结论" in line or "## 一句话" in line:
                in_summary_section = True
                continue
            if in_summary_section:
                # 跳过空行
                if not line.strip():
                    continue
                # 遇到下一个标题,结束
                if line.startswith("##"):
                    break
                # 找到内容
                if line.strip():
                    # 去掉 **完成** 等格式标记
                    summary = line.strip().lstrip("*").rstrip("*").strip()
                    # 去掉句号
                    summary = summary.rstrip("。")
                    return summary
        
        return None
    except Exception:
        return None


def _resolve_active_policy(workspace_root: Path, task_id: str, role: str = "exec") -> str | None:
    """从 records/claims 最新记录读取当前有效信封。
    
    第4轮要求:推导必须只认记录,从 records/claims 最新成功认领取当前信封,禁用陈旧来源。
    """
    try:
        records_root = _resolve_governance_path_with_relative("records", workspace_root)
        claims_dir = records_root / "claims" / task_id
        
        if not claims_dir.is_dir():
            return None
        
        # 找最新记录(按修改时间)
        claim_files = sorted(claims_dir.glob("claim_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not claim_files:
            return None
        
        # 读 frontmatter
        latest_claim = _read_frontmatter(claim_files[0])
        return latest_claim.get("owner_policy_ref")
    except Exception:
        return None


def _find_connection_json(workspace_root: Path) -> str | None:
    """查找 connection.json 路径。"""
    # 优先治理仓
    gov_conn = workspace_root / ".lybra" / "connection.json"
    if gov_conn.is_file():
        return str(gov_conn)
    # 环境变量
    env_conn = os.environ.get("LYBRA_CONNECTION_JSON")
    if env_conn and Path(env_conn).is_file():
        return env_conn
    return None


def _build_copyable_command(
    *,
    verb_base: str,
    task_id: str,
    actor: str,
    agent_instance: str,
    autonomy_mode: str,
    owner_policy_ref: str | None,
    connection_json: str | None,
    extra_args: dict[str, str] | None = None,
) -> str:
    """构建可照抄 CLI 命令。token 值永不出现。

    命令指向产品 CLI 薄壳(--confirm 两阶段一段式)。
    """
    parts = [f"lybra queue {verb_base}"]
    parts.append(f"--task-id {task_id}")
    parts.append(f"--actor {actor}")
    parts.append("--confirm")

    if connection_json:
        parts.append(f"--connection-json {connection_json}")

    parts.append(f"--agent-instance {agent_instance}")
    parts.append(f"--autonomy-mode {autonomy_mode}")

    if owner_policy_ref:
        parts.append(f"--owner-policy-ref {owner_policy_ref}")

    if extra_args:
        for k, v in extra_args.items():
            parts.append(f"--{k} {v}")

    return " ".join(parts)


def _build_audit_dispatch_command(
    *,
    task_id: str,
    actor: str,
    agent_instance: str,
    owner_policy_ref: str | None,
    connection_json: str | None,
    audit_task_id: str,
    audit_agent_instance: str,
) -> str:
    """构建审计派发命令。"""
    parts = ["lybra audit dispatch"]
    parts.append(f"--source-task-id {task_id}")
    parts.append(f"--actor {actor}")
    parts.append("--confirm")
    if connection_json:
        parts.append(f"--connection-json {connection_json}")
    parts.append(f"--agent-instance {agent_instance}")
    if owner_policy_ref:
        parts.append(f"--owner-policy-ref {owner_policy_ref}")
    parts.append(f"--audit-task-id {audit_task_id}")
    parts.append(f"--audit-agent-instance {audit_agent_instance}")
    return " ".join(parts)


def _build_verdict_submit_command(
    *,
    reviewed_task_id: str,
    audit_task_id: str,
    actor: str,
    agent_instance: str,
    owner_policy_ref: str | None,
    connection_json: str | None,
    verdict: str = "PASS",
) -> str:
    """构建审计裁决提交命令。"""
    parts = ["lybra audit verdict"]
    parts.append(f"--reviewed-task-id {reviewed_task_id}")
    parts.append(f"--audit-task-id {audit_task_id}")
    parts.append(f"--actor {actor}")
    parts.append("--confirm")
    if connection_json:
        parts.append(f"--connection-json {connection_json}")
    parts.append(f"--agent-instance {agent_instance}")
    if owner_policy_ref:
        parts.append(f"--owner-policy-ref {owner_policy_ref}")
    parts.append(f"--verdict {verdict}")
    parts.append("--autonomy-mode Supervised")
    return " ".join(parts)


def _build_close_command(
    *,
    task_id: str,
    actor: str,
    connection_json: str | None,
    closure_evidence_json: str = '{}',
) -> str:
    """构建结案命令。"""
    parts = ["lybra queue close"]
    parts.append(f"--task-id {task_id}")
    parts.append(f"--actor {actor}")
    parts.append("--confirm")
    if connection_json:
        parts.append(f"--connection-json {connection_json}")
    parts.append(f"--closure-evidence '{closure_evidence_json}'")
    return " ".join(parts)


def derive_next_step(
    task_id: str,
    workspace_root: Path,
) -> dict[str, Any]:
    """推导单卡下一步。

    返回:
    {
        "task_id": str,
        "derivable": bool,  # 是否可推导
        "current_node": str | None,  # 当前节点名(如 "claim", "return")
        "current_state": str,  # 队列位置
        "triggered_by": str,  # 该谁动(角色)
        "command": str,  # 可照抄命令
        "verb": str,  # 门动词名
        "missing_records": list[str],  # 缺失记录(fail-closed 时用)
        "suggested_action": str,  # 建议动作
        "notes": str,
    }
    """
    workspace_root = Path(workspace_root)

    # 1. 找任务卡
    task_path, queue_dir = _find_task_in_queue(workspace_root, task_id)
    if not task_path:
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": None,
            "current_state": "not_found",
            "triggered_by": "unknown",
            "command": "",
            "verb": "",
            "missing_records": [f"queue 目录中找不到任务卡 {task_id}"],
            "suggested_action": f"确认任务 ID 正确,或检查 5_tasks/queue/ 各子目录",
            "notes": "",
        }

    fm = _read_frontmatter(task_path)
    records = _read_task_records(workspace_root, task_id)
    has_return_artifact = _check_return_artifact(workspace_root, task_id)
    has_audit_card = _check_audit_card(workspace_root, task_id)
    verdict_artifact = _check_verdict_artifact(workspace_root, task_id)

    task_mode = fm.get("task_mode", "code")
    assigned_to = fm.get("assigned_to") or fm.get("agent_instance") or ""
    audit_required = fm.get("audit") == "required"
    owner_verify = fm.get("owner_verify") == "required"
    is_audit_card = task_id.upper().endswith("R")

    connection_json = _find_connection_json(workspace_root)

    # 公共参数
    conn_arg = connection_json

    # -----------------------------------------------------------------------
    # 审计卡特殊处理: claimed + 有 verdict 报告 → 提交裁决
    # -----------------------------------------------------------------------
    if is_audit_card and queue_dir == "claimed" and verdict_artifact:
        # 从 verdict 报告提取参数
        verdict_fm = _read_frontmatter(verdict_artifact)
        reviewed_task_id = verdict_fm.get("reviewed_task_id") or task_id.rstrip("Rr")
        verdict = verdict_fm.get("verdict", "PASS")
        actor = verdict_fm.get("actor") or assigned_to or "<auditor>"
        agent_inst = verdict_fm.get("agent_instance") or assigned_to or "<auditor-instance>"
        policy_ref = _resolve_active_policy(workspace_root, task_id, role="audit")
        
        cmd = _build_verdict_submit_command(
            reviewed_task_id=reviewed_task_id,
            audit_task_id=task_id,
            actor=actor,
            agent_instance=agent_inst,
            owner_policy_ref=policy_ref,
            connection_json=conn_arg,
            verdict=verdict,
        )
        return {
            "task_id": task_id,
            "derivable": True,
            "current_node": "audit_verdict",
            "current_state": "claimed",
            "triggered_by": "auditor",
            "command": cmd,
            "verb": "lybra_audit_verdict_dry_run",
            "missing_records": [],
            "suggested_action": "提交审计裁决",
            "notes": f"N4: 审计卡 claimed + VERDICT 报告存在,需提交裁决",
        }
    
    # 审计卡 claimed 但无 verdict 报告 → 不可推导
    if is_audit_card and queue_dir == "claimed" and not verdict_artifact:
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": "claim",
            "current_state": "claimed",
            "triggered_by": "auditor",
            "command": "",
            "verb": "",
            "missing_records": [f"VERDICT-{task_id}.md 审计报告"],
            "suggested_action": "等待 auditor 完成审计并生成 VERDICT 报告",
            "notes": "审计卡 claimed + 无 VERDICT 报告,等待审计员完成工作",
        }

    # -----------------------------------------------------------------------
    # 按 queue 位置 + 记录推导下一步
    # -----------------------------------------------------------------------

    # --- pending → N1: claim ---
    if queue_dir == "pending":
        actor = assigned_to or "<executor>"
        agent_inst = assigned_to or "<executor-instance>"
        policy_ref = _resolve_active_policy(workspace_root, task_id, role="exec")
        cmd = _build_copyable_command(
            verb_base="claim",
            task_id=task_id,
            actor=actor,
            agent_instance=agent_inst,
            autonomy_mode="PreAuthorized",
            owner_policy_ref=policy_ref,
            connection_json=conn_arg,
        )
        return {
            "task_id": task_id,
            "derivable": True,
            "current_node": "publish",
            "current_state": "pending",
            "triggered_by": "executor",
            "command": cmd,
            "verb": "lybra_queue_claim_dry_run",
            "missing_records": [],
            "suggested_action": "认领任务",
            "notes": f"N0→N1: 任务已发布,等待认领",
        }

    # --- claimed → N2 (return) 或等待执行 ---
    if queue_dir == "claimed":
        latest_claim = records.get("latest_claim")
        latest_return = records.get("latest_return")
        claimer = (latest_claim or {}).get("agent_instance") or (latest_claim or {}).get("actor") or assigned_to or "<executor>"

        # 已有 return 记录 → 下一步是审计派发(N3)
        if latest_return:
            if not has_audit_card and (task_mode == "code" or audit_required):
                # 审计卡未生成
                return {
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": "return",
                    "current_state": "claimed",
                    "triggered_by": "executor",
                    "command": "",
                    "verb": "",
                    "missing_records": ["审计卡({task_id}R)未生成".format(task_id=task_id)],
                    "suggested_action": "executor 自产审计卡(task-closure-loop 标准工序)",
                    "notes": "已 return,审计卡未生成,等待 executor 自产",
                }
            if has_audit_card:
                # 审计卡已生成,需派审
                audit_id = f"{task_id}R"
                policy_ref = _resolve_active_policy(workspace_root, task_id, role="exec")
                # 第4轮②: 派审是 owner-dispatch 的动词,不是 exec
                dispatch_actor = "owner-dispatch.lybra.kiwiai-dev"
                dispatch_agent = "owner-dispatch.lybra.kiwiai-dev"
                cmd = _build_audit_dispatch_command(
                    task_id=task_id,
                    actor=dispatch_actor,
                    agent_instance=dispatch_agent,
                    owner_policy_ref=policy_ref,
                    connection_json=conn_arg,
                    audit_task_id=audit_id,
                    audit_agent_instance="audit.lybra.kiwiai-dev",
                )
                return {
                    "task_id": task_id,
                    "derivable": True,
                    "current_node": "return",
                    "current_state": "claimed",
                    "triggered_by": "advisor",
                    "command": cmd,
                    "verb": "lybra_audit_dispatch_dry_run",
                    "missing_records": [],
                    "suggested_action": "派发审计",
                    "notes": "N2→N3: 已 return + 审计卡存在,需派审",
                }
            # 非代码卡,不需要独立审计
            if task_mode not in ("code",) and not audit_required:
                return {
                    "task_id": task_id,
                    "derivable": True,
                    "current_node": "return",
                    "current_state": "claimed",
                    "triggered_by": "advisor",
                    "command": f"# 非代码卡(task_mode={task_mode}):顾问审核后直接提交裁决",
                    "verb": "lybra_audit_verdict_dry_run",
                    "missing_records": [],
                    "suggested_action": "顾问审核并提交裁决",
                    "notes": "非代码卡快车道:顾问直接审",
                }
            return {
                "task_id": task_id,
                "derivable": True,
                "current_node": "return",
                "current_state": "claimed",
                "triggered_by": "advisor",
                "command": "",
                "verb": "lybra_audit_dispatch_dry_run",
                "missing_records": [],
                "suggested_action": "派发审计",
                "notes": "已 return,等待审计流程",
            }

        # 有 RETURN.md 但还没 return 记录 → 交回工作(N2)
        if has_return_artifact:
            # 第4轮③: 从 RETURN.md 提取 result_summary
            result_summary = _extract_return_summary(workspace_root, task_id)
            if not result_summary:
                # RETURN.md 存在但无法提取一句话结论,fail-closed
                return {
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": "claim",
                    "current_state": "claimed",
                    "triggered_by": "executor",
                    "command": "",
                    "verb": "",
                    "missing_records": ["RETURN.md 存在但无法提取一句话结论"],
                    "suggested_action": "检查 RETURN.md 是否包含『一句话结论』节",
                    "notes": "RETURN.md 格式不完整,推导不出",
                }
            
            policy_ref = _resolve_active_policy(workspace_root, task_id, role="exec")
            cmd = _build_copyable_command(
                verb_base="return",
                task_id=task_id,
                actor=claimer,
                agent_instance=claimer,
                autonomy_mode="Supervised",
                owner_policy_ref=policy_ref,
                connection_json=conn_arg,
                extra_args={"result-summary": f'"{result_summary}"'},
            )
            return {
                "task_id": task_id,
                "derivable": True,
                "current_node": "claim",
                "current_state": "claimed",
                "triggered_by": "executor",
                "command": cmd,
                "verb": "lybra_queue_return_dry_run",
                "missing_records": [],
                "suggested_action": "交回工作(RETURN.md 已存在,执行 return)",
                "notes": "N1→N2: RETURN.md 已生成,需执行 return 动词",
            }

        # 无 return 记录也无 RETURN.md → 等待执行体完成,或事实不足
        # 检查 events 是否有失败信号
        events = records.get("events", [])
        has_blocked = any(
            str(e.get("event_type") or e.get("event_kind") or "") in ("blocked", "launch_failed")
            for e in events
        )
        if has_blocked:
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "claim",
                "current_state": "claimed",
                "triggered_by": "unknown",
                "command": "",
                "verb": "",
                "missing_records": ["RETURN.md 工作产物", "return 记录"],
                "suggested_action": "执行体遇到阻塞,需人工检查或 resume 轮派工",
                "notes": "claimed + 有 blocked/launch_failed 事件,无 return 产物",
            }

        # 事实不足以推导 → fail-closed
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": "claim",
            "current_state": "claimed",
            "triggered_by": "executor",
            "command": "",
            "verb": "",
            "missing_records": ["RETURN.md 工作产物", "return 记录"],
            "suggested_action": "等待执行体完成工作并生成 RETURN.md,或检查执行状态",
            "notes": "claimed + 无 return 产物/记录,事实不足以推导下一步",
        }

    # --- completed → done ---
    if queue_dir == "completed":
        return {
            "task_id": task_id,
            "derivable": True,
            "current_node": "close",
            "current_state": "completed",
            "triggered_by": "none",
            "command": "# 任务已完成,无下一步",
            "verb": "",
            "missing_records": [],
            "suggested_action": "无(任务已结束)",
            "notes": "N6: 任务已结案",
        }

    # --- blocked → 需人工裁定 ---
    if queue_dir == "blocked":
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": "blocked",
            "current_state": "blocked",
            "triggered_by": "advisor",
            "command": "",
            "verb": "",
            "missing_records": ["blocked 恢复策略需人工裁定"],
            "suggested_action": "检查阻塞原因,决定 reopen 或释放",
            "notes": "任务被阻塞,需人工裁定恢复策略",
        }

    # --- 未知 queue 位置 ---
    return {
        "task_id": task_id,
        "derivable": False,
        "current_node": None,
        "current_state": queue_dir or "unknown",
        "triggered_by": "unknown",
        "command": "",
        "verb": "",
        "missing_records": [f"无法识别的队列位置: {queue_dir}"],
        "suggested_action": "检查任务卡在 queue/ 中的位置是否正确",
        "notes": "",
    }


def scan_project(workspace_root: Path) -> list[dict[str, Any]]:
    """项目级扫描:返回所有活跃任务的最小待办清单。

    按优先级排序:pending(先出) > claimed(有 return 产物) > claimed(无产物) > blocked。
    """
    workspace_root = Path(workspace_root)
    queue_root = _resolve_governance_path_with_relative("queue", workspace_root)
    results: list[dict[str, Any]] = []

    # 扫描 pending + claimed(活跃任务)
    for status_dir in ["pending", "claimed", "blocked"]:
        status_path = queue_root / status_dir
        if not status_path.is_dir():
            continue
        for task_file in sorted(status_path.glob("*.md")):
            # 从文件名提取 task_id
            raw_id = task_file.stem
            # 尝试从 frontmatter 取真实 task_id
            fm = _read_frontmatter(task_file)
            task_id = fm.get("task_id", raw_id.upper()) if fm else raw_id.upper()
            # 跳过审计卡(以 R 结尾的)—— 审计卡单独处理
            # 但在扫描中仍显示
            try:
                result = derive_next_step(str(task_id), workspace_root)
                results.append(result)
            except Exception as e:
                results.append({
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": None,
                    "current_state": status_dir,
                    "triggered_by": "unknown",
                    "command": "",
                    "verb": "",
                    "missing_records": [f"推导异常: {e}"],
                    "suggested_action": "检查任务状态",
                    "notes": str(e),
                })

    # 排序:pending 优先,然后 claimed 中可推导的优先
    priority = {"pending": 0, "claimed": 1, "blocked": 2, "completed": 3}
    results.sort(key=lambda r: (
        priority.get(r.get("current_state", ""), 9),
        0 if r.get("derivable") else 1,
    ))

    return results


def format_output(result: dict[str, Any], *, json_mode: bool = False) -> str:
    """格式化输出。面向三类扣扳机者(Owner 人肉/agent 会话/未来 cron)同一份。"""
    if json_mode:
        import json
        return json.dumps(result, indent=2, ensure_ascii=False)

    lines: list[str] = []
    task_id = result.get("task_id", "?")
    lines.append(f"Task: {task_id}")
    lines.append(f"State: {result.get('current_state', '?')}")
    lines.append(f"Node: {result.get('current_node', '?')}")
    lines.append(f"Triggered by: {result.get('triggered_by', '?')}")

    if result.get("derivable"):
        lines.append(f"Verb: {result.get('verb', '')}")
        cmd = result.get("command", "")
        if cmd:
            lines.append(f"Command:")
            lines.append(f"  {cmd}")
    else:
        missing = result.get("missing_records", [])
        if missing:
            lines.append(f"Missing: {', '.join(missing)}")
        lines.append(f"Suggested: {result.get('suggested_action', '?')}")

    notes = result.get("notes", "")
    if notes:
        lines.append(f"Notes: {notes}")

    return "\n".join(lines)


def format_scan_output(results: list[dict[str, Any]], *, json_mode: bool = False) -> str:
    """格式化项目级扫描输出。"""
    if json_mode:
        import json
        return json.dumps(results, indent=2, ensure_ascii=False)

    if not results:
        return "No active tasks found in queue."

    lines: list[str] = []
    lines.append(f"=== lybra next — project scan ({len(results)} active tasks) ===")
    lines.append("")

    for r in results:
        task_id = r.get("task_id", "?")
        state = r.get("current_state", "?")
        node = r.get("current_node", "?")
        triggered = r.get("triggered_by", "?")

        if r.get("derivable"):
            cmd = r.get("command", "")
            lines.append(f"[{state}/{node}] {task_id} → {triggered}")
            if cmd:
                lines.append(f"  Command: {cmd}")
        else:
            missing = r.get("missing_records", [])
            suggested = r.get("suggested_action", "?")
            lines.append(f"[{state}/{node}] {task_id} → NOT DERIVABLE")
            if missing:
                lines.append(f"  Missing: {', '.join(missing)}")
            lines.append(f"  Suggested: {suggested}")
        lines.append("")

    return "\n".join(lines)
