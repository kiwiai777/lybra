from __future__ import annotations

import re
from datetime import date, datetime, timezone
from tools.aipos_cli.clock import iso_ms, iso_z
from tools.aipos_cli.record_writer import record_dir
from pathlib import Path
from typing import Any

from tools.aipos_cli.agent_profiles import actor_matches_task_actor
from tools.aipos_cli.draft_validator import find_case_insensitive_path_collision
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.record_writer import (
    CARD_FRONTMATTER_ORDER,



    build_claim_log_markdown,
    build_runtime_id,
    build_session_record_markdown,
    claim_record_paths,
    load_session_record,
    render_markdown as _render_markdown_single_source,
    session_record_path,
    update_session_record_markdown,
    validate_safe_task_id,
)
from tools.aipos_cli.task_loader import QUEUE_STATES, find_task_by_id, load_task_file, queue_root_for
from tools.aipos_cli.validator import validate_single_task

# AIPOS-F89 件① M8: 原队列根常量(写死 5_tasks/queue)删除——队列根只读 task_loader.queue_root_for(project.json paths.queue_root)
MUTATION_SAFETY_NOTICE = (
    "AIPOS-31 queue mutation only moves validated task cards within 5_tasks/queue/. "
    "It does not write records, run agents, or mutate orchestration state."
)
MUTATION_RECORDS_SAFETY_NOTICE = (
    "AIPOS-32 queue mutation with records enabled writes only within 5_tasks/queue/ and 5_tasks/records/. "
    "Records writing is opt-in and limited to 5_tasks/records/. It does not run agents or mutate orchestration state."
)


def queue_mutation_transitions() -> dict[str, tuple[tuple[str, ...], str]]:
    """AIPOS-F104 件①: 队列搬卡转移表唯一读取口——transitions.schema queue_mutations.transitions。

    返回 {动作: (允许源目录元组, 目标目录)}; 条目带 node = 引用 nodes.<id> 的 from_states/to_state。
    缺声明 / 源或目标不是队列目录(task_loader.QUEUE_STATES, enums queue_state queue_dir 投影)= SchemaLoadError fail-closed。
    原手写 ALLOWED_TRANSITIONS(withdraw/reopen 源写 "*")与 transition_engine.validate_transition 本地简化表退役。
    """
    from tools.schema_loader import SchemaLoadError, load_schema

    schema = load_schema("transitions")
    declared = (schema.get("queue_mutations") or {}).get("transitions")
    if not isinstance(declared, dict) or not declared:
        raise SchemaLoadError("transitions.schema.json queue_mutations.transitions 未声明")
    table: dict[str, tuple[tuple[str, ...], str]] = {}
    for action, spec in declared.items():
        source = spec
        node_ref = spec.get("node") if isinstance(spec, dict) else None
        if node_ref:
            source = (schema.get("nodes") or {}).get(node_ref)
        if not isinstance(source, dict):
            raise SchemaLoadError(f"transitions.schema.json queue_mutations.transitions.{action} 声明无效: {spec!r}")
        from_states = tuple(str(state) for state in (source.get("from_states") or ()))
        to_state = str(source.get("to_state") or "")
        bad = [state for state in (*from_states, to_state) if state not in QUEUE_STATES]
        if not from_states or not to_state or bad:
            raise SchemaLoadError(
                f"transitions.schema.json queue_mutations.transitions.{action} 源/目标须为队列目录 {QUEUE_STATES}: "
                f"from_states={from_states} to_state={to_state!r}"
            )
        table[str(action)] = (from_states, to_state)
    return table


# 模块级投影(只读声明, 非第二份表): {动作: (允许源目录元组, 目标目录)}
ALLOWED_TRANSITIONS = queue_mutation_transitions()


def withdraw_owner_ruling_states() -> tuple[str, ...]:
    """AIPOS-F170 件①: 撤回须带 Owner 裁决引用的源目录——唯一读取口 transitions.schema queue_mutations.transitions.withdraw
    .owner_ruling_required_from(须为 withdraw.from_states 子集; 缺键 = 空元组 = 无此前置, 行为不变; 形不合 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    spec = ((load_schema("transitions").get("queue_mutations") or {}).get("transitions") or {}).get("withdraw") or {}
    raw = spec.get("owner_ruling_required_from") if isinstance(spec, dict) else None
    if raw is None:
        return ()
    states = tuple(str(s) for s in raw) if isinstance(raw, list) else None
    if states is None or not set(states) <= set(ALLOWED_TRANSITIONS["withdraw"][0]):
        raise SchemaLoadError(f"transitions.schema.json queue_mutations.transitions.withdraw.owner_ruling_required_from 须为 "
                              f"from_states {list(ALLOWED_TRANSITIONS['withdraw'][0])} 的子集列表, 得到 {raw!r}")
    return states


def withdraw_owner_ruling_problem(repo_root: Path, task_id: str, owner_ruling_ref: str | None, *, dry_run: bool) -> tuple[str | None, str | None]:
    """AIPOS-F170 件①: 须带 Owner 裁决引用的撤回 → (拒因|None, 提示|None)。引用 = 本治理根已落 owner_decision_record 的 decision_id:
    记录 task_id 须为本卡、allowed_next_action 须为收件箱声明中 effect=withdraw_card 的选项。预演时记录未落 = 提示(owner decide 先落记录
    再执行), 确认时未落 = 拒。记录读不出 = 拒(fail-closed)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.owner_inbox import inbox_declaration
    from tools.aipos_cli.record_writer import record_root

    ref = str(owner_ruling_ref or "").strip()
    if not ref:
        return (f"withdraw from this queue state requires an Owner ruling reference (owner_ruling_ref = decision_id of an "
                f"owner_decision_record ruling withdrawal of {task_id}); rule it via `lybra owner decide`"), None
    if "/" in ref or "\\" in ref or ref.startswith("."):
        return f"owner_ruling_ref {ref!r} is not a decision_id", None
    path = Path(repo_root) / record_root("owner_decisions") / f"{ref}.md"
    if not path.is_file():
        if dry_run:
            return None, f"Owner ruling {ref} not yet on record (owner decide writes it before executing the withdrawal)"
        return f"Owner ruling {ref} not found at {path}", None
    try:
        fm, _body = require_frontmatter(path)
    except FrontmatterReadError as exc:
        return f"Owner ruling {ref} unreadable: {exc}", None
    options = inbox_declaration()["options"]
    allowed = {k for k, v in options.items() if isinstance(v, dict) and v.get("effect") == "withdraw_card"}
    if str(fm.get("task_id") or "").upper() != str(task_id or "").upper() or str(fm.get("allowed_next_action") or "") not in allowed:
        return (f"Owner ruling {ref} does not rule withdrawal of {task_id} (task_id={fm.get('task_id')!r}, "
                f"allowed_next_action={fm.get('allowed_next_action')!r}, withdrawal choices {sorted(allowed)})"), None
    return None, None


SUPERSEDE_DECL_KEYS = ("actor_role_class", "successor_states", "decision_choice", "card_fields")
#: AIPOS-F213 件⑤: supersede.card_fields 的角色(角色 → 卡面键名; transition_engine supersede 分支按角色取键名写卡面)
SUPERSEDE_CARD_FIELD_ROLES = ("by", "at", "owner_words", "owner_ref", "decision_id")


def supersede_declaration() -> dict[str, Any]:
    """AIPOS-F184 件③: 撤换声明唯一读取口 transitions.schema queue_mutations.transitions.supersede(源/目标同 ALLOWED_TRANSITIONS 投影)。
    缺键 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    spec = ((load_schema("transitions").get("queue_mutations") or {}).get("transitions") or {}).get("supersede")
    if not isinstance(spec, dict) or "supersede" not in ALLOWED_TRANSITIONS or any(not spec.get(k) for k in SUPERSEDE_DECL_KEYS):
        raise SchemaLoadError(f"transitions.schema.json queue_mutations.transitions.supersede 未声明齐 {list(SUPERSEDE_DECL_KEYS)}")
    successor = [str(x) for x in spec["successor_states"]]
    bad = [x for x in successor if x not in QUEUE_STATES]
    if bad:
        raise SchemaLoadError(f"transitions.schema.json queue_mutations.transitions.supersede.successor_states 含非队列目录 {bad}")
    fields = spec["card_fields"]
    if not isinstance(fields, dict) or set(fields) != set(SUPERSEDE_CARD_FIELD_ROLES) \
            or not all(isinstance(v, str) and v.strip() for v in fields.values()):
        raise SchemaLoadError("transitions.schema.json queue_mutations.transitions.supersede.card_fields 须为 角色 → 卡面键名, "
                              f"角色恰为 {list(SUPERSEDE_CARD_FIELD_ROLES)}(AIPOS-F213 件⑤)")
    from_states, to_state = ALLOWED_TRANSITIONS["supersede"]
    return {**spec, "from_states": from_states, "to_state": to_state, "successor_states": tuple(successor),
            "card_fields": {str(k): str(v) for k, v in fields.items()}}


def supersede_cross_check(decl: dict[str, Any] | None = None) -> None:
    """AIPOS-F213 件③⑤: 撤换声明的跨文件引用校验(撤换前置 supersede_problems 每次预演 / 确认先过; 全 schema 选项引用对表另有测试):
    card_fields 的卡面键名须为 card.schema fields 已声明字段; decision_choice 须为收件箱选项(校验唯一实现 require_inbox_choices)。
    不符 = SchemaLoadError(fail-closed)。supersede_declaration 本身只读 transitions(结案等只读承继声明的路径不连带依赖 card / verbs)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    d = decl or supersede_declaration()
    undeclared = sorted(set(d["card_fields"].values()) - set(load_schema("card", code_repo_schema_root()).get("fields") or {}))
    if undeclared:
        raise SchemaLoadError(f"transitions.schema.json queue_mutations.transitions.supersede.card_fields 引用 card.schema 未声明字段 {undeclared}")
    require_inbox_choices([d["decision_choice"]], "transitions queue_mutations.transitions.supersede.decision_choice")


def supersede_problems(repo_root: Path, task_id: str, actor: str, supersede: dict[str, Any] | None) -> list[str]:
    """AIPOS-F184 件③: 撤换前置(唯一判定): 执行者角色类(custom_roles.authorize_instance_class)/ 新卡已在队列且在 successor_states /
    新卡 ≠ 原卡 / Owner 原话非空单行。返回拒因列表(空 = 通过); 文案读 verbs.lybra_queue_supersede.texts。"""
    from tools.aipos_cli.custom_roles import authorize_instance_class
    from tools.aipos_cli.task_loader import find_task_card

    decl = supersede_declaration()
    supersede_cross_check(decl)
    texts = supersede_texts()
    spec = dict(supersede or {})
    problems: list[str] = []
    role_gate = authorize_instance_class(str(actor or ""), str(decl["actor_role_class"]), repo_root)
    if not role_gate["ok"]:
        problems.append(texts["role_refused"].format(required_class=decl["actor_role_class"], reason=role_gate["reason"]))
    words = str(spec.get("owner_words") or "")
    if not words.strip():
        problems.append(texts["missing_words"])
    elif "\n" in words or "\r" in words:
        problems.append(texts["invalid_words"].format(value=words))
    by = str(spec.get("superseded_by") or "").strip()
    if not by:
        problems.append(texts["successor_missing"].format(by="<空>"))
    elif by.upper() == str(task_id or "").upper():
        problems.append(texts["successor_self"].format(by=by))
    else:
        path, state = find_task_card(repo_root, by)
        if path is None:
            problems.append(texts["successor_missing"].format(by=by))
        elif state not in decl["successor_states"]:
            problems.append(texts["successor_state"].format(by=by, state=state, states="/".join(decl["successor_states"])))
    return problems


def supersede_texts() -> dict[str, str]:
    """verbs.schema lybra_queue_supersede.texts(唯一读取口; 缺 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    texts = (((load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get("lybra_queue_supersede") or {}).get("texts"))
    need = ("missing_words", "invalid_words", "role_refused", "successor_missing", "successor_state", "successor_self",
            "worktree_note", "reason_template", "preview_line", "done_line", "blocked_line",
            "preview_line_ref", "done_line_ref")  # AIPOS-F213 件②: --decision-id 引用已有裁决的预演 / 完成行
    if not isinstance(texts, dict) or any(not isinstance(texts.get(k), str) for k in need):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_queue_supersede.texts 未声明齐 {list(need)}")
    return texts


SUPERSEDE_CARRY_TEXT_KEYS = ("preview_line", "done_line", "idempotent_note", "worktree_absent", "dirty_refused", "worktree_unreadable", "no_branch",
                             "successor_not_pending", "successor_branch_exists", "repo_mismatch", "carried_conflict", "amend_refused",
                             "claim_note", "claim_tip_moved", "claim_branch_missing", "claim_invalid", "kickoff_line")


def supersede_carry_declaration() -> dict[str, Any]:
    """AIPOS-F186: 撤换承继声明唯一读取口 transitions.schema queue_mutations.transitions.supersede.carry_branch
    ({card_field, successor_states, amendment_type})。缺键 / successor_states 含非队列目录 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    carry = supersede_declaration().get("carry_branch")
    need = ("card_field", "successor_states", "amendment_type")
    if not isinstance(carry, dict) or any(not carry.get(k) for k in need):
        raise SchemaLoadError(f"transitions.schema.json queue_mutations.transitions.supersede.carry_branch 未声明齐 {list(need)}")
    states = tuple(str(x) for x in carry["successor_states"])
    bad = [x for x in states if x not in QUEUE_STATES]
    if bad:
        raise SchemaLoadError(f"transitions.schema.json queue_mutations.transitions.supersede.carry_branch.successor_states 含非队列目录 {bad}")
    return {**carry, "card_field": str(carry["card_field"]), "successor_states": states, "amendment_type": str(carry["amendment_type"])}


def supersede_carry_plan(repo_root: Path, task_id: str, by: str) -> dict[str, Any]:
    """AIPOS-F186 件①: `queue supersede --carry-branch` 的承继判定(唯一实现, 只读零写)。

    原卡分支 = next_resolver.card_branch_name(原卡), 产品仓 / 工作树 = card_worktree_location(原卡, 与认领建树同一推导), tip =
    existing_branch_tip; 未提交改动计数 = 原卡工作树 `git status --porcelain`(含未跟踪; 工作树不在盘 = 0)。
    拒(problems, 文案 carry_texts): 原卡工作树有未提交改动 / 读不出 / 原卡无卡分支 / 新卡不在 carry_branch.successor_states 或已认领 /
    新卡卡分支已存在 / 两卡解析到不同产品仓 / 新卡已记不同承继。新卡已记同一承继 = idempotent(不重写卡面)。
    返回 {carry: {task_id, branch, tip, worktree_path, worktree_exists, uncommitted, code_repo, successor_branch} | None,
    carried_from: {task_id, branch, tip} | None, problems: [str], idempotent: bool, line: str}。"""
    from tools.aipos_cli.next_resolver import (
        _read_frontmatter, card_base_branch, card_branch_name, card_carried_from, card_worktree_location, existing_branch_tip,
    )
    from tools.aipos_cli.task_loader import find_task_card
    from tools.aipos_cli.workspace_config import CardRepoUnresolved

    decl = supersede_carry_declaration()
    texts = supersede_carry_texts()
    out: dict[str, Any] = {"carry": None, "carried_from": None, "problems": [], "idempotent": False, "line": ""}
    orig_path, _orig_state = find_task_card(repo_root, task_id)
    new_path, new_state = find_task_card(repo_root, by)
    if orig_path is None or new_path is None:
        return out  # 卡不在队列: supersede_problems / 搬卡预演已出拒因
    orig_fm = {**_read_frontmatter(orig_path), "task_id": task_id}
    new_fm = {**_read_frontmatter(new_path), "task_id": by}
    try:
        code_repo, worktree = card_worktree_location(repo_root, task_id, orig_fm)
        successor_repo, _successor_tree = card_worktree_location(repo_root, by, new_fm)
    except CardRepoUnresolved as exc:
        out["problems"].append(f"{exc.code}: {exc.reason}")
        return out
    branch = card_branch_name(task_id)
    successor_branch = card_branch_name(by)
    base = card_base_branch()
    if Path(successor_repo).resolve() != Path(code_repo).resolve():
        out["problems"].append(texts["repo_mismatch"].format(by=by, successor_repo=successor_repo, task_id=task_id, repo=code_repo))
        return out
    tip = existing_branch_tip(code_repo, branch)
    if tip is None:
        out["problems"].append(texts["no_branch"].format(task_id=task_id, repo=code_repo, branch=branch))
        return out
    uncommitted = 0
    if worktree.is_dir():
        # AIPOS-F187: 未提交改动计数唯一实现 worktree_cleanup.uncommitted_count(与结案 / 撤回后清理判据同读)
        from tools.aipos_cli.worktree_cleanup import uncommitted_count

        try:
            uncommitted = uncommitted_count(worktree)
        except ValueError as exc:
            out["problems"].append(texts["worktree_unreadable"].format(task_id=task_id, worktree=worktree, error=str(exc)))
            return out
    out["carry"] = {"task_id": task_id, "branch": branch, "tip": tip, "worktree_path": str(worktree), "worktree_exists": worktree.is_dir(),
                    "uncommitted": uncommitted, "code_repo": str(code_repo), "successor_branch": successor_branch}
    out["carried_from"] = {"task_id": task_id, "branch": branch, "tip": tip}
    shown_tree = str(worktree) if worktree.is_dir() else f"{worktree}{texts['worktree_absent']}"
    out["line"] = texts["preview_line"].format(task_id=task_id, branch=branch, tip=tip, worktree=shown_tree, uncommitted=uncommitted, by=by,
                                               successor_branch=successor_branch, base=base)
    if uncommitted:
        out["problems"].append(texts["dirty_refused"].format(task_id=task_id, worktree=worktree, count=uncommitted, branch=branch))
    claimed = str(new_fm.get("claim_id") or new_fm.get("claimed_by") or "").strip()
    if new_state not in decl["successor_states"] or claimed:
        out["problems"].append(texts["successor_not_pending"].format(by=by, states="/".join(decl["successor_states"]), state=new_state,
                                                                     claimed=f", 已认领 {claimed}" if claimed else "", base=base))
    successor_tip = existing_branch_tip(code_repo, successor_branch)
    if successor_tip is not None:
        out["problems"].append(texts["successor_branch_exists"].format(by=by, branch=successor_branch, repo=code_repo, tip=successor_tip))
    try:
        existing = card_carried_from(new_fm)
    except ValueError as exc:
        existing = {"invalid": str(exc)}
    if existing is not None:
        if existing == out["carried_from"]:
            out["idempotent"] = True
        else:
            out["problems"].append(texts["carried_conflict"].format(by=by, existing=existing, task_id=task_id, branch=branch, tip=tip))
    return out


def supersede_carry_texts() -> dict[str, str]:
    """AIPOS-F186: verbs.schema lybra_queue_supersede.carry_texts(撤换承继预演 / 拒因 / 认领建树 / 开工面文案唯一读取口; 缺 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    texts = (((load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get("lybra_queue_supersede") or {}).get("carry_texts"))
    if not isinstance(texts, dict) or any(not isinstance(texts.get(k), str) for k in SUPERSEDE_CARRY_TEXT_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_queue_supersede.carry_texts 未声明齐 {list(SUPERSEDE_CARRY_TEXT_KEYS)}")
    return texts


CORRECT_DECL_KEYS = ("from_states", "actor_role_class", "decision_choice", "dispute_event_type", "disputed_record_kinds", "followup")
CORRECT_FOLLOWUP_KEYS = ("task_id_template", "inherit_fields_ref", "rebind_fields", "ref_field")
CORRECT_TEXT_KEYS = ("header", "chain", "decision", "decision_ref", "reason_part", "dispute", "followup", "criterion", "resume", "publish_blocked",
                     "record_kept_preview", "record_kept_written", "publish_hint", "will", "preview_line", "done_line", "blocked_line",
                     "partial_line", "not_closed", "not_found", "already", "role_refused", "followup_blocked")
CORRECT_TEMPLATE_KEYS = ("title_template", "body_template", "criteria_part", "evidence_none", "artifacts_missing", "none")


def correct_declaration() -> dict[str, Any]:
    """AIPOS-F207 件②: Owner 纠正已结案卡声明唯一读取口 transitions.schema queue_mutations.correct(+ verbs.lybra_queue_correct.followup
    起草模板并入 followup)。缺键 / from_states 含非队列目录 / 选项未在收件箱声明 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    spec = (load_schema("transitions").get("queue_mutations") or {}).get("correct")
    if not isinstance(spec, dict) or any(not spec.get(k) for k in CORRECT_DECL_KEYS):
        raise SchemaLoadError(f"transitions.schema.json queue_mutations.correct 未声明齐 {list(CORRECT_DECL_KEYS)}")
    followup = spec["followup"]
    if not isinstance(followup, dict) or any(not followup.get(k) for k in CORRECT_FOLLOWUP_KEYS) \
            or "{round}" not in str(followup["task_id_template"]):
        raise SchemaLoadError(f"transitions.schema.json queue_mutations.correct.followup 未声明齐 {list(CORRECT_FOLLOWUP_KEYS)}"
                              "(task_id_template 须含 {round})")
    bad = [x for x in spec["from_states"] if x not in QUEUE_STATES]
    if bad:
        raise SchemaLoadError(f"transitions.schema.json queue_mutations.correct.from_states 含非队列目录 {bad}")
    verb = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get("lybra_queue_correct") or {}
    templates = verb.get("followup")
    if not isinstance(templates, dict) or any(not isinstance(templates.get(k), str) or not templates[k] for k in CORRECT_TEMPLATE_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_queue_correct.followup 未声明齐 {list(CORRECT_TEMPLATE_KEYS)}")
    require_inbox_choices([spec["decision_choice"]], "transitions queue_mutations.correct.decision_choice")
    from tools.aipos_cli.roadmap import followup_inherit_fields  # AIPOS-F214 件①: 承继字段单基表(与路线图修复卡同一张)

    inherit = followup_inherit_fields(followup, "transitions.schema.json queue_mutations.correct.followup")
    return {**spec, "from_states": tuple(str(x) for x in spec["from_states"]),
            "followup": {**followup, **templates, "inherit_fields": inherit}}


PAUSE_DECL_KEYS = ("pause_choice", "resume_choice", "from_states", "actor_role_class", "end_reason", "texts", "exit_codes")
PAUSE_TEXT_KEYS = ("paused_line", "resume_command", "preview_line", "done_line", "blocked_line", "not_found", "bad_state", "already_paused",
                   "not_paused", "role_refused", "next_run_refused", "pause_action", "resume_action")


def pause_declaration() -> dict[str, Any]:
    """AIPOS-F231 件①: Owner 暂停 / 恢复卡声明唯一读取口 verbs.schema lybra_queue_pause。缺键 / from_states 含非队列目录 / 两个选项未在
    收件箱声明 / end_reason 未在 loop 结束原因声明 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    verbs = load_schema("verbs", code_repo_schema_root()).get("verbs") or {}
    spec = verbs.get("lybra_queue_pause")
    where = "verbs.schema.json verbs.lybra_queue_pause"
    if not isinstance(spec, dict) or any(not spec.get(k) for k in PAUSE_DECL_KEYS):
        raise SchemaLoadError(f"{where} 未声明齐 {list(PAUSE_DECL_KEYS)}")
    texts = spec["texts"]
    if not isinstance(texts, dict) or any(not isinstance(texts.get(k), str) or not texts[k] for k in PAUSE_TEXT_KEYS):
        raise SchemaLoadError(f"{where}.texts 未声明齐 {list(PAUSE_TEXT_KEYS)}")
    bad = [x for x in spec["from_states"] if x not in QUEUE_STATES]
    if bad:
        raise SchemaLoadError(f"{where}.from_states 含非队列目录 {bad}")
    require_inbox_choices([spec["pause_choice"], spec["resume_choice"]], f"{where}.pause_choice / resume_choice")
    reasons = (((verbs.get("lybra_loop") or {}).get("run_record") or {}).get("end_reasons")) or {}
    if spec["end_reason"] not in reasons:
        raise SchemaLoadError(f"{where}.end_reason {spec['end_reason']!r} 须为 lybra_loop.run_record.end_reasons 已声明者")
    return {**spec, "from_states": tuple(str(x) for x in spec["from_states"])}


def require_inbox_choices(choices: Any, where: str, *, pinned: tuple[str, str] | None = None) -> list[str]:
    """AIPOS-F213 件③(F208 P1-8 选项): 跨文件选项引用的唯一校验(原 correct.decision_choice 内联校验上提, 禁第二实现)——choices
    (非空字符串列表)每项须为收件箱声明 verbs.lybra_owner_inbox.options 的键; pinned = (类别, 事由) 时另须 ∈ 该类别
    pinned_options_by_reason[事由](收件箱对该事由恒给的选项: 改名 / 删掉即红, 不再静默失效)。不符 = SchemaLoadError(点名 where)。
    返回规范化的选项列表。调用方: correct / supersede 的 decision_choice、transitions nodes.N4.audit_blocked.owner_reaudit.choices。"""
    from tools.aipos_cli.owner_inbox import inbox_declaration
    from tools.schema_loader import SchemaLoadError

    if not isinstance(choices, list) or not choices or not all(isinstance(c, str) and c.strip() for c in choices):
        raise SchemaLoadError(f"{where} 须为非空选项键列表, 得到 {choices!r}")
    decl = inbox_declaration()
    missing = [c for c in choices if c not in decl["options"]]
    if missing:
        raise SchemaLoadError(f"verbs.lybra_owner_inbox.options 未声明 {missing}({where} 引用)")
    if pinned is not None:
        category, reason = pinned
        offered = list(((decl["categories"].get(category) or {}).get("pinned_options_by_reason") or {}).get(reason) or [])
        off = [c for c in choices if c not in offered]
        if off:
            raise SchemaLoadError(f"{where} 的选项 {off} 不在收件箱 verbs.lybra_owner_inbox.categories.{category}"
                                  f".pinned_options_by_reason.{reason} = {offered}(收件箱不给这个选项, 该裁决永远等不到)")
    return [str(c) for c in choices]


def correct_texts() -> dict[str, str]:
    """verbs.schema lybra_queue_correct.texts(唯一读取口; 缺 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    texts = (((load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get("lybra_queue_correct") or {}).get("texts"))
    if not isinstance(texts, dict) or any(not isinstance(texts.get(k), str) for k in CORRECT_TEXT_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_queue_correct.texts 未声明齐 {list(CORRECT_TEXT_KEYS)}")
    return texts


# AIPOS-348: reopen 允许源目录(= 声明 reopen.from_states)
REOPEN_SOURCE_STATES = ALLOWED_TRANSITIONS["reopen"][0]
# AIPOS-F87 件①: 卡字段序唯一定义在 record_writer.CARD_FRONTMATTER_ORDER(本名保留为别名, 供既有调用方)。
FRONTMATTER_ORDER = CARD_FRONTMATTER_ORDER


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-{2,}", "-", value) or "actor"



def _normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    return value


def render_task_markdown(metadata: dict[str, Any], body: str) -> str:
    """AIPOS-F46: 收敛到 F22B 单源 (record_writer.render_markdown).

    原实现用本地 _yaml_scalar 拼接, 不处理 **bold**/"/full-width colon 等毒字段,
    第五次复发后以卡收口: 全部写卡路径统一走 safe_dump 单源.
    """
    return _render_markdown_single_source(metadata, body, FRONTMATTER_ORDER)


def _resolved_within(base_dir: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def resolve_queue_path(repo_root: Path, provided_path: str | Path) -> Path:
    raw_path = Path(provided_path)
    path = raw_path.resolve() if raw_path.is_absolute() else (repo_root / raw_path).resolve()
    queue_root = queue_root_for(repo_root).resolve()
    if not _resolved_within(queue_root, path):
        raise ValueError(f"Task path is outside 5_tasks/queue: {provided_path}")
    if path.suffix.lower() != ".md":
        raise ValueError(f"Task path is not a markdown file: {provided_path}")
    state_names = {part.name for part in path.parents if _resolved_within(queue_root, part)}
    if not any(state in state_names for state in QUEUE_STATES):
        raise ValueError(f"Task path is not inside a queue state directory: {provided_path}")
    if not path.exists():
        raise FileNotFoundError(f"Task path does not exist: {provided_path}")
    if not path.is_file():
        raise FileNotFoundError(f"Task path is not a file: {provided_path}")
    return path


def _read_task_markdown(path: Path) -> tuple[dict[str, Any], str, list[str]]:
    text = path.read_text(encoding="utf-8")
    metadata, body, warnings = parse_markdown_frontmatter(text)
    return _normalize_value(metadata), body, warnings


def _select_task(repo_root: Path, *, task_id: str | None = None, task_path: str | None = None) -> dict[str, Any]:
    if bool(task_id) == bool(task_path):
        raise ValueError("Exactly one of --task-id or --path must be provided")
    if task_id:
        selected, matches = find_task_by_id(task_id, repo_root)
        if not matches:
            raise ValueError(f"No task found for task_id: {task_id}")
        if len(matches) > 1:
            paths = ", ".join(sorted(str(match.get("path")) for match in matches))
            raise ValueError(f"Duplicate task_id {task_id} found in: {paths}")
        assert selected is not None
        return selected
    path = resolve_queue_path(repo_root, str(task_path))
    return load_task_file(path, repo_root)


def _prepare_claim(
    metadata: dict[str, Any],
    actor: str,
    timestamp: str,
    *,
    claim_id_override: str | None = None,
    session_id_override: str | None = None,
) -> dict[str, Any]:
    updated = dict(metadata)
    task_id = str(updated.get("task_id"))
    # AIPOS-R4A F-2: claim 走转移引擎（一机制一实现）
    from tools.aipos_cli.transition_engine import apply_transition_metadata
    claim_id = claim_id_override or build_runtime_id("claim", task_id, timestamp, actor)
    session_id = session_id_override or build_runtime_id("session", task_id, timestamp, actor)
    updated = apply_transition_metadata(
        metadata=metadata,
        transition_name="claim",
        actor=actor,
        timestamp=timestamp,
        claim_id=claim_id,
        claimed_at=timestamp,
        active_session_id=session_id,
    )
    if updated.get("needs_owner") is None:
        updated["needs_owner"] = False
    return updated


def _prepare_block(metadata: dict[str, Any], actor: str, timestamp: str, reason: str) -> dict[str, Any]:
    # AIPOS-R4A F-2: block 走转移引擎（一机制一实现）
    from tools.aipos_cli.transition_engine import apply_transition_metadata
    updated = apply_transition_metadata(
        metadata=metadata,
        transition_name="block",
        actor=actor,
        timestamp=timestamp,
        reason=reason,
    )
    updated["needs_owner"] = True
    return updated


def _prepare_complete(metadata: dict[str, Any], actor: str, timestamp: str, report_link: str) -> dict[str, Any]:
    # AIPOS-R4A: 走转移引擎统一处理（一机制一实现）
    from tools.aipos_cli.transition_engine import apply_transition_metadata
    return apply_transition_metadata(
        metadata=metadata,
        transition_name="complete",
        actor=actor,
        timestamp=timestamp,
        schema=None,  # 引擎内部自动加载
        repo_root=None,
        report_link=report_link,
    )


def _prepare_reopen(metadata: dict[str, Any], actor: str, timestamp: str, reason: str) -> dict[str, Any]:
    # AIPOS-R4A: 走转移引擎统一处理（一机制一实现）
    from tools.aipos_cli.transition_engine import apply_transition_metadata
    return apply_transition_metadata(
        metadata=metadata,
        transition_name="reopen",
        actor=actor,
        timestamp=timestamp,
        schema=None,  # 引擎内部自动加载
        repo_root=None,
        reason=reason,
    )


def _prepare_withdraw(metadata: dict[str, Any], actor: str, timestamp: str, reason: str) -> dict[str, Any]:
    """AIPOS-315: prepare metadata for task withdrawal."""
    # AIPOS-R4A F-2: withdraw 走转移引擎（一机制一实现）
    from tools.aipos_cli.transition_engine import apply_transition_metadata
    return apply_transition_metadata(
        metadata=metadata,
        transition_name="withdraw",
        actor=actor,
        timestamp=timestamp,
        reason=reason,
    )


def repair_bad_frontmatter(
    repo_root: Path,
    task_id: str,
    actor: str,
    dry_run: bool = True,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    """AIPOS-F65C 件① 入口(`lybra queue repair`)——AIPOS-F87 件② 起委托 state_lint.repair_frontmatter_invalid 唯一实现。
    AIPOS-F178 件③: 卡面可解析时同一命令接着查「已认领但缺卡工作树」(repair_missing_worktree 唯一实现; confirm=False 预演,
    confirm=True 且非 dry_run 才建树); 卡面坏 = 先只做规整(规整前无从判定是否须建树), 行为同 F178 前。

    原本地正则规整(凡含 `**` 即整行加引号、不验值、不留修复记录、TODO 记录)退役; 现与 `lybra state repair` 同一保值规整:
    仅引号化解析失败的顶层单行标量, 规整后逐字节/逐值核对, 写 records/events/<ID>/event_repair_*.md。输出保留 F65C 字段形。
    """
    from tools.aipos_cli.state_lint import repair_frontmatter_invalid
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card

    result: dict[str, Any] = {
        "operation": "repair_frontmatter",
        "task_id": task_id,
        "actor": actor,
        "dry_run": dry_run,
        "repairs_made": [],
        "verdict": "OK",
    }
    try:
        task_path, _queue_dir = find_task_card(repo_root, task_id)
    except AmbiguousTaskCard as exc:
        result["verdict"] = "BLOCK"
        result["blocking_reasons"] = [str(exc)]
        return result
    if not task_path:
        result["verdict"] = "BLOCK"
        result["blocking_reasons"] = [f"Task file not found for {task_id}"]
        return result

    repair = repair_frontmatter_invalid(repo_root, task_id, dry_run=dry_run, actor=actor)
    result["frontmatter_repair"] = repair
    if not repair["applicable"]:
        # AIPOS-F178 件③: 卡面可解析 → 同一命令再查「已认领但缺卡工作树」(预演缺省, --confirm 建树; --dry-run 恒预演)
        from tools.aipos_cli.next_resolver import repair_missing_worktree

        worktree = repair_missing_worktree(repo_root, task_id, actor, confirm=bool(confirm) and not dry_run)
        result["worktree_repair"] = worktree
        if worktree["verdict"] == "BLOCK":
            result["verdict"] = "BLOCK"
            result["blocking_reasons"] = list(worktree["blocking_reasons"])
        if worktree["action"] == "created":
            result["repairs_made"] = [worktree["message"]]
            result["repairs_count"] = 1
        result["message"] = f"No frontmatter repairs needed (frontmatter appears valid); worktree: {worktree['message']}"
        return result
    result["repairs_made"] = [f"Line {r['line']}: quoted value of {r['key']} (value unchanged)" for r in repair["repairs"]]
    if repair["unresolved"]:
        result["verdict"] = "BLOCK"
        result["blocking_reasons"] = list(repair["unresolved"])
        return result
    result["repairs_count"] = len(result["repairs_made"])
    if repair["repaired"]:
        result["file_written"] = repair["card_path"]
        result["amendment_note"] = f"AIPOS-F87 frontmatter repair record: {repair['repair_record']}"
    return result


def _mutation_metadata(
    action: str,
    metadata: dict[str, Any],
    actor: str,
    *,
    reason: str | None = None,
    report_link: str | None = None,
    claim_id_override: str | None = None,
    session_id_override: str | None = None,
    supersede: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = iso_z()
    if action == RecordType.CLAIM:
        return _prepare_claim(
            metadata,
            actor,
            timestamp,
            claim_id_override=claim_id_override,
            session_id_override=session_id_override,
        )
    if action == "block":
        assert reason is not None
        return _prepare_block(metadata, actor, timestamp, reason)
    if action == "complete":
        assert report_link is not None
        return _prepare_complete(metadata, actor, timestamp, report_link)
    if action == "reopen":
        assert reason is not None
        return _prepare_reopen(metadata, actor, timestamp, reason)
    if action == "withdraw":
        assert reason is not None
        return _prepare_withdraw(metadata, actor, timestamp, reason)
    if action == "supersede":
        assert reason is not None and supersede is not None
        from tools.aipos_cli.transition_engine import apply_transition_metadata

        return apply_transition_metadata(metadata=metadata, transition_name="supersede", actor=actor, timestamp=timestamp,
                                         reason=reason, superseded_by=supersede.get("superseded_by"),
                                         owner_words=supersede.get("owner_words"), owner_ref=supersede.get("owner_ref"),
                                         decision_id=supersede.get("decision_id"))
    raise ValueError(f"Unsupported queue mutation action: {action}")


def _base_result(source_path: Path, repo_root: Path, source_task: dict[str, Any], action: str, dry_run: bool, actor: str, to_state: str) -> dict[str, Any]:
    return {
        "action": f"queue_{action}",
        "dry_run": dry_run,
        "would_write": False,
        "wrote": False,
        "would_move": False,
        "moved": False,
        "task_id": source_task.get("task_id"),
        "source_path": str(source_path.resolve().relative_to(repo_root.resolve())),  # AIPOS-240: symlink-safe
        "target_path": None,
        "from_state": source_task.get("queue_state"),
        "to_state": to_state,
        "actor": actor,
        "verdict": Verdict.PASS,
        "blocking_reasons": [],
        "warnings": [],
        "classification_warnings": [],
        "planned_writes": [],
        "planned_moves": [],
        "updated_frontmatter": {},
        "safety_notice": MUTATION_SAFETY_NOTICE,
        "with_records": False,
        "records_enabled": False,
    }


def _build_record_plan(path: Path, record_type: str, *, would_write: bool = False, wrote: bool = False, would_update: bool = False, updated: bool = False) -> dict[str, Any]:
    item = {"path": str(path), "record_type": record_type}
    if would_write or wrote:
        item["would_write"] = would_write
        item["wrote"] = wrote
    if would_update or updated:
        item["would_update"] = would_update
        item["updated"] = updated
    return item


def _prepare_records_plan(
    repo_root: Path,
    action: str,
    *,
    source_task: dict[str, Any],
    source_metadata: dict[str, Any],
    updated_metadata: dict[str, Any],
    actor: str,
    reason: str | None,
    report_link: str | None,
) -> dict[str, Any]:
    task_id = str(updated_metadata.get("task_id") or "")
    validate_safe_task_id(task_id)
    root = repo_root.resolve()  # AIPOS-240 (F-o3-19): symlink-safe repo-relative render (record paths are .resolve()d)
    timestamp = str(updated_metadata.get("claimed_at") or updated_metadata.get("blocked_at") or updated_metadata.get("completed_at") or updated_metadata.get("reopened_at") or "")
    result: dict[str, Any] = {
        "with_records": True,
        "records_enabled": True,
        "record_writes": [],
        "record_updates": [],
        "record_blocking_reasons": [],
        "record_warnings": [],
        "record_previews": [],
    }
    task_target_path = str(source_task.get("path") or "")

    if action == RecordType.CLAIM:
        claim_id = str(updated_metadata.get("claim_id") or "")
        session_id = str(updated_metadata.get("active_session_id") or "")
        claim_path, session_path = claim_record_paths(repo_root, task_id, claim_id, session_id)
        result["proposed_claim_id"] = claim_id
        result["proposed_session_id"] = session_id
        result["claim_log_path"] = str(claim_path.resolve().relative_to(root))
        result["session_record_path"] = str(session_path.resolve().relative_to(root))
        if claim_path.exists():
            result["record_blocking_reasons"].append(f"Claim log already exists: {claim_path.resolve().relative_to(root)}")
        if session_path.exists():
            result["record_blocking_reasons"].append(f"Session record already exists: {session_path.resolve().relative_to(root)}")
        claim_markdown = build_claim_log_markdown(
            task_id=task_id,
            task_path=task_target_path.replace("/pending/", "/claimed/"),
            actor=actor,
            claim_id=claim_id,
            session_id=session_id,
            created_at=timestamp,
        )
        session_markdown = build_session_record_markdown(
            task_id=task_id,
            task_path=task_target_path.replace("/pending/", "/claimed/"),
            actor=actor,
            session_id=session_id,
            claim_id=claim_id,
            created_at=timestamp,
        )
        result["record_writes"] = [
            _build_record_plan(Path(result["claim_log_path"]), RecordType.CLAIM_LOG, would_write=not result["record_blocking_reasons"]),
            _build_record_plan(Path(result["session_record_path"]), RecordType.SESSION_RECORD, would_write=not result["record_blocking_reasons"]),
        ]
        result["record_previews"] = [
            {"path": result["claim_log_path"], "record_type": RecordType.CLAIM_LOG, "rendered_markdown": claim_markdown},
            {"path": result["session_record_path"], "record_type": RecordType.SESSION_RECORD, "rendered_markdown": session_markdown},
        ]
        result["claim_log_markdown"] = claim_markdown
        result["session_record_markdown"] = session_markdown
        return result

    active_or_last = str(source_metadata.get("active_session_id") or source_metadata.get("last_session_id") or "")
    if action in {"block", "complete"} and not str(source_metadata.get("active_session_id") or "").strip():
        result["record_blocking_reasons"].append(f"{action} with records requires active_session_id")
        return result
    if action == "reopen" and not active_or_last:
        result["record_warnings"].append("reopen with records found no active_session_id or last_session_id; no session record update planned")
        return result

    session_id = str(source_metadata.get("active_session_id") or source_metadata.get("last_session_id") or "")
    path = session_record_path(repo_root, task_id, session_id)
    result["proposed_session_id"] = session_id
    result["session_record_path"] = str(path.resolve().relative_to(root))
    if not path.exists():
        result["record_blocking_reasons"].append(f"Session record does not exist: {path.resolve().relative_to(root)}")
        return result

    existing_metadata, existing_body, parse_warnings = load_session_record(path)
    for warning in parse_warnings:
        result["record_blocking_reasons"].append(f"Session record parse issue: {warning}")
    if existing_metadata.get("task_id") not in (None, task_id):
        result["record_blocking_reasons"].append("Session record task_id does not match queue task")
        return result

    if action == "block":
        updated_markdown = update_session_record_markdown(
            existing_metadata,
            existing_body,
            actor=actor,
            timestamp=timestamp,
            status="blocked",
            current_state="blocked",
            event_line=f"{timestamp} blocked by {actor}: {reason}",
        )
    elif action == "complete":
        updated_markdown = update_session_record_markdown(
            existing_metadata,
            existing_body,
            actor=actor,
            timestamp=timestamp,
            status="completed",
            current_state="completed",
            event_line=f"{timestamp} completed by {actor}: {report_link}",
        )
    else:
        updated_markdown = update_session_record_markdown(
            existing_metadata,
            existing_body,
            actor=actor,
            timestamp=timestamp,
            status="reopened",
            current_state="pending",
            event_line=f"{timestamp} reopened by {actor}: {reason}",
        )
    result["record_updates"] = [_build_record_plan(Path(result["session_record_path"]), RecordType.SESSION_RECORD, would_update=True)]
    result["record_previews"] = [
        {"path": result["session_record_path"], "record_type": RecordType.SESSION_RECORD, "rendered_markdown": updated_markdown}
    ]
    result["session_record_markdown"] = updated_markdown
    return result


def _check_for_pass_audit_verdict(repo_root: Path, task_id: str) -> bool:
    """AIPOS-FND-7F3 + AIPOS-F2: check if a task's LATEST gate-born audit verdict is PASS/PASS_WITH_NOTES.

    Scans 5_tasks/records/audit_verdicts/<task_id>/ for **gate-born** verdict records
    (AIPOS-F2: hand-written files are ignored — same single-source check as
    audit_helpers.is_gate_born_verdict_metadata), sorts by verdict_at
    (null-safe: verdict_at > timestamp > empty string), and checks if the LATEST is PASS.
    Returns True only if the most recent gate-born verdict allows task completion.
    
    This ensures the re-review flow works correctly:
    - FAIL(T1) → re-audit → PASS(T2) → complete ✅ (latest=PASS)
    - PASS(T1) → (hypothetical) FAIL(T2) → complete ❌ (latest=FAIL, though overturn is blocked)
    
    PASS and PASS_WITH_NOTES both allow task completion; FAIL/REQUEST_CHANGES/BLOCKED block.
    """
    if not task_id:
        return False
    verdicts_dir = record_dir(repo_root, "audit_verdicts", task_id)
    if not verdicts_dir.is_dir():
        return False
    
    # AIPOS-F2: 导入共享门生判定函数
    from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata
    
    verdicts = []
    for verdict_file in verdicts_dir.glob("*.md"):
        try:
            text = verdict_file.read_text(encoding="utf-8")
            metadata, _, _ = parse_markdown_frontmatter(text)
            # AIPOS-F2: 只认门生记录,手写文件跳过
            if not is_gate_born_verdict_metadata(metadata):
                continue
            verdict_value = str(metadata.get("verdict") or "").strip().upper()
            # Extract timestamp for sorting (null-safe, same as _verdict_time in board_adapter)
            # Convert to string to ensure consistent comparison (metadata may parse as datetime)
            verdict_at_raw = metadata.get("verdict_at") or metadata.get("timestamp") or ""
            verdict_at = str(verdict_at_raw) if verdict_at_raw else ""
            verdicts.append({"verdict": verdict_value, "verdict_at": verdict_at})
        except (OSError, UnicodeDecodeError):
            continue
    
    if not verdicts:
        return False
    
    # Sort by verdict_at (latest first), then check if latest is PASS/PASS_WITH_NOTES
    latest_verdict = max(verdicts, key=lambda v: v["verdict_at"])
    return latest_verdict["verdict"] in {Verdict.PASS, Verdict.PASS_WITH_NOTES}


def mutate_queue_task(
    repo_root: Path,
    action: str,
    *,
    task_id: str | None = None,
    task_path: str | None = None,
    actor: str,
    reason: str | None = None,
    report_link: str | None = None,
    dry_run: bool = False,
    profiles: dict[str, Any] | None = None,
    with_records: bool = False,
    claim_id_override: str | None = None,
    session_id_override: str | None = None,
    owner_ruling_ref: str | None = None,
    supersede: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # AIPOS-R6A 靶子②: CLI queue mutation 平行路径拆除 — 禁止非 dry-run 执行
    # (绕过 gate 控制执行流程，不产生真实的 claim/return 记录，"卡面假成功")
    # CLI 只能预览(dry-run=True)，真实变更必须走 MCP gate 工具。
    # 实撞案例：CONN-LOOP-2/R5B 各被骗一次
    # AIPOS-R6A FIX2: 例外：board_adapter 调用（profiles 参数存在）是合法的 MCP 路径
    # （包括 PreAuthorized autorelease、MCP confirm），允许非 dry-run 执行
    if not dry_run and profiles is None:
        raise ValueError(
            f"CLI queue {action} no longer supports direct execution (AIPOS-R6A removal of parallel loop paths). "
            f"Use MCP gate tools instead: lybra_queue_claim_dry_run + lybra_queue_claim_confirm for claim, "
            f"lybra_queue_return_dry_run + lybra_queue_return_confirm for return. "
            f"CLI queue commands are now preview-only (add --dry-run flag)."
        )
    
    if action not in ALLOWED_TRANSITIONS:
        raise ValueError(f"Unsupported queue mutation action: {action}")
    if not str(actor or "").strip():
        raise ValueError("--actor is required")
    if action in {"block", "reopen", "withdraw", "supersede"} and not str(reason or "").strip():
        raise ValueError("--reason is required and must be non-empty")
    if action == "complete" and not str(report_link or "").strip():
        raise ValueError("--report-link is required and must be non-empty")

    source_task = _select_task(repo_root, task_id=task_id, task_path=task_path)
    source_path = (repo_root / str(source_task["path"])).resolve()
    from_states, to_state = ALLOWED_TRANSITIONS[action]
    from_state = " or ".join(from_states)

    # AIPOS-315: withdraw supports multiple source states(值域 = 声明 withdraw.from_states); AIPOS-F184: supersede 同(声明 supersede.from_states)
    if action in ("withdraw", "supersede"):
        actual_state = source_task.get("queue_state")
        if actual_state not in from_states:
            raise ValueError(f"{action} only supports {from_state} tasks, found {actual_state}")
    
    target_path = queue_root_for(repo_root) / to_state / source_path.name
    source_metadata, source_body, _warnings = _read_task_markdown(source_path)
    result = _base_result(source_path, repo_root, source_task, action, dry_run, actor, to_state)
    result["target_path"] = str(target_path.relative_to(repo_root))
    if with_records:
        result["with_records"] = True
        result["records_enabled"] = True
        result["safety_notice"] = MUTATION_RECORDS_SAFETY_NOTICE

    # AIPOS-F170 件①: 声明须带 Owner 裁决引用的源目录(blocked)撤回 = 核引用(既有 withdraw 口放宽前置, 不另写解除口)
    if action == "withdraw" and source_task.get("queue_state") in withdraw_owner_ruling_states():
        problem, note = withdraw_owner_ruling_problem(repo_root, str(source_task.get("task_id") or ""), owner_ruling_ref, dry_run=dry_run)
        if problem:
            result["blocking_reasons"].append(problem)
        if note:
            result["warnings"].append(note)
        result["owner_ruling_ref"] = str(owner_ruling_ref or "").strip() or None

    # AIPOS-F184 件③: 撤换前置(角色类 / 新卡 / Owner 原话)——判定唯一实现 supersede_problems
    if action == "supersede":
        for problem in supersede_problems(repo_root, str(source_task.get("task_id") or ""), actor, supersede):
            result["blocking_reasons"].append(problem)
        result["superseded_by"] = str((supersede or {}).get("superseded_by") or "").strip() or None

    validation_profiles = profiles if profiles else None
    # AIPOS-315: withdraw allows any actor (advisor can revoke any task), skip actor validation(AIPOS-F184: supersede 同, 角色类另判)
    validation_actor = None if action in ("withdraw", "supersede") else actor
    validation = validate_single_task(source_task, current_actor=validation_actor, profiles=validation_profiles)
    if validation["blocking_reasons"]:
        # AIPOS-315: filter out actor-related blocks for withdraw
        if action in ("withdraw", "supersede"):
            filtered_blocks = [
                reason for reason in validation["blocking_reasons"]
                # AIPOS-F133: 撤回不受依赖判据约束(依赖只门认领)
                if not any(keyword in str(reason).lower() for keyword in ["actor", "claimed by", "assigned", "dependency_unmet", "dependency_unverifiable"])
            ]
            result["blocking_reasons"].extend(filtered_blocks)
        # AIPOS-R4A: reopen malformed 卡修复路径——降级 active_session_id 残留检查
        elif action == "reopen":
            for reason in validation["blocking_reasons"]:
                if "active_session_id" in str(reason):
                    # malformed 签名（gate 旧自动关闭遗留），降级 WARN，引擎会清理
                    result["warnings"].append(f"MALFORMED_REPAIR: {reason} (will be cleaned by transition engine)")
                else:
                    result["blocking_reasons"].extend([reason])
        else:
            result["blocking_reasons"].extend(validation["blocking_reasons"])
    if validation["warnings"]:
        result["warnings"].extend(validation["warnings"])
    result["classification_warnings"].extend(validation.get("classification_warnings", []))
    needs_owner_reasons: list[str] = []

    # AIPOS-F122 件③: 存量冻结卡拒认领(判定唯一实现 legacy_baseline.frozen_rejection; 本函数是门 claim / 信封自动放行 / CLI 预览
    # 共同的唯一搬卡执行器, 拒因进 blocking_reasons 带解冻命令); 清单读不出 = 拒(fail-closed)
    if action == "claim":
        from tools.aipos_cli.legacy_baseline import LegacyBaselineError, frozen_rejection

        try:
            rejection = frozen_rejection(repo_root, [str(source_task.get("task_id") or "")], action="claim")
        except LegacyBaselineError as exc:
            rejection = {"code": exc.code, "message": f"{exc}; 存量冻结清单读不出, 拒 claim"}
        if rejection is not None:
            result["blocking_reasons"].append(rejection["message"])
            result["error_code"] = rejection["code"]
        # AIPOS-F133 件③: 依赖未满足拒认领(全部 task_class; 判据唯一实现 task_complexity.unmet_dependencies, 只读门生记录,
        # 全部依赖满足才放行)。dependency_gate 级别的 validator 已给出同一拒因时不重复。
        from tools.aipos_cli.task_complexity import unmet_dependencies

        for reason_text in unmet_dependencies(source_metadata, repo_root):
            if reason_text not in result["blocking_reasons"]:
                result["blocking_reasons"].append(reason_text)
            result.setdefault("error_code", "DEPENDENCY_UNMET")

    # AIPOS-315/348: withdraw and reopen have flexible from_state, skip this check
    if action not in ("withdraw", "reopen"):
        if source_task.get("queue_state") not in from_states:
            result["blocking_reasons"].append(
                f"Invalid transition for {action}: expected source state {from_state}, found {source_task.get('queue_state')}"
            )
        if source_task.get("frontmatter_status") not in from_states:
            result["blocking_reasons"].append(
                f"Directory/status mismatch blocks {action}: expected frontmatter status {from_state}"
            )
    # AIPOS-348: reopen from completed/blocked needs frontmatter/status consistency + source state check
    if action == "reopen":
        actual_state = source_task.get("queue_state")
        if actual_state not in REOPEN_SOURCE_STATES:
            result["blocking_reasons"].append(
                f"Invalid transition for reopen: expected source state in {REOPEN_SOURCE_STATES}, found {actual_state}"
            )
        fm_status = source_task.get("frontmatter_status")
        if fm_status and fm_status != actual_state:
            result["blocking_reasons"].append(
                f"Directory/status mismatch blocks reopen: queue_state={actual_state} but frontmatter status={fm_status}"
            )

    claimed_by = str(source_metadata.get("claimed_by") or "")
    claimed_actor_matches = (
        actor_matches_task_actor(actor, claimed_by, profiles)
        if profiles
        else claimed_by == actor
    )
    if action in {"block", "complete"} and not claimed_actor_matches:
        result["blocking_reasons"].append("Task is claimed by another actor")

    # AIPOS-348: audit gate — complete requires PASS verdict when audit: required
    if action == "complete":
        audit_requirement = str(source_metadata.get("audit") or "").strip().lower()
        if audit_requirement == "required":
            resolved_task_id = str(source_metadata.get("task_id") or "")
            has_pass_verdict = _check_for_pass_audit_verdict(repo_root, resolved_task_id)
            if not has_pass_verdict:
                result["blocking_reasons"].append(
                    f"Task {resolved_task_id} has audit: required but no PASS audit verdict found. "
                    f"Cannot complete without a passing audit. "
                    f"Required path: return \u2192 audit dispatch \u2192 audit verdict (PASS) \u2192 then complete."
                )

    pending_collision = find_case_insensitive_path_collision(target_path.parent, target_path.name)
    if pending_collision is not None:
        collision_rel = str(pending_collision.resolve().relative_to(repo_root.resolve()))
        if pending_collision.resolve() != target_path.resolve():
            result["blocking_reasons"].append(f"Case-insensitive target filename collision: {collision_rel}")
        elif target_path.exists():
            result["blocking_reasons"].append(f"Target file already exists: {result['target_path']}")

    updated_metadata = _mutation_metadata(
        action,
        source_metadata,
        actor,
        reason=str(reason or "").strip() or None,
        report_link=str(report_link or "").strip() or None,
        claim_id_override=claim_id_override,
        session_id_override=session_id_override,
        supersede=supersede,
    )
    rendered_markdown = render_task_markdown(updated_metadata, source_body)
    result["updated_frontmatter"] = updated_metadata
    result["planned_writes"] = [{"path": result["target_path"], "kind": "create", "type": "task_markdown"}]
    result["planned_moves"] = [{"from": result["source_path"], "to": result["target_path"], "kind": "queue_state_move"}]

    record_plan: dict[str, Any] = {
        "with_records": False,
        "records_enabled": False,
        "record_writes": [],
        "record_updates": [],
        "record_blocking_reasons": [],
        "record_warnings": [],
        "record_previews": [],
    }
    if with_records:
        record_plan = _prepare_records_plan(
            repo_root,
            action,
            source_task=source_task,
            source_metadata=source_metadata,
            updated_metadata=updated_metadata,
            actor=actor,
            reason=str(reason or "").strip() or None,
            report_link=str(report_link or "").strip() or None,
        )
        result.update({key: value for key, value in record_plan.items() if key not in {"record_previews", "claim_log_markdown", "session_record_markdown"}})
        for reason_text in record_plan.get("record_blocking_reasons", []):
            if reason_text not in result["blocking_reasons"]:
                result["blocking_reasons"].append(reason_text)
        for warning_text in record_plan.get("record_warnings", []):
            if warning_text not in result["warnings"]:
                result["warnings"].append(warning_text)

    preview_task = load_task_file(source_path, repo_root)
    preview_task["metadata"] = updated_metadata
    preview_task["frontmatter_status"] = updated_metadata.get("status")
    preview_task["queue_state"] = to_state
    preview_task["path"] = str(target_path.relative_to(repo_root))
    preview_task["assigned_to"] = updated_metadata.get("assigned_to")
    preview_task["agent_instance"] = updated_metadata.get("agent_instance")
    preview_task["claimed_by"] = updated_metadata.get("claimed_by")
    preview_task["needs_owner"] = updated_metadata.get("needs_owner")
    # AIPOS-315: use same actor logic for preview validation
    preview_actor = None if action in ("withdraw", "supersede") else actor
    preview_validation = validate_single_task(preview_task, current_actor=preview_actor, profiles=validation_profiles)
    if preview_validation["blocking_reasons"]:
        for reason_text in preview_validation["blocking_reasons"]:
            if reason_text not in result["blocking_reasons"]:
                result["blocking_reasons"].append(reason_text)
    for warning_text in preview_validation["warnings"]:
        if warning_text not in result["warnings"]:
            result["warnings"].append(warning_text)
    for warning_text in preview_validation.get("classification_warnings", []):
        if warning_text not in result["classification_warnings"]:
            result["classification_warnings"].append(warning_text)
    needs_owner_reasons.extend(
        reason_text
        for reason_text in preview_validation.get("needs_owner_reasons", [])
        if reason_text not in needs_owner_reasons
    )

    if result["blocking_reasons"]:
        result["verdict"] = Verdict.BLOCK
    elif needs_owner_reasons:
        result["verdict"] = Verdict.NEEDS_OWNER
    elif [warning for warning in result["warnings"] if warning not in result["classification_warnings"]]:
        result["verdict"] = Verdict.WARN
    else:
        result["verdict"] = Verdict.PASS

    result["would_write"] = result["verdict"] != Verdict.BLOCK
    result["would_move"] = result["verdict"] != Verdict.BLOCK
    if dry_run:
        result["rendered_markdown"] = rendered_markdown
        if with_records and record_plan.get("record_previews"):
            result["record_previews"] = record_plan["record_previews"]
        return result
    if result["verdict"] == Verdict.BLOCK:
        return result

    # AIPOS-F90 件①(Owner 2026-10-02 裁定「门认领建工作树失败 = 直接拒绝认领」, 承接 F88 G4): code 卡认领在任何写入
    # (记录/骨架/卡面迁移)之前先经全产品唯一建树实现 next_resolver._ensure_worktree 建/复用卡工作树; 失败 = 认领 BLOCK,
    # 拒因带建树原文与出口, 队列与记录零变更(不留 claimed 无工作树的卡)。
    built_worktree: dict[str, Any] | None = None
    # AIPOS-F89 件③a: 须建树判据唯一 next_resolver.card_needs_worktree(代码卡 = 卡分支工作树; 代码卡的审计卡 = 被审分支 tip
    # 只读 detached 取证工作树, 同一建树入口 _ensure_worktree); 建树失败同样拒认领。
    from tools.aipos_cli.next_resolver import card_needs_worktree

    if action == RecordType.CLAIM and card_needs_worktree(repo_root, dict(updated_metadata)):
        from tools.aipos_cli.next_resolver import _ensure_worktree

        built_worktree = _ensure_worktree(repo_root, str(updated_metadata.get("task_id", "")), card_frontmatter=dict(updated_metadata))
        if not built_worktree.get("ok"):
            reason = str(built_worktree.get("message") or "worktree 建立失败(无拒因原文)")
            result["verdict"] = Verdict.BLOCK
            result["blocking_reasons"].append(f"WORKTREE_CREATE_FAILED: 卡工作树建立失败, 认领被拒(队列与记录未变更): {reason}")
            result["worktree_created"] = False
            result["worktree_error"] = reason
            result["would_write"] = False
            result["would_move"] = False
            return result

    # AIPOS-F64-fix1: 迁移到统一 writer (write_records_atomic)
    if with_records:
        from tools.aipos_cli.record_writer import write_records_atomic
        
        records_to_write = []
        for item in record_plan.get("record_writes", []):
            record_type_str = str(item["record_type"]).lower().replace("recordtype.", "")
            record_id = Path(item["path"]).stem  # 从路径提取 record_id
            
            if item["record_type"] == RecordType.CLAIM_LOG:
                markdown_content = str(record_plan["claim_log_markdown"])
                records_to_write.append(("claim", record_id, markdown_content))
            elif item["record_type"] == RecordType.SESSION_RECORD:
                markdown_content = str(record_plan["session_record_markdown"])
                records_to_write.append(("session", record_id, markdown_content))
        
        # 原子写入所有新建记录
        if records_to_write:
            write_result = write_records_atomic(repo_root, records_to_write)
            for item in record_plan.get("record_writes", []):
                item["wrote"] = write_result["ok"]
        
        # 更新既有记录
        for item in record_plan.get("record_updates", []):
            record_id = Path(item["path"]).stem
            markdown_content = str(record_plan["session_record_markdown"])
            update_records = [("session", record_id, markdown_content)]
            write_result = write_records_atomic(repo_root, update_records)
            item["updated"] = write_result["ok"]
        
        # AIPOS-F65A-fix2: Create RETURN.md skeleton for claim actions
        if action == RecordType.CLAIM:
            from tools.aipos_cli.board_adapter import _create_return_skeleton
            task_id_for_skeleton = str(updated_metadata.get("task_id", "")).strip()
            if task_id_for_skeleton:
                try:
                    skeleton_result = _create_return_skeleton(repo_root, task_id_for_skeleton, dict(updated_metadata))
                    if skeleton_result:
                        # Record the skeleton creation (informational, doesn't block)
                        result["skeleton_created"] = True
                        result["skeleton_path"] = skeleton_result["path"]
                except Exception as skeleton_exc:
                    # Skeleton creation failure blocks claim (fail-closed)
                    raise skeleton_exc

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(rendered_markdown, encoding="utf-8")
    source_path.unlink()
    result["wrote"] = True
    result["moved"] = True
    
    # AIPOS-R5A + F88 件① + F90 件①: 卡工作树已在写入前由唯一建树实现建好(见上, 失败已 BLOCK); 此处回写卡面落点字段
    # (落点 = card_worktree_location, 分支 = N5 branch_pattern 声明; 与 next --run / my-tasks 开工面同一函数)
    if built_worktree is not None:
        updated_metadata["active_worktree_path"] = str(built_worktree["worktree_path"])
        updated_metadata["active_worktree_branch"] = str(built_worktree["branch"])
        rendered_markdown = render_task_markdown(updated_metadata, source_body)
        target_path.write_text(rendered_markdown, encoding="utf-8")
        result["updated_frontmatter"] = updated_metadata
        result["worktree_created"] = True
        result["worktree_path"] = str(built_worktree["worktree_path"])
        result["worktree_branch"] = str(built_worktree["branch"])
        if built_worktree.get("carried_from"):
            # AIPOS-F186 件②: 撤换承继卡——卡分支从原卡分支记录 tip 建, 认领输出写「承继自 <原卡> <分支>@<tip>」
            result["worktree_carried_from"] = dict(built_worktree["carried_from"])
            result["worktree_carried_line"] = str(built_worktree.get("carried_line") or "")
        if built_worktree.get("detached"):
            # AIPOS-F89 件③a: 审计卡取证工作树 = 被审分支 tip 的 detached 只读树
            result["worktree_detached"] = True
            result["worktree_commit"] = str(built_worktree.get("commit") or "")
            result["forensic_reviewed_task_id"] = str(built_worktree.get("reviewed_task_id") or "")

    return result


# ---------------------------------------------------------------------------
# AIPOS-F123 件①: 人肉期在途卡收编(claim 动词族, 声明 transitions nodes.N1.adoption + queue_mutations.transitions.adopt)。
# 判据 + 卡面 + 建树的唯一实现; 记录计划/落盘在 board_adapter.adopt_task(复用 claim 记录 writer), 信封判定在门工具。
# ---------------------------------------------------------------------------

#: 收编前卡面运行时字段原值快照(写入 claim 记录 legacy_card_runtime, 审计追溯)
ADOPTION_RUNTIME_FIELDS = ("claimed_by", "claimed_at", "claim_id", "active_session_id", "active_worktree_branch", "active_worktree_path")


def adoption_declaration() -> dict[str, Any]:
    """transitions.schema nodes.N1.adoption(收编声明唯一读取口)。缺 adopted_from / verb_family / guards = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    node = ((load_schema("transitions").get("nodes") or {}).get("N1") or {})
    decl = node.get("adoption")
    if (not isinstance(decl, dict) or not isinstance(decl.get("guards"), dict)
            or not str(decl.get("adopted_from") or "").strip() or not str(decl.get("verb_family") or "").strip()):
        raise SchemaLoadError("transitions.schema.json nodes.N1.adoption(adopted_from / verb_family / guards)未声明")
    return decl


def adoption_refusal(code: str, detail: str, decl: dict[str, Any] | None = None) -> str:
    """收编拒因唯一文案: `<code>: <声明 error_message>: <事实>。出口: <声明 next_step>`(code 未声明 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError

    guard = (decl or adoption_declaration())["guards"].get(code)
    if not isinstance(guard, dict) or not guard.get("error_message"):
        raise SchemaLoadError(f"transitions.schema.json nodes.N1.adoption.guards.{code} 未声明")
    return f"{code}: {guard['error_message']}: {detail}。出口: {guard.get('next_step') or '(未声明)'}"


def legacy_frozen_refusal(repo_root: Path, task_id: str) -> dict[str, Any] | None:
    """AIPOS-F123 × AIPOS-F122 接口点(AIPOS-F124 件①接通): 卡是否已冻结为历史(迁移基线)。None = 未冻结, 放行。
    判定唯一实现 = legacy_baseline.frozen_rejection(与 claim / 交回 / 派审 / 裁决 / finalize / loop 共用, 禁第二实现):
    冻结 = 其拒因 {code: LEGACY_FROZEN, message(含解冻命令), unfreeze_command}(调用方以 ADOPT_LEGACY_FROZEN 拒);
    清单读不出 = {code: LEGACY_BASELINE_INVALID, message}(fail-closed: 拒收编, 不当未冻结放行)。"""
    from tools.aipos_cli.legacy_baseline import LegacyBaselineError, frozen_rejection

    try:
        return frozen_rejection(repo_root, [task_id], action="adopt")
    except LegacyBaselineError as exc:
        return {"code": exc.code, "message": f"{exc}; 存量冻结清单读不出, 拒 adopt(出口: 修正清单条目 / project.json legacy_baseline)"}


def adoption_paths_refusal(repo_root: Path) -> str | None:
    """AIPOS-F124 件②: 收编按 project.json 落点写报告骨架 / 推导核按落点找产物——收编所依赖的落点键未在 project.json 显式声明
    (缺省值对人肉期项目未必对)= 拒并提示先 set-paths(fail-closed)。键表与拒因文案读声明 config.schema project_json.paths.adoption
    (required_declared_keys / guards); 「是否显式声明」读唯一读取口 workspace_config.project_paths()['declared']。None = 放行。"""
    from tools.aipos_cli.workspace_config import adoption_paths_declaration, project_paths

    decl = adoption_paths_declaration()
    declared = project_paths(repo_root).get("declared") or {}
    missing = [key for key in decl["required_declared_keys"] if not declared.get(key)]
    if not missing:
        return None
    return adoption_refusal(
        "ADOPT_PATHS_UNDECLARED",
        f"{repo_root}/project.json paths 未声明 {', '.join(missing)}",
        {"guards": decl["guards"]},
    )


def _gate_claim_records(repo_root: Path, task_id: str) -> tuple[list[str], list[str]]:
    """claims/<ID>/ 下的 claim_*.md 分两类: 门生(frontmatter 带 claim_id + claimed_at, 声明 N1.record.gate_written_markers)
    与非门生(手写/读不出, 只告警不算)。返回 (门生相对路径, 告警)。"""
    gate_born: list[str] = []
    warnings: list[str] = []
    claims_dir = record_dir(repo_root, "claims", task_id)
    if not claims_dir.is_dir():
        return gate_born, warnings
    root = repo_root.resolve()
    for path in sorted(claims_dir.glob("claim_*.md")):
        rel = str(path.resolve().relative_to(root))
        try:
            metadata, _body, parse_warnings = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"claim 文件读不出, 不计门生记录: {rel}({exc})")
            continue
        if parse_warnings or not isinstance(metadata, dict):
            warnings.append(f"claim 文件 frontmatter 读不出, 不计门生记录: {rel}")
            continue
        if str(metadata.get("claim_id") or "").strip() and str(metadata.get("claimed_at") or "").strip():
            gate_born.append(rel)
        else:
            warnings.append(f"手写 claim 文件(缺门生标记 claim_id/claimed_at), 不计门生记录: {rel}")
    return gate_born, warnings


def adopt_queue_task(
    repo_root: Path,
    *,
    task_id: str,
    branch: str,
    adopted_by: str,
    dry_run: bool = True,
    profiles: dict[str, Any] | None = None,
    claim_id_override: str | None = None,
    session_id_override: str | None = None,
) -> dict[str, Any]:
    """收编一张人肉期在途卡(claimed 目录、无门生 claim 记录、未冻结): 判据全过才给卡面运行时字段(transition_engine claim 同一盖字段,
    claimed_by = 卡面 agent_instance/assigned_to)并按 card_worktree_location 为既有分支建卡工作树(_ensure_worktree start_point)。

    拒因一次列全(声明 nodes.N1.adoption.guards)。dry_run=True 零写入; dry_run=False 先建树(失败 = BLOCK, 卡面零变更)再原位写卡面。
    claim / session 记录不在此写(board_adapter.adopt_task 复用 claim 记录 writer)。"""
    from tools.aipos_cli.next_resolver import (
        _ensure_worktree,
        card_branch_name,
        card_worktree_location,
        existing_branch_tip,
        forensic_subject,
    )
    from tools.aipos_cli.validator import REQUIRED_FIELDS
    from tools.aipos_cli.workspace_config import CardRepoUnresolved

    decl = adoption_declaration()
    if "adopt" not in ALLOWED_TRANSITIONS:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json queue_mutations.transitions.adopt 未声明")
    from_states, to_state = ALLOWED_TRANSITIONS["adopt"]
    for label, value in (("task_id", task_id), ("branch", branch), ("adopted_by", adopted_by)):
        if not str(value or "").strip():
            raise ValueError(f"adopt: {label} is required")
    branch = str(branch).strip()
    source_task = _select_task(repo_root, task_id=str(task_id).strip())
    source_path = (repo_root / str(source_task["path"])).resolve()
    metadata, body, _warnings = _read_task_markdown(source_path)
    result = _base_result(source_path, repo_root, source_task, "adopt", dry_run, adopted_by, to_state)
    result["target_path"] = result["source_path"]
    resolved_id = str(metadata.get("task_id") or task_id)
    blocking: list[str] = result["blocking_reasons"]

    queue_state = source_task.get("queue_state")
    status = source_task.get("frontmatter_status")
    if queue_state not in from_states or status not in from_states:
        blocking.append(adoption_refusal("ADOPT_NOT_CLAIMED", f"{resolved_id} 队列目录={queue_state}, 卡面 status={status}", decl))
    if forensic_subject(metadata) is not None:
        blocking.append(adoption_refusal("ADOPT_AUDIT_CARD", f"{resolved_id} task_mode=audit", decl))
    gate_claims, claim_warnings = _gate_claim_records(repo_root, resolved_id)
    result["warnings"].extend(claim_warnings)
    if gate_claims:
        blocking.append(adoption_refusal("ADOPT_CLAIM_EXISTS", ", ".join(gate_claims), decl))
    frozen = legacy_frozen_refusal(repo_root, resolved_id)
    if frozen is not None:
        from tools.aipos_cli.legacy_baseline import LEGACY_FROZEN

        if frozen["code"] == LEGACY_FROZEN:
            blocking.append(adoption_refusal("ADOPT_LEGACY_FROZEN", frozen["message"], decl))
            result["error_code"] = LEGACY_FROZEN
            result["unfreeze_command"] = frozen.get("unfreeze_command")
        else:
            blocking.append(frozen["message"])
            result["error_code"] = frozen["code"]
    paths_refusal = adoption_paths_refusal(repo_root)
    if paths_refusal:
        blocking.append(paths_refusal)
    missing = [field for field in REQUIRED_FIELDS if metadata.get(field) in (None, "")]
    if missing:
        blocking.append(adoption_refusal("ADOPT_CARD_INTENT_INCOMPLETE", f"缺 {len(missing)} 项: {', '.join(missing)}", decl))

    card_branch = card_branch_name(resolved_id)
    code_repo: Path | None = None
    worktree_path: Path | None = None
    tip: str | None = None
    try:
        code_repo, worktree_path = card_worktree_location(repo_root, resolved_id, metadata)
    except CardRepoUnresolved as exc:
        blocking.append(adoption_refusal("ADOPT_BRANCH_NOT_FOUND", f"卡 lane.repo 解析不到产品仓({exc})", decl))
    if code_repo is not None:
        tip = existing_branch_tip(code_repo, branch)
        if tip is None:
            blocking.append(adoption_refusal("ADOPT_BRANCH_NOT_FOUND", f"分支 {branch} 不在 {code_repo}", decl))
        elif card_branch != branch:
            card_tip = existing_branch_tip(code_repo, card_branch)
            if card_tip is not None and card_tip != tip:
                blocking.append(adoption_refusal(
                    "ADOPT_CARD_BRANCH_DIVERGED", f"{card_branch}@{card_tip} ≠ {branch}@{tip}({code_repo})", decl))

    claimer = str(metadata.get("agent_instance") or metadata.get("assigned_to") or "").strip()
    result["claimer"] = claimer
    result["legacy_card_runtime"] = {key: metadata.get(key) for key in ADOPTION_RUNTIME_FIELDS if metadata.get(key) not in (None, "")}
    result["adoption"] = {
        "adopted_from": str(decl["adopted_from"]),
        "adopted_by": str(adopted_by).strip(),
        "adopted_branch": branch,
        "adopted_branch_tip": tip or "",
        "card_branch": card_branch,
        "code_repo": str(code_repo) if code_repo else "",
    }
    result["worktree_path"] = str(worktree_path) if worktree_path else None
    if blocking:
        result["verdict"] = Verdict.BLOCK
        return result

    updated = _prepare_claim(metadata, claimer, iso_z(), claim_id_override=claim_id_override,
                             session_id_override=session_id_override)
    updated["active_worktree_branch"] = card_branch
    updated["active_worktree_path"] = str(worktree_path)
    preview_task = load_task_file(source_path, repo_root)
    preview_task["metadata"] = updated
    preview_task["frontmatter_status"] = updated.get("status")
    preview_task["queue_state"] = to_state
    for key in ("assigned_to", "agent_instance", "claimed_by", "needs_owner"):
        preview_task[key] = updated.get(key)
    validation = validate_single_task(preview_task, current_actor=claimer, profiles=profiles)
    for reason_text in validation["blocking_reasons"]:
        if reason_text not in blocking:
            blocking.append(reason_text)
    for warning_text in validation["warnings"]:
        if warning_text not in result["warnings"]:
            result["warnings"].append(warning_text)
    result["classification_warnings"].extend(validation.get("classification_warnings", []))
    needs_owner_reasons = list(validation.get("needs_owner_reasons", []))
    result["needs_owner_reasons"] = needs_owner_reasons
    rendered = render_task_markdown(updated, body)
    result["updated_frontmatter"] = updated
    result["planned_writes"] = [{"path": result["target_path"], "kind": "update", "type": "task_markdown"}]
    if blocking:
        result["verdict"] = Verdict.BLOCK
    elif needs_owner_reasons:
        result["verdict"] = Verdict.NEEDS_OWNER
    elif [w for w in result["warnings"] if w not in result["classification_warnings"]]:
        result["verdict"] = Verdict.WARN
    else:
        result["verdict"] = Verdict.PASS
    result["would_write"] = result["verdict"] != Verdict.BLOCK
    if dry_run:
        result["rendered_markdown"] = rendered
        return result
    if result["verdict"] == Verdict.BLOCK:
        return result

    # 建树先于任何写入(F90 件①同纪律): 失败 = BLOCK, 卡面与记录零变更
    built = _ensure_worktree(repo_root, resolved_id, card_frontmatter=dict(updated), start_point=tip)
    if not built.get("ok"):
        reason = str(built.get("message") or "worktree 建立失败(无拒因原文)")
        result["verdict"] = Verdict.BLOCK
        blocking.append(adoption_refusal("ADOPT_WORKTREE_FAILED", reason, decl))
        result["worktree_created"] = False
        result["worktree_error"] = reason
        result["would_write"] = False
        return result
    updated["active_worktree_path"] = str(built["worktree_path"])
    updated["active_worktree_branch"] = str(built["branch"])
    source_path.write_text(render_task_markdown(updated, body), encoding="utf-8")
    result["updated_frontmatter"] = updated
    result["wrote"] = True
    result["worktree_created"] = True
    result["worktree_path"] = str(built["worktree_path"])
    result["worktree_branch"] = str(built["branch"])
    return result


def build_rework_round(
    *,
    repo_root: Path,
    task_id: str,
    verdict_ref: str,
    focus_items: list[str],
    acceptance_criteria: str = "",
    actor: str,
) -> dict[str, Any]:
    """AIPOS-F75 件②: 构建返工轮次对象 (校验 + 构造, 不写磁盘)。
    
    磁盘操作由 amend_task 完成，本函数只负责校验与构造 round 对象。
    """
    import json
    
    # 1. 找到任务卡(AIPOS-F78B 件①: 唯一查找 find_task_card, 只在 claimed/)
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card

    try:
        task_path, queue_dir = find_task_card(repo_root, task_id, states=("claimed",))
    except AmbiguousTaskCard as exc:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": True,
            "blocking_reasons": [str(exc)],
            "warnings": [],
            "data": {},
        }
    
    if not task_path:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": True,
            "blocking_reasons": [
                "NOT_CLAIMED: 任务卡 {task_id} 不在 claimed 状态，只能对 claimed 卡追加返工节。".format(task_id=task_id)
            ],
            "warnings": [],
            "data": {},
        }
    
    # 2. 读取卡内容(AIPOS-F100 件②: 「必须读出」唯一入口; 读不出 = BLOCK——原「解析告警只入 warnings, rework_rounds 缺省 []」
    #    会把返工轮次计数归零、上限失效)
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

    try:
        raw_metadata, body = require_frontmatter(task_path)
    except FrontmatterReadError as e:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": True,
            "blocking_reasons": [f"读取任务卡失败: {e}"],
            "warnings": [],
            "data": {},
        }
    metadata = _normalize_value(raw_metadata)
    warnings: list[str] = []

    # 3. 检查轮次上限(rework_rounds 不是列表 = 卡面已坏, 拒; 禁当 [] 重新计数)
    rework_rounds = metadata.get("rework_rounds", [])
    if rework_rounds is None:
        rework_rounds = []
    if not isinstance(rework_rounds, list):
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": True,
            "blocking_reasons": [f"读取任务卡失败: {task_path}: rework_rounds 不是列表({type(rework_rounds).__name__}), 轮次无法计数"],
            "warnings": [],
            "data": {},
        }
    
    # 读取上限声明 (默认 2); AIPOS-F168: 抽为 rework_max_rounds(收件箱「返工轮次达上限」同读, 禁第二份)
    # AIPOS-F222 件③: 上限判据 = max_rounds + 已消费的 Owner 追加裁决数; 超出且有未消费裁决 = 放行一轮并记 round_field(唯一实现 rework_round_budget)
    try:
        budget = rework_round_budget(repo_root, task_id, rework_rounds, warnings)
    except (OSError, ValueError, LookupError) as exc:  # 裁决记录读不出 = 拒(不猜成无裁决 / 有裁决)
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": True,
            "blocking_reasons": [f"返工上限判定读不出 Owner 追加裁决: {type(exc).__name__}: {exc}"],
            "warnings": warnings,
            "data": {},
        }
    max_rounds = budget["max_rounds"]
    extension = budget["extension"]

    next_round = len(rework_rounds) + 1
    extension_ref: str | None = None
    auto_release: dict[str, Any] | None = None
    if next_round > budget["limit"]:
        auto = None if budget["unconsumed"] else auto_extension_check(repo_root, task_id, raw_metadata, body, next_round=next_round,
                                                                      max_rounds=max_rounds, verdict_ref=verdict_ref)
        if not budget["unconsumed"] and not (auto or {}).get("eligible"):
            texts = extension["texts"]
            consumed = len(budget["consumed"])
            part = str(texts["extension_part"]).format(consumed=consumed) if consumed else ""
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": True,
                "blocking_reasons": [
                    str(texts["refused"]).format(max_rounds=max_rounds, extension_part=part, rounds=len(rework_rounds)),
                    *([str(auto["why"])] if auto and auto.get("why") else []),
                ],
                "warnings": warnings,
                "data": {"current_rounds": len(rework_rounds), "max_rounds": max_rounds, "code": "REWORK_LIMIT",
                         "limit": budget["limit"], "consumed_extensions": list(budget["consumed"]),
                         "extension_choice": str(extension["choice"]), "auto_extension": auto},
            }
        if budget["unconsumed"]:
            extension_ref = str(budget["unconsumed"][0])
        else:  # AIPOS-F225 件②: 第二档自动追加(卡面候选 / 验收 / 范围自发布起未变, 轮次 ≤ 硬上限)
            auto_release = auto

    # 4. 构建新返工轮次(AIPOS-F232 件⑤: 登记时刻毫秒精度 clock.iso_ms——与同秒的下一次复核可区分; 读侧解析比较, 秒级存量照读)
    now_iso = iso_ms()
    new_round = {
        "round": next_round,
        "verdict_ref": verdict_ref,
        "created_at": now_iso,
        "created_by": actor,
        "focus_items": focus_items,
    }
    if acceptance_criteria:
        new_round["acceptance_criteria"] = acceptance_criteria
    if extension_ref:
        new_round[str(extension["round_field"])] = extension_ref
        warnings.append(str(extension["texts"]["granted"]).format(round=next_round, decision_id=extension_ref))
    if auto_release:
        new_round[str(auto_release["round_field"])] = str(auto_release["record_id"])
        warnings.append(str(auto_release["granted"]))
    
    # 5. 构造更新后的 rework_rounds
    updated_rework_rounds = rework_rounds + [new_round]
    
    return {
        "verdict": Verdict.PASS,
        "task_id": task_id,
        "actor": actor,
        "dry_run": True,
        "blocking_reasons": [],
        "warnings": warnings,
        "data": {
            "task_path": str(task_path.relative_to(repo_root)),
            "round_added": next_round,
            "total_rounds": len(updated_rework_rounds),
            "max_rounds": max_rounds,
            "new_round": new_round,
            "updated_rework_rounds": updated_rework_rounds,
            "owner_extension_ref": extension_ref,
            "auto_release": auto_release,
        },
    }


def rework_max_rounds(repo_root: Path, warnings: list[str] | None = None) -> int:
    """返工轮次上限(唯一读取; AIPOS-F168 由 build_rework_round 原地逻辑抽出, 行为不变): <repo_root>/schema/transitions.schema.json
    nodes.queue_rework.guards.rework_limit.max_rounds, 文件不在 = 缺省 2; 读坏 = 缺省 2 并把原因追加进 warnings(调用方出声)。
    门 queue_rework(build_rework_round)与 Owner 收件箱「返工轮次达上限」(owner_inbox)同读此函数。"""
    import json

    max_rounds = 2
    try:
        schema_path = Path(repo_root) / "schema" / "transitions.schema.json"
        if schema_path.exists():
            schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
            max_rounds = schema_data.get("nodes", {}).get("queue_rework", {}).get("guards", {}).get("rework_limit", {}).get("max_rounds", 2)
    except (json.JSONDecodeError, OSError) as e:
        # fail-closed: 解析失败使用默认值，但记录 warning
        if warnings is not None:
            warnings.append(f"Failed to read max_rounds from schema: {e}, using default 2")
    return int(max_rounds)


_EXTENSION_TEXTS = ("refused", "extension_part", "granted")


def rework_extension_declaration() -> dict[str, Any]:
    """AIPOS-F222 件③: transitions.schema nodes.queue_rework.guards.rework_limit.owner_extension(产品仓声明, 唯一读取口)。
    choice / round_field / texts 缺 = SchemaLoadError(fail-closed); choice 须为收件箱声明的选项(lybra_owner_inbox.options)。"""
    from tools.aipos_cli.owner_inbox import inbox_declaration
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    nodes = load_schema("transitions", code_repo_schema_root()).get("nodes") or {}
    decl = ((((nodes.get("queue_rework") or {}).get("guards") or {}).get("rework_limit") or {}).get("owner_extension"))
    texts = decl.get("texts") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or not str(decl.get("choice") or "").strip() or not str(decl.get("round_field") or "").strip()
            or not isinstance(texts, dict) or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _EXTENSION_TEXTS)):
        raise SchemaLoadError("transitions.schema.json nodes.queue_rework.guards.rework_limit.owner_extension(choice / round_field / "
                              f"texts.{list(_EXTENSION_TEXTS)})未声明齐(AIPOS-F222)")
    if str(decl["choice"]) not in (inbox_declaration().get("options") or {}):
        raise SchemaLoadError(f"rework_limit.owner_extension.choice {decl['choice']!r} 不是 verbs.lybra_owner_inbox.options 声明的选项")
    return decl


def rework_round_budget(repo_root: Path, task_id: str, rework_rounds: list[Any] | None,
                        warnings: list[str] | None = None) -> dict[str, Any]:
    """AIPOS-F222 件③: 本卡返工额度(唯一实现; build_rework_round 上限判据与 Owner 收件箱返工上限事项同读)。
    Owner 追加裁决 = 收件箱决策记录(owner_inbox.rulings_for_task: decision_type = 收件箱声明, task_id = 本卡)中 allowed_next_action =
    owner_extension.choice 者, 旧→新; 已被本卡任一返工轮 round_field 引用 = 已消费。limit = max_rounds + 已消费数(len(rounds) ≥ limit 且
    无未消费裁决 = 额度已尽)。返回 {max_rounds, limit, consumed[决策 ID], unconsumed[决策 ID], extension(声明)}。
    裁决记录读不出 = 向上(fail-closed)。"""
    from tools.aipos_cli.owner_inbox import rulings_for_task

    extension = rework_extension_declaration()
    max_rounds = rework_max_rounds(repo_root, warnings)
    field = str(extension["round_field"])
    rounds = [r for r in (rework_rounds or []) if isinstance(r, dict)]
    referenced = {str(r.get(field) or "").strip() for r in rounds} - {""}
    granted = [str(r.get("decision_id") or "").strip() for r in rulings_for_task(Path(repo_root), task_id)
               if str(r.get("allowed_next_action") or "") == str(extension["choice"])]
    granted = [d for d in granted if d]
    consumed = [d for d in granted if d in referenced]
    # AIPOS-F225 件②: 已登记的第二档自动追加轮(round_field = auto_extension.round_field)同样计入额度(轮次已发生, 撤回不删历史)
    auto_field = str(auto_extension_declaration()["round_field"])
    auto_used = [str(r.get(auto_field)) for r in rounds if str(r.get(auto_field) or "").strip()]
    return {"max_rounds": max_rounds, "limit": max_rounds + len(consumed) + len(auto_used), "consumed": consumed,
            "unconsumed": [d for d in granted if d not in referenced], "extension": extension, "auto_extensions": auto_used}


_AUTO_EXTENSION_KEYS = ("hard_cap", "round_field", "kind", "fingerprint_fields", "fingerprint_body", "texts")
_AUTO_EXTENSION_TEXTS = ("granted", "basis", "refused_cap", "refused_changed", "refused_baseline")


def auto_extension_declaration() -> dict[str, Any]:
    """AIPOS-F225 件②: transitions.schema nodes.queue_rework.guards.rework_limit.auto_extension(产品仓声明, 唯一读取口)。
    键 / 文案缺、hard_cap 非 ≥1 整数、kind 不在 verbs.schema lybra_owner_inbox.tiers.tier2.kinds = SchemaLoadError(fail-closed)。"""
    from tools.aipos_cli.owner_inbox import tier_declaration
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    nodes = load_schema("transitions", code_repo_schema_root()).get("nodes") or {}
    decl = ((((nodes.get("queue_rework") or {}).get("guards") or {}).get("rework_limit") or {}).get("auto_extension"))
    texts = decl.get("texts") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or any(k not in decl for k in _AUTO_EXTENSION_KEYS) or not isinstance(texts, dict)
            or any(not str(texts.get(k) or "").strip() for k in _AUTO_EXTENSION_TEXTS)
            or not isinstance(decl["hard_cap"], int) or isinstance(decl["hard_cap"], bool) or decl["hard_cap"] < 1
            or not isinstance(decl["fingerprint_fields"], list) or not decl["fingerprint_fields"]):
        raise SchemaLoadError("transitions.schema.json nodes.queue_rework.guards.rework_limit.auto_extension 未声明齐 "
                              f"{list(_AUTO_EXTENSION_KEYS)} / texts.{list(_AUTO_EXTENSION_TEXTS)}(AIPOS-F225)")
    if str(decl["kind"]) not in tier_declaration()["tier2"]["kinds"]:
        raise SchemaLoadError(f"rework_limit.auto_extension.kind {decl['kind']!r} 不在 verbs.schema lybra_owner_inbox.tiers.tier2.kinds")
    return decl


def card_scope_fingerprint(metadata: dict[str, Any], body: str, decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F225 件②: 卡面「候选 / 验收 / 范围」指纹(唯一实现; 自动追加门核对与发布基线同算): 声明 fingerprint_fields(点号 = 嵌套)+ 正文
    (fingerprint_body; 首尾空白不计)规范 JSON 的 sha256。返回 {digest, values}。"""
    import hashlib
    import json

    decl = decl or auto_extension_declaration()
    values: dict[str, Any] = {}
    for field in decl["fingerprint_fields"]:
        node: Any = metadata
        for part in str(field).split("."):
            node = node.get(part) if isinstance(node, dict) else None
        values[str(field)] = _normalize_value(node)
    if decl["fingerprint_body"]:
        values["__body__"] = str(body or "").strip()
    digest = hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {"digest": digest, "values": values}


def publish_scope_baseline(repo_root: Path, task_id: str, decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F225 件②: 本卡发布基线指纹 —— 门侧发布记录(records publishes, 最新一份)的 source_draft_ref 草稿, 且其 sha256 = 发布记录
    source_sha256(发布后未改, 门可核)才据其算 card_scope_fingerprint。返回 {ok, digest, values, publish_ref, why}; 不可核 = ok False + why。"""
    import hashlib

    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.records import load_records

    root = Path(repo_root)
    records = sorted(dict(load_records(root, groups=["publishes"], task_id=task_id).get("task_publishes") or {}).get(task_id) or [],
                     key=lambda r: str(r.get("published_at") or ""))
    if not records:
        return {"ok": False, "why": f"无门侧发布记录(records publishes/{task_id})"}
    record = records[-1]
    meta0 = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    ref = str(record.get("source_draft_ref") or meta0.get("source_draft_ref") or "").strip()
    expected = str(meta0.get("source_sha256") or "").strip()
    draft = root / ref if ref else None
    if draft is None or not draft.is_file() or not expected:
        return {"ok": False, "why": f"发布记录 {record.get('path') or record.get('publish_id')} 的草稿 {ref or '(未记)'} 不在或未记 source_sha256"}
    actual = hashlib.sha256(draft.read_bytes()).hexdigest()
    if actual != expected:
        return {"ok": False, "why": f"草稿 {ref} 发布后已改(sha256 {actual[:12]} ≠ 发布记录 {expected[:12]})"}
    try:
        meta, body = require_frontmatter(draft)
    except FrontmatterReadError as exc:
        return {"ok": False, "why": f"草稿 {ref} 读不出: {exc}"}
    fp = card_scope_fingerprint(meta, body, decl)
    return {"ok": True, "digest": fp["digest"], "values": fp["values"], "publish_ref": str(record.get("publish_id") or ref), "why": ""}


def auto_extension_check(repo_root: Path, task_id: str, metadata: dict[str, Any], body: str, *, next_round: int,
                         max_rounds: int, verdict_ref: str = "") -> dict[str, Any]:
    """AIPOS-F225 件② 唯一判定(门 queue rework / mark-concluded --outcome rework 同经 build_rework_round): 下一轮超出上限判据时能否第二档
    自动追加——next_round ≤ hard_cap 且卡面指纹 = 发布基线指纹。返回 {eligible, why, record_id, round_field, granted, basis, fingerprint}。"""
    from tools.aipos_cli.owner_inbox import planned_auto_release_id

    decl = auto_extension_declaration()
    texts = decl["texts"]
    cap = int(decl["hard_cap"])
    out: dict[str, Any] = {"eligible": False, "why": "", "hard_cap": cap, "round_field": str(decl["round_field"]), "kind": str(decl["kind"])}
    if next_round > cap:
        out["why"] = str(texts["refused_cap"]).format(hard_cap=cap)
        return out
    baseline = publish_scope_baseline(repo_root, task_id, decl)
    if not baseline["ok"]:
        out["why"] = str(texts["refused_baseline"]).format(why=baseline["why"])
        return out
    current = card_scope_fingerprint(metadata, body, decl)
    if current["digest"] != baseline["digest"]:
        changed = sorted(k for k in set(current["values"]) | set(baseline["values"]) if current["values"].get(k) != baseline["values"].get(k))
        out["why"] = str(texts["refused_changed"]).format(current=current["digest"][:12], baseline=baseline["digest"][:12], fields=", ".join(changed))
        return out
    record_id = planned_auto_release_id(Path(repo_root), task_id, str(decl["kind"]))
    out.update({"eligible": True, "record_id": record_id, "fingerprint": current["digest"], "publish_ref": baseline["publish_ref"],
                "granted": str(texts["granted"]).format(round=next_round, hard_cap=cap, record_id=record_id),
                "basis": str(texts["basis"]).format(round=next_round, max_rounds=max_rounds, hard_cap=cap, fingerprint=current["digest"][:12],
                                                    publish_ref=baseline["publish_ref"], verdict_ref=verdict_ref or "?")})
    return out


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
from tools.schema_constants import RecordType, Verdict
check_direct_invocation(__name__)
