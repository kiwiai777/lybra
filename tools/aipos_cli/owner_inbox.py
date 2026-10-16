"""AIPOS-F168 — Owner 待决收件箱(件①)/ 一处裁决(件②)/ 顾问侧读到裁决(件②)。

一句话: 所有已注册项目里需要 Owner 拍板的事集中到一个收件箱, 写清要定什么、顾问建议、影响与可选裁决; Owner 一处
`lybra owner decide` 裁决, 裁决写成既有 owner_decision_record; 对应顾问下一次 `lybra loop status` / `lybra next` 直接读到裁决接着推进。

防碎片化(卡面硬约束):
- 待决判定只读既有真相, 不另记待办: loop 运行记录下一动作(loop_run_record.loop_status 唯一判定)、卡面 needs_owner(aipos_cli._filter_needs_owner
  经 board_adapter.get_needs_owner)、信封预警(autonomy_policy.envelope_warnings)、返工轮次上限(queue_mutation.rework_max_rounds +
  next_resolver.rework_round_for_verdict)、补审 FAIL(audit_derivation.supplementary_audit_overview)。
- 裁决 = 既有 owner_decision_record(门侧同一预演 / 执行 board_adapter.record_owner_decision + execute_dry_run), 不另立存储; 解除 needs_owner 走既有
  队列出口(board_adapter.amend_task / withdraw_task), 不手改卡; 写出文件经 governance_commit.land_written_paths 同一提交口落账。
- 展示: CLI(render_inbox_text)与看板(board_adapter.get_owner_inbox → web/board /owner/inbox 页 + /api/owner/inbox)同读 collect_inbox。
- 文案、类别、选项、排序、ID 与决策记录常量全部读 verbs.schema lybra_owner_inbox(inbox_declaration 唯一读取口), 禁写死。

AIPOS-F170(收件箱清理): 件① 遗留视图 render_stale_text(同一 collect_inbox 数据, --stale-before)+ decide 批量(--items, 同一 decide 实现)+
blocked 卡经裁决撤回(既有 board_adapter.withdraw_task, 前置放宽 = 带 Owner 裁决引用)/ 冻结为历史(既有 legacy_freeze.plan_freeze_legacy +
legacy_baseline.write_freeze_entry, owner_policy_ref = 决策记录 ID); 件② 暂停项目过滤只在 collect_inbox(读 workspace_config.project_status);
件③ 转达归因写进既有记录字段(captured_by = --relayed-by, decision_rationale = Owner 原话), 顾问侧裁决行(ruling_view)显示二者。
"""
from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

VERB = "lybra_owner_inbox"
DECIDE_VERB = "lybra_owner_decide"
NOTE_VERB = "lybra_loop_note"
CATEGORIES = ("loop_owner_needed", "needs_owner_card", "rework_limit", "supplementary_fail", "envelope_warning", "roadmap_drift")
_DECL_KEYS = ("item_id", "decision", "texts", "rank_labels", "categories", "options", "custom_option", "exit_codes", "stale")
_DECISION_KEYS = ("decision_id_prefix", "decision_type", "decision_status", "capture_surface", "source_tag", "capture_method",
                  "approval_intent", "redaction_status", "token_ref", "capability_ttl_hours", "external_ref_prefix",
                  "summary_template", "authority_boundary", "owner_words_prefix")
_EFFECTS = ("release_needs_owner", "withdraw_card", "freeze_legacy")


class InboxItemNotFound(LookupError):
    """收件箱中无该事项(已裁决 / 已自行解除 / ID 错)。"""


class InboxUsageError(ValueError):
    """decide 用法错(缺转达人 / 原话、--item 与 --items 二选一不合、--stale-before 日期不合): 零写入, 退出码 usage。"""


class InboxChoiceInvalid(InboxUsageError):
    """选项不属该事项。"""


# ---------------------------------------------------------------------------
# 声明(唯一读取口)
# ---------------------------------------------------------------------------

def inbox_declaration() -> dict[str, Any]:
    """verbs.schema lybra_owner_inbox(fail-closed): 键齐; 类别恰为 CATEGORIES; 各类别引用的选项都在 options 且有 label / advisor_next。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(VERB)
    if not isinstance(decl, dict) or any(k not in decl for k in _DECL_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB} 未声明齐 {list(_DECL_KEYS)}")
    if set(decl["categories"]) != set(CATEGORIES):
        raise SchemaLoadError(f"verbs.{VERB}.categories 须恰为 {list(CATEGORIES)}, 得到 {sorted(decl['categories'])}")
    if any(k not in decl["decision"] for k in _DECISION_KEYS):
        raise SchemaLoadError(f"verbs.{VERB}.decision 未声明齐 {list(_DECISION_KEYS)}")
    options = decl["options"]
    for key, spec in options.items():
        if not isinstance(spec, dict) or not str(spec.get("label") or "").strip() or not str(spec.get("advisor_next") or "").strip():
            raise SchemaLoadError(f"verbs.{VERB}.options.{key} 须有非空 label 与 advisor_next")
    referenced: set[str] = set()
    for name, cat in decl["categories"].items():
        for field in ("default_options", "options"):
            referenced |= set(cat.get(field) or [])
        for table in ("options_by_reason", "options_by_queue_state"):
            for keys in (cat.get(table) or {}).values():
                referenced |= set(keys)
    stale = decl["stale"]
    for cat_name, rule in (stale.get("suggest") or {}).items():
        if cat_name not in decl["categories"]:
            raise SchemaLoadError(f"verbs.{VERB}.stale.suggest.{cat_name} 不是声明的类别")
        referenced |= set(rule.values()) if isinstance(rule, dict) else {str(rule)}
    bad_effects = sorted({str(v["effect"]) for v in options.values() if v.get("effect")} - set(_EFFECTS))
    if bad_effects:
        raise SchemaLoadError(f"verbs.{VERB}.options.*.effect 未知: {bad_effects}(已实现 {list(_EFFECTS)})")
    missing = sorted(referenced - set(options))
    if missing:
        raise SchemaLoadError(f"verbs.{VERB}.categories / stale.suggest 引用了未声明的选项 {missing}")
    return decl


def _fmt(template: Any, where: str, **values: Any) -> str:
    """声明模板渲染; 占位缺值 = SchemaLoadError(声明与实现不符, 不猜)。"""
    from tools.schema_loader import SchemaLoadError

    try:
        return str(template).format_map(values)
    except (KeyError, IndexError, ValueError) as exc:
        raise SchemaLoadError(f"verbs.{VERB}.{where} 模板占位无值: {exc}") from exc


def item_id(project: str, category: str, subject: str, anchor: str, decl: dict[str, Any] | None = None) -> str:
    """事项 ID 唯一实现(收件箱 / 裁决 / 顾问侧回查同读)。"""
    spec = (decl or inbox_declaration())["item_id"]
    digest = hashlib.sha1("|".join([project, category, subject, anchor]).encode("utf-8")).hexdigest()
    return f"{spec['prefix']}{digest[:int(spec['hash_chars'])]}"


def decision_id_for(inbox_item_id: str, decl: dict[str, Any] | None = None) -> str:
    d = decl or inbox_declaration()
    prefix = str(d["item_id"]["prefix"])
    tail = inbox_item_id[len(prefix):] if inbox_item_id.startswith(prefix) else inbox_item_id
    return f"{d['decision']['decision_id_prefix']}{tail.lower()}"


def _decision_path(governance_root: Path, decision_id: str) -> Path:
    from tools.aipos_cli.record_writer import record_root

    return Path(governance_root) / record_root("owner_decisions") / f"{decision_id}.md"


def read_ruling(governance_root: Path, inbox_item_id: str, decl: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """该事项的裁决(决策记录 frontmatter)或 None。记录在但读不出 = FrontmatterReadError 向上(fail-closed)。"""
    from tools.aipos_cli.frontmatter import require_frontmatter

    path = _decision_path(Path(governance_root), decision_id_for(inbox_item_id, decl))
    if not path.is_file():
        return None
    return _ruling_record(path, *require_frontmatter(path))


def _ruling_record(path: Path, fm: dict[str, Any], body: str) -> dict[str, Any]:
    """决策记录 frontmatter + 正文一句话结论(owner_decision_writer.SUMMARY_LINE_PREFIX 行; decision_log_pointers 同一读回口径)。
    AIPOS-F170 件③: Rationale 行(RATIONALE_LINE_PREFIX)以声明 owner_words_prefix 起头 = Owner 原话(转达人 = captured_by);
    F168 旧记录无该前缀 = owner_words / relayed_by 为 None(不显示转达段)。"""
    from tools.aipos_cli.owner_decision_writer import RATIONALE_LINE_PREFIX, SUMMARY_LINE_PREFIX

    lines = body.splitlines()
    summary = next((ln[len(SUMMARY_LINE_PREFIX):].strip() for ln in lines if ln.startswith(SUMMARY_LINE_PREFIX)), "")
    rationale = next((ln[len(RATIONALE_LINE_PREFIX):].strip() for ln in lines if ln.startswith(RATIONALE_LINE_PREFIX)), "")
    prefix = str(inbox_declaration()["decision"]["owner_words_prefix"])
    words = rationale[len(prefix):].strip() if prefix and rationale.startswith(prefix) else ""
    return {**fm, "decision_summary": fm.get("decision_summary") or summary, "_path": str(path),
            "owner_words": words or None, "relayed_by": (str(fm.get("captured_by") or "") or None) if words else None}


# ---------------------------------------------------------------------------
# 件① 收集(只读既有真相)
# ---------------------------------------------------------------------------

def _option_list(keys: list[str], decl: dict[str, Any]) -> list[dict[str, str]]:
    return [{"key": k, "label": str(decl["options"][k]["label"])} for k in keys]


def _base_item(decl: dict[str, Any], *, project: str, governance_root: Path, category: str, rank: int, subject: str,
               anchor: str, source: str, ask: str, recommend: str | None, impact: str, options: list[dict[str, str]],
               since: str | None, task_id: str | None = None, policy_id: str | None = None, facts: dict[str, Any] | None = None,
               home_root: Path | None = None, title: str | None = None) -> dict[str, Any]:
    cat = decl["categories"][category]
    iid = item_id(project, category, subject, anchor, decl)
    command = _fmt(decl["texts"]["decide_command"], "texts.decide_command", item_id=iid,
                   home_root=str(home_root) if home_root else "<home 根>")
    return {"item_id": iid, "project": project, "governance_root": str(governance_root), "category": category,
            "category_label": str(cat["label"]), "rank": int(rank), "rank_label": str(decl["rank_labels"].get(str(rank), rank)),
            "subject": subject, "task_id": task_id, "policy_id": policy_id, "title": str(title or "").strip() or None,
            "source": source or str(decl["texts"]["unknown_source"]), "ask": ask, "recommend": recommend or None, "impact": impact,
            "options": options, "since": since, "anchor": anchor, "decision_id": decision_id_for(iid, decl),
            "decide_command": command, "facts": dict(facts or {})}


def _loop_items(root: Path, project: str, decl: dict[str, Any], home_root: Path | None, errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """① 每卡最近一次 loop 运行的顾问下一动作 = owner_needed(唯一判定 loop_run_record.loop_status)。卡已结案 / 撤回 = 已自行解除, 不列;
    冻结卡 = 四视图同一口径不列。运行记录 owner_ask(lybra loop note 补写)优先于声明缺省文案。"""
    from tools.aipos_cli.loop_run_record import loop_runs_root, loop_status
    from tools.aipos_cli.task_loader import find_task_card

    cat = decl["categories"]["loop_owner_needed"]
    runs_root = loop_runs_root(root)
    out: list[dict[str, Any]] = []
    if not runs_root.is_dir():
        return out
    for directory in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        task_id = directory.name
        try:
            card_path, state = find_task_card(root, task_id)
            if state in ("completed", "withdrawn"):  # 卡已结案 / 撤回 = 已自行解除: 先判, 免为它算下一动作(结案取下一张会扫全项目)
                continue
            report = loop_status(root, task_id)
        except (OSError, ValueError, LookupError, RuntimeError) as exc:  # 读不出 = 点名进 errors(不猜不吞)
            errors.append({"project": project, "source": f"loop 运行记录 {task_id}", "error": f"{type(exc).__name__}: {exc}"})
            continue
        runs = report.get("runs") or []
        if not runs:
            continue
        view = runs[-1]
        na = view.get("next_action") or {}
        if na.get("action") != "owner_needed" or view.get("frozen"):
            continue
        reason = str(na.get("reason") or "")
        ask_rec = view.get("owner_ask") if isinstance(view.get("owner_ask"), dict) else {}
        if ask_rec.get("options"):
            options = [{"key": str(o.get("key")), "label": str(o.get("label"))} for o in ask_rec["options"] if isinstance(o, dict)]
        else:
            options = _option_list(list((cat.get("options_by_reason") or {}).get(reason) or cat["default_options"]), decl)
        recommend = ask_rec.get("recommend") or None
        if not recommend and reason in (cat.get("recommend_by_reason") or {}):
            recommend = _fmt(cat["recommend_by_reason"][reason], f"categories.loop_owner_needed.recommend_by_reason.{reason}",
                             hint=str(na.get("hint") or ""), task_id=task_id)
        subject = _fmt(cat["subject"], "categories.loop_owner_needed.subject", task_id=task_id)
        title = _card_title(card_path, errors, project)
        out.append(_base_item(
            decl, project=project, governance_root=root, category="loop_owner_needed", rank=int(cat["rank"]), subject=subject,
            anchor=str(view.get("run_id") or ""), source=str(view.get("driver") or ""),
            ask=str(ask_rec.get("ask") or na.get("detail") or reason), recommend=recommend,
            impact=_fmt(cat["impact"], "categories.loop_owner_needed.impact", task_id=task_id, reason=reason),
            options=options, since=view.get("ended_at") or view.get("started_at"), task_id=task_id, home_root=home_root, title=title,
            facts={"run_id": view.get("run_id"), "reason": reason, "driver": view.get("driver"), "envelope": view.get("envelope"),
                   "hint": na.get("hint"), "record_path": view.get("record_path"), "owner_ask_by": ask_rec.get("noted_by")}))
    return out


def _card_title(path: Path | None, errors: list[dict[str, str]], project: str) -> str | None:
    """卡面 title(遗留视图每项一行用); 卡面读不出 = 点名进 errors, 标题缺省(不猜)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

    if path is None:
        return None
    try:
        fm, _body = require_frontmatter(Path(path))
    except FrontmatterReadError as exc:
        errors.append({"project": project, "source": f"卡面 {Path(path).name}", "error": str(exc)})
        return None
    return str(fm.get("title") or "").strip() or None


def _needs_owner_items(root: Path, project: str, decl: dict[str, Any], home_root: Path | None,
                       errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """② 卡面 needs_owner: 与看板「需 Owner 决定」/ CLI needs-owner 同一筛选(board_adapter.get_needs_owner), 只取声明的可裁决队列状态。"""
    from tools.aipos_cli.board_adapter import get_needs_owner

    cat = decl["categories"]["needs_owner_card"]
    resp = get_needs_owner(repo_root=root)
    if not resp.get("ok"):
        errors.append({"project": project, "source": "needs_owner 卡", "error": json.dumps(resp.get("errors") or resp.get("message"),
                                                                                         ensure_ascii=False)[:400]})
        return []
    out: list[dict[str, Any]] = []
    for task in (resp.get("data") or {}).get("tasks") or []:
        state = str(task.get("queue_state") or "")
        if state not in cat["queue_states"]:
            continue
        meta = task.get("metadata") or {}
        task_id = str(task.get("task_id") or meta.get("task_id") or "")
        reasons = [str(r) for r in (task.get("needs_owner_reasons") or [])]
        flags = [f for f in ("needs_owner", "owner_review_required", "approval_required") if meta.get(f) is True]
        anchor = json.dumps([state, sorted(reasons), flags, str(meta.get("blocked_at") or ""), str(meta.get("claim_id") or "")],
                            ensure_ascii=False)
        subject = _fmt(cat["subject"], "categories.needs_owner_card.subject", task_id=task_id, queue_state=state)
        ask = _fmt(cat["ask"], "categories.needs_owner_card.ask",
                   reasons="; ".join(reasons + [r for r in [str(meta.get("block_reason") or meta.get("blocked_reason") or "")] if r])
                   or ", ".join(flags) or str(task.get("verdict") or ""))
        out.append(_base_item(
            decl, project=project, governance_root=root, category="needs_owner_card", rank=int(cat["rank_by_queue_state"][state]),
            subject=subject, anchor=anchor,
            source=str(meta.get("blocked_by") or meta.get("created_by") or meta.get("draft_created_by") or ""),
            ask=ask, recommend=None, impact=str(cat["impact_by_queue_state"][state]),
            options=_option_list(list(cat["options_by_queue_state"][state]), decl),
            since=str(meta.get("blocked_at") or meta.get("claimed_at") or meta.get("created_at") or meta.get("draft_created_at") or "") or None,
            task_id=task_id, home_root=home_root, title=meta.get("title"),
            facts={"queue_state": state, "flags": flags, "path": task.get("path")}))
    return out


def _rework_items(root: Path, project: str, decl: dict[str, Any], home_root: Path | None,
                  errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """④ 在途卡返工轮次达上限(门 queue_rework 同一上限读取 rework_max_rounds)且最新常规裁决未过、无针对它的返工节。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.next_resolver import _PASS_VERDICTS, _read_frontmatter, _read_task_records, rework_round_for_verdict
    from tools.aipos_cli.queue_mutation import rework_max_rounds
    from tools.aipos_cli.task_loader import iter_queue_task_paths

    cat = decl["categories"]["rework_limit"]
    warnings: list[str] = []
    max_rounds = rework_max_rounds(root, warnings)
    for text in warnings:
        errors.append({"project": project, "source": "返工轮次上限声明", "error": text})
    out: list[dict[str, Any]] = []
    for path in iter_queue_task_paths(root, states=("claimed",)):
        try:
            fm = _read_frontmatter(path)
        except FrontmatterReadError as exc:
            errors.append({"project": project, "source": f"卡面 {path.name}", "error": str(exc)})
            continue
        rounds = fm.get("rework_rounds")
        if not isinstance(rounds, list) or len(rounds) < max_rounds:
            continue
        task_id = str(fm.get("task_id") or "")
        try:
            verdict = _read_task_records(root, task_id).get("latest_verdict") or {}
        except (OSError, ValueError, FrontmatterReadError) as exc:
            errors.append({"project": project, "source": f"裁决记录 {task_id}", "error": f"{type(exc).__name__}: {exc}"})
            continue
        value = str(verdict.get("verdict") or "").upper()
        if not value or value in _PASS_VERDICTS or rework_round_for_verdict(fm, verdict) is not None:
            continue
        subject = _fmt(cat["subject"], "categories.rework_limit.subject", task_id=task_id)
        vid = str(verdict.get("verdict_id") or "")
        out.append(_base_item(
            decl, project=project, governance_root=root, category="rework_limit", rank=int(cat["rank"]), subject=subject,
            anchor=f"{len(rounds)}|{vid}", source=str(fm.get("created_by") or fm.get("draft_created_by") or ""),
            ask=_fmt(cat["ask"], "categories.rework_limit.ask", rounds=len(rounds), max_rounds=max_rounds, verdict=value, verdict_id=vid),
            recommend=None, impact=str(cat["impact"]), options=_option_list(list(cat["options"]), decl),
            since=str(verdict.get("verdict_at") or "") or None, task_id=task_id, home_root=home_root, title=fm.get("title"),
            facts={"rounds": len(rounds), "max_rounds": max_rounds, "verdict_id": vid}))
    return out


def _supplementary_items(root: Path, project: str, decl: dict[str, Any], home_root: Path | None,
                         errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """⑤ 已结案卡补审裁决 ∈ fail_verdicts 且未派修复卡(audit_derivation.supplementary_audit_overview 同一链路)。"""
    from tools.aipos_cli.audit_derivation import supplementary_audit_declaration, supplementary_audit_overview

    cat = decl["categories"]["supplementary_fail"]
    unreadable: list[str] = []
    overview = supplementary_audit_overview(root, unreadable)
    for text in unreadable:
        errors.append({"project": project, "source": "补充审计链路", "error": text})
    fails = set(supplementary_audit_declaration()["fail_verdicts"])
    out: list[dict[str, Any]] = []
    for row in overview:
        for link in row["chain"]:
            if link.get("verdict") not in fails or link.get("repair_task_id"):
                continue
            task_id = str(row["task_id"])
            subject = _fmt(cat["subject"], "categories.supplementary_fail.subject", task_id=task_id)
            out.append(_base_item(
                decl, project=project, governance_root=root, category="supplementary_fail", rank=int(cat["rank"]), subject=subject,
                anchor=str(link.get("verdict_id") or link.get("audit_task_id")), source="",
                ask=_fmt(cat["ask"], "categories.supplementary_fail.ask", audit_task_id=link["audit_task_id"], verdict=link["verdict"],
                         verdict_id=link.get("verdict_id") or "?"),
                recommend=None, impact=str(cat["impact"]), options=_option_list(list(cat["options"]), decl), since=None,
                task_id=task_id, home_root=home_root, facts={"audit_task_id": link["audit_task_id"], "verdict_id": link.get("verdict_id")}))
    return out


def _envelope_items(root: Path, project: str, decl: dict[str, Any], home_root: Path | None,
                    errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """③ 仍在用的信封达预警阈值(信封状态单一推导 autonomy_policy.envelope_overview: status=warn, brief / 看板信封页 / loop status 同读;
    AIPOS-F172(gap #160): 已被同驱动方更新信封接替的旧信封不列)。「裁决」指向看板信封页(Owner 亲自续签; 声明 categories.envelope_warning.decide_board,
    缺省 = 原 CLI 裁决行), CLI 命令保留。"""
    from tools.aipos_cli.autonomy_policy import envelope_overview
    from tools.aipos_cli.envelope_board import board_envelope_url

    cat = decl["categories"]["envelope_warning"]
    out: list[dict[str, Any]] = []
    for row in envelope_overview(root)["envelopes"]:
        if row["status"] != "warn":
            continue
        pid, who = str(row["policy_id"]), str(row["agent_or_role"] or "")
        subject = _fmt(cat["subject"], "categories.envelope_warning.subject", policy_id=pid)
        item = _base_item(
            decl, project=project, governance_root=root, category="envelope_warning", rank=int(cat["rank"]), subject=subject,
            anchor=f"{pid}|{row['state']}", source=who,
            ask=_fmt(cat["ask"], "categories.envelope_warning.ask", why=row["why"]),
            recommend=_fmt(cat["recommend"], "categories.envelope_warning.recommend", renew_command=row["renew_command"]),
            impact=_fmt(cat["impact"], "categories.envelope_warning.impact", agent_or_role=who or "?"),
            options=_option_list(list(cat["options"]), decl), since=None, policy_id=pid, home_root=home_root,
            facts={"used": row["used"], "max_tasks": row["max_tasks"], "expires_at": row["expires_at"],
                   "renew_command": row["renew_command"], "next_policy_id": row["next_policy_id"]})
        if cat.get("decide_board"):
            item["board_url"] = board_envelope_url(project, pid)
            item["decide_line"] = _fmt(cat["decide_board"], "categories.envelope_warning.decide_board", board_url=item["board_url"],
                                       command=item["decide_command"])
        out.append(item)
    return out


def _roadmap_items(root: Path, project: str, decl: dict[str, Any], home_root: Path | None,
                   errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """⑥ AIPOS-F173: 路线图严重偏差 = 推导唯一实现 roadmap_drift.roadmap_status 中 severity=inbox 的信号(声明 verbs.lybra_roadmap.status
    .signals.*.severity), 每条一个事项; 锚点 = 信号 anchor(真相不变 = 同一事项 ID, 重算不重复)。项目无路线图 = 无事项。
    推导内某卡记录读不出 = 点名进 errors(不猜)。路线图 / 阈值读不出 = 异常向上(collect_project_inbox 点名该类)。"""
    from tools.aipos_cli.roadmap_drift import roadmap_status
    from tools.schema_loader import SchemaLoadError

    cat = decl["categories"]["roadmap_drift"]
    view = roadmap_status(root, project=project)
    for err in view["errors"]:
        errors.append({"project": project, "source": f"路线图偏差: {err['source']}", "error": err["error"]})
    out: list[dict[str, Any]] = []
    for sig in view["signals"]:
        if sig["severity"] != "inbox":
            continue
        if sig["kind"] not in (cat.get("rank_by_kind") or {}) or sig["kind"] not in (cat.get("subject_by_kind") or {}):
            raise SchemaLoadError(f"verbs.{VERB}.categories.roadmap_drift.rank_by_kind / subject_by_kind 缺严重偏差种类 {sig['kind']!r}")
        facts = sig.get("facts") or {}
        fill = {"milestone": sig.get("milestone") or "", "criterion": sig.get("criterion") or "", "lane": sig.get("lane") or "",
                "kind_label": sig["label"], "reason": sig["reason"], "met": facts.get("met", "?"), "total": facts.get("total", "?")}
        out.append(_base_item(
            decl, project=project, governance_root=root, category="roadmap_drift", rank=int(cat["rank_by_kind"][sig["kind"]]),
            subject=sig["subject"], anchor=str(sig["anchor"]), source=str(sig.get("advisor") or ""),
            ask=_fmt(cat["ask"], "categories.roadmap_drift.ask", **fill), recommend=sig["advice"],
            impact=_fmt(cat["impact"], "categories.roadmap_drift.impact", **fill),
            options=_option_list(list(cat["options"]), decl), since=facts.get({"stalled": "last_at", "overdue": "target_date"}.get(sig["kind"], "")),
            home_root=home_root, facts={"kind": sig["kind"], "milestone": sig.get("milestone"), "criterion": sig.get("criterion"),
                                        "lane": sig.get("lane"), **facts}))
    return out


_COLLECTORS = (("loop_owner_needed", _loop_items), ("needs_owner_card", _needs_owner_items), ("rework_limit", _rework_items),
               ("supplementary_fail", _supplementary_items), ("envelope_warning", _envelope_items), ("roadmap_drift", _roadmap_items))


def collect_project_inbox(governance_root: Path, *, project: str | None = None, home_root: Path | None = None,
                          decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """一个项目治理根的待决事项(未裁决者)+ 读不出处。项目名缺省 = 治理根目录名(home 根下的注册名)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.task_loader import task_card_lookup_scope
    from tools.schema_loader import SchemaLoadError

    decl = decl or inbox_declaration()
    root = Path(governance_root).resolve()
    name = project or root.name
    errors: list[dict[str, str]] = []
    items: list[dict[str, Any]] = []
    decided: list[dict[str, Any]] = []
    with task_card_lookup_scope(root):
        for category, collect in _COLLECTORS:
            try:
                found = collect(root, name, decl, home_root, errors)
            except SchemaLoadError:
                raise  # 声明坏 = 整个命令拒(不是某项目的真相问题)
            except (OSError, ValueError, LookupError, RuntimeError, FrontmatterReadError) as exc:
                errors.append({"project": name, "source": decl["categories"][category]["label"], "error": f"{type(exc).__name__}: {exc}"})
                continue
            for item in found:
                try:
                    ruling = read_ruling(root, item["item_id"], decl)
                except FrontmatterReadError as exc:
                    errors.append({"project": name, "source": f"裁决记录 {item['decision_id']}", "error": str(exc)})
                    continue
                (decided if ruling else items).append(item)
    return {"project": name, "governance_root": str(root), "items": items, "decided_hidden": len(decided), "errors": errors}


def _sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (int(item["rank"]), str(item.get("since") or "9999"), item["project"], item["item_id"])


def home_projects(home_root: str | Path | None = None) -> dict[str, Any]:
    """home 根已注册项目的唯一枚举(workspace_config._project_candidates: 5_tasks/queue + project.json 同在)+ 各项目推进状态
    (workspace_config.project_status)。收件箱(collect_inbox)与看板项目列表(AIPOS-F171 件③, web/board/app.py)同调, 禁另写枚举。
    返回 {home_root, home_source, projects: [{name, root, status, status_error}]}; status 读不出 / 值不合 = status None + status_error
    (调用方点名, 不猜成暂停)。"""
    from tools.aipos_cli.workspace_config import _project_candidates, project_status, resolve_home_root_with_source

    home, source = resolve_home_root_with_source(explicit_root=home_root)
    projects: list[dict[str, Any]] = []
    for name in _project_candidates(home):
        try:
            status, error = project_status(home / name), None
        except ValueError as exc:  # 值不合 / project.json 读不出
            status, error = None, str(exc)
        projects.append({"name": name, "root": home / name, "status": status, "status_error": error})
    return {"home_root": home, "home_source": source, "projects": projects}


def collect_inbox(home_root: str | Path | None = None, *, project: str | None = None, include_paused: bool = False) -> dict[str, Any]:
    """跨 home 根全部已注册项目(workspace_config._project_candidates 唯一枚举)。返回 {home_root, home_source, projects, items(已排序),
    decided_hidden, paused_hidden, errors}。--project 不在注册清单 = ValueError(用法错, 不猜)。
    AIPOS-F170 件②: project.json status = paused(唯一读取口 workspace_config.project_status)的项目缺省不列(include_paused / 显式 --project
    照列), 名单进 paused_hidden; status 读不出 / 值不合 = 该项目照列并在 errors 点名(不猜成暂停)。"""
    decl = inbox_declaration()
    enumerated = home_projects(home_root)  # AIPOS-F171: 枚举 + 状态读取收为一处(看板项目列表同调)
    home, source = enumerated["home_root"], enumerated["home_source"]
    by_name = {p["name"]: p for p in enumerated["projects"]}
    names = list(by_name)
    if project:
        if project not in names:
            raise ValueError(f"项目 {project!r} 不在 home 根 {home} 的已注册项目中(5_tasks/queue + project.json 同在): {names}")
        names = [project]
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    hidden = 0
    paused: list[str] = []
    for name in names:
        if not include_paused and not project:
            entry = by_name[name]
            status = entry["status"]
            if entry["status_error"]:  # 值不合 / project.json 读不出: 照列并点名
                errors.append({"project": name, "source": "project.json status", "error": entry["status_error"]})
            if status == "paused":
                paused.append(name)
                continue
        part = collect_project_inbox(home / name, project=name, home_root=home, decl=decl)
        items += part["items"]
        errors += part["errors"]
        hidden += part["decided_hidden"]
    items.sort(key=_sort_key)
    return {"home_root": str(home), "home_source": source, "projects": [n for n in names if n not in paused], "items": items,
            "decided_hidden": hidden, "paused_hidden": paused, "errors": errors, "count": len(items)}


# ---------------------------------------------------------------------------
# 渲染(CLI 文本 / 看板 HTML 同一数据)
# ---------------------------------------------------------------------------

def _item_lines(item: dict[str, Any], texts: dict[str, Any]) -> list[str]:
    lines = [_fmt(texts["item"], "texts.item", **item), _fmt(texts["ask"], "texts.ask", ask=item["ask"])]
    if item.get("recommend"):
        lines.append(_fmt(texts["recommend"], "texts.recommend", recommend=item["recommend"]))
    elif item["category"] == "loop_owner_needed":
        lines.append(_fmt(texts["no_recommend"], "texts.no_recommend", task_id=item.get("task_id") or ""))
    lines.append(_fmt(texts["impact"], "texts.impact", impact=item["impact"]))
    lines.append(_fmt(texts["options"], "texts.options", options=" / ".join(f"{o['key']}={o['label']}" for o in item["options"])))
    if item.get("since"):
        lines.append(_fmt(texts["since"], "texts.since", since=item["since"]))
    # AIPOS-F172: 信封项「裁决」指向看板信封页(同行保留 CLI 命令; 行文读声明 categories.envelope_warning.decide_board)
    lines.append(item.get("decide_line") or _fmt(texts["decide"], "texts.decide", command=item["decide_command"]))
    return lines


def _paused_line(inbox: dict[str, Any], texts: dict[str, Any]) -> str | None:
    names = inbox.get("paused_hidden") or []
    return _fmt(texts["paused_hidden"], "texts.paused_hidden", projects=", ".join(names)) if names else None


def render_inbox_text(inbox: dict[str, Any]) -> str:
    texts = inbox_declaration()["texts"]
    head = "header" if inbox["items"] else "empty"
    out = [_fmt(texts[head], f"texts.{head}", count=inbox["count"], projects=len(inbox["projects"]), home_root=inbox["home_root"])]
    paused = _paused_line(inbox, texts)
    if paused:
        out.append(paused)
    for item in inbox["items"]:
        out.append("")
        out.extend(_item_lines(item, texts))
    if inbox["errors"]:
        out.append("")
        out.append(_fmt(texts["errors_header"], "texts.errors_header", count=len(inbox["errors"])))
        out.extend(_fmt(texts["error"], "texts.error", **err) for err in inbox["errors"])
    return "\n".join(out)


def render_inbox_html(inbox: dict[str, Any]) -> str:
    """看板「Owner 待决」页(服务端渲染, 与 CLI 同一数据与同一声明文案; 全部值经 html.escape)。"""
    texts = inbox_declaration()["texts"]
    esc = html.escape
    head = "header" if inbox["items"] else "empty"
    title = _fmt(texts[head], f"texts.{head}", count=inbox["count"], projects=len(inbox["projects"]), home_root=inbox["home_root"])
    parts = ["<!doctype html>", "<html lang=\"zh\"><head><meta charset=\"utf-8\">",
             "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
             f"<title>{esc(title)}</title><link rel=\"stylesheet\" href=\"/styles.css\"></head>",
             "<body class=\"owner-inbox\"><main>", f"<h1 data-owner-inbox-count=\"{inbox['count']}\">{esc(title)}</h1>"]
    paused = _paused_line(inbox, texts)
    if paused:
        parts.append(f"<p class=\"owner-inbox-paused\">{esc(paused)}</p>")
    for item in inbox["items"]:
        lines = _item_lines(item, texts)
        parts.append(f"<section class=\"owner-inbox-item\" data-item-id=\"{esc(item['item_id'])}\" data-category=\"{esc(item['category'])}\""
                     f" data-rank=\"{item['rank']}\" data-project=\"{esc(item['project'])}\">")
        parts.append(f"<h2>{esc(lines[0])}</h2><ul>")
        parts.extend(f"<li>{esc(line.strip())}</li>" for line in lines[1:])
        if item.get("board_url"):  # AIPOS-F172: 信封项可点到看板信封页
            parts.append(f"<li class=\"owner-inbox-board\"><a href=\"{esc(item['board_url'])}\">{esc(item['board_url'])}</a></li>")
        parts.append("</ul></section>")
    if inbox["errors"]:
        parts.append(f"<section class=\"owner-inbox-errors\"><h2>{esc(_fmt(texts['errors_header'], 'texts.errors_header', count=len(inbox['errors'])))}</h2><ul>")
        parts.extend(f"<li>{esc(_fmt(texts['error'], 'texts.error', **err).strip())}</li>" for err in inbox["errors"])
        parts.append("</ul></section>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


def render_inbox_error_html(message: str) -> str:
    """看板页错误态(收件箱整体读不出: 声明坏 / home 根解析不了), 拒因原文经 html.escape。"""
    text = _fmt(inbox_declaration()["texts"]["page_error"], "texts.page_error", error=message)
    return ("<!doctype html>\n<html lang=\"zh\"><head><meta charset=\"utf-8\"><title>owner inbox</title></head>"
            f"<body class=\"owner-inbox\"><main><p class=\"owner-inbox-error\">{html.escape(text)}</p></main></body></html>")


# ---------------------------------------------------------------------------
# AIPOS-F170 件①: 遗留视图(同一 collect_inbox 数据; 按项目分组, 每项一行, 同建议给批量裁决命令)
# ---------------------------------------------------------------------------

def _parse_utc(text: Any) -> datetime | None:
    """ISO 时刻或 YYYY-MM-DD → UTC datetime; 空 / 解析不了 = None(调用方定口径)。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_stale_before(value: str) -> datetime:
    """--stale-before 参数 → UTC datetime; 不合 = InboxUsageError(文案读声明 stale.date_invalid)。"""
    parsed = _parse_utc(value)
    if parsed is None:
        raise InboxUsageError(_fmt(inbox_declaration()["stale"]["date_invalid"], "stale.date_invalid", value=value))
    return parsed


def stale_suggestion(item: dict[str, Any], decl: dict[str, Any] | None = None) -> str | None:
    """遗留事项的建议选项键(声明 stale.suggest; 须属该事项选项, 否则 None = 保留)。"""
    rule = ((decl or inbox_declaration())["stale"].get("suggest") or {}).get(item["category"])
    key = rule.get(str((item.get("facts") or {}).get("queue_state") or "")) if isinstance(rule, dict) else rule
    return str(key) if key and any(o["key"] == key for o in item["options"]) else None


def stale_view(inbox: dict[str, Any], stale_before: str) -> dict[str, Any]:
    """遗留视图数据: 发生时刻早于 stale_before 的事项(发生时刻未知 = 不算遗留), 按项目分组, 每项带建议; 每组按建议聚批量命令。"""
    decl = inbox_declaration()
    cutoff = parse_stale_before(stale_before)
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in inbox["items"]:
        since = _parse_utc(item.get("since"))
        if since is None or since >= cutoff:
            continue
        groups.setdefault(item["project"], []).append({**item, "suggest": stale_suggestion(item, decl)})
    projects = []
    for name in sorted(groups):
        rows = groups[name]
        batches: dict[str, list[str]] = {}
        for row in rows:
            if row["suggest"]:
                batches.setdefault(row["suggest"], []).append(row["item_id"])
        projects.append({"project": name, "items": rows, "batches": [{"choice": k, "item_ids": v} for k, v in batches.items()]})
    return {"stale_before": stale_before, "cutoff": cutoff.isoformat().replace("+00:00", "Z"), "home_root": inbox["home_root"],
            "projects_scanned": len(inbox["projects"]), "paused_hidden": inbox.get("paused_hidden") or [], "groups": projects,
            "count": sum(len(g["items"]) for g in projects), "errors": inbox["errors"]}


def render_stale_text(view: dict[str, Any]) -> str:
    decl = inbox_declaration()
    st, texts = decl["stale"], decl["texts"]
    labels = st.get("suggest_labels") or {}
    head = "header" if view["count"] else "empty"
    out = [_fmt(st[head], f"stale.{head}", stale_before=view["stale_before"], count=view["count"], projects=len(view["groups"])
                if view["count"] else view["projects_scanned"], home_root=view["home_root"])]
    paused = _paused_line(view, texts)
    if paused:
        out.append(paused)
    for group in view["groups"]:
        out.append("")
        out.append(_fmt(st["project_header"], "stale.project_header", project=group["project"], count=len(group["items"])))
        for row in group["items"]:
            title_part = _fmt(st["title_part"], "stale.title_part", title=row["title"]) if row.get("title") else ""
            label = str(labels.get(row["suggest"]) or row["suggest"]) if row["suggest"] else str(st["keep_label"])
            out.append(_fmt(st["line"], "stale.line", item_id=row["item_id"], subject=row["subject"], title_part=title_part,
                            ask=" ".join(str(row["ask"]).split()), suggest_label=label))
        for batch in group["batches"]:
            out.append(_fmt(st["batch_line"], "stale.batch_line", suggest_label=str(labels.get(batch["choice"]) or batch["choice"]),
                            count=len(batch["item_ids"]), item_ids=",".join(batch["item_ids"]), choice=batch["choice"],
                            home_root=view["home_root"]))
    if view["errors"]:
        out.append("")
        out.append(_fmt(texts["errors_header"], "texts.errors_header", count=len(view["errors"])))
        out.extend(_fmt(texts["error"], "texts.error", **err) for err in view["errors"])
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 件② 裁决(AIPOS-F170: 批量 / 转达归因 / blocked 卡撤回与冻结)
# ---------------------------------------------------------------------------

def _client_tag(project: str) -> str:
    import re

    tag = re.sub(r"[^a-z0-9_]", "_", project.lower())
    return tag if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", tag) else f"p_{tag}"[:64]


def _canonical_item(item: dict[str, Any]) -> str:
    keep = {k: item.get(k) for k in ("item_id", "project", "category", "subject", "task_id", "policy_id", "ask", "recommend",
                                     "impact", "options", "anchor", "source")}
    return json.dumps(keep, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def relay_fields(relayed_by: str | None, owner_words: str | None) -> tuple[str, str]:
    """件③: --relayed-by / --owner-words 必填(非空单行)。缺 / 不合 = InboxUsageError(文案读声明 lybra_owner_decide.relay)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    relay = ((load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(DECIDE_VERB) or {}).get("relay")
    if not isinstance(relay, dict) or any(not isinstance(relay.get(k), str) for k in ("owner_self", "missing", "invalid")):
        raise SchemaLoadError(f"verbs.schema.json verbs.{DECIDE_VERB}.relay(owner_self / missing / invalid)未声明齐")
    given = {"--relayed-by": relayed_by, "--owner-words": owner_words}
    missing = [flag for flag, value in given.items() if value is None or not str(value).strip()]
    if missing:
        raise InboxUsageError(_fmt(relay["missing"], f"{DECIDE_VERB}.relay.missing", flags=" / ".join(missing)))
    for flag, value in given.items():
        if "\n" in str(value) or "\r" in str(value):
            raise InboxUsageError(_fmt(relay["invalid"], f"{DECIDE_VERB}.relay.invalid", flag=flag, value=value))
    return str(relayed_by).strip(), str(owner_words).strip()


def build_decision_payload(item: dict[str, Any], choice: str, note: str | None, actor: str, *, decided_at: str,
                           relayed_by: str, owner_words: str, decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """事项 + 选项 → 既有 owner_decision_record 全量 payload(owner_decision_writer 非信封路径, AIPOS-110 形)。
    AIPOS-F170 件③: captured_by(两处)= 转达人; decision_rationale = 声明 owner_words_prefix + Owner 原话。"""
    from datetime import datetime as _dt

    from tools.aipos_cli.clock import iso_z

    decl = decl or inbox_declaration()
    d = decl["decision"]
    label = next(o["label"] for o in item["options"] if o["key"] == choice)
    note_text = str(note or "").strip()
    note_part = _fmt(decl["texts"]["note_part"], "texts.note_part", note=note_text) if note_text else ""
    summary = _fmt(d["summary_template"], "decision.summary_template", category_label=item["category_label"], subject=item["subject"],
                   choice=choice, label=label, note_part=note_part)
    if len(summary) > 320:
        summary = summary[:319] + "…"
    project = str(item["project"])
    tag = _client_tag(project)
    external_ref = f"{d['external_ref_prefix']}{item['item_id']}"
    decided = _dt.fromisoformat(decided_at.replace("Z", "+00:00"))
    return {
        "decision_id": item["decision_id"],
        "decision_type": d["decision_type"],
        "decision_status": d["decision_status"],
        "decided_at": decided_at,
        "decided_by_ref": actor,
        "captured_by": relayed_by,
        "capture_surface": d["capture_surface"],
        "decision_summary": summary,
        "decision_rationale": f"{d['owner_words_prefix']}{owner_words}",
        "applies_to": {"project": project if project == tag else None, "task_id": item.get("task_id"), "external_ref": external_ref},
        "approval_scope": {"operation": "owner_decision_record",
                           "authority_boundary": _fmt(d["authority_boundary"], "decision.authority_boundary", category=item["category"],
                                                      subject=item["subject"])[:160],
                           "allowed_next_action": choice, "expires_at": None},
        "owner_approval_evidence": {
            "evidence_id": f"{d['source_tag']}:{item['item_id']}", "source_tag": d["source_tag"], "client_tag": tag,
            "external_ref": external_ref, "approval_actor_ref": actor, "approval_timestamp": decided_at,
            "approval_intent": d["approval_intent"], "evidence_hash": hashlib.sha256(_canonical_item(item).encode("utf-8")).hexdigest(),
            "evidence_ref": None, "captured_by": relayed_by, "capture_method": d["capture_method"], "redaction_status": d["redaction_status"],
            "refs": [external_ref],
        },
        "refs": [external_ref],
        "capability_scope": {"token_ref": d["token_ref"], "operations": ["owner_decision_record"], "projects": [project],
                             "expires_at": iso_z(decided + timedelta(hours=int(d["capability_ttl_hours"])))},
    }


def _effect(item: dict[str, Any], choice: str, decl: dict[str, Any]) -> str | None:
    spec = decl["options"].get(choice) if item["category"] == "needs_owner_card" else None
    return str(spec.get("effect")) if isinstance(spec, dict) and spec.get("effect") else None


def _ruling_reason(decision_ids: list[str], item_ids: list[str]) -> str:
    return f"Owner inbox ruling {', '.join(decision_ids)} ({', '.join(item_ids)})"


def _release_preview(item: dict[str, Any], effect: str | None, actor: str, decision_id: str, *, dry_run: bool) -> dict[str, Any] | None:
    """逐卡解除动作 = 既有队列出口(不手改卡): release_needs_owner → board_adapter.amend_task(只收 pending, 改为真的 needs_owner 类标志);
    withdraw_card → board_adapter.withdraw_task(AIPOS-F170: 带 Owner 裁决引用 owner_ruling_ref = decision_id, blocked 卡凭此可撤)。
    freeze_legacy 按治理根成批, 见 _freeze_group。dry_run=True 预演、False 执行; 无 effect = None。"""
    from tools.aipos_cli.board_adapter import amend_task, withdraw_task

    if not effect or effect == "freeze_legacy":
        return None
    root = Path(item["governance_root"])
    reason = _ruling_reason([decision_id], [item["item_id"]])
    if effect == "release_needs_owner":
        flags = list((item.get("facts") or {}).get("flags") or []) or ["needs_owner"]
        return amend_task(task_id=item["task_id"], actor=actor, amendments={f: False for f in flags}, amendment_reason=reason,
                          dry_run=dry_run, repo_root=root)
    if effect == "withdraw_card":
        return withdraw_task(task_id=item["task_id"], actor=actor, reason=reason, dry_run=dry_run, repo_root=root,
                             owner_ruling_ref=decision_id)
    from tools.schema_loader import SchemaLoadError

    raise SchemaLoadError(f"verbs.{VERB}.options.*.effect 未知: {effect!r}")


def _freeze_group(root: Path, entries: list[dict[str, Any]], actor: str, *, dry_run: bool) -> dict[str, Any]:
    """AIPOS-F170 件①: 同一治理根内裁决为 freeze_legacy 的卡 = 一条存量冻结清单条目(既有口 legacy_freeze.plan_freeze_legacy 选卡 +
    legacy_baseline.write_freeze_entry 落条目; owner_policy_ref = 本批决策记录 ID)。返回 {ok, verdict, plan, written, blocking_reasons}。"""
    from tools.aipos_cli.legacy_baseline import LegacyBaselineError, write_freeze_entry
    from tools.aipos_cli.legacy_freeze import plan_freeze_legacy

    task_ids = [str(e["item"]["task_id"]) for e in entries]
    decision_ids = [str(e["item"]["decision_id"]) for e in entries]
    try:
        plan = plan_freeze_legacy(root, task_ids=task_ids)
        written = None if dry_run else write_freeze_entry(root, plan, reason=_ruling_reason(decision_ids, [e["item"]["item_id"] for e in entries]),
                                                          actor=actor, owner_policy_ref=",".join(decision_ids))
    except (LegacyBaselineError, OSError) as exc:
        return {"ok": False, "verdict": "BLOCK", "plan": None, "written": None, "blocking_reasons": [f"{type(exc).__name__}: {exc}"],
                "task_ids": task_ids}
    return {"ok": True, "verdict": "PASS", "plan": {k: plan[k] for k in ("action", "selected", "already", "manifest_dir")},
            "written": written, "blocking_reasons": [], "task_ids": task_ids}


def _release_writes(resp: dict[str, Any] | None) -> list[str]:
    """逐卡解除动作写出的文件(冻结组的清单条目由 decide 按治理根另计)。"""
    if not resp or not resp.get("ok"):
        return []
    data = resp.get("data") or {}
    paths = [data.get(k) for k in ("task_path", "amendment_record_path", "source_path", "target_path")]
    return [str(p) for p in paths if p]


def _release_blocked(resp: dict[str, Any] | None) -> bool:
    return resp is not None and (not resp.get("ok") or resp.get("verdict") == "BLOCK")


def find_items(item_ids: list[str], *, home_root: str | Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """收件箱只算一次, 逐 ID 取事项; 任一不在 = InboxItemNotFound(点名全部缺的)。暂停项目照列(裁决口不受缺省视图影响)。"""
    inbox = collect_inbox(home_root, include_paused=True)
    by_id = {item["item_id"]: item for item in inbox["items"]}
    missing = [i for i in item_ids if i not in by_id]
    if missing:
        raise InboxItemNotFound(f"收件箱中无事项 {', '.join(missing)}(已裁决 / 已自行解除 / ID 错; 当前 {inbox['count']} 项, "
                                f"lybra owner inbox --home-root {inbox['home_root']} 查看)")
    return [by_id[i] for i in item_ids], inbox


def find_item(inbox_item_id: str, *, home_root: str | Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    items, inbox = find_items([inbox_item_id], home_root=home_root)
    return items[0], inbox


def split_item_ids(item: str | None, items: str | list[str] | None) -> list[str]:
    """--item 与 --items 二选一 → 去重保序的事项 ID 列表; 不合 = InboxUsageError。"""
    raw = items if isinstance(items, list) else [p for p in str(items or "").split(",")]
    many = [str(p).strip() for p in raw if str(p).strip()] if items else []
    if bool(str(item or "").strip()) == bool(many):
        raise InboxUsageError("须且只能给 --item <事项ID> 或 --items <ID,ID,…> 之一")
    return list(dict.fromkeys(many or [str(item).strip()]))


def decide(item_ids: str | list[str], choice: str, *, note: str | None = None, home_root: str | Path | None = None,
           actor: str = "owner", relayed_by: str | None = None, owner_words: str | None = None,
           confirm: bool = False) -> dict[str, Any]:
    """件② 唯一实现(AIPOS-F170: 单项与批量同一路径)。先整批预演: 每项决策记录经门侧同一预演(board_adapter.record_owner_decision)+
    解除动作预演(逐卡既有出口 dry_run / 冻结按治理根成批 dry_run); 任一被拒 = 整批不写(blocked)。确认: 逐项门侧同一执行
    (board_adapter.execute_dry_run, 含 decision_log 自动指针)→ 解除动作(既有出口; 冻结同根一条清单条目)→ 每个治理根经
    land_written_paths 落账一次。返回 {ok, stage, choice, entries[{item, decision, release, effect}], written, landings, rc_outcome, ...};
    单项时另带 item / decision / release / effect / landing(F168 形)。rc_outcome = 声明出口名。"""
    from tools.aipos_cli.board_adapter import execute_dry_run, record_owner_decision
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN

    ids = [item_ids] if isinstance(item_ids, str) and "," not in item_ids else split_item_ids(None, item_ids)
    relayed, words = relay_fields(relayed_by, owner_words)
    decl = inbox_declaration()
    items, inbox = find_items(ids, home_root=home_root)
    for item in items:
        keys = [o["key"] for o in item["options"]]
        if choice not in keys:
            raise InboxChoiceInvalid(f"选项 {choice!r} 不属事项 {item['item_id']}; 可选: {', '.join(keys)}")
    decided_at = iso_z()
    report: dict[str, Any] = {"ok": False, "stage": "confirm" if confirm else "preview", "choice": choice, "note": note, "actor": actor,
                              "relayed_by": relayed, "owner_words": words, "home_root": inbox["home_root"], "entries": [],
                              "freeze": [], "written": [], "landings": [], "rc_outcome": "ok"}
    # ① 整批预演(零写入)
    tokens: list[str] = []
    for item in items:
        root = Path(item["governance_root"])
        payload = build_decision_payload(item, choice, note, actor, decided_at=decided_at, relayed_by=relayed, owner_words=words, decl=decl)
        dry = record_owner_decision(payload, dry_run=True, repo_root=root, actor=actor)
        decision = {k: dry.get(k) for k in ("ok", "verdict", "blocking_reasons", "warnings", "planned_writes")}
        decision["target_path"] = (dry.get("data") or {}).get("target_path")
        effect = _effect(item, choice, decl)
        entry = {"item": item, "effect": effect, "decision": decision, "release": None, "written": []}
        report["entries"].append(entry)
        if dry.get("verdict") == "BLOCK" or not dry.get("ok") or not dry.get("dry_run_token"):
            report["rc_outcome"] = "blocked"
        tokens.append(str(dry.get("dry_run_token") or ""))
        entry["release"] = _release_preview(item, effect, actor, item["decision_id"], dry_run=True)
        if _release_blocked(entry["release"]):
            report["rc_outcome"] = "blocked"
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in report["entries"]:
        if entry["effect"] == "freeze_legacy":
            groups.setdefault(entry["item"]["governance_root"], []).append(entry)
    for root_text, entries in groups.items():
        group = _freeze_group(Path(root_text), entries, actor, dry_run=True)
        report["freeze"].append({"governance_root": root_text, **group})
        if _release_blocked(group):
            report["rc_outcome"] = "blocked"
    _mirror_single(report)
    if report["rc_outcome"] == "blocked" or not confirm:
        report["ok"] = report["rc_outcome"] != "blocked"
        return report
    # ② 确认: 逐项写决策记录 → 解除动作 → 每根落账一次
    by_root: dict[str, list[str]] = {}
    failed = False
    for entry, token in zip(report["entries"], tokens):
        item = entry["item"]
        root = Path(item["governance_root"])
        done = execute_dry_run(token, actor, owner_confirmation_token=OWNER_CONFIRMATION_TOKEN, repo_root=root)
        entry["decision"] = {k: done.get(k) for k in ("ok", "verdict", "blocking_reasons", "warnings", "performed_writes", "errors")}
        written = [str(w.get("path")) for w in (done.get("performed_writes") or []) if isinstance(w, dict) and w.get("path")]
        if not done.get("ok") or not written:
            failed = True
            continue
        entry["decision"]["target_path"] = written[0]
        entry["release"] = _release_preview(item, entry["effect"], actor, item["decision_id"], dry_run=False)
        written += _release_writes(entry["release"])
        failed = failed or _release_blocked(entry["release"])
        entry["written"] = written
        by_root.setdefault(item["governance_root"], []).extend(written)
    report["freeze"] = []
    for root_text, entries in groups.items():
        ready = [e for e in entries if e["written"]]  # 决策记录已落的才冻结(引用须真实)
        if not ready:
            continue
        group = _freeze_group(Path(root_text), ready, actor, dry_run=False)
        report["freeze"].append({"governance_root": root_text, **group})
        failed = failed or _release_blocked(group)
        written_entry = (group.get("written") or {})
        if written_entry.get("written"):
            from tools.aipos_cli.workspace_config import project_json_path

            extra = [str(written_entry["entry"])] + ([str(project_json_path(root_text))] if written_entry.get("project_json_declared") else [])
            by_root.setdefault(root_text, []).extend(extra)
    for root_text, written in by_root.items():
        ids_here = [e["item"]["item_id"] for e in report["entries"] if e["item"]["governance_root"] == root_text]
        landing = _land(Path(root_text), written, actor, item_id=",".join(ids_here), choice=choice)
        report["landings"].append({"governance_root": root_text, **landing})
        report["written"] += written
    _mirror_single(report)
    landed = all(lg.get("landed") for lg in report["landings"])
    report["ok"] = not failed
    report["rc_outcome"] = "blocked" if failed else ("ok" if landed else "landing_failed")
    return report


def _mirror_single(report: dict[str, Any]) -> None:
    """单项裁决时把唯一条目的 item / decision / release / effect / landing 提到顶层(F168 输出形不变)。"""
    if len(report["entries"]) != 1:
        return
    entry = report["entries"][0]
    report.update({"item": entry["item"], "decision": entry["decision"], "effect": entry["effect"],
                   "release": entry["release"] if entry["effect"] != "freeze_legacy" else (report["freeze"][0] if report["freeze"] else None),
                   "landing": report["landings"][0] if report["landings"] else None})


def _land(root: Path, written: list[str], actor: str, **fields: Any) -> dict[str, Any]:
    """写出文件经 F158 写后落账唯一实现落账(绝对路径换算为治理根相对); 落账声明不可读 = 不落账(fail-closed), 写入已生效: 拒因出声。"""
    from tools.aipos_cli.governance_commit import land_written_paths
    from tools.schema_loader import SchemaLoadError

    base = Path(root).resolve()
    rels = sorted({(Path(p).resolve().relative_to(base) if Path(p).is_absolute() else Path(p)).as_posix() for p in written})
    try:
        return land_written_paths(root, rels, actor, command="owner_decide", **fields)
    except SchemaLoadError as exc:
        return {"landed": False, "exit_code": 1, "reason": str(exc), "paths": rels,
                "line": f"⚠ 落账未完成(写入已生效, 勿重复执行写命令): {exc}"}


# ---------------------------------------------------------------------------
# 件② 顾问侧: loop status / next 读到裁决
# ---------------------------------------------------------------------------

def loop_item_id(governance_root: Path, task_id: str, run_id: str, decl: dict[str, Any] | None = None) -> str:
    """loop 类事项 ID(收件箱 _loop_items 同一构造: 项目 = 治理根目录名, 主体 = 声明 subject, 锚点 = run_id)。"""
    decl = decl or inbox_declaration()
    root = Path(governance_root).resolve()
    subject = _fmt(decl["categories"]["loop_owner_needed"]["subject"], "categories.loop_owner_needed.subject", task_id=task_id)
    return item_id(root.name, "loop_owner_needed", subject, run_id, decl)


def ruling_view(ruling: dict[str, Any], decl: dict[str, Any], *, resume: str, task_id: str, governance_root: Path,
                labels: dict[str, str] | None = None) -> dict[str, Any]:
    """决策记录 → {choice, label, note, relayed_by, owner_words, decided_at, decision_id, line, advisor_next}(文案读声明)。
    AIPOS-F170 件③: 记录带 Owner 原话(_ruling_record 读回)= 裁决行附转达段 texts.relay_part(转达人 + 原话); 旧记录无 = 不附。"""
    choice = str(ruling.get("allowed_next_action") or "")
    spec = decl["options"].get(choice)
    label = str((spec or {}).get("label") or (labels or {}).get(choice) or choice)
    summary = str(ruling.get("decision_summary") or "")
    note_marker = _fmt(decl["texts"]["note_part"], "texts.note_part", note="")
    note = summary.split(note_marker, 1)[1] if note_marker and note_marker in summary else ""
    note_part = _fmt(decl["texts"]["note_part"], "texts.note_part", note=note) if note else ""
    words = str(ruling.get("owner_words") or "")
    relay_part = (_fmt(decl["texts"]["relay_part"], "texts.relay_part", relayed_by=str(ruling.get("relayed_by") or "?"), owner_words=words)
                  if words else "")
    line = _fmt(decl["texts"]["ruling_line"], "texts.ruling_line", choice=choice, label=label, note_part=note_part,
                relay_part=relay_part, decided_at=str(ruling.get("decided_at") or "?"))
    template = (spec or {}).get("advisor_next") or decl["custom_option"]["advisor_next"]
    advisor_next = _fmt(template, "options.advisor_next", resume=resume, task_id=task_id, governance_root=str(governance_root), label=label)
    return {"choice": choice, "label": label, "note": note or None, "relayed_by": ruling.get("relayed_by"), "owner_words": words or None,
            "decided_at": ruling.get("decided_at"),
            "decision_id": ruling.get("decision_id"), "record": ruling.get("_path"), "line": line, "advisor_next": advisor_next}


def apply_owner_ruling(governance_root: Path, view: dict[str, Any], na: dict[str, Any]) -> dict[str, Any]:
    """loop status 每个运行的下一动作: owner_needed 且该运行对应事项已有 Owner 裁决 = advisor_fix(owner_ruled), detail = 裁决原文,
    hint = 该选项 advisor_next(含续跑命令, 占位与例行门拒同一实现 loop_run_record._resume_fill)。其余原样返回。"""
    if not isinstance(na, dict) or na.get("action") != "owner_needed":
        return na
    from tools.aipos_cli.loop_run_record import _resume_fill, status_contract

    decl = inbox_declaration()
    task_id = str(view.get("task_id") or "")
    iid = loop_item_id(Path(governance_root), task_id, str(view.get("run_id") or ""), decl)
    ruling = read_ruling(Path(governance_root), iid, decl)
    if ruling is None:
        return {**na, "inbox_item_id": iid}
    resume = _fmt(status_contract()["next_action"]["routine_rejections"]["resume_hint"], "resume_hint",
                  **_resume_fill(view, Path(governance_root)))
    ask = view.get("owner_ask") if isinstance(view.get("owner_ask"), dict) else {}
    labels = {str(o.get("key")): str(o.get("label")) for o in (ask.get("options") or []) if isinstance(o, dict)}
    rv = ruling_view(ruling, decl, resume=resume, task_id=task_id, governance_root=Path(governance_root), labels=labels)
    return {"action": "advisor_fix", "reason": "owner_ruled", "detail": f"{rv['line']}(原待决: {na.get('detail')})",
            "hint": rv["advisor_next"], "owner_ruling": rv, "inbox_item_id": iid, "ruled_from": {"reason": na.get("reason")}}


def rulings_for_task(governance_root: Path, task_id: str) -> list[dict[str, Any]]:
    """该卡的收件箱裁决(决策记录 decision_type = 声明 decision_type 且 task_id = 该卡), 按裁决时刻旧→新。读不出 = 向上。"""
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.record_writer import record_root

    decl = inbox_declaration()
    d = decl["decision"]
    directory = Path(governance_root) / record_root("owner_decisions")
    out: list[dict[str, Any]] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob(f"{d['decision_id_prefix']}*.md")):
        fm, body = require_frontmatter(path)
        if str(fm.get("decision_type") or "") == d["decision_type"] and str(fm.get("task_id") or "") == str(task_id):
            out.append(_ruling_record(path, fm, body))
    out.sort(key=lambda r: str(r.get("decided_at") or ""))
    return out


def owner_ask_labels(governance_root: Path, task_id: str) -> dict[str, str]:
    """AIPOS-F170 件④: 该卡 loop 运行记录 owner_ask 的自定义选项 {键: 标签}(lybra loop note 补写; 新运行覆盖旧运行同键)。
    `lybra next` 裁决行据此显示标签(原只显示键)。运行记录读不出 = LoopRunRecordError 向上(不吞)。"""
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import _run_files, loop_runs_dir, read_run, run_record_declaration

    rdecl = run_record_declaration(load_loop_contract())
    metas = sorted((read_run(p, rdecl) for p in _run_files(loop_runs_dir(Path(governance_root), task_id))),
                   key=lambda m: (str(m.get("started_at") or ""), str(m.get("run_id") or "")))
    labels: dict[str, str] = {}
    for meta in metas:
        ask = meta.get("owner_ask") if isinstance(meta.get("owner_ask"), dict) else {}
        labels.update({str(o.get("key")): str(o.get("label")) for o in (ask.get("options") or []) if isinstance(o, dict)})
    return labels


def next_ruling_lines(governance_root: Path, task_id: str) -> list[str]:
    """`lybra next --task-id` 附行: 该卡最近一条收件箱裁决(读不出 = 向上, 不吞)。"""
    rulings = rulings_for_task(Path(governance_root), task_id)
    if not rulings:
        return []
    decl = inbox_declaration()
    rv = ruling_view(rulings[-1], decl, resume=f"lybra loop --task-id {task_id} --workspace-root {Path(governance_root)}",
                     task_id=task_id, governance_root=Path(governance_root), labels=owner_ask_labels(Path(governance_root), task_id))
    return [rv["line"], _fmt(decl["texts"]["next_hint"], "texts.next_hint", advisor_next=rv["advisor_next"])]


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
