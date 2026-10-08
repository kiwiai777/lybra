from __future__ import annotations

from typing import Any





_TASK_CLASS_DECL_KEYS = (
    "required_fields",
    "distinct_pairs",
    "dependency_gate",
    "orchestration_continuity",
    "code_mode_advisory",
    "suggested_workflow",
)


def task_class_declarations() -> dict[str, dict[str, Any]]:
    """AIPOS-F104 件④: task_class 值域与每级治理要求唯一读取口——enums.schema task_class(值顺序保留)。

    每值须声明 _TASK_CLASS_DECL_KEYS 全部键; 缺键 = 声明缺失, SchemaLoadError fail-closed(禁回退手写值域)。
    """
    from tools.schema_loader import SchemaLoadError, load_schema

    enum_def = (load_schema("enums").get("enums") or {}).get("task_class") or {}
    entries = enum_def.get("values") or []
    if not entries:
        raise SchemaLoadError("enums.schema.json task_class 未声明")
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        missing = [key for key in ("value", *_TASK_CLASS_DECL_KEYS) if not isinstance(entry, dict) or key not in entry]
        if missing:
            raise SchemaLoadError(f"enums.schema.json task_class 值 {entry!r} 缺声明 {missing}")
        out[str(entry["value"])] = {key: entry[key] for key in _TASK_CLASS_DECL_KEYS}
    return out


def default_task_class() -> str:
    """缺省 task_class = enums.schema task_class.default(须为声明值之一, 否则 fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    default = str(((load_schema("enums").get("enums") or {}).get("task_class") or {}).get("default") or "")
    if default not in task_class_declarations():
        raise SchemaLoadError(f"enums.schema.json task_class.default 未声明或不在值域内: {default!r}")
    return default


# 代码校验值域 = 声明值域(模块级投影, 供 CLI choices 等只读; 非第二份清单)
ALLOWED_TASK_CLASSES = tuple(task_class_declarations())
CODE_TASK_MODES = {"code", "coding"}
AUDIT_PASS_VALUES = {"pass", "passed", "pass_with_notes"}
# AIPOS-F133 件③: 依赖条件值域 = card.schema dependency_gate.conditions(dependency_gate_declaration 唯一读取口; 原手写集合退役)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if value in (None, ""):
        return []
    return [_text(value)]


def effective_task_class(metadata: dict[str, Any]) -> str:
    raw = _lower(metadata.get("task_class"))
    return raw or default_task_class()


def dependency_gate_declaration() -> dict[str, Any]:
    """AIPOS-F133 件③: card.schema dependency_gate(条件值域 → 门生记录类; 缺省条件)。缺/形坏 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("card").get("dependency_gate")
    conditions = decl.get("conditions") if isinstance(decl, dict) else None
    if not isinstance(conditions, dict) or not conditions:
        raise SchemaLoadError("card.schema.json dependency_gate.conditions 未声明")
    for name, spec in conditions.items():
        if not isinstance(spec, dict) or not str(spec.get("record_kind") or "").strip():
            raise SchemaLoadError(f"card.schema.json dependency_gate.conditions.{name} 缺 record_kind")
    if decl.get("default_condition") not in conditions:
        raise SchemaLoadError("card.schema.json dependency_gate.default_condition 未声明或不在 conditions 内")
    return decl


def unmet_dependencies(metadata: dict[str, Any], governance_root: "Path | None") -> list[str]:
    """AIPOS-F133 件③: 依赖满足判据唯一实现——返回未满足依赖的拒因(空 = 全部满足)。

    - 条件 = 卡面 dependency_condition, 缺省 card.schema dependency_gate.default_condition; 值不在声明 = 拒因(不猜)。
    - 每个 depends_on 都须满足(全部, 非任一); 判据只读被依赖卡的门生记录(record_writer.gate_record_files; audit_pass 另须
      audit_helpers.is_gate_born_verdict_metadata 且 verdict ∈ AUDIT_PASS_VALUES), 不读卡面自报字段。
    - 无治理根 = 读不到记录 = 拒因(fail-closed); 记录 frontmatter 读不出 = 拒因点名文件(不当作满足, 也不静默跳过)。"""
    depends_on = _as_list(metadata.get("depends_on"))
    if not depends_on:
        return []
    decl = dependency_gate_declaration()
    conditions = decl["conditions"]
    condition = _lower(metadata.get("dependency_condition")) or str(decl["default_condition"])
    if condition not in conditions:
        return [f"DEPENDENCY_CONDITION_INVALID: dependency_condition={condition!r} 不在 card.schema dependency_gate.conditions "
                f"{list(conditions)} 内。出口: 改为其一(缺省 {decl['default_condition']})"]
    if governance_root is None:
        return [f"DEPENDENCY_UNVERIFIABLE: 卡声明 depends_on {depends_on} 但调用方未给治理根, 读不到门生记录(fail-closed)"]
    from pathlib import Path as _Path

    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.record_writer import gate_record_files, record_dir  # 延迟: record_writer→records→task_loader→本模块 循环

    spec = conditions[condition]
    kind = str(spec["record_kind"])
    root = _Path(governance_root)
    unmet: list[str] = []
    for dep in depends_on:
        files = gate_record_files(record_dir(root, kind, dep), kind)
        satisfied = bool(files)
        problems: list[str] = []
        if satisfied and spec.get("requires_pass_verdict") is True:
            from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata

            satisfied = False
            for path in files:
                try:
                    vfm, _body = require_frontmatter(path)
                except FrontmatterReadError as exc:
                    problems.append(str(exc))
                    continue
                if is_gate_born_verdict_metadata(vfm) and _lower(vfm.get("verdict")) in AUDIT_PASS_VALUES:
                    satisfied = True
                    break
        if not satisfied:
            detail = f"; 读不出: {problems}" if problems else ""
            unmet.append(
                f"DEPENDENCY_UNMET: 依赖 {dep} 未满足 {condition}({spec.get('description') or kind}): "
                f"{record_dir(root, kind, dep)} 下无{'门生 PASS 裁决' if spec.get('requires_pass_verdict') else '门生'}记录{detail}。"
                f"出口: 先把 {dep} 推进到满足 {condition}, 本卡再认领(判据 card.schema dependency_gate)"
            )
    return unmet


def dependencies_satisfied(metadata: dict[str, Any], governance_root: "Path | None") -> bool:
    """AIPOS-F133 件③: 全部依赖满足才真(唯一判据 unmet_dependencies; 无 depends_on = 真)。"""
    return not unmet_dependencies(metadata, governance_root)


def _class_label(task_class: str) -> str:
    return f"{task_class.capitalize()}-class"


# AIPOS-232 — execution-layer workflow role counts. These are LABELS for the
# accountability template (executor-only vs executor + independent auditor), NOT
# an engine: nothing here runs, schedules, or launches an agent (gate-not-engine).
ONE_ROLE = "1-role"
TWO_ROLE = "2-role"


def suggest_workflow_roles(metadata: dict[str, Any]) -> dict[str, Any]:
    """Pure heuristic hint (NOT enforcement) for 1-role vs 2-role.

    Reads only the EXISTING ``task_class`` complexity signal: complex -> suggest
    2-role (independent audit recommended); otherwise -> suggest 1-role.

    Honesty (AIPOS-232 R-3): ``task_class`` is a *complexity tier*, not a *task
    type*, so this only APPROXIMATES "doc/config vs ops/design/code" and is not
    precise. The Owner always decides; there is NO auto-select and this never
    mutates the card (``auto_selected`` is always False). Pure function: same
    input -> same output, no side effect, no stored state, no background work.
    """
    task_class = effective_task_class(metadata)
    declaration = task_class_declarations().get(task_class) or {}
    if declaration.get("suggested_workflow") == TWO_ROLE:
        suggested = TWO_ROLE
        rationale = (
            f"{task_class}-class -> independent audit recommended (executor + distinct auditor)"
        )
    else:
        suggested = ONE_ROLE
        rationale = (
            "non-complex-class -> single role acceptable; choose 2-role explicitly "
            "if an independent audit is wanted"
        )
    return {
        "suggested_workflow": suggested,
        "suggested_role_count": 2 if suggested == TWO_ROLE else 1,
        "suggestion_basis": "task_class",
        "suggestion_is_heuristic": True,
        "suggestion_rationale": rationale,
        "auto_selected": False,
    }


def complexity_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    raw = metadata.get("task_class")
    return {
        "task_class": raw,
        "effective_task_class": effective_task_class(metadata),
        "task_class_explicit": raw not in (None, ""),
        "complexity_note": metadata.get("complexity_note"),
        # AIPOS-232: advisory-only role-count suggestion; never auto-applied.
        "workflow_suggestion": suggest_workflow_roles(metadata),
    }


def validate_task_complexity(
    metadata: dict[str, Any],
    *,
    enforce_dependency_gate: bool,
    governance_root: "Path | None" = None,  # AIPOS-C3B 大项C④: 用于读 records 校验 audit_pass
) -> dict[str, list[str]]:
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    needs_owner_reasons: list[str] = []
    raw_class = _lower(metadata.get("task_class"))
    task_class = effective_task_class(metadata)
    task_mode = _lower(metadata.get("task_mode"))

    declarations = task_class_declarations()
    if raw_class and raw_class not in declarations:
        blocking_reasons.append(f"task_class must be one of: {', '.join(declarations)} (enums.schema task_class)")
        return {
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
            "needs_owner_reasons": needs_owner_reasons,
        }
    declaration = declarations[task_class]
    label = _class_label(task_class)

    if task_mode in CODE_TASK_MODES and declaration["code_mode_advisory"] is True:
        if raw_class:
            warnings.append(f"Code-mode task is explicitly classified {task_class}; review whether complex-class governance is required")
        else:
            warnings.append(f"Code-mode task omits task_class and defaults to {task_class}; review whether complex-class governance is required")

    # AIPOS-R6E 靶⑤: N0容量lint——启发式WARN交付大项>3或验证+修复+清账混装
    artifact_scope = _text(metadata.get("artifact_scope"))
    if artifact_scope:
        # 按中文或英文分隔符拆分大项
        import re
        # 支持:中文顿号、中英文逗号、加号、分号等
        major_items = re.split(r'[、,;,;+/\s]+', artifact_scope)
        major_items = [item.strip() for item in major_items if item.strip()]
        
        if len(major_items) > 3:
            warnings.append(
                f"N0 capacity lint: artifact_scope declares {len(major_items)} major items (>{3}). "
                f"Consider splitting into multiple focused cards for better autonomy and audit clarity."
            )
        
        # 检测验证+修复+清账混装(常见反模式)
        scope_lower = artifact_scope.lower()
        mixed_concerns = []
        if any(keyword in scope_lower for keyword in ['验证', 'verify', 'validate', 'test']):
            mixed_concerns.append('verification')
        if any(keyword in scope_lower for keyword in ['修复', 'fix', 'repair', 'patch']):
            mixed_concerns.append('fix')
        if any(keyword in scope_lower for keyword in ['清账', 'cleanup', 'reconcile', '收尾']):
            mixed_concerns.append('cleanup')
        
        if len(mixed_concerns) >= 2:
            warnings.append(
                f"N0 capacity lint: artifact_scope mixes {'+'.join(mixed_concerns)}. "
                f"Verification, fixes, and cleanup should typically be separate cards for clear acceptance criteria."
            )

    # AIPOS-F104 件④: 每级治理要求读声明(required_fields / distinct_pairs / dependency_gate / orchestration_continuity)
    for field in declaration["required_fields"]:
        if not _text(metadata.get(field)):
            blocking_reasons.append(f"{label} task missing {field}")
    for left, right in declaration["distinct_pairs"]:
        left_value, right_value = _text(metadata.get(left)), _text(metadata.get(right))
        if left_value and right_value and left_value == right_value:
            blocking_reasons.append(f"{label} {left} must not equal {right}")

    orchestration = metadata.get("orchestration")
    if declaration["orchestration_continuity"] is True and isinstance(orchestration, dict) and orchestration.get("enabled") is True:
        assignment_status = _lower(orchestration.get("planner_assignment_status"))
        if assignment_status == "active":
            if not _text(orchestration.get("continuity_planner_agent")):
                blocking_reasons.append(f"{label} active orchestration missing continuity_planner_agent")
            if not _text(orchestration.get("continuity_planner_agent_instance")):
                blocking_reasons.append(f"{label} active orchestration missing continuity_planner_agent_instance")

    # AIPOS-F133 件③: 依赖判据单源 = unmet_dependencies / dependencies_satisfied(全部依赖满足才放行, 只读门生记录)。
    # dependency_gate=true 的 task_class(发布 draft_writer / validator 用)另要求显式声明 dependency_condition(值域读 card.schema
    # dependency_gate.conditions); 原「frontmatter 自报字段判 + audit_pass 任一依赖 PASS 即放行 + 无记录退回自报」退役。
    depends_on = _as_list(metadata.get("depends_on"))
    if declaration["dependency_gate"] is True and enforce_dependency_gate and depends_on:
        conditions = dependency_gate_declaration()["conditions"]
        dependency_condition = _lower(metadata.get("dependency_condition"))
        if dependency_condition not in conditions:
            blocking_reasons.append(
                f"{label} dependent task requires dependency_condition: {', '.join(conditions)} (card.schema dependency_gate.conditions)"
            )
        else:
            blocking_reasons.extend(unmet_dependencies(metadata, governance_root))

    return {
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "needs_owner_reasons": needs_owner_reasons,
    }
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
