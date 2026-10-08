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
from datetime import datetime
from tools.aipos_cli.clock import local_now, utc_now
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
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


def _get_stage_snapshot_info(governance_root: Path, repo_root: Path | None = None,
                             unreadable: list[str] | None = None) -> dict[str, Any]:
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
    # AIPOS-F100 件②: 读不出不省略——收进 unreadable(展示「读不出: <路径>: <原因>」)
    try:
        fm, _ = require_frontmatter(latest, allow_missing_block=True)
        snapshot_date = fm.get("snapshot_date")
        stage_name = fm.get("stage_name")
    except FrontmatterReadError as exc:
        if unreadable is not None:
            unreadable.append(str(exc))
        snapshot_date = None
        stage_name = None
    
    # 计算距今天数
    days_since = None
    if snapshot_date:
        dt = _parse_date(snapshot_date)
        if dt:
            now = utc_now() if dt.tzinfo else local_now()
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
    unreadable: list[str] | None = None,
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
                fm, body = require_frontmatter(md_file, allow_missing_block=True)
                
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
            except FrontmatterReadError as exc:
                if unreadable is not None:
                    unreadable.append(str(exc))
                continue
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


def _get_governance_docs(governance_root: Path, repo_root: Path | None = None,
                         unreadable: list[str] | None = None) -> list[dict[str, Any]]:
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
            fm, body = require_frontmatter(md_file, allow_missing_block=True)
            
            status = fm.get("status", "").lower()
            jurisdiction = fm.get("jurisdiction", "")
            
            docs.append({
                "path": md_file,
                "name": md_file.name,
                "status": status,
                "jurisdiction": jurisdiction,
                "frontmatter": fm,
            })
        except FrontmatterReadError as exc:
            if unreadable is not None:
                unreadable.append(str(exc))
            continue
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


def _queue_dir_states() -> tuple[str, ...]:
    """AIPOS-F104 件②: 队列目录名 = task_loader.QUEUE_STATES(enums queue_state queue_dir 投影; returned 为记录推导态, 无目录)。"""
    from tools.aipos_cli.task_loader import QUEUE_STATES

    return QUEUE_STATES


def _get_queue_summary(governance_root: Path, repo_root: Path | None = None,
                       unreadable: list[str] | None = None, *, lane: str | list[str] | None = None) -> dict[str, Any]:
    """获取队列摘要 (转调 records.py 读取记录; 卡遍历只走 task_loader.iter_queue_task_paths, AIPOS-F133 件①)。

    AIPOS-F133 件②: 每张卡的 lane 经唯一派生 machine_zone.lane_of_card; lane 给出 = 经 filter_rows_by_lane 过滤
    (四命令同一函数); lanes = 按 lane 分组的同形摘要(group_rows_by_lane)。

    Returns:
        {
            <task_loader.QUEUE_STATES 各目录名>: int,  # pending/claimed/completed/blocked/withdrawn
            "in_flight": list[dict],  # 在途卡详情
            "lane_filter": list[str] | None,  # AIPOS-F139: --lane 仓集合(规范 lane 键)
            "lanes": {<lane>: {<各目录名>: int, "in_flight": list[dict]}},
        }
    """
    from tools.aipos_cli.machine_zone import filter_rows_by_lane, group_rows_by_lane, lane_of_card
    from tools.aipos_cli.records import load_records
    from tools.aipos_cli.task_loader import iter_queue_task_paths

    # load_records 的第一个参数是 repo_root (治理工作区根)
    records_data = load_records(governance_root, groups=["claims", "returns", "closures"])

    claims = records_data.get("claims", [])
    returns = records_data.get("returns", [])
    closures = records_data.get("closures", [])

    # 队列根解析(唯一读取口 task_loader.queue_root_for); 解析失败/目录不在 = fail-closed 报错而非返回全零
    try:
        from tools.aipos_cli.task_loader import queue_root_for  # AIPOS-F89 件① M8: 队列根唯一读取口

        queue_dir = queue_root_for(governance_root)
    except Exception as e:
        return {
            "error": f"Queue directory resolution failed: {e}",
            **{state: None for state in _queue_dir_states()},
            "in_flight": [],
        }

    if not queue_dir.exists():
        return {
            "error": f"Queue directory does not exist: {queue_dir}",
            **{state: None for state in _queue_dir_states()},
            "in_flight": [],
        }

    rows: list[dict[str, Any]] = []
    for card_file in iter_queue_task_paths(Path(governance_root)):
        try:
            fm, _ = require_frontmatter(card_file)
        except FrontmatterReadError as exc:
            if unreadable is not None:
                unreadable.append(str(exc))
            fm = None  # 读不出的卡仍计数(按所在目录), lane = 未解析(照列不隐藏)
        rows.append({
            "task_id": (fm or {}).get("task_id"),
            "queue_state": card_file.parent.name,
            "status": (fm or {}).get("status"),
            **lane_of_card(fm, Path(governance_root)),
        })
    rows = filter_rows_by_lane(rows, lane)

    def _summary(subset: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {state: sum(1 for r in subset if r["queue_state"] == state) for state in _queue_dir_states()}
        # 在途卡三查 (claimed 但缺某环节的)
        in_flight = []
        for r in subset:
            task_id = r.get("task_id")
            if r["queue_state"] != "claimed" or not task_id:
                continue
            missing = []
            if not any(c.get("task_id") == task_id for c in claims):
                missing.append("claim")
            if not any(x.get("task_id") == task_id for x in returns):
                missing.append("return")
            if not any(c.get("task_id") == task_id for c in closures):
                missing.append("closure")
            if missing:
                in_flight.append({"task_id": task_id, "missing": missing, "status": r.get("status"), "lane": r.get("lane")})
        return {**counts, "in_flight": in_flight}

    lanes = {name: _summary(subset) for name, subset in group_rows_by_lane(rows, Path(governance_root)).items()}
    lane_errors = sorted({f"{r.get('task_id') or '?'}: {r['lane_error']}" for r in rows if r.get("lane_error")})
    return {**_summary(rows), "lane_filter": lane, "lanes": lanes, "lane_errors": lane_errors}


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
    lane: str | list[str] | None = None,
) -> int:
    """运行 lybra brief 命令。
    
    Args:
        workspace_root: 治理工作区根 (默认: 当前目录或环境变量)
        repo_root: 产品仓根 (用于读取 schema, 默认: 自动检测)
        output_format: 输出格式 ("text" | "json")
        since: 只显示此日期之后的 decision (YYYY-MM-DD)
        lane: AIPOS-F133 件②: 只看该 lane(machine_zone.resolve_lane_filter 校验); 缺省 = 全部并按 lane 分组
    
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
    
    # AIPOS-F133 件②: --lane 校验(不在声明 = 拒, 点名可选值; 退出码读 verbs.schema lane_view.invalid_lane_exit_code)
    from tools.aipos_cli.machine_zone import LaneFilterInvalid, lane_filter_label, lane_view_declaration, resolve_lane_filter

    try:
        lane = resolve_lane_filter(workspace_root, lane)
    except LaneFilterInvalid as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return int(lane_view_declaration()["invalid_lane_exit_code"])

    try:
        # 1. 阶段坐标
        unreadable: list[str] = []  # AIPOS-F100 件②: 读不出的文件不省略, 末节逐条列出
        stage_info = _get_stage_snapshot_info(workspace_root, repo_root, unreadable)
        
        # 2. 增量真相 (decision_log)
        snapshot_date = None
        if stage_info["snapshot_date"]:
            snapshot_date = _parse_date(stage_info["snapshot_date"])
        
        # 使用 since 参数或 snapshot_date (取较晚者)
        filter_date = since_dt
        if snapshot_date and (not filter_date or snapshot_date > filter_date):
            filter_date = snapshot_date
        
        decisions = _get_decision_log_entries(workspace_root, filter_date, repo_root, unreadable)
        
        # 3. 队列摘要
        queue_summary = _get_queue_summary(workspace_root, repo_root, unreadable, lane=lane)
        
        # Fail-closed: 检查 queue_summary 是否有错误
        if "error" in queue_summary:
            print(f"Error: {queue_summary['error']}", file=sys.stderr)
            print(f"\nQueue path resolution failed. Please verify:", file=sys.stderr)
            print(f"  - Workspace root: {workspace_root}", file=sys.stderr)
            print(f"  - Expected queue structure: <workspace>/5_tasks/queue/", file=sys.stderr)
            return 1
        
        # 4. 治理文档清单
        governance_docs = _get_governance_docs(workspace_root, repo_root, unreadable)
        
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
                "unreadable": unreadable,
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
            print("【3. 当前在跑什么】" + (f"(lane {lane_filter_label(lane)})" if lane else ""))  # AIPOS-F139: 集合显示
            for state in _queue_dir_states():  # AIPOS-F104 件②: 队列目录唯一投影(原写死含恒为 0 的 returned 行、漏 withdrawn)
                print(f"  {state + ':':<10} {queue_summary.get(state, 0)}")
            
            if queue_summary["in_flight"]:
                print()
                print("  在途卡缺环 (claimed 但缺记录):")
                for card in queue_summary["in_flight"][:10]:
                    missing_str = ", ".join(card["missing"])
                    print(f"    - {card['task_id']}: 缺 {missing_str}")
                if len(queue_summary["in_flight"]) > 10:
                    print(f"    ... 还有 {len(queue_summary['in_flight']) - 10} 张")
            if not lane:
                # AIPOS-F133 件②: 不带 --lane 时按 lane 分组(总顾问/Owner 一眼看各子项目)
                print()
                print("  按 lane:")
                for name, part in queue_summary.get("lanes", {}).items():
                    counts = " ".join(f"{state}={part.get(state, 0)}" for state in _queue_dir_states())
                    gap = f"  在途缺环 {len(part['in_flight'])}" if part["in_flight"] else ""
                    print(f"    - {name}: {counts}{gap}")
            for line in queue_summary.get("lane_errors", [])[:10]:
                print(f"  lane 不可解析: {line}")
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

            if unreadable:
                print(f"【6. 读不出的文件】(共 {len(unreadable)} 份, 上文统计未计入)")
                for line in unreadable:
                    print(f"  - {line}")
                print()
            
            print("=" * 80)
        
        return 0
        
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
