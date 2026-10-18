"""AIPOS-F188 件③ — 同仓车道重叠提示(判定唯一实现; 发卡与 loop 共用)。

一句话: 两张在途卡要改同一块代码时, 发卡与 loop 起跑(认领前)由产品主动提示先串行, 不再靠顾问记忆纪律(10-16 F167/F168/F169
并行同改命令注册段撞 BRANCH_WRONG_BASE / MERGE_CONFLICT)。只提示不拒(Owner 10-16 定)。

声明: verbs.schema 顶层 lane_overlap(在途状态 inflight_states / 文案 texts / 出现处); 豁免前缀 = project.json lane_overlap.exempt_path_prefixes
(唯一读取口 workspace_config.lane_overlap_exemptions, 缺省 tests/ 与 docs/)。判据:
  - 同仓: 两卡各经 workspace_config.resolve_card_repo(按卡取仓唯一解析)解析后同一路径;
  - 路径相交: lane.paths 前缀按路径段比较, 一方是另一方的前缀即相交, 相交路径 = 更具体的那个; 相交路径落在豁免前缀下 = 不报;
  - 在途: inflight_states 队列目录(task_loader.iter_queue_task_paths 卡遍历唯一口)里的执行卡(审计卡不改代码, 不判)。
本卡缺 lane.paths / 仓解析不了 / 豁免声明形坏 = 不判, notes 写明原因(不猜、不吞); 他卡缺 lane.paths / 仓解析不了 = 跳过该卡。只读。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

DECLARATION_KEY = "lane_overlap"


def overlap_declaration() -> dict[str, Any]:
    """verbs.schema 顶层 lane_overlap(inflight_states / texts.warning / texts.invalid)唯一读取口。缺 / 形不合 = SchemaLoadError。"""
    from tools.aipos_cli.task_loader import QUEUE_STATES, QUEUE_TERMINAL_STATES
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = load_schema("verbs", code_repo_schema_root()).get(DECLARATION_KEY)
    states = decl.get("inflight_states") if isinstance(decl, dict) else None
    texts = decl.get("texts") if isinstance(decl, dict) else None
    if (not isinstance(states, list) or not states
            or not all(isinstance(s, str) and s in QUEUE_STATES and s not in QUEUE_TERMINAL_STATES for s in states)
            or not isinstance(texts, dict) or not all(isinstance(texts.get(k), str) and texts[k] for k in ("warning", "invalid"))):
        raise SchemaLoadError(f"verbs.schema.json {DECLARATION_KEY}(inflight_states ⊆ 非终态队列目录 / texts.warning / texts.invalid)未声明齐")
    return decl


def _segments(prefix: str) -> tuple[str, ...]:
    """路径前缀 → 路径段(去首尾空白、前导 ./ 与空段); "" / "." / "./" = ()(整仓)。"""
    return tuple(part for part in str(prefix).strip().split("/") if part and part != ".")


def _within(path: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(path) >= len(prefix) and path[:len(prefix)] == prefix


def _intersect(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...] | None:
    """两前缀相交 = 一方在另一方之下; 相交路径 = 更具体的那个。不相交 = None。"""
    if _within(a, b):
        return a
    if _within(b, a):
        return b
    return None


def _lane_paths(fm: dict[str, Any]) -> list[str]:
    lane = fm.get("lane") if isinstance(fm.get("lane"), dict) else {}
    return [str(p) for p in (lane.get("paths") or []) if isinstance(p, str) and p.strip()]


def _render_path(segments: tuple[str, ...]) -> str:
    return "/".join(segments) + "/" if segments else "(整仓)"


def lane_overlaps(governance_root: str | Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> dict[str, Any]:
    """本卡(task_id + 卡面 frontmatter, 发卡时 = 派生后的发布稿, loop 时 = 待认领卡面)与在途卡的车道重叠(唯一判定)。
    返回 {task_id, repo, overlaps: [{other, other_state, repo, paths, line}], notes: [原因行], exempt: {exempt_path_prefixes, source}}。
    只读; 声明缺 = SchemaLoadError 上抛(fail-closed)。"""
    from tools.aipos_cli.audit_derivation import is_audit_card
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.next_resolver import card_base_branch
    from tools.aipos_cli.task_loader import iter_queue_task_paths
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, LaneOverlapDeclarationError, lane_overlap_exemptions, resolve_card_repo

    decl = overlap_declaration()
    texts = decl["texts"]
    root = Path(governance_root)
    fm = dict(card_frontmatter or {})
    task_id = str(task_id or fm.get("task_id") or "").strip()
    fm["task_id"] = task_id
    out: dict[str, Any] = {"task_id": task_id, "repo": None, "overlaps": [], "notes": [], "exempt": None}

    def note(reason: str) -> dict[str, Any]:
        out["notes"].append(str(texts["invalid"]).format(reason=reason))
        return out

    if is_audit_card(task_id, fm):
        return out  # 审计卡不改代码, 不判(不出声)
    own_paths = _lane_paths(fm)
    if not own_paths:
        return note(f"卡 {task_id} 无 lane.paths(不猜车道)")
    try:
        exempt = lane_overlap_exemptions(root)
    except LaneOverlapDeclarationError as exc:
        return note(str(exc))
    out["exempt"] = exempt
    exempt_segs = [_segments(p) for p in exempt["exempt_path_prefixes"]]
    exempt_segs = [e for e in exempt_segs if e]  # 空前缀(整仓)不作豁免, 否则一切不报
    try:
        repo = resolve_card_repo(root, fm).resolve()
    except CardRepoUnresolved as exc:
        return note(f"卡 {task_id} 仓解析不了({exc.code}): {exc}")
    out["repo"] = str(repo)
    base = card_base_branch()
    own = [_segments(p) for p in own_paths]
    for path in iter_queue_task_paths(root, states=tuple(decl["inflight_states"])):
        try:
            other_fm, _body = require_frontmatter(path)
        except (FrontmatterReadError, OSError):
            continue  # 他卡卡面读不出: 不判该卡(state lint 另报), 不阻断本卡提示
        other = str(other_fm.get("task_id") or "").strip()
        if not other or other == task_id or is_audit_card(other, other_fm):
            continue
        other_paths = _lane_paths(other_fm)
        if not other_paths:
            continue
        try:
            other_repo = resolve_card_repo(root, other_fm).resolve()
        except CardRepoUnresolved:
            continue
        if other_repo != repo:
            continue
        hits: list[str] = []
        for mine in own:
            for theirs in (_segments(p) for p in other_paths):
                shared = _intersect(mine, theirs)
                if shared is None or any(_within(shared, e) for e in exempt_segs):
                    continue
                rendered = _render_path(shared)
                if rendered not in hits:
                    hits.append(rendered)
        if not hits:
            continue
        state = path.parent.name
        line = str(texts["warning"]).format(task_id=task_id, other=other, other_state=state, repo=repo, paths=", ".join(hits), base=base)
        out["overlaps"].append({"other": other, "other_state": state, "repo": str(repo), "paths": hits, "line": line})
    return out


def render_lines(result: dict[str, Any]) -> list[str]:
    """提示行(发卡 Warnings / loop 输出 / loop status 同一文案): 每条重叠一行 + 未判原因行。"""
    return [item["line"] for item in result.get("overlaps") or []] + list(result.get("notes") or [])
