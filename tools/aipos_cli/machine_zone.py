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
        from tools.aipos_cli.next_resolver import (
            CandidateSubjectError, card_candidate_subject, card_report_contract, card_report_path, render_report_frontmatter_clause,
        )

        card_fm = {**metadata, "task_id": task_id}
        # AIPOS-F196 件①: 固定候选卡(validation)不建卡分支 / 工作树, 不写产品仓源码(enums.schema task_mode.candidate_subject)
        try:
            candidate = card_candidate_subject(card_fm)
            candidate_problem = ""
        except CandidateSubjectError as exc:
            candidate, candidate_problem = None, str(exc)
        if candidate is not None or candidate_problem:
            subject = (f"来源卡 {candidate['source']} 的固定候选 `{candidate['candidate']}`" if candidate
                       else f"(固定候选不合: {candidate_problem})")
            lines.append(f"- **分支**: 不建(交付主体 = {subject}; 不写产品仓源码, 产物只落验证根与报告落点; 判据 enums.schema task_mode.candidate_subject)")
            lines.append("- **工作起点**: 固定候选(只读取证; 验证根 run-start 的候选须等于卡面候选)")
        else:
            lines.append(f"- **分支**: `{branch_name}` (读自 transitions.schema.json N5.branch_integration.branch_pattern)")
            lines.append("- **工作起点**: 从当前 main 拉取")
        
        # AIPOS-F89 件① H9: 报告落点唯一读取口 next_resolver.card_report_path(执行卡 = project.json paths.return_root,
        # 审计卡 = paths.verdict_root; 文件候选读 transitions artifact_ingest)。原读 config.schema
        # governance_structure.paths.task_cards(第二份声明)已删。声明读取失败 = 向上抛(fail-closed)。
        report_path = card_report_path(governance_root, task_id, card_fm)
        lines.append(f"- **报告落点**: `{report_path}` (读自项目 project.json paths 声明)")
        # AIPOS-F93 件①: 报告必填字段(声明 transitions artifact_ingest 单源渲染, 与派生审计卡 / my-tasks / 认领模板 / 章程同源)
        try:
            contract = card_report_contract(governance_root, task_id, card_fm, branch_pattern=branch_pattern)
            lines.append(f"- **报告必填**: {render_report_frontmatter_clause(contract)}")
        except CandidateSubjectError as exc:  # 起草 / 发卡另有拒因(candidate_subject_refusals), 此处只如实写明
            lines.append(f"- **报告必填**: 不可推导(固定候选不合: {exc}; 发卡前须修正)")
        
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
                # AIPOS-F197 件①: 「只写治理根」保留值只给不写产品仓的模式(writes_product_repo=false 且非固定候选)
                from tools.aipos_cli.next_resolver import task_mode_subject_is_candidate, task_mode_writes_product_repo
                from tools.aipos_cli.workspace_config import governance_root_lane_ref

                mode = str(metadata.get("task_mode") or "")
                if repo == governance_root_lane_ref() and (task_mode_writes_product_repo(mode) or task_mode_subject_is_candidate(mode)):
                    blocking.append(f"LANE_REPO_UNDECLARED: lane.repo={repo!r}(只写治理根)不适用于 task_mode={mode or '(缺)'}"
                                    "(该模式写产品仓或交付主体为固定候选); 出口: lane.repo 改写产品仓名, 或改 task_mode 为 docs / governance / config")
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
    required = ("cli_flag", "help", "commands", "group_by_default", "unresolved_lane", "invalid_lane_exit_code", "visible_cards")
    visible_keys = ("include_frozen_flag", "include_frozen_help", "frozen_marker", "frozen_summary", "frozen_error_summary",
                    "unresolved_summary", "unresolved_list_limit")
    if not isinstance(decl, dict) or any(key not in decl for key in required) \
            or not isinstance(decl.get("visible_cards"), dict) or any(key not in decl["visible_cards"] for key in visible_keys):
        raise SchemaLoadError(f"verbs.schema.json lane_view 未声明或缺键 {list(required)}(visible_cards: {list(visible_keys)})")
    return decl


def add_lane_argument(parser: Any) -> None:
    """四命令的视图参数: --lane(声明 verbs.schema lane_view.cli_flag / help; argparse 缺省 None = 不过滤)。
    AIPOS-F139: 可重复(`--lane a --lane b` = 仓集合, 与 `lybra envelope mint --lane-repo` 同一写法)。
    AIPOS-F141 件①: 同处挂 --include-frozen(声明 lane_view.visible_cards.include_frozen_flag; 缺省 False = 冻结卡不列)。"""
    decl = lane_view_declaration()
    parser.add_argument(str(decl["cli_flag"]), dest="lane", action="append", default=None, metavar=str(decl.get("metavar") or "LANE"),
                        help=str(decl["help"]))
    visible = decl["visible_cards"]
    parser.add_argument(str(visible["include_frozen_flag"]), dest="include_frozen", action="store_true", default=False,
                        help=str(visible["include_frozen_help"]))


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


def resolve_lane_filter(governance_root: Path, value: str | list[str] | None) -> list[str] | None:
    """--lane 值(单个或可重复给出的仓集合)→ 规范 lane 键列表(仓名或该仓绝对路径均可; 匹配走 workspace_config._match_repo_ref,
    与 lane.repo 校验同一判据; 集合解析唯一 workspace_config.repo_name_set)。None/空 = 不过滤。
    集合内任一不在清单 = LaneFilterInvalid(点名该值与可选值; 不按「过滤后为空」处理)。"""
    from tools.aipos_cli.workspace_config import _match_repo_ref, project_repos, repo_name_set

    names = repo_name_set(value)
    if not names:
        return None
    root = Path(governance_root)
    repos = project_repos(root)
    keys: list[str] = []
    for text in names:
        matched = _match_repo_ref(text, repos, root)
        if matched is None:
            raise LaneFilterInvalid(f"--lane {text!r} 不是本项目声明的 lane; 可选: {declared_lanes(root)}"
                                    "(project.json repos.items 仓名; 无仓清单 = code_repo 路径)")
        keys.append(_lane_name_for_path(root, matched, repos))
    return repo_name_set(keys)


def lane_filter_label(lane: str | list[str] | None) -> str:
    """lane 过滤集合的显示文字(标题/提示用; 集合解析唯一 repo_name_set): 单仓 = 仓名本身, 多仓 = 逗号连接。空 = ""。"""
    from tools.aipos_cli.workspace_config import repo_name_set

    return ", ".join(repo_name_set(lane))


def filter_rows_by_lane(rows: list[dict[str, Any]], lane: str | list[str] | None,
                        *, set_aside: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """AIPOS-F133 件② / F141 件②: lane 过滤唯一实现(四命令经 visible_cards 调用)。rows 每项须带 "lane"(经 lane_of_card)。
    lane=None/空 = 原样(未解析 lane 的行照列, 分组输出不变); lane 给出 = 只留 lane ∈ 所给集合(F139: 仓集合)的行——lane 解析不了的行
    不混入所选 lane; 给了 set_aside 时这些行追加进 set_aside(调用方作「未归 lane」组单独计数, 不静默丢)。"""
    from tools.aipos_cli.workspace_config import repo_name_set

    lanes = set(repo_name_set(lane))
    if not lanes:
        return list(rows)
    unresolved = str(lane_view_declaration()["unresolved_lane"])
    if set_aside is not None:
        set_aside.extend(row for row in rows if row.get("lane") == unresolved)
    return [row for row in rows if row.get("lane") in lanes]


def _view_card_id(row: dict[str, Any], metadata: dict[str, Any] | None) -> str:
    """可见卡入口的卡号(冻结判定键): 卡面 task_id → 行 task_id → 卡文件名(卡面读不出时, 与 state_lint.queue_task_index 同一回落)。"""
    raw = (metadata or {}).get("task_id") or row.get("task_id") or (Path(str(row["path"])).stem if row.get("path") else "")
    return str(raw or "").strip().upper()


def visible_cards(governance_root: Path, entries: Any, *, lane: str | list[str] | None = None,
                  include_frozen: bool = False, viewer: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F141: 四个查看命令(lybra next 项目扫描 / brief / loop status / needs-owner)共用的唯一「可见卡」入口。

    entries = 可迭代的 (row, metadata) 对: row 至少带 task_id 或 path; metadata = 卡面 frontmatter(读不出 = None)。
    row 已带 "lane" 键(调用方已反查, 如 loop status 的运行)= 不重算; 否则经 lane_of_card 唯一派生。
    顺序(冻结在先, 冻结卡不进「未归 lane」组):
      ① 冻结: 是否冻结只调 F122 唯一判定 legacy_baseline.frozen_tasks; 缺省不列, 卡号进 frozen_hidden; include_frozen = 照列并标
         row["frozen"]=True。清单读不出 = frozen_error(原文), 不隐藏任何卡(fail-closed)。
      ② lane: 唯一过滤 filter_rows_by_lane; 给定 lane 时 lane 解析不了的行不混入, 进 unresolved_lane(卡号 + 原因)。
      ③ 归属(AIPOS-F156 件②): viewer = owner_viewer 结果(None = 不按归属分, 行为不变)——每行经唯一判定 card_owner 标 owner /
         owner_source; 非总顾问视角下归属其他实例的行移出 rows 进 owner_other(不取、不推导, 汇总行单列「其他子项目(不取)」);
         归属本实例 / 归属不明的行留在 rows。总顾问 = 只标归属不分组。
    返回 {rows, frozen_hidden: [卡号], unresolved_lane: [{task_id, lane_error}], frozen_error: str|None, lane_filter, include_frozen,
          owner_view: viewer, owner_other: [{task_id, owner, owner_source, lane, ...}]}。"""
    from tools.aipos_cli.legacy_baseline import LegacyBaselineError, frozen_tasks

    root = Path(governance_root)
    frozen_error: str | None = None
    try:
        frozen = frozen_tasks(root)
    except LegacyBaselineError as exc:
        frozen, frozen_error = {}, str(exc)
    rows: list[dict[str, Any]] = []
    hidden: list[str] = []
    publishes = publish_index(root) if viewer is not None else {}
    for row, metadata in entries:
        card_id = _view_card_id(row, metadata)
        is_frozen = bool(card_id) and card_id in frozen
        if is_frozen and not include_frozen:
            hidden.append(card_id)
            continue
        out = dict(row) if "lane" in row else {**row, **lane_of_card(metadata, root)}
        if is_frozen:
            out["frozen"] = True
        if viewer is not None:  # AIPOS-F156 件②: 归属唯一判定(发布记录索引一趟读, 逐行查)
            out.update(card_owner(root, card_id, metadata, publishes=publishes))
        rows.append(out)
    aside: list[dict[str, Any]] = []
    rows = filter_rows_by_lane(rows, lane, set_aside=aside)
    unresolved = [{"task_id": _view_card_id(row, None) or "?", "lane_error": row.get("lane_error")} for row in aside]
    other: list[dict[str, Any]] = []
    if viewer is not None and not viewer.get("chief"):
        other = [row for row in rows if row.get("owner") and row.get("owner") != viewer.get("instance")]
        rows = [row for row in rows if not (row.get("owner") and row.get("owner") != viewer.get("instance"))]
    return {"rows": rows, "frozen_hidden": hidden, "unresolved_lane": unresolved, "frozen_error": frozen_error,
            "lane_filter": lane, "include_frozen": bool(include_frozen), "owner_view": viewer,
            "owner_other": [{k: row.get(k) for k in ("task_id", "owner", "owner_source", "lane", "current_state", "queue_state")
                             if row.get(k) is not None} | {"task_id": _view_card_id(row, None) or "?"} for row in other]}


def render_visible_summary(view: dict[str, Any]) -> list[str]:
    """visible_cards 结果的汇总行(四命令文字输出同一渲染; 模板读 verbs.schema lane_view.visible_cards)。无可报 = []。"""
    decl = lane_view_declaration()["visible_cards"]
    lines: list[str] = []
    if view.get("frozen_error"):
        lines.append(str(decl["frozen_error_summary"]).format(error=view["frozen_error"]))
    if view.get("frozen_hidden"):
        lines.append(str(decl["frozen_summary"]).format(count=len(view["frozen_hidden"]), flag=decl["include_frozen_flag"]))
    aside = view.get("unresolved_lane") or []
    if aside:
        limit = int(decl["unresolved_list_limit"])
        ids = ", ".join(str(item.get("task_id")) for item in aside[:limit]) + (f" …(另 {len(aside) - limit} 张, --json 看全部)"
                                                                                if len(aside) > limit else "")
        lines.append(str(decl["unresolved_summary"]).format(count=len(aside), task_ids=ids,
                                                            lane=lane_filter_label(view.get("lane_filter"))))
    viewer = view.get("owner_view")
    if viewer:  # AIPOS-F156 件②: 归属视角一行 + 其他子项目(不取)单列(模板读 lane_view.owner_view)
        owner_decl = owner_view_declaration()
        if viewer.get("chief"):
            lines.append(str(owner_decl["chief_summary"]).format(instance=viewer["instance"]))
        else:
            lines.append(str(owner_decl["viewer_summary"]).format(instance=viewer["instance"], source=viewer.get("source")))
        if view.get("owner_other"):
            lines.append(render_owner_other(view["owner_other"]))
    return lines


# ---------------------------------------------------------------------------
# AIPOS-F156 件②③: 卡归属(唯一判定 card_owner)+ 归属视角(唯一判定 owner_viewer)。声明 verbs.schema lane_view.owner_view。
# 四视图经 visible_cards(viewer=)同一判定; loop 启动核归属(loop_driver)与结案取下一张(loop_run_record._take_next)同读此处。
# ---------------------------------------------------------------------------

_OWNER_VIEW_KEYS = ("cli_flag", "dest", "commands", "help", "unattributed_actors", "self_report_keys", "self_report_marker",
                    "chief_summary", "viewer_summary", "other_summary", "other_item", "row_tag", "loop_refusal", "publish_signer")


def owner_view_declaration() -> dict[str, Any]:
    """verbs.schema lane_view.owner_view(缺键 = SchemaLoadError, fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("verbs").get("lane_view") or {}).get("owner_view")
    if not isinstance(decl, dict) or any(key not in decl for key in _OWNER_VIEW_KEYS):
        raise SchemaLoadError(f"verbs.schema.json lane_view.owner_view 未声明或缺键 {list(_OWNER_VIEW_KEYS)}")
    return decl


def add_owner_view_argument(parser: Any) -> None:
    """归属视角参数(声明 lane_view.owner_view.cli_flag / dest / help; 缺省 None = 不按归属分)。"""
    decl = owner_view_declaration()
    parser.add_argument(str(decl["cli_flag"]), dest=str(decl["dest"]), default=None, metavar=str(decl.get("metavar") or "ACTOR"),
                        help=str(decl["help"]))


def owner_marker(row: dict[str, Any]) -> str:
    """归属来源标注: 自报(卡面 created_by 回退)= 「(自报)」后缀; 发布记录 = 空。"""
    if row.get("owner_source") == "self_reported":
        return f"({owner_view_declaration()['self_report_marker']})"
    return ""


def render_owner_other(other: list[dict[str, Any]]) -> str:
    """「其他子项目(不取)」一行(四视图汇总行与 loop status 结案取下一张同一渲染; 模板 lane_view.owner_view.other_summary / other_item)。"""
    decl = owner_view_declaration()
    limit = int(lane_view_declaration()["visible_cards"]["unresolved_list_limit"])
    items = ", ".join(str(decl["other_item"]).format(task_id=o.get("task_id"), owner=o.get("owner"), marker=owner_marker(o))
                      for o in other[:limit])
    if len(other) > limit:
        items += f" …(另 {len(other) - limit} 张, --json 看全部)"
    return str(decl["other_summary"]).format(count=len(other), items=items)


def owner_tag(row: dict[str, Any]) -> str:
    """行内归属标签(next 文字输出; 无归属信息 = 空串)。"""
    if not row.get("owner_source"):
        return ""
    owner = row.get("owner") or "(未知)"
    return str(owner_view_declaration()["row_tag"]).format(owner=owner, marker=owner_marker(row))


def publish_index(governance_root: Path) -> dict[str, list[dict[str, Any]]]:
    """发布记录按卡索引(records.load_records 唯一入口, 只读 publishes 分组, 一次视图一趟)。"""
    from tools.aipos_cli.records import load_records

    return dict(load_records(Path(governance_root), groups=["publishes"]).get("task_publishes") or {})


def card_owner(governance_root: Path, task_id: str, metadata: dict[str, Any] | None, *,
               publishes: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """AIPOS-F156 件②: 卡归属唯一判定 → {owner, owner_source: publish_record|self_reported|unknown}。
    ① 门侧发布记录: 该卡(审计卡 = 被审卡, audit_derivation.audit_card_reviewed_id)最近一份 actor 不在 unattributed_actors 的发布记录
       签发实例; ② 存量卡回退卡面 self_report_keys 按序首个合实例名模板者(custom_roles.instance_name_canonical_refusal)= 自报;
    ③ 都无 = 归属不明(owner None)。publishes = publish_index 结果(缺省按卡读)。只读。"""
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, is_audit_card
    from tools.aipos_cli.custom_roles import instance_name_canonical_refusal

    decl = owner_view_declaration()
    unattributed = {str(a) for a in decl["unattributed_actors"]}
    fm = metadata if isinstance(metadata, dict) else {}
    tid = str(task_id or fm.get("task_id") or "").strip()
    subject = tid
    if tid and is_audit_card(tid, fm):
        subject = audit_card_reviewed_id(tid, fm) or tid
    if publishes is None:
        from tools.aipos_cli.records import load_records

        publishes = dict(load_records(Path(governance_root), groups=["publishes"], task_id=subject).get("task_publishes") or {})
    records = sorted(publishes.get(subject) or [], key=lambda r: str(r.get("published_at") or ""), reverse=True)
    for record in records:
        actor = str(record.get("actor") or "").strip()
        if actor and actor not in unattributed:
            return {"owner": actor, "owner_source": "publish_record", "owner_ref": record.get("path")}
    if subject == tid:
        for key in decl["self_report_keys"]:
            value = str(fm.get(key) or "").strip()
            if value and value not in unattributed and not instance_name_canonical_refusal(value):
                return {"owner": value, "owner_source": "self_reported", "owner_ref": f"卡面 {key}"}
    return {"owner": None, "owner_source": "unknown", "owner_ref": None}


def _valid_envelopes(governance_root: Path) -> list[dict[str, Any]]:
    """当前有效的信封(有效性唯一判据 autonomy_policy.select_envelope → match_claim_envelope: 状态 / 批准 / 时间窗 / 选择器 / 额度),
    身份 = 信封自身 agent_or_role。读不出 / 不合规的信封不计(select_envelope 已给原因, 归属视角不据其放宽或收紧)。"""
    from tools.aipos_cli.autonomy_policy import load_policy, policy_ids, select_envelope

    root = Path(governance_root)
    valid: list[dict[str, Any]] = []
    for pid in policy_ids(root):
        policy = load_policy(root, pid)
        covered = str((policy or {}).get("agent_or_role") or "").strip()
        if not covered:
            continue
        matched, _reasons = select_envelope(root, identities=[(covered, covered, None)], policy_id=pid)
        if matched is not None:
            valid.append(matched)
    return valid


def owner_viewer(governance_root: Path, *, actor: str | None = None, lane: str | list[str] | None = None,
                 policy: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """AIPOS-F156 件②: 归属视角唯一判定 → None(不按归属分, 行为不变)或 {instance, source, chief, chief_reason}。
    actor 给出 = 该实例(source actor); 否则 lane 给出且有效信封中 lane 集合 ⊇ 所给 lane、agent_or_role 为实例名者恰一个 = 该实例
    (source lane_envelope); 其余 = None。总顾问(chief): policy 给出(loop 本次运行信封)= 其不限 lane; 否则该实例的有效信封里有不限 lane 者。"""
    from tools.aipos_cli.custom_roles import instance_name_canonical_refusal
    from tools.aipos_cli.workspace_config import repo_name_set

    root = Path(governance_root)
    instance, source = str(actor or "").strip(), "actor"
    envelopes: list[dict[str, Any]] | None = None
    if not instance:
        wanted = set(repo_name_set(lane))
        if not wanted:
            return None
        envelopes = _valid_envelopes(root)
        owners = set()
        for env in envelopes:
            covered = str(env.get("agent_or_role") or "").strip()
            try:
                env_lanes = set(resolve_lane_filter(root, env.get("task_selector_lane_repo")) or [])
            except LaneFilterInvalid:
                continue
            if env_lanes and wanted <= env_lanes and not instance_name_canonical_refusal(covered):
                owners.add(covered)
        if len(owners) != 1:
            return None
        instance, source = owners.pop(), "lane_envelope"
    if policy is not None:
        lanes = repo_name_set(policy.get("task_selector_lane_repo"))
        chief = not lanes
        reason = f"本次信封 {policy.get('policy_id')} " + ("不限 lane" if chief else f"限 lane {lanes}")
    else:
        envelopes = envelopes if envelopes is not None else _valid_envelopes(root)
        own = [env for env in envelopes if str(env.get("agent_or_role") or "").strip() == instance]
        free = [env for env in own if not repo_name_set(env.get("task_selector_lane_repo"))]
        chief = bool(free)
        reason = (f"有效信封 {free[0].get('policy_id')} 不限 lane" if free
                  else f"有效信封 {[e.get('policy_id') for e in own]} 均限 lane" if own else "无本实例的有效信封")
    return {"instance": instance, "source": source, "chief": chief, "chief_reason": reason}


def card_owner_refusal(governance_root: Path, task_id: str, card: dict[str, Any] | None, *, driver_actor: str,
                       policy: dict[str, Any] | None) -> str:
    """AIPOS-F156 件②: loop 启动核卡归属(唯一判定 card_owner + owner_viewer)。驱动方非总顾问(本次信封限 lane)且卡归属来自门侧发布记录
    且 ≠ 驱动方 = 拒因原文(模板 lane_view.owner_view.loop_refusal); 否则 ""。自报归属 / 归属不明不作拒据(卡面字段是起草输入自报)。"""
    from tools.aipos_cli.workspace_config import repo_name_set

    viewer = owner_viewer(Path(governance_root), actor=driver_actor, policy=policy)
    if viewer is None or viewer["chief"]:
        return ""
    own = card_owner(Path(governance_root), task_id, card)
    if own["owner_source"] != "publish_record" or own["owner"] == viewer["instance"]:
        return ""
    return str(owner_view_declaration()["loop_refusal"]).format(
        task_id=task_id, owner=own["owner"], driver=driver_actor, envelope=(policy or {}).get("policy_id") or "(未定)",
        lanes=repo_name_set((policy or {}).get("task_selector_lane_repo")))


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


# ---------------------------------------------------------------------------
# AIPOS-F181 item 2/3: board card statistics + subproject grouping (declaration verbs.schema lane_view.card_stats).
# One derivation for the overview / project page / roadmap subproject filter / onboarding "first closed card": frozen cards
# go through the single visible-card entry (visible_cards -> legacy_baseline.frozen_tasks) and are only counted as history.
# ---------------------------------------------------------------------------

CARD_GROUPS = ("inflight", "todo", "closed")


def card_stats_declaration() -> dict[str, Any]:
    """verbs.schema lane_view.card_stats (fail-closed): todo_states = non-empty list of non-terminal queue directories."""
    from tools.aipos_cli.task_loader import QUEUE_STATES, QUEUE_TERMINAL_STATES
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("verbs").get("lane_view") or {}).get("card_stats")
    todo = decl.get("todo_states") if isinstance(decl, dict) else None
    if (not isinstance(todo, list) or not todo
            or not all(isinstance(s, str) and s in QUEUE_STATES and s not in QUEUE_TERMINAL_STATES for s in todo)):
        raise SchemaLoadError(f"verbs.schema.json lane_view.card_stats.todo_states must list non-terminal queue states of {list(QUEUE_STATES)}")
    return decl


def card_state_groups() -> dict[str, tuple[str, ...]]:
    """Queue state -> board group: todo = declared todo_states; closed = lybra_roadmap.status.satisfied_queue_states; inflight = the
    other non-terminal states; remaining terminal states (withdrawn) belong to no group."""
    from tools.aipos_cli.roadmap_drift import status_declaration
    from tools.aipos_cli.task_loader import QUEUE_STATES, QUEUE_TERMINAL_STATES

    todo = tuple(card_stats_declaration()["todo_states"])
    closed = tuple(status_declaration()["satisfied_queue_states"])
    inflight = tuple(s for s in QUEUE_STATES if s not in QUEUE_TERMINAL_STATES and s not in todo and s not in closed)
    return {"inflight": inflight, "todo": todo, "closed": closed}


def subproject_label(key: str) -> str:
    """Short display name of a subproject key (display only, never a role judgement): an instance name -> its prefix segment
    (naming_profile.parse_instance_name, the single instance-name parser); a lane that is a path -> the last path segment."""
    from tools.aipos_cli.naming_profile import parse_instance_name

    text = str(key or "")
    if "/" in text:
        return Path(text).name or text
    parsed = parse_instance_name(text)
    return str(parsed.get("prefix") or text) if parsed else text


def card_subproject(governance_root: Path, row: dict[str, Any], metadata: dict[str, Any] | None,
                    publishes: dict[str, list[dict[str, Any]]], drivers: set[str]) -> dict[str, Any]:
    """AIPOS-F181: subproject key of one card (single derivation): the card's owning advisor (card_owner: gate publish record, legacy
    cards fall back to the self-reported card field) when that instance drives the project (holds one of its envelopes, any state:
    drivers = autonomy_policy.envelope_overview drivers); otherwise the card's lane key (lane_of_card). Audit cards whose owner is
    unknown are resolved later by card_stats to the reviewed card's subproject. Returns {subproject, owner, owner_source, lane,
    lane_error}."""
    root = Path(governance_root)
    task_id = _view_card_id(row, metadata)
    own = card_owner(root, task_id, metadata, publishes=publishes)
    if own["owner"] and str(own["owner"]) in drivers:
        return {"subproject": str(own["owner"]), "owner": own["owner"], "owner_source": own["owner_source"], "lane": None,
                "lane_error": None}
    lane = lane_of_card(metadata, root)
    return {"subproject": str(lane["lane"]), "owner": None, "owner_source": own["owner_source"], "publisher": own["owner"],
            "lane": lane["lane"], "lane_error": lane["lane_error"]}


def card_stats(governance_root: Path) -> dict[str, Any]:
    """AIPOS-F181: the single board card statistics (see card_stats_declaration). Read-only; inside a read scope one derivation
    per governance root (callers get deep copies). Returns {groups, totals: {inflight, todo, closed}, by_state, frozen, frozen_error,
    rows: [{task_id, title, queue_state, group, subproject, owner, owner_source, lane, created_at}], subprojects: [{key, label,
    advisor, lanes, counts, by_state}]}."""
    from tools.aipos_cli.task_loader import lookup_scope_memo, task_card_lookup_scope

    root = Path(governance_root)
    with task_card_lookup_scope(root):
        return lookup_scope_memo(root, "machine_zone.card_stats", lambda: _card_stats(root), copy_out=True)


def _card_stats(root: Path) -> dict[str, Any]:
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, is_audit_card
    from tools.aipos_cli.frontmatter import read_markdown_frontmatter
    from tools.aipos_cli.task_loader import QUEUE_STATES, iter_queue_task_paths

    groups = card_state_groups()
    group_of = {state: name for name, states in groups.items() for state in states}
    entries: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for state in QUEUE_STATES:
        for path in iter_queue_task_paths(root, states=(state,)):
            try:
                meta, _body, warnings, _fence = read_markdown_frontmatter(path)
            except (OSError, UnicodeDecodeError):
                meta, warnings = None, ["unreadable"]
            metadata = meta if isinstance(meta, dict) and not warnings else None
            # "lane" present = visible_cards does not derive it; card_subproject derives it only for cards without an owner
            entries.append(({"task_id": (metadata or {}).get("task_id"), "path": str(path), "queue_state": state, "lane": None}, metadata))
    from tools.aipos_cli.autonomy_policy import envelope_overview

    meta_by_path = {row["path"]: metadata for row, metadata in entries}
    view = visible_cards(root, entries)
    publishes = publish_index(root)
    drivers = {str(d.get("agent_or_role") or "") for d in envelope_overview(root)["drivers"]} - {""}
    rows: list[dict[str, Any]] = []
    for row in view["rows"]:
        metadata = meta_by_path.get(row["path"])
        sub = card_subproject(root, row, metadata, publishes, drivers)
        rows.append({"task_id": _view_card_id(row, metadata), "title": str((metadata or {}).get("title") or "").strip() or None,
                     "queue_state": row["queue_state"], "group": group_of.get(row["queue_state"]), **sub,
                     "created_at": str((metadata or {}).get("created_at") or (metadata or {}).get("draft_created_at") or "") or None,
                     "audit_of": (audit_card_reviewed_id(_view_card_id(row, metadata), metadata) or "").upper()
                     if is_audit_card(_view_card_id(row, metadata), metadata) else None})
    by_id = {r["task_id"]: r for r in rows}
    for r in rows:  # an audit card with no known owner joins the reviewed card's subproject
        reviewed = by_id.get(r["audit_of"] or "")
        if r["owner"] is None and reviewed is not None and reviewed["owner"]:
            r.update(subproject=reviewed["subproject"], owner=reviewed["owner"], owner_source=reviewed["owner_source"])
    subs: dict[str, dict[str, Any]] = {}
    for r in rows:
        entry = subs.setdefault(r["subproject"], {"key": r["subproject"], "label": subproject_label(r["subproject"]),
                                                  "advisor": r["owner"], "lanes": [], "counts": dict.fromkeys(CARD_GROUPS, 0),
                                                  "by_state": {}})
        if r["group"]:
            entry["counts"][r["group"]] += 1
        entry["by_state"][r["queue_state"]] = entry["by_state"].get(r["queue_state"], 0) + 1
        lane = r["lane"] if r["lane"] is not None else None
        if lane and lane not in entry["lanes"]:
            entry["lanes"].append(lane)
    for entry in subs.values():
        if entry["advisor"] is None:  # lane-keyed group: the advisor whose valid envelope covers exactly this lane (if any)
            lane_key = entry["key"]
            if lane_key != str(lane_view_declaration()["unresolved_lane"]):
                viewer = owner_viewer(root, lane=lane_key)
                entry["advisor"] = viewer["instance"] if viewer else None
    # a group with nothing in flight, to do or closed (only withdrawn cards) is not a subproject worth a row
    ordered = sorted((e for e in subs.values() if any(e["counts"].values())),
                     key=lambda e: (-(e["counts"]["inflight"] + e["counts"]["todo"]), -e["counts"]["closed"], e["key"]))
    totals = {name: sum(1 for r in rows if r["group"] == name) for name in CARD_GROUPS}
    by_state: dict[str, int] = {}
    for r in rows:
        by_state[r["queue_state"]] = by_state.get(r["queue_state"], 0) + 1
    return {"groups": {k: list(v) for k, v in groups.items()}, "totals": totals, "by_state": by_state,
            "frozen": len(view["frozen_hidden"]), "frozen_error": view["frozen_error"], "rows": rows, "subprojects": ordered}
