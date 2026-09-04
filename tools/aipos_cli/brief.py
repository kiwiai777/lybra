"""AIPOS-F67: lybra brief — 顾问真相派生(冷启动简报算出来,不写出来)

零新解析器红线: 全部转调既有实现
- records 读取 → tools/aipos_cli/records.py (F55 分组缓存)
- queue 读取 → 既有 lybra_queue_list via confirm_client
- 治理档/decision/stage 解析 → 既有声明解析 (governance_add 用的同一套)
- 阶段快照检查 → finalize.py::check_stage_archive_gate

输出必含五项:
1. 阶段坐标 (最新 stage 快照)
2. 增量真相 (该快照后的 decision_log, 按 status/superseded_by 裁剪)
3. 当前在跑什么 (queue 各态计数 + 在途卡三查)
4. active 契约清单 (治理档按 status 筛选, 标出辖域冲突)
5. 新鲜度自曝 (stage 快照距今多久、多少卡未入快照)
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.schema_loader import get_governance_structure, resolve_governance_path


def _parse_date(date_str: str | None) -> datetime | None:
    """解析日期字符串为 datetime (支持 ISO8601 和 YYYY-MM-DD)。"""
    if not date_str:
        return None
    date_str = str(date_str).strip()
    # Try ISO8601 with timezone
    for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"]:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def _get_stage_snapshot_info(governance_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    """获取最新阶段快照信息 (转调 finalize.py 的 stage gate 逻辑)。
    
    Returns:
        {
            "latest_snapshot": Path | None,
            "snapshot_date": str | None,
            "stage_name": str | None,
            "snapshot_count": int,
            "days_since_snapshot": int | None,
        }
    """
    from tools.aipos_cli.finalize import check_stage_archive_gate
    
    gate_result = check_stage_archive_gate(governance_root, repo_root)
    
    gs = get_governance_structure(repo_root)
    stage_dir = resolve_governance_path("stage_archive", governance_root, repo_root)
    
    if not stage_dir.is_dir():
        return {
            "latest_snapshot": None,
            "snapshot_date": None,
            "stage_name": None,
            "snapshot_count": 0,
            "days_since_snapshot": None,
        }
    
    # 获取所有快照 (排除 README/index) - 按 mtime 排序（最新的在最后）
    snapshots = sorted(
        (p for p in stage_dir.glob("*.md")
         if p.name.lower() not in {"readme.md", "index.md"}),
        key=lambda p: p.stat().st_mtime
    )
    
    if not snapshots:
        return {
            "latest_snapshot": None,
            "snapshot_date": None,
            "stage_name": None,
            "snapshot_count": 0,
            "days_since_snapshot": None,
        }
    
    latest = snapshots[-1]
    
    # 解析 frontmatter
    try:
        fm, _, _ = parse_markdown_frontmatter(latest.read_text(encoding="utf-8"))
        snapshot_date = fm.get("snapshot_date")
        stage_name = fm.get("stage_name")
    except Exception:
        snapshot_date = None
        stage_name = None
    
    # 计算距今天数
    days_since = None
    if snapshot_date:
        dt = _parse_date(snapshot_date)
        if dt:
            now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
            delta = now - dt
            days_since = delta.days
    
    return {
        "latest_snapshot": latest,
        "snapshot_date": snapshot_date,
        "stage_name": stage_name,
        "snapshot_count": len(snapshots),
        "days_since_snapshot": days_since,
    }


def _get_decision_log_entries(
    governance_root: Path,
    since_date: datetime | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """获取 decision_log 条目 (按 status 裁剪, 尊重 superseded_by)。
    
    Args:
        governance_root: 治理工作区根
        since_date: 只返回此日期之后的条目
        repo_root: 产品仓根
    
    Returns:
        按时间排序的 decision 条目列表, 每项含 {path, frontmatter, decided_at_dt}
    """
    try:
        decision_log_dir = resolve_governance_path("decision_log_dir", governance_root, repo_root)
    except Exception as e:
        # Fail-closed: 路径解析失败
        raise ValueError(f"decision_log_dir resolution failed: {e}")
    
    if not decision_log_dir.is_dir():
        # Fail-closed: 目录不存在
        raise ValueError(f"decision_log directory does not exist: {decision_log_dir}")
    
    entries = []
    
    # 遍历 YYYY-MM 子目录
    for month_dir in sorted(decision_log_dir.glob("*")):
        if not month_dir.is_dir():
            continue
        
        for md_file in sorted(month_dir.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                fm, body, _ = parse_markdown_frontmatter(content)
                
                # 解析 decided_at
                decided_at = fm.get("decided_at")
                decided_dt = _parse_date(decided_at)
                
                # since_date 过滤
                if since_date and decided_dt:
                    if decided_dt < since_date:
                        continue
                
                entries.append({
                    "path": md_file,
                    "frontmatter": fm,
                    "body": body,
                    "decided_at_dt": decided_dt,
                })
            except Exception:
                continue
    
    # 按 decided_at 排序
    entries.sort(key=lambda x: x["decided_at_dt"] or datetime.min)
    
    # 按 status 和 superseded_by 裁剪
    active_entries = []
    superseded_map: dict[str, str] = {}  # old_path -> new_path
    
    for entry in entries:
        status = entry["frontmatter"].get("status", "").lower()
        superseded_by = entry["frontmatter"].get("superseded_by")
        
        # 只保留 active 的
        if status == "active":
            active_entries.append(entry)
        
        # 记录 superseded 关系
        if superseded_by:
            old_path = str(entry["path"])
            superseded_map[old_path] = str(superseded_by)
    
    return active_entries


def _get_governance_docs(governance_root: Path, repo_root: Path | None = None) -> list[dict[str, Any]]:
    """获取治理文档清单 (按 status 筛选 active, 标出辖域冲突)。
    
    Returns:
        治理文档列表, 每项含 {path, name, status, jurisdiction, conflicts}
    """
    try:
        governance_docs_dir = resolve_governance_path("governance_docs", governance_root, repo_root)
    except Exception as e:
        # Fail-closed: 路径解析失败
        raise ValueError(f"governance_docs resolution failed: {e}")
    
    if not governance_docs_dir.is_dir():
        # Fail-closed: 目录不存在
        raise ValueError(f"governance_docs directory does not exist: {governance_docs_dir}")
    
    docs = []
    
    for md_file in sorted(governance_docs_dir.glob("*.md")):
        if md_file.name.lower() in {"readme.md", "index.md"}:
            continue
        
        try:
            content = md_file.read_text(encoding="utf-8")
            fm, body, _ = parse_markdown_frontmatter(content)
            
            status = fm.get("status", "").lower()
            jurisdiction = fm.get("jurisdiction", "")
            
            docs.append({
                "path": md_file,
                "name": md_file.name,
                "status": status,
                "jurisdiction": jurisdiction,
                "frontmatter": fm,
            })
        except Exception:
            continue
    
    # 只保留 active 的
    active_docs = [d for d in docs if d["status"] == "active"]
    
    # 检测辖域冲突 (两个 active 文档辖域重叠)
    for doc in active_docs:
        doc["conflicts"] = []
    
    # 简单冲突检测: 如果 jurisdiction 字段有值且相同, 标记冲突
    jurisdiction_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc in active_docs:
        jurisdiction = doc.get("jurisdiction", "").strip()
        if jurisdiction:
            jurisdiction_groups[jurisdiction].append(doc)
    
    for jurisdiction, group in jurisdiction_groups.items():
        if len(group) > 1:
            for doc in group:
                doc["conflicts"] = [other["name"] for other in group if other != doc]
    
    return active_docs


def _resolve_nested_governance_path(key: str, governance_root: Path, repo_root: Path | None = None) -> Path:
    """解析嵌套 relative_to 的治理路径 (e.g., queue relative_to tasks_root)。
    
    resolve_governance_path 只支持一层 relative_to governance_root，
    对于 queue/records/drafts 等需要手动嵌套解析。
    """
    from tools.schema_loader import get_governance_structure
    
    gs = get_governance_structure(repo_root)
    paths = gs.get("paths", {})
    entry = paths.get(key, {})
    
    if not entry:
        raise ValueError(f"Path key '{key}' not found in governance_structure.paths")
    
    relative_to = entry.get("relative_to", "governance_root")
    path_str = str(entry.get("path", "")).strip().strip("/")
    
    if not path_str:
        raise ValueError(f"Path key '{key}' has empty path")
    
    # 嵌套解析
    if relative_to == "governance_root":
        return governance_root / path_str
    else:
        # 递归解析父路径
        parent_path = _resolve_nested_governance_path(relative_to, governance_root, repo_root)
        return parent_path / path_str


def _get_queue_summary(governance_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    """获取队列摘要 (转调 records.py 读取 queue 状态)。
    
    Returns:
        {
            "pending": int,
            "claimed": int,
            "returned": int,
            "completed": int,
            "blocked": int,
            "in_flight": list[dict],  # 在途卡详情
        }
    """
    from tools.aipos_cli.records import load_records
    
    # load_records 的第一个参数是 repo_root (治理工作区根)
    records_data = load_records(governance_root, groups=["claims", "returns", "closures"])
    
    claims = records_data.get("claims", [])
    returns = records_data.get("returns", [])
    closures = records_data.get("closures", [])
    
    # 统计队列状态 (通过扫描 queue 目录) - 使用嵌套路径解析
    try:
        queue_dir = _resolve_nested_governance_path("queue", governance_root, repo_root)
    except Exception as e:
        # Fail-closed: 路径不存在 → 报错而非返回全零
        return {
            "error": f"Queue directory resolution failed: {e}",
            "pending": None,
            "claimed": None,
            "returned": None,
            "completed": None,
            "blocked": None,
            "withdrawn": None,
            "in_flight": [],
        }
    
    if not queue_dir.exists():
        # Fail-closed: 目录不存在 → 报错而非返回全零
        return {
            "error": f"Queue directory does not exist: {queue_dir}",
            "pending": None,
            "claimed": None,
            "returned": None,
            "completed": None,
            "blocked": None,
            "withdrawn": None,
            "in_flight": [],
        }
    
    counts = {}
    for subdir in ["pending", "claimed", "returned", "completed", "blocked", "withdrawn"]:
        subdir_path = queue_dir / subdir
        if subdir_path.is_dir():
            cards = list(subdir_path.glob("*.md"))
            counts[subdir] = len(cards)
        else:
            counts[subdir] = 0
    
    # 在途卡三查 (claimed 但缺某环节的)
    in_flight = []
    claimed_dir = queue_dir / "claimed"
    
    if claimed_dir.is_dir():
        for card_file in sorted(claimed_dir.glob("*.md")):
            try:
                content = card_file.read_text(encoding="utf-8")
                fm, _, _ = parse_markdown_frontmatter(content)
                task_id = fm.get("task_id")
                
                if not task_id:
                    continue
                
                # 检查三环: claim / return / closure
                has_claim = any(c.get("task_id") == task_id for c in claims)
                has_return = any(r.get("task_id") == task_id for r in returns)
                has_closure = any(c.get("task_id") == task_id for c in closures)
                
                missing = []
                if not has_claim:
                    missing.append("claim")
                if not has_return:
                    missing.append("return")
                if not has_closure:
                    missing.append("closure")
                
                if missing:
                    in_flight.append({
                        "task_id": task_id,
                        "missing": missing,
                        "status": fm.get("status"),
                    })
            except Exception:
                continue
    
    return {
        **counts,
        "in_flight": in_flight,
    }


def _count_cards_since_snapshot(
    governance_root: Path,
    snapshot_date: datetime | None,
    repo_root: Path | None = None,
) -> int:
    """统计快照后完成的卡数 (通过 closures 记录)。"""
    if not snapshot_date:
        return 0
    
    from tools.aipos_cli.records import load_records
    
    records_data = load_records(governance_root, groups=["closures"])
    closures = records_data.get("closures", [])
    
    count = 0
    for closure in closures:
        closed_at = closure.get("closed_at")
        closed_dt = _parse_date(closed_at)
        
        if closed_dt and closed_dt > snapshot_date:
            count += 1
    
    return count


def run_brief(
    workspace_root: Path | None = None,
    repo_root: Path | None = None,
    output_format: str = "text",
    since: str | None = None,
) -> int:
    """运行 lybra brief 命令。
    
    Args:
        workspace_root: 治理工作区根 (默认: 当前目录或环境变量)
        repo_root: 产品仓根 (用于读取 schema, 默认: 自动检测)
        output_format: 输出格式 ("text" | "json")
        since: 只显示此日期之后的 decision (YYYY-MM-DD)
    
    Returns:
        退出码 (0=成功)
    """
    # 解析 workspace_root
    if workspace_root is None:
        workspace_root = Path.cwd()
    else:
        workspace_root = Path(workspace_root)
    
    if not workspace_root.is_dir():
        print(f"Error: Workspace root not found: {workspace_root}", file=sys.stderr)
        return 1
    
    # 解析 since 参数
    since_dt = None
    if since:
        since_dt = _parse_date(since)
        if not since_dt:
            print(f"Error: Invalid date format: {since}. Use YYYY-MM-DD.", file=sys.stderr)
            return 1
    
    try:
        # 1. 阶段坐标
        stage_info = _get_stage_snapshot_info(workspace_root, repo_root)
        
        # 2. 增量真相 (decision_log)
        snapshot_date = None
        if stage_info["snapshot_date"]:
            snapshot_date = _parse_date(stage_info["snapshot_date"])
        
        # 使用 since 参数或 snapshot_date (取较晚者)
        filter_date = since_dt
        if snapshot_date and (not filter_date or snapshot_date > filter_date):
            filter_date = snapshot_date
        
        decisions = _get_decision_log_entries(workspace_root, filter_date, repo_root)
        
        # 3. 队列摘要
        queue_summary = _get_queue_summary(workspace_root, repo_root)
        
        # Fail-closed: 检查 queue_summary 是否有错误
        if "error" in queue_summary:
            print(f"Error: {queue_summary['error']}", file=sys.stderr)
            print(f"\nQueue path resolution failed. Please verify:", file=sys.stderr)
            print(f"  - Workspace root: {workspace_root}", file=sys.stderr)
            print(f"  - Expected queue structure: <workspace>/5_tasks/queue/", file=sys.stderr)
            return 1
        
        # 4. 治理文档清单
        governance_docs = _get_governance_docs(workspace_root, repo_root)
        
        # 5. 新鲜度
        cards_since_snapshot = 0
        if snapshot_date:
            cards_since_snapshot = _count_cards_since_snapshot(workspace_root, snapshot_date, repo_root)
        
        # 输出
        if output_format == "json":
            result = {
                "stage": {
                    "latest_snapshot": str(stage_info["latest_snapshot"]) if stage_info["latest_snapshot"] else None,
                    "snapshot_date": str(stage_info["snapshot_date"]) if stage_info["snapshot_date"] else None,
                    "stage_name": stage_info["stage_name"],
                    "snapshot_count": stage_info["snapshot_count"],
                    "days_since_snapshot": stage_info["days_since_snapshot"],
                },
                "decisions": [
                    {
                        "path": str(d["path"]),
                        "decided_at": str(d["frontmatter"].get("decided_at")) if d["frontmatter"].get("decided_at") else None,
                        "status": d["frontmatter"].get("status"),
                        "title": d["path"].stem,
                    }
                    for d in decisions
                ],
                "queue": queue_summary,
                "governance_docs": [
                    {
                        "name": d["name"],
                        "status": d["status"],
                        "jurisdiction": d["jurisdiction"],
                        "conflicts": d["conflicts"],
                    }
                    for d in governance_docs
                ],
                "freshness": {
                    "cards_since_snapshot": cards_since_snapshot,
                    "days_since_snapshot": stage_info["days_since_snapshot"],
                },
            }
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            # Text 输出
            print("=" * 80)
            print("Lybra Brief — 冷启动简报 (算出来, 不写出来)")
            print("=" * 80)
            print()
            
            # 1. 阶段坐标
            print("【1. 阶段坐标】")
            if stage_info["latest_snapshot"]:
                print(f"  当前阶段: {stage_info['stage_name'] or 'N/A'}")
                print(f"  快照日期: {stage_info['snapshot_date'] or 'N/A'}")
                print(f"  快照文件: {stage_info['latest_snapshot'].name}")
                if stage_info["days_since_snapshot"] is not None:
                    print(f"  距今: {stage_info['days_since_snapshot']} 天")
            else:
                print("  ⚠️  无阶段快照 (stage_archive/ 为空)")
            print()
            
            # 2. 增量真相
            print(f"【２. 增量真相】(decision_log, 自快照后 active 条目)")
            if decisions:
                print(f"  共 {len(decisions)} 条:")
                for d in decisions[:20]:  # 最多显示 20 条
                    decided_at_raw = d["frontmatter"].get("decided_at", "")
                    # decided_at 可能是字符串或 datetime 对象
                    if isinstance(decided_at_raw, str):
                        decided_at = decided_at_raw[:10]  # YYYY-MM-DD
                    else:
                        decided_at = str(decided_at_raw)[:10] if decided_at_raw else ""
                    title = d["path"].stem
                    print(f"    - [{decided_at}] {title}")
                if len(decisions) > 20:
                    print(f"    ... 还有 {len(decisions) - 20} 条 (使用 --json 查看全部)")
            else:
                print("  (无)")
            print()
            
            # 3. 队列状态
            print("【3. 当前在跑什么】")
            print(f"  pending:   {queue_summary.get('pending', 0)}")
            print(f"  claimed:   {queue_summary.get('claimed', 0)}")
            print(f"  returned:  {queue_summary.get('returned', 0)}")
            print(f"  completed: {queue_summary.get('completed', 0)}")
            print(f"  blocked:   {queue_summary.get('blocked', 0)}")
            
            if queue_summary["in_flight"]:
                print()
                print("  在途卡缺环 (claimed 但缺记录):")
                for card in queue_summary["in_flight"][:10]:
                    missing_str = ", ".join(card["missing"])
                    print(f"    - {card['task_id']}: 缺 {missing_str}")
                if len(queue_summary["in_flight"]) > 10:
                    print(f"    ... 还有 {len(queue_summary['in_flight']) - 10} 张")
            print()
            
            # 4. 契约文档
            print("【4. Active 契约清单】")
            if governance_docs:
                for doc in governance_docs:
                    conflict_str = ""
                    if doc["conflicts"]:
                        conflict_str = f" ⚠️  辖域冲突: {', '.join(doc['conflicts'])}"
                    print(f"  - {doc['name']}{conflict_str}")
            else:
                print("  (无)")
            print()
            
            # 5. 新鲜度
            print("【5. 新鲜度自曝】")
            if stage_info["days_since_snapshot"] is not None:
                print(f"  距上次快照: {stage_info['days_since_snapshot']} 天")
                print(f"  快照后完成: {cards_since_snapshot} 张卡")
                
                if cards_since_snapshot > 10 or stage_info["days_since_snapshot"] > 14:
                    print("  💡 建议: 运行 `lybra governance add stage` 出新快照")
            else:
                print("  ⚠️  无快照基线, 无法评估新鲜度")
            print()
            
            print("=" * 80)
        
        return 0
        
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
