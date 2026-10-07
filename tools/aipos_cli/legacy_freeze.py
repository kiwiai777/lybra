"""AIPOS-F122 件②: `lybra project freeze-legacy` 选卡干跑(零写入)——按卡号 / queue 目录选卡, 列每张卡的 queue 目录与卡面 status
快照及当前 state lint 问题计数。是否已冻结读 legacy_baseline.frozen_tasks(唯一判定); 落条目在 legacy_baseline.write_freeze_entry。
单列本模块: 依赖 state lint(计数), 不放进被 workspace_config 懒导入的 legacy_baseline(finalize 合并后预载闭包须轻)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.aipos_cli.legacy_baseline import (
    ACTION_FREEZE,
    ACTION_UNFREEZE,
    LEGACY_UNKNOWN_TASK,
    LegacyBaselineError,
    default_manifest_dir,
    frozen_tasks,
    manifest_location,
)


def _queue_index(governance_root: Path, states: tuple[str, ...] | None = None) -> dict[str, list[tuple[str, Path]]]:
    """队列卡列举(唯一实现 state_lint.queue_task_index), 卡号归一为大写(冻结判定 / lint / next 一律按大写比对);
    仅大小写不同的两张卡归到同一键 = 多义(下游拒)。"""
    from tools.aipos_cli.state_lint import queue_task_index

    index: dict[str, list[tuple[str, Path]]] = {}
    for task_id, located in queue_task_index(governance_root, states).items():
        index.setdefault(task_id.upper(), []).extend(located)
    return index


def _card_status(path: Path) -> str | None:
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    try:
        meta, _body, warnings = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    if warnings or not isinstance(meta, dict):
        return None
    return str(meta.get("status") or "").strip() or None


def _snapshot(task_id: str, located: tuple[str, Path]) -> dict[str, Any]:
    state, path = located
    return {"task_id": task_id, "queue_state": state, "card_status": _card_status(path)}


def plan_freeze_legacy(
    governance_root: Path,
    *,
    task_ids: list[str] | None = None,
    queues: list[str] | None = None,
    exclude: list[str] | None = None,
    unfreeze: list[str] | None = None,
) -> dict[str, Any]:
    """干跑(零写入): 选卡 → {action, selected: [快照+当前 lint 问题计数], already: [...], ...}。
    未知卡号 / 多义 / 未知 queue = LegacyBaselineError(整条命令拒)。"""
    from tools.aipos_cli.task_loader import QUEUE_STATES

    root = Path(governance_root)
    modes = [m for m in (task_ids, queues, unfreeze) if m]
    if len(modes) != 1:
        raise LegacyBaselineError(LEGACY_UNKNOWN_TASK, "须且只能给 --task-ids / --queue / --unfreeze 之一(且非空)")
    frozen = frozen_tasks(root)
    index = _queue_index(root)
    action = ACTION_UNFREEZE if unfreeze else ACTION_FREEZE
    if queues:
        unknown_states = [q for q in queues if q not in QUEUE_STATES]
        if unknown_states:
            raise LegacyBaselineError(LEGACY_UNKNOWN_TASK, f"--queue {unknown_states} 不是 queue 目录(声明 {list(QUEUE_STATES)})")
        scoped = _queue_index(root, tuple(queues))
        excluded = {str(t).strip().upper() for t in (exclude or []) if str(t).strip()}
        unknown_excl = sorted(t for t in excluded if t not in index)
        if unknown_excl:
            raise LegacyBaselineError(LEGACY_UNKNOWN_TASK, f"--exclude 卡号不在任何 queue 目录: {unknown_excl}")
        wanted = sorted(t for t in scoped if t not in excluded)
    else:
        wanted = sorted({str(t).strip().upper() for t in (task_ids or unfreeze or []) if str(t).strip()})
        excluded = set()
    unknown = [t for t in wanted if t not in index]
    if unknown:
        raise LegacyBaselineError(LEGACY_UNKNOWN_TASK, f"卡号不在任何 queue 目录: {unknown}(整条命令拒)")
    ambiguous = {t: [str(p) for _s, p in index[t]] for t in wanted if len(index[t]) > 1}
    if ambiguous:
        raise LegacyBaselineError(LEGACY_UNKNOWN_TASK, f"同号多文件多义(禁): {ambiguous}")
    if action == ACTION_FREEZE:
        todo = [t for t in wanted if t not in frozen]
        already = [t for t in wanted if t in frozen]
    else:
        todo = [t for t in wanted if t in frozen]
        already = [t for t in wanted if t not in frozen]
    from tools.aipos_cli.state_lint import run_state_lint

    lint = run_state_lint(root, apply_legacy_baseline=False)
    counts: dict[str, int] = {}
    for issue in lint["issues"]:
        key = str(issue.get("task_id") or "").upper()
        counts[key] = counts.get(key, 0) + 1
    selected = [{**_snapshot(t, index[t][0]), "lint_issues": counts.get(t, 0)} for t in todo]
    return {
        "action": action,
        "governance_root": str(root),
        "selected": selected,
        "already": already,
        "excluded": sorted(excluded),
        "manifest_dir": str(manifest_location(root) or default_manifest_dir(root)),
        "manifest_declared": manifest_location(root) is not None,
        "lint_issues_total": sum(item["lint_issues"] for item in selected),
    }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
