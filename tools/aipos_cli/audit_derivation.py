"""
AIPOS-253: Audit task derivation on return_confirm.

Gate mechanically derives audit tasks after successful return, eliminating
executor self-authoring of audit cards. Zero LLM, zero new dependencies.
"""

from __future__ import annotations

import hashlib
import json
from tools.aipos_cli.clock import iso_z
from pathlib import Path
from typing import Any

from tools.aipos_cli.draft_writer import render_publish_record, stable_publish_id
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.queue_mutation import render_task_markdown
from tools.aipos_cli.records import expected_publish_record_path
from tools.aipos_cli.task_loader import find_task_by_id, queue_root_for, queue_state_ref
from tools.aipos_cli.naming_profile import default_instance_name  # AIPOS-R4B-1: single naming impl
from tools.schema_constants import RecordType
from tools.schema_loader import get_required_card_fields  # AIPOS-F17 大项A: schema 单源必填集
from tools.aipos_cli.record_writer import card_field_defaults, record_dir  # AIPOS-F108 件①: 卡字段缺省值读 card.schema 声明; AIPOS-F109 件①: 记录落点读声明
from tools.aipos_cli.next_resolver import task_mode_is_audit  # AIPOS-F216 件④: 审计卡识别卡面层单源





def _resolve_code_repo(repo_root: Path | None, source_metadata: dict[str, Any] | None = None) -> str:
    """AIPOS-A1 大项C + F78C 件②: 审计卡取证锚点的产品仓 = 被审卡声明的仓(workspace_config.resolve_card_repo 唯一解析:
    卡 lane.repo → project.json repos 清单 → code_repo 别名; 禁写死、禁第二读法)。repo_root 缺 = "<unresolved>";
    解析失败 = 出声(stderr)并以 "<unresolved: CODE>" 入锚点, 不吞、不猜。"""
    if repo_root is None:
        return "<unresolved>"
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, resolve_card_repo

    try:
        return str(resolve_card_repo(repo_root, source_metadata or {}))
    except CardRepoUnresolved as exc:
        import sys

        print(f"Warning: 审计卡取证锚点产品仓不可解析: {exc}", file=sys.stderr)
        return f"<unresolved: {exc.code}>"


def render_audit_report_location(governance_root: Path | None, audit_task_id: str) -> str:
    """AIPOS-F66B 件③: 审计报告落点句子的**唯一渲染函数**。

    落点 = 治理根 <paths.verdict_root>/<审计卡ID>/<首个候选文件>(lybra 形 = task_cards/<审计卡ID>/RETURN.md)。
    声明两处既有: config.schema project_json.paths.verdict_root(落点根, 与 F78 artifact_ingest 的 return 落点声明同源)
    + transitions.schema artifact_ingest.verdict.verdict_file_candidates(文件候选);
    读取口 = next_resolver.audit_report_artifact_path(与 card render / ingest 同一函数)。
    派生审计卡内**所有**落点句子(取证锚点段 / 门领地纪律段 / governance_refs 锚点 / 审计指令「报告落位」)
    出自本函数; 禁写死 task_cards/{被审卡ID}/(2026-09-22 审计体因两处矛盾把 F79DR 报告写到产品仓根)。
    governance_root 缺 = 渲染声明相对位 `<governance_root>/<verdict_root 声明缺省>/<审计卡ID>/<候选>`, 不猜绝对路径。
    """
    audit_task_id = str(audit_task_id or "").strip()
    if not audit_task_id:
        raise ValueError("render_audit_report_location: audit_task_id 为空(报告落点按审计卡 ID 目录, 禁用被审卡 ID)")
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration, audit_report_artifact_path

    if governance_root is not None:
        return str(audit_report_artifact_path(Path(governance_root), audit_task_id))
    from tools.aipos_cli.workspace_config import _project_paths_declaration

    default_root = str((_project_paths_declaration().get("verdict_root") or {}).get("default") or "").strip()
    cands = list(_artifact_ingest_declaration()["verdict"].get("verdict_file_candidates") or [])
    first = next((str(c) for c in cands if not any(ch in str(c) for ch in "*?[")), None)
    if not default_root or first is None:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("审计报告落点声明缺: config.schema paths.verdict_root.default / transitions artifact_ingest.verdict.verdict_file_candidates")
    return f"<governance_root>/{default_root}/{audit_task_id}/{first}"


def forensic_delivery_subject(repo_root: Path | None, source_task_id: str,
                              source_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F210 件②: 被审卡的交付主体(next_resolver.card_delivery_subject 唯一判定; 被审卡卡面 source_metadata 缺 = 读队列)。
    治理根缺 = 只按卡面判固定候选, 其余按卡分支(card_delivery_subject 同口径, 不猜)。"""
    from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter, card_delivery_subject

    fm = source_metadata
    if fm is None and repo_root is not None:
        path, _q = _find_task_in_queue(Path(repo_root), source_task_id)
        fm = _read_frontmatter(path) if path else {}
    return card_delivery_subject(repo_root, source_task_id, {**(fm or {}), "task_id": source_task_id})


def render_forensic_anchor(subject: dict[str, Any], *, form: str = "line") -> str:
    """AIPOS-F210 件②(F208 P0-2): 审计取证锚点的唯一渲染——按被审卡交付主体三态(forensic_delivery_subject)给取证动作:
    卡分支 = `git diff <基线>...<被审分支>`(禁 checkout 卡分支); 固定候选 = 只读该提交 + 源码送达核 src-verify(无卡分支可 diff);
    治理根产出清单 = 核交回绑定的 outputs_digest(无卡分支、不涉产品仓)。审计卡正文取证锚点段(form=line)、审计卡 governance_refs
    锚点(form=ref, 自动派生与手动派审同读 forensic_anchor_ref)两种排版, 措辞一处; 禁在别处拼「基线...卡分支」。"""
    from tools.aipos_cli.next_resolver import governance_outputs_kind  # AIPOS-F214 件⑤: kind 值读声明

    if form not in ("line", "ref"):
        raise ValueError(f"render_forensic_anchor: form={form!r} 不在 ('line', 'ref')")
    kind = subject.get("kind")
    if kind == "candidate":
        sha, source = subject.get("candidate"), subject.get("source")
        if not sha:
            problem = subject.get("problem") or "来源卡 / 候选未声明"
            return (f"- **交付主体 = 固定候选(卡面不合)**: {problem}; 被审卡不建卡分支, 无分支可 diff; 裁决入口将按固定候选判据拒收, "
                    "先请顾问修正卡面" if form == "line" else f"交付主体=固定候选(卡面不合: {problem}; 无卡分支, 先请顾问修正卡面)")
        if form == "line":
            return (f"- **交付主体 = 固定候选(只读)**: 来源卡 `{source}` 的固定候选 `{sha}`(被审卡不建卡分支, 禁 diff / checkout 卡分支); "
                    f"只读取证该提交(`git show {sha}`), 源码送达以验证根 src-verify 末行 SRC_OK(送达树 = 候选 tree)为准")
        return f"交付主体=固定候选 {sha}(来源卡 {source}; 只读取证 git show {sha}, 源码送达核 src-verify SRC_OK; 无卡分支)"
    if kind == governance_outputs_kind():
        digest = subject.get("outputs_digest") or "(交回未绑定: 以开工稿 / 报告必填句给值为准)"
        if form == "line":
            return (f"- **交付主体 = 治理根产出清单**: 被审卡只写治理根(无卡分支、不改产品仓, 禁 diff 卡分支); 审计对象 = 交回绑定的产出清单 "
                    f"outputs_digest `{digest}`; 逐项读 Return frontmatter outputs 所列文件核对内容与 sha256(产品入口按同一清单重算核对)")
        return f"交付主体=治理根产出清单(outputs_digest={digest}; 逐项核 Return outputs 文件 sha256; 无卡分支)"
    base, branch = subject["base"], subject["branch"]
    if form == "line":
        return f"- **禁 checkout 卡分支**: 用 `git diff {base}...{branch}` 取证(不切换工作区)"
    return f"禁checkout卡分支(git diff {base}...{branch})"


def forensic_anchor_ref(repo_root: Path | None, source_task_id: str, source_metadata: dict[str, Any] | None,
                        audit_task_id: str) -> str:
    """AIPOS-A1 大项C + AIPOS-F210 件②: 审计卡 governance_refs 的取证锚点一行(自动派生 build_derived_audit_task 与手动派审
    board_adapter._build_audit_dispatch_preview 同读; 原两份字面合一)。产品仓 = 被审卡声明的仓(只写治理根 = 不涉及);
    报告落点 = render_audit_report_location(审计卡 ID 目录)。"""
    from tools.aipos_cli.next_resolver import governance_outputs_kind  # AIPOS-F214 件⑤: kind 值读声明

    subject = forensic_delivery_subject(repo_root, source_task_id, source_metadata)
    code_repo = ("不涉及(被审卡只写治理根)" if subject.get("kind") == governance_outputs_kind()
                 else _resolve_code_repo(repo_root, source_metadata))
    report_location = render_audit_report_location(repo_root, audit_task_id)
    return (f"\u2605取证锚点(AIPOS-A1 大项C): 产品仓={code_repo} | {render_forensic_anchor(subject, form='ref')} | "
            f"报告落点={report_location} | 不存在结论必附pwd+命令+输出")


def build_forensic_anchor_section(
    source_task_id: str,
    repo_root: Path | None = None,
    source_metadata: dict[str, Any] | None = None,
    *,
    audit_task_id: str | None = None,
    audit_metadata: dict[str, Any] | None = None,
) -> str:
    """AIPOS-A1 大项C: 构建取证锚点段(注入审计卡 governance_refs)。

    AIPOS-F162 件①: 卡面声明 audit_runtime_evidence(审计卡 audit_metadata 优先, 缺省被审卡)时追加「独立运行证据要求」段
    (唯一渲染 artifact_ingest.render_runtime_evidence_requirement, 与裁决入口判据同一声明)。

    路径值全部来自声明/注册表, 禁写死。内容基准=AIPOS-C1R2 实证有效的那段。
    AIPOS-F78C: 产品仓按被审卡 source_metadata(lane.repo)解析。
    AIPOS-F66B 件③: 报告落点 = 审计卡 ID 目录(render_audit_report_location 唯一渲染); audit_task_id 缺省
    = 本模块 derive_audit_task_id 首号(与 build_derived_audit_task 同一派生), 手动派审传显式审计卡 ID。
    """
    from tools.aipos_cli.next_resolver import governance_outputs_kind  # AIPOS-F214 件⑤: kind 值读声明

    subject = forensic_delivery_subject(repo_root, source_task_id, source_metadata)  # AIPOS-F210 件②: 交付主体三态
    code_repo = ("不涉及(被审卡只写治理根)" if subject.get("kind") == governance_outputs_kind()
                 else _resolve_code_repo(repo_root, source_metadata))
    report_location = render_audit_report_location(
        repo_root, audit_task_id or derive_audit_task_id(source_task_id, repo_root=None)
    )

    # AIPOS-F149 件③: main 侧基线查询命令(唯一渲染 post_merge_regression.audit_baseline_command, 章程占位同源); 何时复用 / 何时自跑
    # 以章程「独立全量基线」为准, 此处只给本卡实值(治理根 + 被审卡产品仓)。治理根不可得(手动派审未给)= 不出此行, 不猜。
    baseline_line = ""
    if repo_root is not None:
        from tools.aipos_cli.post_merge_regression import audit_baseline_command, baseline_state_labels

        repo_arg = code_repo if not code_repo.startswith("<unresolved") else None
        labels = baseline_state_labels()  # AIPOS-F212 件④: 三态标签唯一声明
        baseline_line = (f"- **main 侧基线先查记录**: `{audit_baseline_command(repo_root, repo_arg)}`"
                         f" (只读; {labels['recorded']} = main 侧不再自跑, {labels['self_run_required']} = 自跑 main 侧; 被审分支一侧必须自跑;"
                         " 细则以审计章程「独立全量基线」为准)\n")
    # AIPOS-F186 件③: 撤换承继卡——差异基线仍为 base_branch, 承继自原卡的提交在 base...卡分支 内一并审(卡面 carried_from 唯一读取口)
    from tools.aipos_cli.next_resolver import card_carried_from

    carried = card_carried_from(source_metadata) if subject.get("kind") == "branch" else None
    carried_line = (f"- **撤换承继**: 本卡分支承继自 `{carried['task_id']}` 分支 `{carried['branch']}`@`{carried['tip']}`; "
                    f"差异基线仍为 `{subject['base']}`(上一行三点 diff 含承继提交, 一并审)\n") if carried else ""
    return (
        "\n## 取证锚点(AIPOS-A1 大项C: 默认注入, 路径来自注册表)\n\n"
        f"- **产品仓绝对路径**: `{code_repo}` (被审卡 lane.repo → project.json repos/code_repo, 禁写死)\n"
        f"{render_forensic_anchor(subject, form='line')}\n"
        f"{carried_line}"
        f"- **报告落点绝对路径**: `{report_location}` (治理根 verdict_root/<审计卡ID>/, 声明渲染; 禁落被审卡目录、禁落产品仓)\n"
        f"{baseline_line}"
        "- **不存在结论必须附**: `pwd` + 命令 + 输出(三条缺一即无效证据)\n"
    ) + _runtime_evidence_section(audit_metadata, source_metadata, repo_root=repo_root, source_task_id=source_task_id,
                                  audit_task_id=audit_task_id or derive_audit_task_id(source_task_id, repo_root=None))


def _runtime_evidence_section(audit_metadata: dict[str, Any] | None, source_metadata: dict[str, Any] | None, *,
                              repo_root: Path | None = None, source_task_id: str = "", audit_task_id: str = "") -> str:
    """AIPOS-F162 件①: 审计卡正文的独立运行证据要求段(未声明 = 空串; 声明形不对 = 点名, 裁决入口届时 fail-closed 拒)。
    AIPOS-F199 件②: 被审卡交付主体为固定候选 = 附「验证卡运行证据步骤」(artifact_ingest.render_candidate_evidence_steps, 开工稿同一渲染);
    治理根不可得(手动派审未给)= 步骤按占位渲染不出, 只给段落(不猜)。"""
    from tools.aipos_cli.artifact_ingest import (
        RuntimeEvidenceDeclarationError, render_candidate_evidence_steps, render_runtime_evidence_requirement, runtime_evidence_requirement,
    )
    from tools.aipos_cli.next_resolver import CandidateSubjectError, card_candidate_subject

    try:
        requirement = runtime_evidence_requirement(audit_metadata, source_metadata)
    except RuntimeEvidenceDeclarationError as exc:
        return f"\n## 独立运行证据要求(AIPOS-F162)\n\n- 卡面声明不合: {exc}; 裁决入口将拒收, 先请顾问修正卡面声明\n"
    if not requirement:
        return ""
    steps: list[str] = []
    try:
        candidate = card_candidate_subject(source_metadata)
        if candidate is not None and repo_root is not None:
            instance = next((str((audit_metadata or {}).get(k) or "").strip() for k in ("claimed_by", "agent_instance", "assigned_to")
                             if str((audit_metadata or {}).get(k) or "").strip()), "") or resolve_audit_instance(source_metadata or {}, repo_root)
            steps = render_candidate_evidence_steps(repo_root, audit_task_id=audit_task_id, audit_instance=instance,
                                                    reviewed_task_id=source_task_id, reviewed_fm=source_metadata, requirement=requirement)
        elif candidate is not None:
            steps = ["### 验证卡运行证据步骤(AIPOS-F199): 治理根未给, 步骤以开工稿为准(lybra kickoff --task-id <审计卡>)"]
    except (CandidateSubjectError, ValueError, OSError) as exc:
        steps = [f"### 验证卡运行证据步骤(AIPOS-F199): 渲染不出({exc}); 裁决入口将按固定候选判据核对, 先请顾问修正卡面"]
    return render_runtime_evidence_requirement(requirement, candidate_steps=steps or None)


def build_gate_territory_discipline_section(
    source_task_id: str,
    repo_root: Path | None = None,
    *,
    audit_task_id: str | None = None,
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

    report_location = render_audit_report_location(
        repo_root, audit_task_id or derive_audit_task_id(source_task_id, repo_root=None)
    )

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
        f"- **审计报告草稿只能落**:`{report_location}`"
        "(治理根 verdict_root/<审计卡ID>/, 声明渲染; 禁落 records/、禁落被审卡目录、禁落产品仓)。\n"
        "- **精确提交配方(参数名派生自 gate 注册表 verb_contract, 禁写死)**\n"
        f"  1. 预览:`{dry_name}`, 必填 `{dry_params_inline}`"
        "(裁决三值 PASS / PASS_WITH_NOTES / FAIL)。\n"
        f"{selector_section}"
        f"  2. 审阅预览无 BLOCK 后确认:`{confirm_name}`, 必填 `{confirm_params_inline}`;"
        "其中 `owner_confirmation_token='OWNER_CONFIRMED'`(字面常量, 非秘密)。\n"
    )


def zero_gate_report_sentence(report_location: str, reviewed_task_id: str | None = None,
                              governance_root: Path | None = None, audit_task_id: str | None = None) -> str:
    """AIPOS-F80 件①: 零门审计卡的落点句(唯一措辞)。落点值只接 render_audit_report_location 的输出。
    AIPOS-F93 件①: 句内带报告必填 frontmatter(声明 artifact_ingest.verdict 单源渲染 report_frontmatter_clause), F92R 缺 commit_sha 被拒的病根。"""
    return (f"报告写到 `{report_location}`; {report_frontmatter_clause(reviewed_task_id, governance_root, audit_task_id)}; "
            "写完即止, 认领与裁决提交由驱动方完成。")


def report_frontmatter_clause(reviewed_task_id: str | None, governance_root: Path | None = None,
                              audit_task_id: str | None = None) -> str:
    """AIPOS-F93 件①: 审计卡正文的报告必填字段句(next_resolver.render_report_frontmatter_clause 单源)。
    AIPOS-F210 件③: 契约 = next_resolver.card_report_contract(审计卡跟随被审卡的交付主体三态: 卡分支 / 固定候选 / 治理根产出清单),
    不另判。被审卡固定候选不合 = 如实写不可推导(裁决入口同判据 fail-closed)。治理根缺(调用方未给)= 交付主体不可判定, 按卡分支契约
    渲染(存量口径; 生产派生与派审均给治理根)。"""
    from tools.aipos_cli.next_resolver import (CandidateSubjectError, card_report_contract, render_report_frontmatter_clause,
                                               report_frontmatter_contract)

    reviewed = str(reviewed_task_id or "").strip() or None
    if governance_root is None:
        return render_report_frontmatter_clause(report_frontmatter_contract("verdict", branch_task_id=reviewed))
    audit_id = str(audit_task_id or "").strip() or derive_audit_task_id(reviewed or "", repo_root=None)
    audit_fm = {"task_id": audit_id, "task_mode": "audit", "reviewed_task_id": reviewed or ""}
    try:
        contract = card_report_contract(Path(governance_root), audit_id, audit_fm)
    except CandidateSubjectError as exc:
        return f"报告 frontmatter 必填: 不可推导(被审卡固定候选不合: {exc}); 裁决入口将同判据拒收, 先请顾问修正被审卡卡面"
    return render_report_frontmatter_clause(contract)


def build_zero_gate_delivery_section(
    source_task_id: str,
    repo_root: Path | None = None,
    *,
    audit_task_id: str | None = None,
) -> str:
    """AIPOS-F80 件①: 零门审计卡的交付纪律节(取代门领地纪律 + 提交配方节; 判据 = draft_writer.card_carries_gate_contract_section)。

    审计体零门(Owner 09-06, 与执行卡 F73C 同口径): 只写报告, 不连门、不调门动词、不写队列/记录区;
    落点句出自 render_audit_report_location(F66B 件③ 单源)。
    """
    report_location = render_audit_report_location(
        repo_root, audit_task_id or derive_audit_task_id(source_task_id, repo_root=None)
    )
    return (
        "\n## 交付纪律(AIPOS-F80 件①: 审计体零门)\n\n"
        f"- {zero_gate_report_sentence(report_location, source_task_id, repo_root, audit_task_id)}\n"
        "- 你不连门、不读凭据、不调任何门动词; 队列与记录区是门领地, 只读不写(裁决由驱动方经门落盘)。\n"
    )


_GATE_TERRITORY_SECTION_RE = r"\n?## 门领地纪律.*?(?=\n## |\Z)"
_REPORT_LOCATION_LINE_RE = r"^- \*\*报告落位\*\*:.*$"


def zero_gate_audit_body(body: str, governance_root: Path | None, audit_task_id: str,
                         reviewed_task_id: str | None = None) -> str:
    """AIPOS-F80 件①: 存量派生审计卡正文零门收口(regen 入口用; 与新派生同一组函数)。

    删「门领地纪律」节(门动词提交配方)、把「报告落位」行换成零门落点句、缺则补交付纪律节;
    「认领与交回」节由调用方按同一判据删除。幂等。
    """
    import re

    report_location = render_audit_report_location(governance_root, audit_task_id)
    out = re.sub(_GATE_TERRITORY_SECTION_RE, "", body, flags=re.DOTALL)
    out = re.sub(
        _REPORT_LOCATION_LINE_RE,
        lambda _m: f"- **报告落位**:{zero_gate_report_sentence(report_location, reviewed_task_id, governance_root, audit_task_id)}",
        out,
        flags=re.MULTILINE,
    )
    if "## 交付纪律(AIPOS-F80" not in out:
        out = out.rstrip() + "\n" + build_zero_gate_delivery_section(reviewed_task_id or "", governance_root,
                                                                      audit_task_id=audit_task_id)
    return out


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


def _registry_prefix(role: str) -> str:
    """角色在注册表的实例名前缀(roles.schema role.naming.prefix 唯一来源); 缺 = ValueError(禁写死前缀)。"""
    from tools.schema_loader import get_role_naming_prefix

    prefix = str(get_role_naming_prefix(role) or "").strip()
    if not prefix:
        raise ValueError(f"roles.schema.json 角色 {role!r} 无 naming.prefix, 实例名无从推导")
    return prefix


def resolve_audit_instance(source_metadata: dict[str, Any], governance_root: str | Path | None = None) -> str:
    """AIPOS-F102 件①: 审计卡认领实例的唯一解析(派审 / 交回派生审计卡 / 修复后复审 同口径)。

    被审卡 audit_by 声明(card.schema)优先; 缺则按项目推导: naming_profile.default_instance_name(审计角色注册表前缀,
    项目 = 被审卡 project, 再缺读治理根 project.json#project; 都缺 = 拒 ProjectSegmentUnresolved)。
    原写死的 lybra 审计实例缺省与「不读 audit_by」退役(非 lybra 项目审计卡认领实例 = 其 audit_by)。
    """
    declared = str(source_metadata.get("audit_by") or "").strip()
    if declared:
        return declared
    project = str(source_metadata.get("project") or "").strip() or None
    return default_instance_name(_registry_prefix("auditor"), project=project, project_root=governance_root)


def resolve_repair_executor_instance(source_metadata: dict[str, Any], governance_root: str | Path | None = None) -> str:
    """AIPOS-F102 件①: 修复卡执行实例——承继被审卡 agent_instance / assigned_to 声明; 都缺按项目推导执行角色实例名
    (default_instance_name, 项目缺 = 拒)。原写死的 lybra 执行实例 / 短名缺省退役。"""
    for key in ("agent_instance", "assigned_to"):
        declared = str(source_metadata.get(key) or "").strip()
        if declared:
            return declared
    project = str(source_metadata.get("project") or "").strip() or None
    return default_instance_name(_registry_prefix("executor"), project=project, project_root=governance_root)


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
    if task_mode_is_audit(source_metadata.get("task_mode")):  # AIPOS-F216 件④: 审计卡识别卡面层单源
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


def _declared_revision_suffixes(*, fresh: bool = True) -> list[str] | None:
    """AIPOS-F18-fix2 F-G-1: 从声明读卡号演进模式(transitions.schema fix_card_closure.revision_card_numbering.pattern)。

    pattern 形如 ``<原卡ID>R[迭代序号]``: ``<原卡ID>`` = 原卡占位, ``[迭代序号]`` = 序号槽。
    依声明生成后缀序列 ['R','R2','R3',…](第1轮序号槽为空, 其后为数字), 上限R100;
    声明不可读/不可解析 → 返回 None(调用方回退内置序列, 行为与旧版一致)。
    改声明模式即改行为(验收②"卡号演进模式改声明跟随"由此实现)。
    AIPOS-F112: fresh=False = 只读审计轮号(推导核/loop/产物入口每步都读), 不清全局 schema 缓存; 派生写路径仍 fresh。
    """
    try:
        from tools.schema_loader import clear_cache, code_repo_schema_root, load_schema

        if fresh:
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


def audit_round_suffixes(*, fresh: bool = False) -> list[str]:
    """AIPOS-F112: 审计轮卡号后缀序列唯一实现(声明 fix_card_closure.revision_card_numbering; 不可读回退内置 R→R2→…→R100)。
    derive_audit_task_id(派生写)与轮号读取(audit_round_ids / reviewed_task_id_of)同读此序列, 禁第二份。"""
    declared = _declared_revision_suffixes(fresh=fresh)
    return declared if declared else ["R"] + [f"R{i}" for i in range(2, 101)]


def reviewed_task_id_of(audit_task_id: str) -> str | None:
    """AIPOS-F112: 按审计轮号形(声明后缀序列, 最长后缀优先, 大小写不敏感)从审计卡号取被审卡号; 不是审计轮号形 = None。
    只在拿不到卡面时用; 有卡面以卡面 reviewed_task_id 为准(audit_card_reviewed_id)。"""
    tid = str(audit_task_id or "").strip()
    upper = tid.upper()
    for suffix in sorted(audit_round_suffixes(), key=len, reverse=True):
        if suffix and len(tid) > len(suffix) and upper.endswith(suffix.upper()):
            return tid[: -len(suffix)]
    return None


def is_audit_card(task_id: str, card_frontmatter: dict[str, Any] | None = None) -> bool:
    """AIPOS-F112: 审计卡判据唯一实现(推导核 / 产物入口 / loop 回读 / 开工核验同读)。
    卡面在: 审计模式(next_resolver.task_mode_is_audit, AIPOS-F216)或首轮号形(<ID>R, 存量手发审计卡); 卡面不在: 审计轮号形(R/R2/R3…, 声明序列)。
    原各处 `task_id.upper().endswith("R")` 认不出复审轮 R2(gap #69 的一环)。"""
    tid = str(task_id or "").strip()
    if isinstance(card_frontmatter, dict) and card_frontmatter:
        if task_mode_is_audit(card_frontmatter.get("task_mode")):
            return True
        first = audit_round_suffixes()[0]
        return bool(first) and tid.upper().endswith(first.upper())
    return reviewed_task_id_of(tid) is not None


def audit_card_reviewed_id(task_id: str, card_frontmatter: dict[str, Any] | None = None) -> str:
    """AIPOS-F112: 审计卡的被审卡号 = 卡面 reviewed_task_id → derived_from → 审计轮号形; 都无 = ""(调用方 fail-closed)。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    declared = str(fm.get("reviewed_task_id") or fm.get("derived_from") or "").strip()
    return declared or (reviewed_task_id_of(task_id) or "")


def audit_round_ids(source_task_id: str, repo_root: Path) -> list[str]:
    """AIPOS-F112: 被审卡已有的审计轮卡号(按声明序列从首轮起, 队列中存在者; 首个缺号即止——轮号由 derive_audit_task_id 连续派生)。"""
    from tools.aipos_cli.task_loader import find_task_card_matches

    out: list[str] = []
    for suffix in audit_round_suffixes():
        candidate = f"{source_task_id}{suffix}"
        if not find_task_card_matches(Path(repo_root), candidate):
            break
        out.append(candidate)
    return out


def find_open_audit_task(reviewed_task_id: str, repo_root: Path) -> str | None:
    """AIPOS-F213 件④(F208 P1-9): 被审卡当前在途审计卡的卡号(查找语义; 审计卡查找唯一实现, `lybra audit-verdict` 不给
    --audit-task-id 时自发现用; 原 audit_helpers 同名 derive_audit_task_id 第二份查找删除)。

    轮号读审计轮唯一实现 audit_round_ids(声明序列 R → R2 → …, 与派审派生 derive_audit_task_id 同读一份后缀表): 最新一轮 = 当前一轮
    (current_audit_task_id 同一口径; 更早的轮已被取代), 其卡不在终态(enums queue_state.terminal 投影 task_loader.QUEUE_TERMINAL_STATES)
    = 在途, 返回它。F202 重新派审后同卡 R / R2… 多轮并存 = 命中最新一轮(原同名函数命中多张即 None, 自发现必失败)。一轮都没有 /
    最新一轮已结 = None(调用方响亮报错, 让审计体显式给 --audit-task-id)。读卡错误不再被宽捕获吞成「拿不准」: 只把「卡号对应的卡
    不唯一」(task_loader.AmbiguousTaskCard)当拿不准返回 None, 其余读错向上抛(fail-closed)。repo_root = 调用方已解析的治理根
    (CLI 经 resolve_governance_root 薄壳, 本函数不另解析)。"""
    from tools.aipos_cli.task_loader import QUEUE_TERMINAL_STATES, AmbiguousTaskCard, find_task_card

    if not reviewed_task_id:
        return None
    root = Path(repo_root)
    rounds = audit_round_ids(reviewed_task_id, root)
    if not rounds:
        return None
    try:
        _path, state = find_task_card(root, rounds[-1])
    except AmbiguousTaskCard:
        return None
    return rounds[-1] if state and state not in QUEUE_TERMINAL_STATES else None


def current_audit_task_id(source_task_id: str, repo_root: Path) -> str:
    """AIPOS-F112: 当前一轮审计卡号 = 已有轮次中最新一张; 一张都没有 = 首轮号(派审将生成它)。
    推导核等待/派审、loop 拉起、落账范围均认这一张(gap #69: 原写死 <ID>R, 复审 R2 永远等不到)。"""
    rounds = audit_round_ids(source_task_id, repo_root)
    return rounds[-1] if rounds else f"{source_task_id}{audit_round_suffixes()[0]}"


def superseding_round(audit_task_id: str, repo_root: Path, card_frontmatter: dict[str, Any] | None = None) -> dict[str, str] | None:
    """AIPOS-F114: 审计轮是否已被取代的唯一读取(推导核 / 开工核验 / 裁决入口同读, 禁第二份)。
    取代关系唯一记在后一轮的门派审记录 supersedes 字段(AIPOS-F112 既有, F72 字段), 不改写旧轮卡面/记录。
    返回 {audit_task_id: 取代它的那一轮, dispatch_id}; 未被取代 / 不是审计轮 = None。"""
    from tools.aipos_cli.next_resolver import _find_latest_record

    reviewed = audit_card_reviewed_id(audit_task_id, card_frontmatter)
    if not reviewed:
        return None
    rounds = audit_round_ids(reviewed, Path(repo_root))
    if audit_task_id not in rounds:
        return None
    dispatch_root = record_dir(Path(repo_root), "audit_dispatches")
    for later in rounds[rounds.index(audit_task_id) + 1:]:
        dispatch = _find_latest_record(dispatch_root / later, "dispatch") or {}
        if str(dispatch.get("supersedes") or "").strip() == audit_task_id:
            return {"audit_task_id": later, "dispatch_id": str(dispatch.get("dispatch_id") or "")}
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
    suffixes = audit_round_suffixes(fresh=True)

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
        # AIPOS-F88 件③: project.json 只在治理根自身(workspace_config.project_json_path), 原按 lybra 布局回退退役
        from tools.aipos_cli.workspace_config import project_json_path

        return resolve_collaboration_profile(project_json_path(repo_root))
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
    reaudit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build derived audit task frontmatter and body.

    AIPOS-F112: reaudit = 裁决过期复审(nodes.N4.verdict_stale)的派生依据 {audit_task_id(演进后的下一轮号), superseded_audit_task_id,
    stale(next_resolver.verdict_staleness 结果)}; 卡面注入取代说理。缺省 None = 首轮(行为不变)。
    
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
    if reaudit:
        # AIPOS-F112: 复审轮号由调用方按演进声明定(derive_audit_task_on_return 的 verdict_stale 分支, derive_audit_task_id 带 repo_root)
        audit_task_id = str(reaudit["audit_task_id"])
    
    # AIPOS-F66 F-002: 项目名取不到=raise 带出口(fail-closed),禁默认 lybra
    project = source_metadata.get("project")
    if not project:
        raise ValueError(
            f"PROJECT_REQUIRED: derive_audit_card_on_return 无法从 source task {source_task_id} "
            f"metadata 获取 project 字段。审计卡派生需要明确项目名,禁默认回落。"
        )
    project = str(project)
    
    branch_id = _resolve_branch_id(source_metadata, collaboration_profile, repo_root)
    
    audit_metadata = {
        "task_id": audit_task_id,
        "title": f"Audit {source_metadata.get('title', source_task_id)}",
        "project": project,
        "assigned_to": _derive_audit_assigned_to(project),
        "agent_instance": resolve_audit_instance(source_metadata, repo_root),  # AIPOS-F102 件①: audit_by 优先
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
    
    # AIPOS-A1 大项C + F78C: 注入取证锚点到 governance_refs(产品仓 = 被审卡声明的仓)
    # AIPOS-F66B 件③: 报告落点 = 审计卡 ID 目录, 与正文两段同一渲染函数(禁写死被审卡目录)
    # AIPOS-F210 件②: 锚点一行 = forensic_anchor_ref(与手动派审同一函数, 交付主体三态)
    report_location = render_audit_report_location(repo_root, audit_task_id)
    forensic_anchors = [forensic_anchor_ref(repo_root, source_task_id, source_metadata, audit_task_id)]
    # AIPOS-F219 件②(F218R F-1): 复审说理按交付主体措辞——非卡分支主体(固定候选 / 治理根产出, verdict_staleness 结果带 subject)读
    # transitions nodes.N4.non_branch_rework.by_subject.<kind>.reaudit_ref / reaudit_note(占位值唯一构建 next_resolver.non_branch_stale_texts);
    # 卡分支主体措辞不变
    non_branch_reaudit: tuple[dict[str, str], dict[str, Any]] | None = None
    if reaudit and reaudit.get("kind") != "return_stale":
        from tools.aipos_cli.next_resolver import non_branch_rework_declaration, non_branch_stale_texts

        stale = reaudit.get("stale") or {}
        if str(stale.get("subject") or "") in non_branch_rework_declaration()["by_subject"]:
            texts, values = non_branch_stale_texts(stale)
            non_branch_reaudit = texts, {**values, "superseded": str(reaudit.get("superseded_audit_task_id") or "")}
    if non_branch_reaudit is not None:
        forensic_anchors.append(non_branch_reaudit[0]["reaudit_ref"].format(**non_branch_reaudit[1]))
    elif reaudit and reaudit.get("kind") == "return_stale":
        stale = reaudit.get("stale") or {}
        superseded = str(reaudit.get("superseded_audit_task_id") or "")
        forensic_anchors.append(
            f"\u2605复审说理(AIPOS-F114 return_stale): 上一轮 {superseded} 在途未出裁决, 其所审交回 {stale.get('return_id')} 绑定 "
            f"{str(stale.get('bound_commit_sha') or '')[:12]}, 卡分支 {stale.get('branch')} 交回后又前进到 {str(stale.get('tip') or '')[:12]} "
            f"(交回已过期); 本轮准绳不变(原卡全文), 审的是新 tip 的整卡产物; 上一轮作废(本轮派审记录 supersedes), 其报告不入门"
        )
    elif reaudit:
        stale = reaudit.get("stale") or {}
        superseded = str(reaudit.get("superseded_audit_task_id") or "")
        forensic_anchors.append(
            f"\u2605复审说理(AIPOS-F112 verdict_stale): 上一轮 {superseded} 的裁决 {stale.get('verdict_id')} 覆盖 "
            f"{str(stale.get('verdict_commit_sha') or '')[:12]}, 卡分支 {stale.get('branch')} 已前进到 {str(stale.get('tip') or '')[:12]} "
            f"(产物已变化, finalize F70 拒); 本轮准绳不变(原卡全文), 审的是新 tip 的整卡产物; 上一轮已结案, 其裁决留作旧 commit 的历史事实"
        )
    existing_governance_refs = list(audit_metadata.get("governance_refs") or [])
    audit_metadata["governance_refs"] = existing_governance_refs + forensic_anchors
    
    # AIPOS-F80 件①: 「带不带认领与交回节 / 门动词配方」唯一判据(与执行卡同口径, manual_gate_mode 项目例外)
    from tools.aipos_cli.draft_writer import card_carries_gate_contract_section

    gate_mode = card_carries_gate_contract_section(audit_metadata, repo_root)
    if gate_mode:
        report_line = (f"- **报告落位**:`{report_location}`(审计报告归审计卡 ID 目录; 裁决记录由门落 records/, 不是你的落点);"
                       f" {report_frontmatter_clause(source_task_id, repo_root, audit_task_id)}。")
    else:
        report_line = f"- **报告落位**:{zero_gate_report_sentence(report_location, source_task_id, repo_root, audit_task_id)}"

    # Build body (mechanical signpost) — AIPOS-338 S2: fixed audit instructions
    artifact_list = "\n".join(f"- `{ref}`" for ref in artifact_refs) if artifact_refs else "- (see return record)"
    
    reaudit_note = ""
    if non_branch_reaudit is not None:
        reaudit_note = non_branch_reaudit[0]["reaudit_note"].format(**non_branch_reaudit[1])
    elif reaudit and reaudit.get("kind") == "return_stale":
        stale = reaudit.get("stale") or {}
        reaudit_note = (
            f"\n## 复审说理(AIPOS-F114 return_stale)\n"
            f"本卡取代在途的上一轮审计卡 `{reaudit.get('superseded_audit_task_id')}`: 它所审的交回 `{stale.get('return_id')}` 绑定 "
            f"`{stale.get('bound_commit_sha')}`, 卡分支 `{stale.get('branch')}` 交回后又前进到 `{stale.get('tip')}`"
            f"(多为执行体在卡工作树合 main), 上一轮审的不是当前产物, 已作废、其报告不入门。按原卡全文对新 tip 整卡审计。\n"
        )
    elif reaudit:
        stale = reaudit.get("stale") or {}
        reaudit_note = (
            f"\n## 复审说理(AIPOS-F112 verdict_stale)\n"
            f"本卡取代上一轮审计卡 `{reaudit.get('superseded_audit_task_id')}`: 其裁决 `{stale.get('verdict_id')}` 覆盖 "
            f"`{stale.get('verdict_commit_sha')}`, 卡分支 `{stale.get('branch')}` 之后又前进到 `{stale.get('tip')}`"
            f"(多为 finalize 合并冲突后在卡工作树合 main), 旧裁决不再覆盖当前产物。按原卡全文对新 tip 整卡重审。\n"
        )
    audit_body = f"""## Audit Subject
Independent audit of task `{source_task_id}`.
{reaudit_note}
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
{report_line}
- **如实报红线**:结论三值 PASS / PASS_WITH_NOTES / FAIL(附 F-* 清单);失败如实报,禁止“应该没问题”。
"""
    # AIPOS-A1 大项C: 注入取证锚点段(路径来自注册表, 禁写死)
    audit_body += build_forensic_anchor_section(source_task_id, repo_root, source_metadata, audit_task_id=audit_task_id)

    # AIPOS-F12 大项D: 注入门领地纪律 + 精确提交配方(值来自声明)——仅 manual_gate_mode 项目(F80 件①);
    # 零门卡面 = 交付纪律节(报告写到落点即止, 认领与裁决提交由驱动方完成)。
    if gate_mode:
        audit_body += build_gate_territory_discipline_section(source_task_id, repo_root, audit_task_id=audit_task_id)
    else:
        audit_body += build_zero_gate_delivery_section(source_task_id, repo_root, audit_task_id=audit_task_id)

    if branch_id == "code_with_deploy":
        audit_body += (
            "\n## 部署门提醒(AIPOS-338 S6)\n"
            "本被审卡 `deploy: true`。审计 PASS ≠ 可部署 —— 部署确认属 Owner"
            "(`owner_verify: required` 的不可逆确认,判断在 Owner)。仅生产级部署触发,开发环回部署不触发。\n"
        )
    
    # AIPOS-338 S2: append the auditor's card-bound contract section (single-source)
    # AIPOS-F80 件①: 仅 manual_gate_mode 项目(同一判据); 零门审计卡不带该节。
    # 生成失败不再静默吞(禁 except Exception: pass): 信封/连接声明缺(ValueError/OSError)= 卡面出声 + stderr,
    # 其余异常原样上抛。
    if gate_mode and repo_root is not None:
        from tools.aipos_cli.gate_contract_section import (
            render_gate_contract_section, workspace_connection_info,
        )
        try:
            conn = workspace_connection_info(repo_root)
            # AIPOS-F103 件④: 审计信封身份 = 本审计卡的认领实例(audit_metadata.agent_instance), 判定对象 = 被审卡(source_metadata)
            section = render_gate_contract_section(
                _resolve_profile(source_metadata, collaboration_profile, repo_root),
                {**source_metadata, "audit_by": audit_metadata.get("agent_instance") or source_metadata.get("audit_by")}, role="auditor",
                gate_url=conn["gate_url"], connection_json_rel=conn["connection_json_rel"],
                workspace_display=conn["workspace_display"], task_id=audit_task_id,
                workspace_root=repo_root,
            )
        except (ValueError, OSError) as exc:
            import sys

            print(f"Warning: 审计卡 {audit_task_id}「认领与交回」节生成失败: {exc}", file=sys.stderr)
            section = (
                "## 【认领与交回】\n\n"
                f"> 生成失败(manual_gate_mode 项目): {exc}\n"
                f"> 出口: 在信封目录(project.json paths.policies_root)补覆盖审计实例与被审卡的 active 信封 / 修 .lybra/connection.json 后 regen 本卡。"
            )
        audit_body = audit_body.rstrip() + "\n\n" + section + "\n"

    audit_task_path = f"{queue_state_ref(repo_root, 'pending')}{_task_filename_for(audit_task_id)}"  # AIPOS-F89 件① M8
    
    return {
        "metadata": audit_metadata,
        "body": audit_body,
        "audit_task_id": audit_task_id,
        "audit_task_path": audit_task_path,
    }


def _reaudit_round(repo_root: Path, source_task_id: str, source_metadata: dict[str, Any], *, branch_id: str) -> dict[str, Any] | None:
    """AIPOS-F112 件② + F114 件①: 交回时是否走「复审」派生。None = 不适用(无裁决且无在途过期轮 / 未过期 / 非代码 / 不派审, 走既有首轮派生判据);
    {"skip": 原因} = 适用但当前一轮在途且未过期(幂等不演进); 否则 {kind(verdict_stale|return_stale), audit_task_id(下一轮,
    derive_audit_task_id 演进), superseded_audit_task_id, stale}。
    判据唯一实现: 裁决过期 next_resolver.verdict_staleness(当前一轮已裁); 交回过期 next_resolver.return_staleness(当前一轮在途,
    其所审交回绑定的 tip ≠ 卡分支 tip——门已落的新交回不是该轮所审的那份)。"""
    if str(source_metadata.get("audit", "")).strip().lower() == "none":
        return None
    if task_mode_is_audit(source_metadata.get("task_mode")) or branch_id == "noncode_bench_audit":  # AIPOS-F216 件④
        return None
    from tools.aipos_cli.next_resolver import return_staleness, verdict_staleness
    from tools.aipos_cli.task_loader import task_card_lookup_scope

    with task_card_lookup_scope(Path(repo_root)):  # 只读判定: 轮号查找共用一趟队列索引
        current = current_audit_task_id(source_task_id, repo_root)
        stale = verdict_staleness(repo_root, source_task_id, source_metadata)
        kind = "verdict_stale"
        if stale is None or str(stale.get("audit_task_id") or "") != current:
            # AIPOS-F114: 当前一轮在途(无本轮裁决)且其所审交回已过期 → 演进下一轮取代它(含 F112 复审轮 R2 在途时分支再前进)
            in_flight = return_staleness(repo_root, source_task_id, source_metadata)
            if in_flight is not None and str(in_flight.get("audit_task_id") or "") == current:
                stale, kind = in_flight, "return_stale"
            elif stale is None:
                return None
            else:
                return {"skip": f"re-audit round {current} already derived and awaiting its verdict (idempotency, AIPOS-F112)"}
        next_round = derive_audit_task_id(source_task_id, repo_root=repo_root)
    return {
        "kind": kind,
        "audit_task_id": next_round,
        "superseded_audit_task_id": current,
        "stale": stale,
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
    # AIPOS-F112 件②: 已有裁决但按 nodes.N4.verdict_stale 判据过期(卡分支 tip ≠ 最新 PASS 裁决覆盖的 commit)的重交回 →
    # 按卡号演进声明派下一轮(R2/R3…); 当前一轮尚无裁决(在途)= 幂等不演进。判据唯一实现 next_resolver.verdict_staleness。
    reaudit = _reaudit_round(repo_root, source_task_id, source_metadata, branch_id=branch_id)
    if reaudit is not None and reaudit.get("skip"):
        return {"derived": False, "reason": str(reaudit["skip"])}
    if reaudit is None and not should_derive_audit(source_metadata, branch_id=branch_id, repo_root=repo_root):
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
        reaudit=reaudit,
    )
    
    audit_task_id = audit_spec["audit_task_id"]
    audit_task_path = audit_spec["audit_task_path"]
    audit_task_file = repo_root / audit_task_path
    audit_metadata = audit_spec["metadata"]

    # AIPOS-F38 大项A(F17 原则覆盖全部 writer): 必填字段从 schema 单源补全(值承继原卡,
    # 缺则安全默认), 再产前自检——产物必过与 publish/修复卡 writer 同一的 schema 必填校验;
    # 审计身份 = resolve_audit_instance(被审卡 audit_by → 项目推导, AIPOS-F102 件①; 同一实现), 禁承继原卡执行实例。
    _required_fields = get_required_card_fields()
    _inherit_defaults = card_field_defaults()  # AIPOS-F108 件①: 安全默认值 = card.schema fields.<键>.default(单源)
    for _field in _required_fields:
        if _field not in audit_metadata or audit_metadata[_field] is None:
            if _field in source_metadata and source_metadata[_field] is not None:
                audit_metadata[_field] = source_metadata[_field]
            elif _field in _inherit_defaults:
                audit_metadata[_field] = _inherit_defaults[_field]
    _missing = [f for f in _required_fields if f not in audit_metadata or audit_metadata[f] is None]
    
    # AIPOS-F66 F-002: 项目名取不到=raise 带出口(fail-closed),禁默认 lybra
    source_project = source_metadata.get("project")
    if not source_project:
        return {
            "derived": False,
            "reason": (
                f"AIPOS-F38 派生校验 FAIL: 源任务 {source_task_id} 缺少 project 字段,"
                f"无法推导审计实例。审计卡派生需要明确项目名,禁默认回落。"
            ),
        }
    
    _expected_instance = resolve_audit_instance(source_metadata, repo_root)  # AIPOS-F102 件①: 同一解析(audit_by 优先)
    _reviewed_executor = str(source_metadata.get("agent_instance") or source_metadata.get("claimed_by") or "").strip()
    if _reviewed_executor and _expected_instance == _reviewed_executor:
        return {
            "derived": False,
            "reason": (
                f"AIPOS-F102 派生校验 FAIL: 审计卡 {audit_task_id} 审计实例 {_expected_instance} = 被审卡执行实例(独立性不成立); "
                f"出口: 被审卡 audit_by 声明独立的审计实例"
            ),
        }
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
    published_at = iso_z()
    
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
    from tools.aipos_cli.record_writer import build_mcp_audit_dispatch_record_markdown, build_runtime_id, write_records_atomic

    # AIPOS-F109 件①: 派审记录 id = 落盘名, 与声明 N3 record.location 同式样(build_runtime_id, compact 时间; 原手写 compact_t 压缩法退役,
    # 存量旧名照读——读侧按目录扫描)
    dispatch_id = build_runtime_id("dispatch", audit_task_id, published_at, "gate-derivation")
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
        # AIPOS-F112: 复审轮派审记录写 supersedes=<上一轮审计卡号>(F72 既有字段; 上一轮随其裁决结案, 取代说理在审计卡正文)
        supersedes=str(reaudit["superseded_audit_task_id"]) if reaudit else None,
    )
    
    # AIPOS-F64: 统一写入器 - 原子写入publish和dispatch两条记录
    write_result = write_records_atomic(
        repo_root=repo_root,
        records=[
            ("publish", publish_id, publish_record_markdown, audit_task_id),
            ("audit_dispatch", dispatch_id, dispatch_record_markdown, audit_task_id),
        ],
    )
    
    dispatch_record_path = repo_root / write_result["paths"][1]  # 第二条记录是dispatch
    
    return {
        "derived": True,
        "audit_task_id": audit_task_id,
        "audit_task_path": audit_task_path,
        "superseded_audit_task_id": str(reaudit["superseded_audit_task_id"]) if reaudit else None,
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
    supplementary_of: str | None = None,
) -> dict[str, Any]:
    """AIPOS-C3B 大项C⑤: 审计 FAIL 自动派审——审计员判 FAIL 时自动派一张'修复卡'回队列。

    AIPOS-F162 件②: supplementary_of = 已结案原卡(补充审计 FAIL): 同一 writer 派修复卡, 不受「原卡已收账」级联终局判拦截;
    修复卡承继原卡 lane/harness/output_target/audit/audit_by/audit_runtime_evidence, 卡面 supplementary_of=原卡, 不带
    derived_from_audit_task_id(不触发 fix_card_closure 原卡复审派生), 走常规执行/审计/合入(声明 transitions supplementary_audit.repair_card)。

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
    if task_closures and not supplementary_of:
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
    queue_dir = queue_root_for(governance_root) / "pending"
    claimed_dir = queue_root_for(governance_root) / "claimed"
    completed_dir = queue_root_for(governance_root) / "completed"
    
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
    for qdir in [queue_dir, claimed_dir, queue_root_for(governance_root) / "completed"]:
        candidate = qdir / _task_filename_for(reviewed_task_id)
        if candidate.exists():
            source_card = candidate
            break
    if source_card is None and supplementary_of:
        # AIPOS-F162 件②: 卡文件名不限(chris 形带 slug)——按 frontmatter task_id 唯一查找(task_loader.find_task_card)
        from tools.aipos_cli.task_loader import find_task_card

        source_card, _state = find_task_card(governance_root, reviewed_task_id)

    source_metadata = {}
    source_body = ""
    if source_card:
        # AIPOS-F102: 被审卡读不出 = 拒(原 except Exception: pass 静默当空卡, 修复卡身份/项目无据)
        try:
            text = source_card.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"derive_repair_card_on_fail: 被审卡 {source_card} 读不出: {exc}") from exc
        source_metadata, source_body, _ = parse_markdown_frontmatter(text)

    # AIPOS-F66 F-002: 项目名取不到=raise 带出口(fail-closed),禁默认 lybra
    project = source_metadata.get("project")
    if not project:
        raise ValueError(
            f"PROJECT_REQUIRED: derive_repair_card 无法从 reviewed task {reviewed_task_id} "
            f"metadata 获取 project 字段。修复卡派生需要明确项目名,禁默认回落。"
        )
    project = str(project)
    # AIPOS-F211 件②: 修复卡 task_mode 必填(card.schema), 承继被审卡; 被审卡缺 task_mode(人肉期存量)= 拒(原补 "code" 第二缺省退役),
    # 出口 = 顾问 lybra queue amend 给被审卡补 task_mode 后重派
    source_mode = str(source_metadata.get("task_mode") or "").strip()
    if not source_mode:
        raise ValueError(
            f"TASK_MODE_REQUIRED: derive_repair_card 被审卡 {reviewed_task_id} 缺 task_mode, 修复卡 task_mode 无从承继(禁补缺省); "
            f"出口: lybra queue amend --task-id {reviewed_task_id} 补 task_mode 后重派"
        )

    # AIPOS-F17 大项A: 构建修复卡 — 必填字段从 schema 单源派生, 值承继原卡, 禁手写第二份清单。
    # AIPOS-F102 件①: 修复卡执行体承继被审卡声明, 都缺按项目推导(resolve_repair_executor_instance), 禁 lybra 身份字面
    repair_executor = resolve_repair_executor_instance(source_metadata, governance_root)
    repair_metadata = {
        "task_id": repair_task_id,
        "title": f"Fix: {source_metadata.get('title', reviewed_task_id)} (round {fix_round})",
        "project": project,
        # AIPOS-F102 件① / F108 件①(M9) 同口径: 执行实例承继原卡声明 → 缺则按项目推导 → 项目也无则拒; 禁写死实例名缺省
        "assigned_to": str(source_metadata.get("assigned_to") or "").strip() or repair_executor,
        "agent_instance": str(source_metadata.get("agent_instance") or "").strip() or repair_executor,
        "context_bundle": source_metadata.get("context_bundle", "default"),
        # AIPOS-F211 件②: 修复卡承继被审卡 task_mode 原值; 被审卡缺 task_mode 在上方已拒(禁补 "code")
        "task_mode": source_mode,
        "task_class": source_metadata.get("task_class") or card_field_defaults()["task_class"],
        "priority": source_metadata.get("priority", "high"),
        "status": "pending",
        "created_by": "gate_derivation",
        "created_at": iso_z(),
        "derived_from_verdict_id": verdict_id,
        "derived_from_audit_task_id": audit_task_id,
        "fix_round": fix_round,
        "depends_on": [],
        "anchor_refs": source_metadata.get("anchor_refs", ["g1_owner_gate"]),
        "artifact_scope": source_metadata.get("artifact_scope", ""),
    }

    if supplementary_of:
        repair_metadata.pop("derived_from_audit_task_id", None)
        repair_metadata["supplementary_of"] = str(supplementary_of)
        for key in supplementary_audit_declaration().get("repair_inherit_fields") or []:  # 承继字段读声明
            if source_metadata.get(key) not in (None, "", [], {}):
                repair_metadata[key] = source_metadata[key]

    # AIPOS-F17 大项A: 从 schema 必填集补全——值承继原卡, 原卡无则用安全默认值。
    # 禁手写第二份字段清单; schema 改即自动跟随。
    _required_fields = get_required_card_fields()
    _inherit_defaults = card_field_defaults()  # AIPOS-F108 件①: 安全默认值 = card.schema fields.<键>.default(单源)
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

    origin_line = (f"已结案卡 {supplementary_of} 的补充审计 {audit_task_id} 裁决 FAIL(裁决 {verdict_id}), 自动派生此修复卡; "
                   f"原卡 closure 与原裁决不动, 本卡走常规执行/审计/合入。" if supplementary_of
                   else f"审计任务 {audit_task_id} 裁决 FAIL, 自动派生此修复卡。")
    repair_body = f"""## 修复任务 (第 {fix_round} 轮)

{origin_line}

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


# ---------------------------------------------------------------------------
# AIPOS-F162 件②: 已结案卡的补充审计——声明 transitions.schema supplementary_audit(唯一); 本节是补审卡号 / 在途判定 /
# 链路视图的唯一实现(audit dispatch --supplementary、推导核、loop 结案出口、落账范围、next/brief/loop status/needs-owner 同读)
# ---------------------------------------------------------------------------

def supplementary_audit_declaration() -> dict[str, Any]:
    """transitions.schema supplementary_audit 唯一读取口。缺 / 关键键缺 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = load_schema("transitions", code_repo_schema_root()).get("supplementary_audit")
    needed = ("card_numbering", "actor_role_class", "owner_policy_ref", "fail_verdicts", "pass_verdicts", "status_text")
    if not isinstance(decl, dict) or any(not decl.get(k) for k in needed):
        raise SchemaLoadError(f"transitions.schema.json supplementary_audit({'/'.join(needed)})未声明")
    pattern = str((decl["card_numbering"] or {}).get("pattern") or "")
    if "<原卡ID>" not in pattern or "[序号]" not in pattern:
        raise SchemaLoadError("transitions.schema.json supplementary_audit.card_numbering.pattern 须含 <原卡ID> 与 [序号]")
    return decl


def supplementary_audit_id(source_task_id: str, number: int) -> str:
    pattern = str(supplementary_audit_declaration()["card_numbering"]["pattern"])
    return pattern.replace("<原卡ID>", str(source_task_id)).replace("[序号]", str(int(number)))


def supplementary_audit_status_text(kind: str, **values: str) -> str:
    """补审结论文案(声明 supplementary_audit.status_text 唯一来源)。"""
    template = str((supplementary_audit_declaration()["status_text"] or {}).get(kind) or "")
    if not template:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"transitions.schema.json supplementary_audit.status_text.{kind} 未声明")
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def supplementary_audit_cards(repo_root: Path, source_task_id: str) -> list[dict[str, Any]]:
    """原卡已有的补审卡(按声明卡号形从 1 起连续查队列, 首个缺号即止; 卡面 supplementary_of 须 = 原卡, 否则不认)。
    返回 [{audit_task_id, queue_state, path, frontmatter}]。卡面读不出 = FrontmatterReadError 向上(fail-closed)。"""
    from tools.aipos_cli.next_resolver import _read_frontmatter
    from tools.aipos_cli.task_loader import find_task_card

    out: list[dict[str, Any]] = []
    number = 1
    while True:
        candidate = supplementary_audit_id(source_task_id, number)
        path, state = find_task_card(Path(repo_root), candidate)
        if path is None:
            break
        fm = _read_frontmatter(path)
        if str(fm.get("supplementary_of") or "").strip() == str(source_task_id):
            out.append({"audit_task_id": candidate, "queue_state": state, "path": path, "frontmatter": fm})
        number += 1
    return out


def next_supplementary_audit_id(repo_root: Path, source_task_id: str) -> str:
    """下一张补审卡号 = 已占用卡号(不论卡面)之后的首个序号。"""
    from tools.aipos_cli.task_loader import find_task_card_matches

    number = 1
    while find_task_card_matches(Path(repo_root), supplementary_audit_id(source_task_id, number)):
        number += 1
    return supplementary_audit_id(source_task_id, number)


def supplementary_in_flight(repo_root: Path, source_task_id: str) -> str | None:
    """同一原卡在途补审卡号(pending/claimed); 无 = None。"""
    for card in supplementary_audit_cards(repo_root, source_task_id):
        if card["queue_state"] in ("pending", "claimed"):
            return str(card["audit_task_id"])
    return None


def supplementary_repair_cards(repo_root: Path, source_task_id: str) -> list[dict[str, Any]]:
    """补审 FAIL 派生的修复卡(derive_repair_card_on_fail 卡号 <原卡ID>-fix<n>, 卡面 supplementary_of = 原卡)。"""
    from tools.aipos_cli.next_resolver import _read_frontmatter
    from tools.aipos_cli.task_loader import find_task_card

    out: list[dict[str, Any]] = []
    number = 1
    while True:
        candidate = f"{source_task_id}-fix{number}"
        path, state = find_task_card(Path(repo_root), candidate)
        if path is None:
            break
        fm = _read_frontmatter(path)
        if str(fm.get("supplementary_of") or "").strip() == str(source_task_id):
            out.append({"task_id": candidate, "queue_state": state, "verdict_id": str(fm.get("derived_from_verdict_id") or "")})
        number += 1
    return out


def supplementary_audit_chain(repo_root: Path, source_task_id: str) -> list[dict[str, Any]]:
    """链路视图(唯一实现): 每张补审卡 {audit_task_id, queue_state, reason, verdict, verdict_id, repair_task_id, repair_state, status}。
    裁决读原卡裁决记录中 audit_task_id = 补审卡者(补审裁决记录带 supplementary_of)。无补审 = []。"""
    from tools.aipos_cli.next_resolver import _read_frontmatter
    from tools.aipos_cli.record_writer import record_file_prefix

    cards = supplementary_audit_cards(repo_root, source_task_id)
    if not cards:
        return []
    verdicts: dict[str, dict[str, Any]] = {}
    vdir = record_dir(Path(repo_root), "audit_verdicts", source_task_id)
    if vdir.is_dir():
        prefix = record_file_prefix("audit_verdicts")
        for path in sorted(vdir.glob(f"{prefix}*.md"), key=lambda p: p.stat().st_mtime):
            fm = _read_frontmatter(path)
            if str(fm.get("supplementary_of") or "").strip() == str(source_task_id):
                verdicts[str(fm.get("audit_task_id") or "")] = fm
    repairs = {r["verdict_id"]: r for r in supplementary_repair_cards(repo_root, source_task_id)}
    decl = supplementary_audit_declaration()
    chain: list[dict[str, Any]] = []
    for card in cards:
        audit_id = str(card["audit_task_id"])
        verdict_fm = verdicts.get(audit_id) or {}
        verdict = str(verdict_fm.get("verdict") or "").upper()
        verdict_id = str(verdict_fm.get("verdict_id") or "")
        repair = repairs.get(verdict_id) if verdict_id else None
        if not verdict:
            status = supplementary_audit_status_text("in_flight", audit_task_id=audit_id)
        elif verdict in decl["pass_verdicts"]:
            status = supplementary_audit_status_text("pass", audit_task_id=audit_id)
        elif verdict in decl["fail_verdicts"] and repair:
            status = supplementary_audit_status_text("fail", repair_task_id=repair["task_id"])
        else:
            status = supplementary_audit_status_text("other", verdict=verdict, audit_task_id=audit_id)
        chain.append({"audit_task_id": audit_id, "queue_state": card["queue_state"],
                      "reason": str(card["frontmatter"].get("supplementary_reason") or ""), "verdict": verdict,
                      "verdict_id": verdict_id, "repair_task_id": repair["task_id"] if repair else "",
                      "repair_state": repair["queue_state"] if repair else "", "status": status})
    return chain


def supplementary_audit_overview(repo_root: Path, unreadable: list[str] | None = None) -> list[dict[str, Any]]:
    """全项目补充审计链路(brief / needs-owner 同读): 队列中补审卡(task_mode=audit 且卡面 supplementary_of)指向的原卡逐张出链路。
    返回 [{task_id, chain, line}](按原卡号排序)。视图口径(AIPOS-F100): 卡面/记录读不出的不省略也不猜——原文追加进 unreadable
    (调用方逐条列出), 该卡/该链路不计; unreadable=None = 读不出即 FrontmatterReadError 向上。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.next_resolver import _read_frontmatter
    from tools.aipos_cli.task_loader import iter_queue_task_paths, task_card_lookup_scope

    root = Path(repo_root)
    out: list[dict[str, Any]] = []
    with task_card_lookup_scope(root):
        sources = set()
        for path in iter_queue_task_paths(root):
            try:
                fm = _read_frontmatter(path)
            except FrontmatterReadError as exc:
                if unreadable is None:
                    raise
                if str(exc) not in unreadable:
                    unreadable.append(str(exc))
                continue
            if task_mode_is_audit(fm.get("task_mode")) and str(fm.get("supplementary_of") or "").strip():  # AIPOS-F216 件④
                sources.add(str(fm["supplementary_of"]).strip())
        for source in sorted(sources):
            try:
                chain = supplementary_audit_chain(root, source)
            except FrontmatterReadError as exc:
                if unreadable is None:
                    raise
                unreadable.append(f"{source} 补充审计链路不可读: {exc}")
                continue
            if chain:
                out.append({"task_id": source, "chain": chain, "line": render_supplementary_chain(source, chain)})
    return out


def render_supplementary_chain(source_task_id: str, chain: list[dict[str, Any]]) -> str:
    """链路一行(四视图同一渲染): <原卡> 补审: <补审卡>[<队列>] <结论>(修复卡 <ID>[<队列>]) → …"""
    parts = []
    for row in chain:
        part = f"{row['audit_task_id']}[{row['queue_state']}] {row['status']}"
        if row.get("repair_task_id"):
            part += f"(修复卡 {row['repair_task_id']}[{row['repair_state']}])"
        parts.append(part)
    return f"{source_task_id} 补审: " + " → ".join(parts)


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
