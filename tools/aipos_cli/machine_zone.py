"""AIPOS-F68: Machine zone derivation and validation.

Machine zone = fields derived from schema declarations that advisors cannot hand-edit.
draft_create generates them from schema; draft_publish validates they match current
schema derivation (preventing advisor hand-edits from creating a second truth source).

All values come through schema_loader (single read interface), no hardcoded literals.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.schema_loader import (
    get_branch_integration,
    get_machine_zone_fields,
)
from tools.aipos_cli.clock import iso_z


def derive_machine_zone_fields(
    metadata: dict[str, Any],
    repo_root: Path,
    *,
    governance_root: Path | None = None,
) -> dict[str, Any]:
    """Derive machine zone field values from schema declarations.
    
    AIPOS-F68: Machine zone fields are generated from schema, never hand-written.
    All values come through schema_loader (no hardcoded paths/branch names).
    
    Args:
        metadata: Task card metadata (for context like task_id, created_by)
        repo_root: Repository root (code repo for schema_loader)
        
    Returns:
        Dictionary of machine zone field values
        
    Example machine zone fields (from card.schema machine_zone.fields):
        - draft_status: "draft"
        - draft_created_by: from metadata.created_by
        - draft_created_at: current timestamp
        - draft_updated_at: current timestamp
        - draft_publish_target: from project.json paths.queue_root (task_loader.queue_state_ref)
    """
    
    machine = {}
    
    # Read machine zone field list from schema (single source)
    machine_fields = get_machine_zone_fields(repo_root)
    
    # Derive each field value from schema declarations
    if "draft_status" in machine_fields:
        machine["draft_status"] = "draft"
    
    if "draft_created_by" in machine_fields:
        created_by = metadata.get("created_by")
        if created_by not in (None, ""):
            machine["draft_created_by"] = created_by
    
    timestamp = iso_z()
    
    if "draft_created_at" in machine_fields:
        machine["draft_created_at"] = timestamp
    
    if "draft_updated_at" in machine_fields:
        machine["draft_updated_at"] = timestamp
    
    if "draft_publish_target" in machine_fields:
        # AIPOS-F89 件① M8: 队列根只读项目声明(project.json paths.queue_root)——唯一读取口 task_loader.queue_root_for;
        # governance_root 缺省 = repo_root(draft_writer 调用方传治理根)。原读 config.schema governance_structure.paths.queue
        # (第二份声明)已删。
        from tools.aipos_cli.task_loader import queue_state_ref

        machine["draft_publish_target"] = queue_state_ref(governance_root or repo_root, "pending")
    
    return machine


def derive_machine_zone_纪律段(
    task_id: str,
    metadata: dict[str, Any],
    governance_root: Path,
    *,
    product_root: Path | None = None,
) -> str:
    """Derive machine-generated 纪律段 (discipline section) from schema.
    
    AIPOS-F68: 纪律段 content is derived from:
    - transitions.schema.json N5.branch_integration (branch pattern, merge strategy)
    - project.json paths(报告落点, 经 next_resolver.card_report_path)
    
    All values read through schema_loader, no hardcoded literals.
    
    AIPOS-F76-R2: Separated governance_root (path resolution) from product_root (schema reading).
    
    Args:
        task_id: Task ID for path substitution
        metadata: Task card metadata (for task_mode, output_target, etc.)
        governance_root: Governance repo root (for path resolution like task_cards/)
        product_root: Product repo root (for schema reading; auto-detected if None)
        
    Returns:
        Markdown string for discipline section
    """
    lines = []
    
    # Read branch integration from transitions.schema (single source)
    try:
        branch_integration = get_branch_integration(product_root)
        branch_pattern = branch_integration.get("branch_pattern")
        if not branch_pattern:
            raise ValueError(
                "transitions.schema.json N5.branch_integration.branch_pattern 声明缺失。"
                "可执行出口: 在 schema 中声明 branch_pattern (如 'card/{task_id}')"
            )
        # Substitute {task_id} placeholder
        branch_name = branch_pattern.replace("{task_id}", task_id)
        
        lines.append("## 工作纪律")
        lines.append("")
        lines.append(f"- **分支**: `{branch_name}` (读自 transitions.schema.json N5.branch_integration.branch_pattern)")
        lines.append("- **工作起点**: 从当前 main 拉取")
        
        # AIPOS-F89 件① H9: 报告落点唯一读取口 next_resolver.card_report_path(执行卡 = project.json paths.return_root,
        # 审计卡 = paths.verdict_root; 文件候选读 transitions artifact_ingest)。原读 config.schema
        # governance_structure.paths.task_cards(第二份声明)已删。声明读取失败 = 向上抛(fail-closed)。
        from tools.aipos_cli.next_resolver import card_report_contract, card_report_path, render_report_frontmatter_clause

        card_fm = {**metadata, "task_id": task_id}
        report_path = card_report_path(governance_root, task_id, card_fm)
        lines.append(f"- **报告落点**: `{report_path}` (读自项目 project.json paths 声明)")
        # AIPOS-F93 件①: 报告必填字段(声明 transitions artifact_ingest 单源渲染, 与派生审计卡 / my-tasks / 认领模板 / 章程同源)
        contract = card_report_contract(governance_root, task_id, card_fm, branch_pattern=branch_pattern)
        lines.append(f"- **报告必填**: {render_report_frontmatter_clause(contract)}")
        
        lines.append("- **治理仓**: 永远停在 main 分支，不 commit")
        lines.append("- **写完停手**: 等待托管/审计，不自行 push")
        
    except ValueError:
        # Re-raise ValueError (fail-closed: schema declaration missing)
        raise
    
    return "\n".join(lines)


def validate_machine_zone_unchanged(
    source_metadata: dict[str, Any],
    repo_root: Path,
) -> tuple[bool, list[str]]:
    """Validate that machine zone fields match current schema derivation.
    
    AIPOS-F68: draft_publish must validate machine zone hasn't been hand-edited.
    Compare source_metadata (from draft file) against fresh derivation from schema.
    
    Args:
        source_metadata: Metadata from draft file (potentially hand-edited)
        repo_root: Repository root
        
    Returns:
        Tuple of (is_valid, list_of_blocking_reasons)
    """
    blocking = []
    
    # Derive fresh machine zone from schema
    expected = derive_machine_zone_fields(source_metadata, repo_root)
    
    # Check each machine zone field
    machine_fields = get_machine_zone_fields(repo_root)
    for field in machine_fields:
        if field not in expected:
            continue  # Field not derived (e.g., conditional fields)
        
        expected_value = expected[field]
        actual_value = source_metadata.get(field)
        
        # For timestamp fields, allow drift (timestamps are generated at creation time)
        # Only validate non-timestamp machine zone fields
        timestamp_fields = {"draft_created_at", "draft_updated_at"}
        if field in timestamp_fields:
            # Skip timestamp validation - they're machine-generated but not stable
            continue
        
        if actual_value != expected_value:
            blocking.append(
                f"机器区字段 {field} 被手改: 期望 {expected_value!r}, 实际 {actual_value!r}. "
                f"机器区由 schema 派生，禁止手动编辑。可执行出口: 删除手改值，重新 draft create"
            )
    
    return (len(blocking) == 0, blocking)


def validate_output_target_coverage(
    metadata: dict[str, Any],
    anchor_refs: list[str],
) -> tuple[bool, list[str]]:
    """Validate that output_target covers all files mentioned in anchor_refs.
    
    AIPOS-F68 大项②: output_target 覆盖度校验 — 锚点对照表提到的文件必须被
    output_target 覆盖，否则拒收（治顾问三次漏列）。
    
    Args:
        metadata: Task card metadata (contains output_target)
        anchor_refs: List of anchor references from governance_refs/anchor_refs
        
    Returns:
        Tuple of (is_valid, list_of_blocking_reasons)
    """
    blocking = []
    
    output_target = str(metadata.get("output_target") or "").strip()
    if not output_target:
        # If no output_target, can't validate coverage (but this should be caught by required field check)
        return (True, [])
    
    # Parse output_target: comma-separated list of paths/patterns
    target_patterns = [p.strip() for p in output_target.split(",")]
    
    # Extract file paths from anchor_refs
    # anchor_refs format: "★锚点对照表: ... → 锚点 `path/to/file.py` ..."
    mentioned_files = []
    for ref in anchor_refs:
        if not isinstance(ref, str):
            continue
        # Simple extraction: look for `...` patterns that look like paths
        import re
        # Match `path/to/file.ext` or `path/to/dir/`
        for match in re.finditer(r'`([a-zA-Z0-9_./+-]+\.[a-zA-Z0-9]+|[a-zA-Z0-9_./+-]+/)`', ref):
            mentioned_files.append(match.group(1))
    
    if not mentioned_files:
        # No files mentioned in anchor_refs, coverage check passes
        return (True, [])
    
    # Check each mentioned file is covered by at least one output_target pattern
    uncovered = []
    for file_path in mentioned_files:
        covered = False
        for pattern in target_patterns:
            # Simple coverage check: file starts with pattern (treating patterns as prefixes)
            # or pattern contains wildcard directory that matches
            if file_path.startswith(pattern.rstrip("/")):
                covered = True
                break
            # Also check if pattern is a parent directory
            if pattern.endswith("/") and file_path.startswith(pattern):
                covered = True
                break
        
        if not covered:
            uncovered.append(file_path)
    
    if uncovered:
        blocking.append(
            f"output_target 覆盖度不足: 锚点对照表提到 {uncovered} 但 output_target 未覆盖. "
            f"可执行出口: 修改 output_target 添加这些路径后重新 publish"
        )
    
    return (len(blocking) == 0, blocking)


# ---------------------------------------------------------------------------
# AIPOS-F78 件①: 意图面声明派生(harness / lane)——create/publish/regen 三口一函数。
# 派生规则只声明在 card.schema.json intent_face 一处; 本模块只读不写死。
# ---------------------------------------------------------------------------

def intent_face_declaration(repo_root: Path | None = None) -> dict[str, Any]:
    """读 card.schema intent_face(缺 = SchemaLoadError, fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("card", repo_root).get("intent_face")
    if not isinstance(decl, dict) or "harness" not in decl or "lane" not in decl:
        raise SchemaLoadError("card.schema.json intent_face(harness/lane 派生表)未声明")
    return decl


def parse_output_target_paths(output_target: str) -> list[str]:
    """从 output_target 文本解析路径片段(唯一解析函数: lane.paths 派生与交回判据 CHANGES_OUT_OF_SCOPE 共用)。

    规则(与 F49 判据②原实现同口径): 取含 `/` 或 `.` 的 token(如 tools/aipos_cli/, tests/, schema/card.schema.json),
    去重保序; 中文说明/括号内容不计。
    """
    import re

    if not output_target:
        return []
    tokens = re.findall(r"([\w/._-]+(?:\.\w+)?)", str(output_target))
    seen: list[str] = []
    for tok in tokens:
        if ("/" in tok or "." in tok) and tok not in seen and not tok.startswith("."):
            seen.append(tok)
    return seen


def default_harness_for_task_mode(task_mode: str, repo_root: Path | None = None) -> str:
    """harness 缺省: card.schema intent_face.harness.default_by_task_mode[task_mode](缺条目 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError

    table = intent_face_declaration(repo_root).get("harness", {}).get("default_by_task_mode") or {}
    value = str(table.get(str(task_mode or "").strip()) or "").strip()
    if not value:
        raise SchemaLoadError(
            f"card.schema.json intent_face.harness.default_by_task_mode 未声明 task_mode={task_mode!r} 的缺省 harness"
        )
    return value


def derive_intent_declarations(
    metadata: dict[str, Any],
    governance_root: Path | None,
    *,
    product_root: Path | None = None,
) -> dict[str, Any]:
    """派生卡意图面的 harness 与 lane(缺则派生, 已有则原样保留)。

    返回 {"harness": str, "lane": {repo, paths, roles}, "derived": [已派生的键], "blocking_reasons": [...]}:
    - harness: 卡面值 ∈ allowed; 缺省 default_by_task_mode[task_mode]。
    - lane.repo: 卡面 lane.repo(校验在 project.json repos 清单内, AIPOS-F78C) → repos.default 仓名 → code_repo 路径 → 治理根自身(workspace_config.default_lane_repo)。
    - lane.paths: 卡面 lane.paths → parse_output_target_paths(output_target); 两者皆空 = LANE_REQUIRED。
    - lane.roles: 卡面 lane.roles → assigned_to/agent_instance 经 roles 注册表解析出的角色类(解析不到 = [])。
    """
    decl = intent_face_declaration(product_root)
    allowed = list(decl.get("harness", {}).get("allowed") or [])
    blocking: list[str] = []
    derived: list[str] = []

    harness = str(metadata.get("harness") or "").strip()
    if not harness:
        harness = default_harness_for_task_mode(str(metadata.get("task_mode") or ""), product_root)
        derived.append("harness")
    if allowed and harness not in allowed:
        blocking.append(
            f"HARNESS_INVALID: harness={harness!r} 不在 card.schema intent_face.harness.allowed {allowed}。"
            f"出口: 改为 {'|'.join(allowed)} 之一"
        )

    raw_lane = metadata.get("lane") if isinstance(metadata.get("lane"), dict) else {}
    lane: dict[str, Any] = {}

    # AIPOS-F78C 件①: lane.repo 派生/校验只经 workspace_config 一处解析(仓清单 repos → 仓名; 无清单 → code_repo 路径; 缺 → 治理根)。
    # 卡面已写 lane.repo(仓名/绝对路径)→ resolve_card_repo 校验其在清单内, 否则 LANE_REPO_UNDECLARED / REPOS_CONFLICT 拒发布。
    repo = str(raw_lane.get("repo") or "").strip()
    if governance_root is not None:
        from tools.aipos_cli.workspace_config import CardRepoUnresolved, default_lane_repo, resolve_card_repo

        try:
            if repo:
                resolve_card_repo(governance_root, {**metadata, "lane": {**raw_lane, "repo": repo}})
            else:
                repo = default_lane_repo(governance_root)
                derived.append("lane.repo")
        except CardRepoUnresolved as exc:
            blocking.append(f"{exc.code}: {exc.reason}")
        except (OSError, ValueError) as exc:
            import sys

            print(f"Warning: project.json unreadable at {governance_root}, lane.repo falls back to governance root: {exc}", file=sys.stderr)
            if not repo:
                repo = str(governance_root)
    lane["repo"] = repo

    paths = raw_lane.get("paths") if isinstance(raw_lane.get("paths"), list) else []
    paths = [str(p).strip() for p in paths if str(p).strip()]
    if not paths:
        paths = parse_output_target_paths(str(metadata.get("output_target") or ""))
        if paths:
            derived.append("lane.paths")
    if not paths:
        blocking.append(
            "LANE_REQUIRED: 卡面无 lane.paths 且 output_target 解析不出任何路径片段(card.schema intent_face.lane.missing_rule)。"
            "出口: 在草稿 frontmatter 声明 lane: {repo, paths: [...], roles: [...]}, 或在 output_target 写明目录/文件路径"
        )
    lane["paths"] = paths

    roles = raw_lane.get("roles") if isinstance(raw_lane.get("roles"), list) else []
    roles = [str(r).strip() for r in roles if str(r).strip()]
    if not roles:
        from tools.aipos_cli.custom_roles import UnknownRoleClass
        from tools.aipos_cli.draft_writer import _card_role_class

        try:
            role_class = _card_role_class(metadata, governance_root)
        except UnknownRoleClass as exc:  # AIPOS-F102 件②: 角色类不可解析 = 拒(派生不出 lane.roles 点名出口), 不静默留空
            role_class = None
            blocking.append(f"LANE_ROLES_UNRESOLVED: 卡面无 lane.roles 且角色类不可解析: {exc}")
        roles = [role_class] if role_class else []
        if roles:
            derived.append("lane.roles")
    lane["roles"] = roles
    if not raw_lane:
        derived.append("lane")

    return {"harness": harness, "lane": lane, "derived": derived, "blocking_reasons": blocking}


# ---------------------------------------------------------------------------
# AIPOS-F133 件①②: lane 键唯一派生 + 按 lane 视图的唯一过滤/分组(next 扫描 / brief / loop status / needs-owner 四命令共用)。
# lane 键 = 卡解析后的产品仓名(workspace_config.resolve_card_repo 解析 → project.json repos.items 中的仓名);
# 无仓清单的单仓项目 = 解析到的仓路径(唯一 lane)。参数声明只在 verbs.schema lane_view 一处; 本段只读声明不写死。
# ---------------------------------------------------------------------------


def lane_view_declaration(repo_root: Path | None = None) -> dict[str, Any]:
    """读 verbs.schema lane_view(--lane 参数与未解析 lane 标签的唯一声明; 缺 = SchemaLoadError, fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("verbs", repo_root).get("lane_view")
    required = ("cli_flag", "help", "commands", "group_by_default", "unresolved_lane", "invalid_lane_exit_code")
    if not isinstance(decl, dict) or any(key not in decl for key in required):
        raise SchemaLoadError(f"verbs.schema.json lane_view 未声明或缺键 {list(required)}")
    return decl


def add_lane_argument(parser: Any) -> None:
    """四命令的 --lane 参数(声明 verbs.schema lane_view.cli_flag / help; argparse 缺省 None = 不过滤)。"""
    decl = lane_view_declaration()
    parser.add_argument(str(decl["cli_flag"]), dest="lane", default=None, metavar=str(decl.get("metavar") or "LANE"),
                        help=str(decl["help"]))


def _lane_name_for_path(governance_root: Path, path: Path, repos: dict[str, Any]) -> str:
    """已解析的仓路径 → lane 键(仓清单内仓名; 无清单 = 路径本身)。"""
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, _same_path

    if not repos["declared"]:
        return str(path)
    for name, item in repos["items"].items():
        if _same_path(item, path):
            return str(name)
    raise CardRepoUnresolved("LANE_REPO_UNDECLARED", f"仓路径 {path} 不在 {governance_root} project.json repos.items 内")


def card_lane_key(metadata: dict[str, Any], governance_root: Path) -> str:
    """AIPOS-F133 件①: 卡的 lane 键唯一派生(四个查看命令与「下一张」共用, 禁各命令自取 lane.repo 字面)。

    解析只走 workspace_config.resolve_card_repo(卡 lane.repo → repos 清单 → code_repo → 治理根, 与发布派生同源);
    返回 project.json repos.items 中的仓名(无清单 = 解析到的仓路径)。解析失败 = CardRepoUnresolved 原样抛(调用方经
    lane_of_card 标为未解析 lane 照列, 不隐藏)。"""
    from tools.aipos_cli.workspace_config import project_repos, resolve_card_repo

    root = Path(governance_root)
    path = resolve_card_repo(root, metadata if isinstance(metadata, dict) else {})
    return _lane_name_for_path(root, path, project_repos(root))


def lane_of_card(metadata: dict[str, Any] | None, governance_root: Path) -> dict[str, Any]:
    """视图行用: {"lane": 键, "lane_error": None} | 解析不了 = {"lane": 声明的未解析标签, "lane_error": "<CODE>: <原因>"}。
    metadata=None(卡面读不出 / 找不到卡)= 未解析(带原因由调用方补)。"""
    from tools.aipos_cli.workspace_config import CardRepoUnresolved

    unresolved = str(lane_view_declaration()["unresolved_lane"])
    if metadata is None:
        return {"lane": unresolved, "lane_error": "卡面不可读或找不到卡, lane 无从解析"}
    try:
        return {"lane": card_lane_key(metadata, governance_root), "lane_error": None}
    except CardRepoUnresolved as exc:
        return {"lane": unresolved, "lane_error": f"{exc.code}: {exc.reason}"}
    except (OSError, ValueError) as exc:  # project.json 读不出 = 未解析 lane(点名原因, 不猜)
        return {"lane": unresolved, "lane_error": f"project.json 读不出: {exc}"}


def declared_lanes(governance_root: Path) -> list[str]:
    """项目声明的全部 lane 键(仓清单仓名; 无清单 = 唯一仓路径)。"""
    from tools.aipos_cli.workspace_config import project_repos, resolve_card_repo

    root = Path(governance_root)
    repos = project_repos(root)
    if repos["declared"]:
        return list(repos["items"])
    return [str(resolve_card_repo(root, {}))]


class LaneFilterInvalid(ValueError):
    """--lane 值不是本项目声明的 lane(fail-closed: 拒, 点名可选值, 不当作「过滤后为空」)。"""


def resolve_lane_filter(governance_root: Path, value: str | None) -> str | None:
    """--lane 值 → 规范 lane 键(仓名或该仓绝对路径均可; 匹配走 workspace_config._match_repo_ref, 与 lane.repo 校验同一判据)。
    None/空 = 不过滤。不在清单 = LaneFilterInvalid(点名可选值)。"""
    from tools.aipos_cli.workspace_config import _match_repo_ref, project_repos

    text = str(value or "").strip()
    if not text:
        return None
    root = Path(governance_root)
    repos = project_repos(root)
    matched = _match_repo_ref(text, repos, root)
    if matched is None:
        raise LaneFilterInvalid(f"--lane {text!r} 不是本项目声明的 lane; 可选: {declared_lanes(root)}"
                                "(project.json repos.items 仓名; 无仓清单 = code_repo 路径)")
    return _lane_name_for_path(root, matched, repos)


def filter_rows_by_lane(rows: list[dict[str, Any]], lane: str | None) -> list[dict[str, Any]]:
    """AIPOS-F133 件②: 四命令同一过滤函数。rows 每项须带 "lane"(经 lane_of_card)。lane=None = 原样;
    lane 给出 = 只留该 lane 与未解析 lane 的行(未解析 = 无法证明不属本 lane, 照列不隐藏)。"""
    if lane is None:
        return list(rows)
    unresolved = str(lane_view_declaration()["unresolved_lane"])
    return [row for row in rows if row.get("lane") in (lane, unresolved)]


def group_rows_by_lane(rows: list[dict[str, Any]], governance_root: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """按 lane 分组(保序): 先项目声明的 lane 次序(给出治理根时), 再其余 lane 字典序, 未解析 lane 末尾。"""
    unresolved = str(lane_view_declaration()["unresolved_lane"])
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get("lane") or unresolved), []).append(row)
    order: list[str] = []
    if governance_root is not None:
        try:
            order = [lane for lane in declared_lanes(Path(governance_root)) if lane in groups]
        except (OSError, ValueError):  # 声明读不出: 分组仍按字典序给出, 不吞行
            order = []
    rest = sorted(lane for lane in groups if lane not in order and lane != unresolved)
    tail = [unresolved] if unresolved in groups else []
    return {lane: groups[lane] for lane in [*order, *rest, *tail]}
