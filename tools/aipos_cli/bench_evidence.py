"""AIPOS-336 S2 — Bench evidence type registry + ring2 check runner.

Data-driven registry of the four non-code evidence types (per AIPOS-304 D4):
deploy health / config diff / content output / research conclusion.

Design (advisor note #4 — "四类证据检查做成【数据/模板】,新增证据类零代码(活口)"):
  - EVIDENCE_TYPES is declarative data. Adding a new evidence type, or a new
    check within a type, = adding a data entry. No code changes to the runner.
  - Each ring2 check declares a ``check_kind``. The runner dispatches by kind.
    Adding a new auto-checkable kind = registering one handler in CHECK_RUNNERS.

Ring model (304 D2 branch-1):
  - ring2 = auto-checkable evidence checklist (machine runs what it can)
  - ring3 = Owner eye-verify items (human judgment — never auto-judged)

Boundary (304 D4 + acceptance #7):
  - D4 explicitly lists auto-checkable items vs the "缺口" (remote health, log
    scan, version match, citation completeness, semantic quality) that are NOT
    auto-checkable yet. Those are declared ``check_kind="manual"`` → the runner
    marks them ``needs_human`` and NEVER pretends to auto-judge them.
  - "不可完全可视化的部分照 D4 边界处理:标注为'需人判',不假装能自动判定。"
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Evidence type registry — declarative data (活口: add a type = add an entry)
# ---------------------------------------------------------------------------

EVIDENCE_TYPES: dict[str, dict[str, Any]] = {
    "deploy": {
        "label": "部署健康",
        "task_modes": ["deploy"],
        "description": "部署任务的证据:健康检查、部署日志、端点可达、版本一致。",
        "ring2_checks": [
            {"id": "health_check", "label": "远端服务健康检查通过", "check_kind": "manual",
             "hint": "提供健康检查端点/命令输出;当前为需人判(D4 缺口:远端系统状态检查)。"},
            {"id": "deploy_log", "label": "部署日志无 ERROR", "check_kind": "manual",
             "hint": "提供部署日志路径;ERROR 扫描为需人判(D4 缺口)。"},
            {"id": "endpoint_reachable", "label": "关键端点可达", "check_kind": "manual",
             "hint": "提供端点 URL/探测结果;远端探测为需人判(D4 缺口)。"},
            {"id": "version_match", "label": "版本号与预期一致", "check_kind": "manual",
             "hint": "提供实际版本号与预期版本;比对为需人判。"},
        ],
        "ring3_human": [
            "业务功能是否符合预期",
            "用户体验是否可接受",
        ],
    },
    "config": {
        "label": "配置 diff",
        "task_modes": ["config"],
        "description": "配置变更的证据:语法有效、关键字段、变更 diff。",
        "ring2_checks": [
            {"id": "syntax_valid", "label": "配置语法有效(JSON/YAML lint)", "check_kind": "format_valid",
             "hint": "提供配置文件路径(workspace 相对);runner 自动校验语法。"},
            {"id": "key_fields", "label": "关键字段存在且类型正确", "check_kind": "manual",
             "hint": "配置语义校验为需人判(D4 缺口:端口范围/路径可写性等)。"},
            {"id": "diff_generatable", "label": "变更 diff 可生成", "check_kind": "file_exists",
             "hint": "提供 diff 文件路径;runner 自动校验文件存在。"},
        ],
        "ring3_human": [
            "配置值是否合理",
            "是否遗漏关联配置",
        ],
    },
    "content": {
        "label": "内容产出",
        "task_modes": ["content"],
        "description": "内容产出的证据:文件存在、语法有效、链接可达。",
        "ring2_checks": [
            {"id": "file_exists_nonempty", "label": "文件存在且非空", "check_kind": "file_exists",
             "hint": "提供产出文件路径(workspace 相对);runner 自动校验存在且非空。"},
            {"id": "markdown_valid", "label": "Markdown 语法有效", "check_kind": "format_valid",
             "hint": "提供 .md 文件路径;runner 自动校验可解析。"},
            {"id": "links_reachable", "label": "内链可达(内链检查)", "check_kind": "manual",
             "hint": "提供文档路径;远端/外链可达性为需人判(D4 缺口);内链可后续补齐。"},
        ],
        "ring3_human": [
            "内容质量与完整性",
            "是否符合文档规范",
        ],
    },
    "research": {
        "label": "调研结论",
        "task_modes": ["research"],
        "description": "调研任务的证据:结论文档、引用链、结构。",
        "ring2_checks": [
            {"id": "conclusion_doc", "label": "结论文档存在", "check_kind": "file_exists",
             "hint": "提供结论文档路径(workspace 相对);runner 自动校验存在。"},
            {"id": "citation_chain", "label": "引用链完整(至少 N 个来源)", "check_kind": "manual",
             "hint": "引用完整性/数量校验为需人判(D4 缺口);提供引用清单供 Owner 核验。"},
            {"id": "structure_template", "label": "结构符合模板", "check_kind": "manual",
             "hint": "结构符合度为需人判;提供文档供 Owner 核验。"},
        ],
        "ring3_human": [
            "调研深度是否足够",
            "结论是否有说服力",
        ],
    },
}

# task_mode -> evidence_type lookup (derived from the registry, single source)
_TASK_MODE_TO_TYPE: dict[str, str] = {}
for _etype, _spec in EVIDENCE_TYPES.items():
    for _mode in _spec.get("task_modes", []):
        _TASK_MODE_TO_TYPE[_mode] = _etype


def list_evidence_types() -> list[str]:
    """Return the ordered list of evidence type ids."""
    return list(EVIDENCE_TYPES.keys())


def get_evidence_type(evidence_type: str | None, *, task_mode: str | None = None) -> dict[str, Any] | None:
    """Resolve an evidence type spec by explicit type or inferred from task_mode.

    Returns the spec dict, or None if unresolvable. Explicit evidence_type wins.
    """
    if evidence_type and evidence_type in EVIDENCE_TYPES:
        return EVIDENCE_TYPES[evidence_type]
    if task_mode and task_mode in _TASK_MODE_TO_TYPE:
        return EVIDENCE_TYPES[_TASK_MODE_TO_TYPE[task_mode]]
    return None


def evidence_type_for_task_mode(task_mode: str | None) -> str | None:
    """Map a task_mode to its evidence type id, or None."""
    if not task_mode:
        return None
    return _TASK_MODE_TO_TYPE.get(task_mode)


def evidence_requirements(evidence_type: str | None = None, *, task_mode: str | None = None) -> dict[str, Any] | None:
    """Return the human-readable evidence requirements for a type/task_mode.

    Used by the card template (S3) and the verification station — same source as
    the ring2 runner, never a second hand-written list (red line: 不新造并行真相).
    """
    spec = get_evidence_type(evidence_type, task_mode=task_mode)
    if spec is None:
        return None
    return {
        "evidence_type": [k for k, v in EVIDENCE_TYPES.items() if v is spec][0],
        "label": spec["label"],
        "description": spec.get("description", ""),
        "ring2_checks": [
            {"id": c["id"], "label": c["label"], "check_kind": c["check_kind"], "hint": c.get("hint", "")}
            for c in spec["ring2_checks"]
        ],
        "ring3_human": list(spec.get("ring3_human", [])),
    }


# ---------------------------------------------------------------------------
# ring2 check runners — dispatch by check_kind (活口: add a kind = add a handler)
# ---------------------------------------------------------------------------

def _check_file_exists(workspace_root: Path, ref: str | None) -> tuple[str, str]:
    """Auto-check: ref file exists and is non-empty. workspace_root is the only
    guaranteed root (S10); refs must be workspace-relative or absolute-under-root.
    """
    if not ref:
        return "missing", "未提供证据引用"
    target = _resolve_under_root(workspace_root, ref)
    if target is None:
        return "fail", f"证据引用越界或不可解析:{ref}"
    if not target.is_file():
        return "fail", f"文件不存在:{ref}"
    if target.stat().st_size == 0:
        return "fail", f"文件为空:{ref}"
    return "pass", f"文件存在且非空:{ref}"


def _check_format_valid(workspace_root: Path, ref: str | None) -> tuple[str, str]:
    """Auto-check: ref file passes a syntax lint (json/yaml/markdown by extension).

    YAML is linted as JSON-tolerant when possible; unknown extensions fall back to
    'non-empty + readable'. This is the implementable subset; semantic validity is
    a D4 缺口 (needs_human) and is NOT claimed here.
    """
    if not ref:
        return "missing", "未提供证据引用"
    target = _resolve_under_root(workspace_root, ref)
    if target is None:
        return "fail", f"证据引用越界或不可解析:{ref}"
    if not target.is_file():
        return "fail", f"文件不存在:{ref}"
    suffix = target.suffix.lower()
    text = target.read_text(encoding="utf-8") if target.stat().st_size > 0 else ""
    if suffix == ".json":
        try:
            json.loads(text)
            return "pass", "JSON 语法有效"
        except (json.JSONDecodeError, ValueError) as exc:
            return "fail", f"JSON 语法无效:{exc}"
    if suffix in (".yaml", ".yml"):
        ok, msg = _yaml_lint(text)
        return ("pass", "YAML 语法有效") if ok else ("fail", f"YAML 语法无效:{msg}")
    if suffix in (".md", ".markdown"):
        # Markdown: lenient — non-empty + no unclosed fenced code block
        if not text.strip():
            return "fail", "Markdown 为空"
        fences = text.count("\n```")
        if fences % 2 != 0:
            return "fail", "Markdown 代码围栏未闭合"
        return "pass", "Markdown 可解析"
    # Unknown extension: only assert non-empty (honest about the boundary)
    return ("pass", "文件非空(未做语义校验)") if text.strip() else ("fail", "文件为空")


def _yaml_lint(text: str) -> tuple[bool, str]:
    """Best-effort YAML lint without adding a dependency.

    Tries the stdlib-free heuristic: documents must be non-empty and not contain
    obvious tab-indentation errors. If PyYAML is available (already a dep in this
    repo's runtime), use it; otherwise fall back to the heuristic.
    """
    try:
        import yaml  # type: ignore
        list(yaml.safe_load_all(text))
        return True, ""
    except ImportError:
        pass
    except Exception as exc:  # YAML parse error
        return False, str(exc)
    # Heuristic fallback (no PyYAML): reject tabs in indentation
    for line in text.splitlines():
        if line.startswith("\t"):
            return False, "行首使用了 Tab 缩进(YAML 禁止)"
    return True, ""


# Registry of auto-checkable kinds (活口: add a kind = register a handler)
CHECK_RUNNERS: dict[str, Any] = {
    "file_exists": _check_file_exists,
    "format_valid": _check_format_valid,
}

# Kinds that are explicitly NOT auto-checkable (D4 缺口) → always needs_human
MANUAL_KIND = "manual"


def _resolve_under_root(workspace_root: Path, ref: str) -> Path | None:
    """Resolve a ref under workspace_root, rejecting escapes (S10 / path safety).

    Accepts workspace-relative paths. Absolute paths are allowed only if they
    resolve under workspace_root. ``..`` traversal that escapes the root is rejected.
    """
    text = str(ref).strip()
    if not text:
        return None
    root = workspace_root.resolve()
    candidate = (root / text).resolve() if not text.startswith("/") else Path(text).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


# ---------------------------------------------------------------------------
# ring2 runner — produces the evidence checklist for a submission
# ---------------------------------------------------------------------------

def run_ring2_checks(
    workspace_root: Path | str,
    *,
    evidence_type: str | None = None,
    task_mode: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the ring2 evidence checklist for one evidence type.

    Args:
        workspace_root: the workspace (only guaranteed root, S10).
        evidence_type: explicit type id (wins over task_mode).
        task_mode: used to infer the evidence type if evidence_type is absent.
        evidence_refs: list of {check_id, ref, note} the executor submitted.

    Returns:
        {
            "evidence_type": str,
            "label": str,
            "checks": [{id, label, check_kind, status, detail, ref, note}],
            "summary": {auto_total, auto_passed, auto_failed, missing, needs_human},
            "missing_items": [str],   # human-readable list of what is missing
            "ring3_human": [str],
        }
        status is one of: pass | fail | missing | needs_human
    """
    root = Path(workspace_root).resolve()
    spec = get_evidence_type(evidence_type, task_mode=task_mode)
    if spec is None:
        return {
            "evidence_type": evidence_type or "",
            "label": "",
            "resolved": False,
            "error": f"无法解析证据类型(evidence_type={evidence_type!r}, task_mode={task_mode!r})",
            "checks": [],
            "summary": {"auto_total": 0, "auto_passed": 0, "auto_failed": 0, "missing": 0, "needs_human": 0},
            "missing_items": [],
            "ring3_human": [],
        }
    etype_id = next(k for k, v in EVIDENCE_TYPES.items() if v is spec)
    refs_by_check = {}
    for entry in (evidence_refs or []):
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("check_id") or "").strip()
        if cid:
            refs_by_check.setdefault(cid, []).append(entry)

    checks: list[dict[str, Any]] = []
    summary = {"auto_total": 0, "auto_passed": 0, "auto_failed": 0, "missing": 0, "needs_human": 0}
    missing_items: list[str] = []

    for check in spec["ring2_checks"]:
        cid = check["id"]
        kind = check.get("check_kind", MANUAL_KIND)
        submitted = refs_by_check.get(cid, [])
        ref = str(submitted[0].get("ref") or "").strip() if submitted else ""
        note = str(submitted[0].get("note") or "").strip() if submitted else ""

        if kind == MANUAL_KIND:
            status, detail = "needs_human", "需人判(D4 缺口:不可自动判定)"
            summary["needs_human"] += 1
        elif kind in CHECK_RUNNERS:
            summary["auto_total"] += 1
            status, detail = CHECK_RUNNERS[kind](root, ref or None)
            if status == "pass":
                summary["auto_passed"] += 1
            elif status == "fail":
                summary["auto_failed"] += 1
            elif status == "missing":
                summary["missing"] += 1
                missing_items.append(f"{check['label']}(check_id={cid}):未提供证据引用")
        else:
            # Unknown kind: treat as needs_human (honest, never auto-pass)
            status, detail = "needs_human", f"未知 check_kind={kind},按需人判处理"
            summary["needs_human"] += 1

        checks.append({
            "id": cid,
            "label": check["label"],
            "check_kind": kind,
            "status": status,
            "detail": detail,
            "ref": ref or None,
            "note": note or None,
        })

    return {
        "evidence_type": etype_id,
        "label": spec["label"],
        "resolved": True,
        "checks": checks,
        "summary": summary,
        "missing_items": missing_items,
        "ring3_human": list(spec.get("ring3_human", [])),
    }


def checklist_human_summary(checklist: dict[str, Any]) -> str:
    """One-line layered conclusion: '自动检查 N/M 通过,K 项需人工确认(缺 J 项)'."""
    s = checklist.get("summary", {})
    auto_total = int(s.get("auto_total", 0))
    auto_passed = int(s.get("auto_passed", 0))
    needs_human = int(s.get("needs_human", 0))
    missing = int(s.get("missing", 0))
    parts = [f"自动检查 {auto_passed}/{auto_total} 通过"]
    if needs_human:
        parts.append(f"{needs_human} 项需人工确认")
    if missing:
        parts.append(f"缺 {missing} 项证据")
    return "、".join(parts) + "。"


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
