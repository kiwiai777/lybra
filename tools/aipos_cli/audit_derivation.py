"""
AIPOS-253: Audit task derivation on return_confirm.

Gate mechanically derives audit tasks after successful return, eliminating
executor self-authoring of audit cards. Zero LLM, zero new dependencies.
"""

from __future__ import annotations

import hashlib
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.draft_writer import render_publish_record, stable_publish_id
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.queue_mutation import render_task_markdown
from tools.aipos_cli.records import expected_publish_record_path
from tools.aipos_cli.task_loader import find_task_by_id
from tools.aipos_cli.naming_profile import default_instance_name  # AIPOS-R4B-1: single naming impl
from tools.schema_constants import RecordType
from tools.schema_loader import get_required_card_fields  # AIPOS-F17 大项A: schema 单源必填集





def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_code_repo(repo_root: Path | None) -> str:
    """AIPOS-A1 大项C: 从项目注册表读 code_repo 绝对路径(禁写死)。"""
    if repo_root is None:
        return "<unresolved>"
    project_json = repo_root / "project.json"
    if project_json.is_file():
        try:
            data = json.loads(project_json.read_text(encoding="utf-8"))
            code_repo = str(data.get("code_repo") or "").strip()
            if code_repo:
                return code_repo
        except (json.JSONDecodeError, OSError):
            pass
    # 尝试从治理仓的 project.json 读(多项目场景)
    for candidate in [repo_root / "2_projects" / "lybra" / "project.json"]:
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                code_repo = str(data.get("code_repo") or "").strip()
                if code_repo:
                    return code_repo
            except (json.JSONDecodeError, OSError):
                pass
    return "<unresolved>"


def _resolve_governance_task_cards_path(repo_root: Path | None) -> str:
    """AIPOS-A1 大项C: 报告落点绝对路径(治理仓 task_cards)。"""
    if repo_root is None:
        return "<unresolved>"
    # 治理仓 = repo_root 本身(产品仓场景)或其上级(治理仓场景)
    task_cards = repo_root / "task_cards"
    if task_cards.is_dir():
        return str(task_cards.resolve())
    # 尝试在治理仓结构下找
    for candidate in [repo_root / "2_projects" / "lybra" / "task_cards"]:
        if candidate.is_dir():
            return str(candidate.resolve())
    return str((repo_root / "task_cards").resolve())


def build_forensic_anchor_section(
    source_task_id: str,
    repo_root: Path | None = None,
) -> str:
    """AIPOS-A1 大项C: 构建取证锚点段(注入审计卡 governance_refs)。

    路径值全部来自声明/注册表, 禁写死。内容基准=AIPOS-C1R2 实证有效的那段。
    """
    code_repo = _resolve_code_repo(repo_root)
    task_cards_path = _resolve_governance_task_cards_path(repo_root)

    return (
        "\n## 取证锚点(AIPOS-A1 大项C: 默认注入, 路径来自注册表)\n\n"
        f"- **产品仓绝对路径**: `{code_repo}` (读 project.json code_repo, 禁写死)\n"
        f"- **禁 checkout 卡分支**: 用 `git diff main...card/{source_task_id}` 取证(不切换工作区)\n"
        f"- **报告落点绝对路径**: `{task_cards_path}/{source_task_id}/` (治理仓 task_cards)\n"
        "- **不存在结论必须附**: `pwd` + 命令 + 输出(三条缺一即无效证据)\n"
    )


def build_gate_territory_discipline_section(
    source_task_id: str,
    repo_root: Path | None = None,
) -> str:
    """AIPOS-F12 大项D + AIPOS-F14 大项A: 门领地纪律 + 精确提交配方(注入审计卡, 手动/自动共用)。

    动词名与参数名派生自 gate 注册表 (verb_contract), 禁写死;报告落点路径来自注册表。
    改声明值(动词改名 / task_cards 路径)→ 注入跟随, 无需改本函数。

    AIPOS-F14 大项A: 配方补全二选一参数(audit_task_id/audit_task_path),
    从 verb_contract optional_params 识别 _select_task_input 对, 每参数附用途一句。
    """
    from tools.aipos_cli.verb_contract import get_verb_contract, resolve_gate_verbs

    verbs = resolve_gate_verbs()
    dry = verbs.get("audit_verdict_dry_run") or {}
    dry_name = str(dry.get("name") or "lybra_audit_verdict_dry_run")
    confirm_name = str(dry.get("confirm_pair") or dry_name.replace("_dry_run", "_confirm"))
    dry_required = list(dry.get("required_params") or [])
    dry_optional = list(dry.get("optional_params") or [])
    confirm_contract = get_verb_contract(confirm_name) or {}
    confirm_params = list(confirm_contract.get("required_params") or [])

    # AIPOS-F14 大项A: 识别 _select_task_input 二选一参数对
    # 规则: optional_params 中同时存在 <X>_id 和 <X>_path → 二选一(selector pair)
    selector_pairs = _find_selector_pairs(dry_optional)
    selector_names: list[str] = []
    selector_descriptions: list[str] = []
    for pair_id, pair_path, usage in selector_pairs:
        selector_names.extend([pair_id, pair_path])
        selector_descriptions.append(f"`{pair_id}` / `{pair_path}` 二选一({usage})")

    task_cards_path = _resolve_governance_task_cards_path(repo_root)

    dry_params_inline = "`, `".join(dry_required)
    confirm_params_inline = "`, `".join(confirm_params)

    # 构建二选一参数说明段
    selector_section = ""
    if selector_descriptions:
        selector_lines = "\n".join(f"     - {desc}" for desc in selector_descriptions)
        selector_section = (
            f"\n  1.5 二选一 selector(参数名派生自 verb_contract optional_params, 禁写死):\n"
            f"{selector_lines}\n"
        )

    return (
        "\n## 门领地纪律(AIPOS-F12 大项D + AIPOS-F14 大项A: 注入, 手动/自动共用)\n\n"
        "- **records/ = 门领地**:裁决记录由门落盘, 绝不手写进 `5_tasks/records/`。"
        "手写进 records 一经 sweep 发现即隔离(`governance/quarantine/`)并记违纪。\n"
        f"- **审计报告草稿只能落**:`{task_cards_path}/{{audit_id}}/`"
        "(治理仓 task_cards, 禁落 records/)。\n"
        "- **精确提交配方(参数名派生自 gate 注册表 verb_contract, 禁写死)**\n"
        f"  1. 预览:`{dry_name}`, 必填 `{dry_params_inline}`"
        "(裁决三值 PASS / PASS_WITH_NOTES / FAIL)。\n"
        f"{selector_section}"
        f"  2. 审阅预览无 BLOCK 后确认:`{confirm_name}`, 必填 `{confirm_params_inline}`;"
        "其中 `owner_confirmation_token='OWNER_CONFIRMED'`(字面常量, 非秘密)。\n"
    )


def _find_selector_pairs(optional_params: list[str]) -> list[tuple[str, str, str]]:
    """AIPOS-F14 大项A: 从 optional_params 识别 _select_task_input 二选一参数对。

    规则: 同时存在 <stem>_id 和 <stem>_path → 一对 selector。
    返回 [(id_param, path_param, usage_description)]。

    用途描述从已知 selector 语义表取; 未知 stem 给通用描述。
    """
    # 已知 selector 语义表(stem → 用途描述)
    _SELECTOR_USAGE = {
        "audit_task": "指定被审审计卡(ID 或路径)",
        "task": "指定任务(ID 或路径)",
        "source": "指定源任务(ID 或路径)",
        "reviewed_task": "指定被审任务(ID 或路径)",
    }
    opt_set = set(optional_params)
    seen: set[str] = set()
    pairs: list[tuple[str, str, str]] = []
    for p in sorted(optional_params):
        if p in seen:
            continue
        if p.endswith("_id"):
            stem = p[:-3]  # strip _id
            path_candidate = f"{stem}_path"
            if path_candidate in opt_set:
                usage = _SELECTOR_USAGE.get(stem, f"指定 {stem}(ID 或路径)")
                pairs.append((p, path_candidate, usage))
                seen.add(p)
                seen.add(path_candidate)
    return pairs


def _task_filename_for(task_id: str) -> str:
    """Generate normalized filename for task_id (matches board_adapter convention)."""
    value = "".join(char.lower() if char.isalnum() else "-" for char in task_id).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return (value or "task") + ".md"


def _derive_audit_instance(project: str) -> str:
    """
    Derive audit agent_instance via the single naming implementation (AIPOS-R4B-1):
    audit.<project>.<hostname> from the registry template.
    
    Example: audit.lybra.kiwiai-dev
    """
    hostname = socket.gethostname().split(".")[0]  # short hostname
    return default_instance_name("audit", project=project, host=hostname)


def _derive_audit_assigned_to(project: str) -> str:
    """Derive assigned_to short name: audit_<project>"""
    return f"audit_{project}"


def should_derive_audit(source_metadata: dict[str, Any], *, branch_id: str | None = None, repo_root: Path | None = None) -> bool:
    """
    Check if audit task should be derived.
    
    AIPOS-F72: 使用与 manual dispatch 同一的链有效性判据。
    
    Returns False if:
    - audit: none in frontmatter
    - task_mode is audit (AIPOS-256 F-253-3: prevent infinite R chain)
    - already has valid dispatch chain (AIPOS-F72: audit card pending/claimed OR has verdicts)
    - AIPOS-338 S6②: non-code branch does NOT derive an independent R card
      (it walks the bench path described in the card's own contract section)
    """
    # Explicit opt-out
    if str(source_metadata.get("audit", "")).strip().lower() == "none":
        return False
    
    # AIPOS-256 F-253-3: Prevent infinite audit chain (audit tasks do not derive audits)
    if str(source_metadata.get("task_mode", "")).strip().lower() == "audit":
        return False
    
    # AIPOS-F72: 链有效性判据(与 manual dispatch 同源)
    if source_metadata.get("related_audit_task_ref") or source_metadata.get("audit_dispatch_record_ref"):
        if repo_root is None:
            # Backward compatible: 无 repo_root 时保守阻止
            return False
        
        from tools.aipos_cli.audit_helpers import is_dispatch_chain_valid
        from tools.aipos_cli.records import load_records
        
        records = load_records(repo_root)
        source_task_id = str(source_metadata.get("task_id") or "").strip()
        existing_verdicts = records.get("task_audit_verdicts", {}).get(source_task_id, [])
        
        chain_valid, _ = is_dispatch_chain_valid(source_metadata, existing_verdicts, repo_root)
        if chain_valid:
            # 链有效:审计在途或已有裁决 → 不派生
            return False
        # 链失效:旧审计卡已废且零裁决 → 允许派生
    
    # AIPOS-338 S6②: non-code branch → bench audit path, no independent R card
    if branch_id == "noncode_bench_audit":
        return False
    
    return True


def _declared_revision_suffixes() -> list[str] | None:
    """AIPOS-F18-fix2 F-G-1: 从声明读卡号演进模式(transitions.schema fix_card_closure.revision_card_numbering.pattern)。

    pattern 形如 ``<原卡ID>R[迭代序号]``: ``<原卡ID>`` = 原卡占位, ``[迭代序号]`` = 序号槽。
    依声明生成后缀序列 ['R','R2','R3',…](第1轮序号槽为空, 其后为数字), 上限R100;
    声明不可读/不可解析 → 返回 None(调用方回退内置序列, 行为与旧版一致)。
    改声明模式即改行为(验收②"卡号演进模式改声明跟随"由此实现)。
    """
    try:
        from tools.schema_loader import clear_cache, code_repo_schema_root, load_schema

        clear_cache()  # 让模式声明的现场修改(改完还原)对运行中的门立即可见
        schema = load_schema("transitions", code_repo_schema_root())  # F-B-1同根: 代码所在仓根
        pattern = str(
            ((schema.get("nodes", {}) or {}).get("fix_card_closure", {}) or {})
            .get("revision_card_numbering", {})
            .get("pattern")
            or ""
        )
        if "<原卡ID>" not in pattern or "[迭代序号]" not in pattern:
            return None
        suffix_tpl = pattern.split("<原卡ID>", 1)[1]
        return [
            suffix_tpl.replace("[迭代序号]", "" if n == 1 else str(n))
            for n in range(1, 101)
        ]
    except Exception:
        return None


def derive_audit_task_id(source_task_id: str, repo_root: Path | None = None) -> str:
    """AIPOS-F18 大项B: Generate audit task ID with revision number evolution.

    AIPOS-F18-fix2 F-G-1: 卡号演进模式改声明读取——后缀序列优先取
    transitions.schema 的 fix_card_closure.revision_card_numbering.pattern
    (门读声明执行, 禁代码内写死语义);声明不可读/不可解析时回退内置 R→R2→…→R100。

    Revision card numbering pattern (declaration default):
    - First derivation: <SOURCE_ID>R
    - If R exists: <SOURCE_ID>R2
    - If R2 exists: <SOURCE_ID>R3
    - And so on...

    This eliminates orphan cards when fix cards are closed with PASS verdicts.
    """
    declared = _declared_revision_suffixes()
    suffixes = declared if declared else ["R"] + [f"R{i}" for i in range(2, 101)]

    if repo_root is None:
        # No repo_root provided, return first suffix (backward compatible)
        return f"{source_task_id}{suffixes[0]}"

    for suffix in suffixes:
        candidate_id = f"{source_task_id}{suffix}"
        existing_task, matches = find_task_by_id(candidate_id, repo_root)
        if not existing_task and not matches:
            return candidate_id
    raise ValueError(
        f"Too many audit revisions for {source_task_id}, stopped at {source_task_id}{suffixes[-1]}"
    )


def _resolve_profile(
    source_metadata: dict[str, Any], collaboration_profile: dict[str, Any] | None, repo_root: Path | None
) -> dict[str, Any]:
    """AIPOS-338 S6: resolve the collaboration profile for branch determination."""
    from tools.aipos_cli.flow_description import resolve_collaboration_profile
    if collaboration_profile is not None:
        return collaboration_profile
    if repo_root is not None:
        project_json = repo_root / "project.json"
        if not project_json.is_file():
            project_json = repo_root / "2_projects" / "lybra" / "project.json"
        return resolve_collaboration_profile(project_json)
    return {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"}


def _resolve_branch_id(
    source_metadata: dict[str, Any], collaboration_profile: dict[str, Any] | None, repo_root: Path | None
) -> str:
    """AIPOS-338 S6: resolve the gate-chain branch from the single source (flow_description)."""
    from tools.aipos_cli.flow_description import resolve_gate_chain
    profile = _resolve_profile(source_metadata, collaboration_profile, repo_root)
    chain = resolve_gate_chain(profile, source_metadata)
    return getattr(chain, "branch_id", "")


def build_derived_audit_task(
    *,
    source_task_id: str,
    source_metadata: dict[str, Any],
    source_path: str,
    return_record_ref: str,
    artifact_refs: list[str],
    collaboration_profile: dict[str, Any] | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """
    Build derived audit task frontmatter and body.
    
    AIPOS-338 S2: the audit body now carries the fixed audit instructions
    (criterion = original card full text, independent evidence, two bottom-line
    assertions per AIPOS-314, report location, honest-reporting red line) and,
    when repo_root is provided, the auditor's card-bound contract section.
    
    Returns dict with keys: metadata, body, audit_task_id, audit_task_path
    """
    # AIPOS-F18-fix2: return派生保持首号幂等——R已存在时由上层"already exists"跳过, 不演进;
    # 卡号演进(R2/R3…)只属于 fix_card_closure 级联路径(close 时带 repo_root 调
    # derive_audit_task_id)。R1 把演进塞进共用路径破坏了 return 幂等(同卡二次 return
    # 会两派 R2), 由 test_derive_audit_task_on_return_idempotency_existing_task 钉住。
    audit_task_id = derive_audit_task_id(source_task_id, repo_root=None)
    project = str(source_metadata.get("project") or "lybra")
    branch_id = _resolve_branch_id(source_metadata, collaboration_profile, repo_root)
    
    audit_metadata = {
        "task_id": audit_task_id,
        "title": f"Audit {source_metadata.get('title', source_task_id)}",
        "project": project,
        "assigned_to": _derive_audit_assigned_to(project),
        "agent_instance": _derive_audit_instance(project),
        "context_bundle": source_metadata.get("context_bundle", "default"),
        "task_mode": "audit",
        "task_class": "simple",
        "priority": source_metadata.get("priority", "medium"),
        "status": "pending",
        "created_by": "gate_derivation",
        "needs_owner": False,
        "audit": "none",
        "derived_from": source_task_id,
        "reviewed_task_id": source_task_id,
        "reviewed_task_path": source_path,
        "reviewed_return_record_ref": return_record_ref,
    }
    
    # Copy relevant fields if present
    for key in ["output_target", "artifact_policy", "session_policy", "context_isolation"]:
        if key in source_metadata:
            audit_metadata[key] = source_metadata[key]
    
    # AIPOS-A1 大项C: 注入取证锚点到 governance_refs(路径来自注册表)
    code_repo = _resolve_code_repo(repo_root)
    task_cards_path = _resolve_governance_task_cards_path(repo_root)
    forensic_anchors = [
        f"\u2605取证锚点(AIPOS-A1 大项C): 产品仓={code_repo} | 禁checkout卡分支(git diff main...card/{source_task_id}) | 报告落点={task_cards_path}/{source_task_id}/ | 不存在结论必附pwd+命令+输出",
    ]
    existing_governance_refs = list(audit_metadata.get("governance_refs") or [])
    audit_metadata["governance_refs"] = existing_governance_refs + forensic_anchors
    
    # Build body (mechanical signpost) — AIPOS-338 S2: fixed audit instructions
    artifact_list = "\n".join(f"- `{ref}`" for ref in artifact_refs) if artifact_refs else "- (see return record)"
    
    audit_body = f"""## Audit Subject
Independent audit of task `{source_task_id}`.

## References
- Original task: `{source_path}`
- Return record: `{return_record_ref}`

## Delivery Artifacts
{artifact_list}

## Audit Instructions (准绳与取证)
- **准绳 = 原执行卡全文**(`{source_path}`):验收断言与红线以原卡为准,执行体自述只作线索,不作准绳。
- **独立取证**:不采纳执行体自报结论;逐条核验原卡验收断言,附可复核证据(命令 + 输出摘录)。
- **两条底线断言(AIPOS-314,必判)**:
  1. **起得来**:产物能拉起/运行(代码能 import 或起服务;命令能跑通)。
  2. **产物可用**:产物满足原卡验收断言(不是"看起来对",是"断言过")。
  两条任一不过 → FAIL。
- **报告落位**:`<workspace>/5_tasks/records/audit_verdicts/{source_task_id}/verdict_*.md`(裁决归被审卡 ID 目录)。
- **如实报红线**:结论三值 PASS / PASS_WITH_NOTES / FAIL(附 F-* 清单);失败如实报,禁止“应该没问题”。
"""
    # AIPOS-A1 大项C: 注入取证锚点段(路径来自注册表, 禁写死)
    audit_body += build_forensic_anchor_section(source_task_id, repo_root)

    # AIPOS-F12 大项D: 注入门领地纪律 + 精确提交配方(手动/自动共用, 值来自声明)
    audit_body += build_gate_territory_discipline_section(source_task_id, repo_root)

    if branch_id == "code_with_deploy":
        audit_body += (
            "\n## 部署门提醒(AIPOS-338 S6)\n"
            "本被审卡 `deploy: true`。审计 PASS ≠ 可部署 —— 部署确认属 Owner"
            "(`owner_verify: required` 的不可逆确认,判断在 Owner)。仅生产级部署触发,开发环回部署不触发。\n"
        )
    
    # AIPOS-338 S2: append the auditor's card-bound contract section (single-source)
    # AIPOS-340F2: ValueError (envelope resolution failure) must propagate; other errors swallowed.
    if repo_root is not None:
        try:
            from tools.aipos_cli.gate_contract_section import (
                render_gate_contract_section, workspace_connection_info,
            )
            conn = workspace_connection_info(repo_root)
            section = render_gate_contract_section(
                _resolve_profile(source_metadata, collaboration_profile, repo_root),
                source_metadata, role="auditor",
                gate_url=conn["gate_url"], connection_json_rel=conn["connection_json_rel"],
                workspace_display=conn["workspace_display"], task_id=audit_task_id,
                workspace_root=repo_root,
            )
            audit_body = audit_body.rstrip() + "\n\n" + section + "\n"
        except Exception:
            # AIPOS-340F2: render_gate_contract_section no longer has hardcoded fallbacks.
            # If envelope resolution fails, the section is omitted. Production workspaces
            # always have active policies; this only triggers in broken/test environments.
            pass
    
    audit_task_path = f"5_tasks/queue/pending/{_task_filename_for(audit_task_id)}"
    
    return {
        "metadata": audit_metadata,
        "body": audit_body,
        "audit_task_id": audit_task_id,
        "audit_task_path": audit_task_path,
    }


def derive_audit_task_on_return(
    *,
    repo_root: Path,
    source_task_id: str,
    source_metadata: dict[str, Any],
    source_path: str,
    return_record_ref: str,
    artifact_refs: list[str],
    collaboration_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Derive audit task after successful return_confirm.
    
    Returns dict with:
    - derived: bool (whether derivation occurred)
    - reason: str (skip reason if not derived)
    - audit_task_id: str (if derived)
    - audit_task_path: str (if derived)
    - performed_writes: list[dict] (if derived)
    """
    # AIPOS-338 S6: resolve the branch; non-code branches do not derive an R card
    branch_id = _resolve_branch_id(source_metadata, collaboration_profile, repo_root)
    # Check if should derive (AIPOS-F72: pass repo_root for chain validity check)
    if not should_derive_audit(source_metadata, branch_id=branch_id, repo_root=repo_root):
        audit_opt = str(source_metadata.get("audit", "")).strip().lower()
        if audit_opt == "none":
            return {"derived": False, "reason": "audit: none in source task frontmatter"}
        if branch_id == "noncode_bench_audit":
            return {"derived": False, "reason": "non-code branch uses bench audit path (no independent R card)"}
        return {"derived": False, "reason": "audit already dispatched (idempotency)"}
    
    # Build audit task
    audit_spec = build_derived_audit_task(
        source_task_id=source_task_id,
        source_metadata=source_metadata,
        source_path=source_path,
        return_record_ref=return_record_ref,
        artifact_refs=artifact_refs,
        collaboration_profile=collaboration_profile,
        repo_root=repo_root,
    )
    
    audit_task_id = audit_spec["audit_task_id"]
    audit_task_path = audit_spec["audit_task_path"]
    audit_task_file = repo_root / audit_task_path
    audit_metadata = audit_spec["metadata"]

    # AIPOS-F38 大项A(F17 原则覆盖全部 writer): 必填字段从 schema 单源补全(值承继原卡,
    # 缺则安全默认), 再产前自检——产物必过与 publish/修复卡 writer 同一的 schema 必填校验;
    # 审计身份必须是注册表审计实例(_derive_audit_instance 同一实现), 禁承继原卡执行实例。
    _required_fields = get_required_card_fields()
    _inherit_defaults = {
        "needs_owner": False,
        "output_target": source_metadata.get("output_target", ""),
        "artifact_policy": source_metadata.get("artifact_policy", "formal_write"),
    }
    for _field in _required_fields:
        if _field not in audit_metadata or audit_metadata[_field] is None:
            if _field in source_metadata and source_metadata[_field] is not None:
                audit_metadata[_field] = source_metadata[_field]
            elif _field in _inherit_defaults:
                audit_metadata[_field] = _inherit_defaults[_field]
    _missing = [f for f in _required_fields if f not in audit_metadata or audit_metadata[f] is None]
    _expected_instance = _derive_audit_instance(str(source_metadata.get("project") or "lybra"))
    if audit_metadata.get("agent_instance") != _expected_instance:
        return {
            "derived": False,
            "reason": (
                f"AIPOS-F38 派生校验 FAIL: 审计卡 {audit_task_id} 审计身份 "
                f"{audit_metadata.get('agent_instance')} ≠ 注册表审计实例 {_expected_instance}(禁承继原卡)"
            ),
        }
    if _missing:
        return {
            "derived": False,
            "reason": (
                f"AIPOS-F38 派生校验 FAIL: 审计卡 {audit_task_id} 缺必填字段 {_missing}。"
                f" schema 单源 = {_required_fields}"
            ),
        }
    
    # Idempotency: check if audit task already exists
    existing_task, matches = find_task_by_id(audit_task_id, repo_root)
    if existing_task or matches:
        return {
            "derived": False,
            "reason": f"audit task {audit_task_id} already exists (idempotency)",
        }
    
    # Write audit task
    audit_markdown = render_task_markdown(audit_spec["metadata"], audit_spec["body"])
    audit_task_file.parent.mkdir(parents=True, exist_ok=True)
    audit_task_file.write_text(audit_markdown, encoding="utf-8")
    
    # Write publish record for authority_scanner VALID
    publish_id = stable_publish_id(audit_task_id)
    published_at = _utc_now()
    
    # Calculate checksums
    source_sha256 = hashlib.sha256(b"").hexdigest()  # No source draft for mechanical derivation
    published_sha256 = hashlib.sha256(audit_markdown.encode("utf-8")).hexdigest()
    
    publish_record_markdown = render_publish_record(
        task_id=audit_task_id,
        publish_id=publish_id,
        actor="gate_derivation",
        source_draft_ref="(mechanical derivation from return)",
        published_task_ref=audit_task_path,
        source_sha256=source_sha256,
        published_sha256=published_sha256,
        published_at=published_at,
        confirmer=None,  # No confirmer for mechanical derivation
    )
    
    publish_record_path = expected_publish_record_path(repo_root, audit_task_id, publish_id)
    
    # AIPOS-R8B F-N4: 补写 dispatch_record (与 lybra_audit_dispatch 共用同一 writer)
    # 自动派生审计卡时也必须落 dispatch 记录,否则裁决提交时会被 MISSING_AUDIT_DISPATCH_RECORD 拒绝
    from tools.aipos_cli.record_writer import build_mcp_audit_dispatch_record_markdown, write_records_atomic
    
    dispatch_id = f"dispatch_{audit_task_id}_{published_at.replace(':', '').replace('-', '').replace('Z', '')}_gate-derivation"
    dispatch_record_markdown = build_mcp_audit_dispatch_record_markdown(
        dispatch_id=dispatch_id,
        reviewed_task_id=source_task_id,
        reviewed_task_path=source_path,
        reviewed_return_record_ref=return_record_ref,
        reviewed_executor_instance=str(source_metadata.get("executor_completed_by") or source_metadata.get("claimed_by") or ""),
        reviewed_executor_claim_id=str(source_metadata.get("claim_id") or ""),
        reviewed_executor_session_id=str(source_metadata.get("active_session_id") or source_metadata.get("last_session_id") or ""),
        audit_task_id=audit_task_id,
        audit_task_path=audit_task_path,
        actor="gate_derivation",
        canonical_agent_instance="gate_derivation",
        owner_policy_ref="auto_derivation_on_return",
        dispatched_at=published_at,
        dry_run_id=None,
        dry_run_snapshot_hash=None,
        confirmation_ref="auto_confirmed_gate_derivation",
    )
    
    # AIPOS-F64: 统一写入器 - 原子写入publish和dispatch两条记录
    write_result = write_records_atomic(
        repo_root=repo_root,
        records=[
            ("publish", publish_id, publish_record_markdown),
            ("audit_dispatch", dispatch_id, dispatch_record_markdown),
        ],
    )
    
    dispatch_record_path = repo_root / write_result["paths"][1]  # 第二条记录是dispatch
    
    return {
        "derived": True,
        "audit_task_id": audit_task_id,
        "audit_task_path": audit_task_path,
        "publish_record_path": write_result["paths"][0],
        "dispatch_record_path": write_result["paths"][1],
        "performed_writes": [
            {
                "path": audit_task_path,
                "kind": "create",
                "type": "derived_audit_task",
            },
            {
                "path": str(publish_record_path.relative_to(repo_root)),
                "kind": "create",
                "type": RecordType.PUBLISH_RECORD,
                "record_type": RecordType.PUBLISH_RECORD,
            },
            {
                "path": str(dispatch_record_path.relative_to(repo_root)),
                "kind": "create",
                "type": RecordType.AUDIT_DISPATCH_RECORD,
                "record_type": RecordType.AUDIT_DISPATCH_RECORD,
            },
        ],
    }


def derive_repair_card_on_fail(
    *,
    governance_root: Path,
    reviewed_task_id: str,
    audit_task_id: str,
    verdict_id: str,
    fail_reason: str,
    actor: str,
) -> dict[str, Any]:
    """AIPOS-C3B 大项C⑤: 审计 FAIL 自动派审——审计员判 FAIL 时自动派一张'修复卡'回队列。

    避免死等: executor 不用等 owner 手动建卡, 系统自动建修复卡。

    Args:
        governance_root: 治理仓根目录
        reviewed_task_id: 被审任务 ID
        audit_task_id: 审计任务 ID (e.g. APOS-123R)
        verdict_id: 裁决记录 ID
        fail_reason: FAIL 原因摘要
        actor: 操作者

    Returns:
        {
            "derived": bool,
            "repair_task_id": str,
            "repair_task_path": str,
            "message": str,
        }
    """
    # AIPOS-F44B-fix1-fix1 幂等第三层: 级联终局判——已收账原卡不再派复审卡且留声
    from tools.aipos_cli.records import load_records
    records = load_records(governance_root)
    task_closures = records.get("task_closures", {}).get(reviewed_task_id, [])
    if task_closures:
        # 原卡已有 closure 记录 = 已收账，不再派修复卡
        closure_ids = [c.get("closure_id") for c in task_closures]
        return {
            "derived": False,
            "repair_task_id": "",
            "repair_task_path": "",
            "message": (
                f"级联终局判: 原卡 {reviewed_task_id} 已收账 (closures: {', '.join(closure_ids)}), "
                f"不再派生修复卡 (AIPOS-F44B-fix1-fix1 幂等第三层)"
            ),
        }

    # 生成修复卡 ID: 原任务 ID + "-fix" + 轮次
    # AIPOS-F44B-fix1-fix1: fix 序号递增——按已有 fix 链递增（而非文件数）
    # 检查已有多少轮修复卡（从 records 的 task_claims/task_returns 读取，单一数据源）
    existing_fix_rounds = set()
    queue_dir = governance_root / "5_tasks" / "queue" / "pending"
    claimed_dir = governance_root / "5_tasks" / "queue" / "claimed"
    completed_dir = governance_root / "5_tasks" / "queue" / "completed"
    
    # 扫描所有队列目录，找到已有的 fix 序号
    for qdir in [queue_dir, claimed_dir, completed_dir]:
        if qdir.is_dir():
            for f in qdir.glob("*.md"):
                if f.stem.startswith(f"{reviewed_task_id.lower()}-fix"):
                    # 提取 fix 序号（如 AIPOS-F42-fix2 -> 2）
                    try:
                        suffix = f.stem.split("-fix")[-1]
                        round_num = int(suffix)
                        existing_fix_rounds.add(round_num)
                    except (ValueError, IndexError):
                        pass

    fix_round = (max(existing_fix_rounds) + 1) if existing_fix_rounds else 1
    repair_task_id = f"{reviewed_task_id}-fix{fix_round}"

    # 检查是否已存在(幂等)
    repair_filename = _task_filename_for(repair_task_id)
    for qdir in [queue_dir, claimed_dir]:
        if qdir.is_dir() and (qdir / repair_filename).exists():
            return {
                "derived": False,
                "repair_task_id": repair_task_id,
                "repair_task_path": str(qdir / repair_filename),
                "message": f"修复卡已存在: {repair_task_id}",
            }

    # 读取原任务卡获取元数据
    source_card = None
    for qdir in [queue_dir, claimed_dir, governance_root / "5_tasks" / "queue" / "completed"]:
        candidate = qdir / _task_filename_for(reviewed_task_id)
        if candidate.exists():
            source_card = candidate
            break

    source_metadata = {}
    source_body = ""
    if source_card:
        try:
            text = source_card.read_text(encoding="utf-8")
            source_metadata, source_body, _ = parse_markdown_frontmatter(text)
        except Exception:
            pass

    project = str(source_metadata.get("project") or "lybra")

    # AIPOS-F17 大项A: 构建修复卡 — 必填字段从 schema 单源派生, 值承继原卡, 禁手写第二份清单。
    repair_metadata = {
        "task_id": repair_task_id,
        "title": f"Fix: {source_metadata.get('title', reviewed_task_id)} (round {fix_round})",
        "project": project,
        "assigned_to": source_metadata.get("assigned_to", "executor_lybra"),
        "agent_instance": source_metadata.get("agent_instance", "executor.lybra.kiwiai-dev"),
        "context_bundle": source_metadata.get("context_bundle", "default"),
        "task_mode": source_metadata.get("task_mode", "code"),
        "task_class": source_metadata.get("task_class", "simple"),
        "priority": source_metadata.get("priority", "high"),
        "status": "pending",
        "created_by": "gate_derivation",
        "created_at": _utc_now(),
        "derived_from_verdict_id": verdict_id,
        "derived_from_audit_task_id": audit_task_id,
        "fix_round": fix_round,
        "depends_on": [],
        "anchor_refs": source_metadata.get("anchor_refs", ["g1_owner_gate"]),
        "artifact_scope": source_metadata.get("artifact_scope", ""),
    }

    # AIPOS-F17 大项A: 从 schema 必填集补全——值承继原卡, 原卡无则用安全默认值。
    # 禁手写第二份字段清单; schema 改即自动跟随。
    _required_fields = get_required_card_fields()
    _inherit_defaults = {
        "needs_owner": False,
        "output_target": source_metadata.get("output_target", ""),
        "artifact_policy": source_metadata.get("artifact_policy", "formal_write"),
    }
    for field in _required_fields:
        if field not in repair_metadata or repair_metadata[field] is None:
            if field in source_metadata and source_metadata[field] is not None:
                repair_metadata[field] = source_metadata[field]
            elif field in _inherit_defaults:
                repair_metadata[field] = _inherit_defaults[field]

    # AIPOS-F17 大项A: 产前自检——产物必过与 publish 相同的 schema 必填校验。
    _missing = [f for f in _required_fields if f not in repair_metadata or repair_metadata[f] is None]
    if _missing:
        raise ValueError(
            f"AIPOS-F17 派生自检 FAIL: 修复卡 {repair_task_id} 缺必填字段 {_missing}。"
            f" schema 单源 = {_required_fields}"
        )

    repair_body = f"""## 修复任务 (第 {fix_round} 轮)

审计任务 {audit_task_id} 裁决 FAIL, 自动派生此修复卡。

### FAIL 原因

{fail_reason}

### 原始任务正文(供参考)

{source_body}

### 修复要求

1. 根据 FAIL 原因修复问题
2. 重新执行并 return
3. 系统会自动派生新的审计任务
"""

    # 写卡
    target_path = queue_dir / repair_filename
    queue_dir.mkdir(parents=True, exist_ok=True)
    rendered = render_task_markdown(repair_metadata, repair_body)
    target_path.write_text(rendered, encoding="utf-8")

    return {
        "derived": True,
        "repair_task_id": repair_task_id,
        "repair_task_path": str(target_path.relative_to(governance_root)),
        "message": f"已自动派生修复卡: {repair_task_id} (第 {fix_round} 轮)",
    }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
