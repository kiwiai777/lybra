"""AIPOS-F173 — 路线图偏差推导(唯一实现; 只读适配器)。

一句话: 根据路线图声明与卡、门记录、loop 运行记录、审计裁决, 算出每个里程碑的进度(标准满足数 / 总数)与偏差信号
(游离卡占比 / 停滞 / 无卡覆盖的标准 / 反复返工 / 逾期 / 路线图近期被改), 给每条一句人话原因与给顾问的命令建议。

防碎片化(卡面硬约束):
- 本模块是推导的唯一实现: CLI `lybra roadmap status`(aipos_cli 薄壳)、Owner 收件箱 roadmap_drift 类别(owner_inbox._roadmap_items)、
  看板(board_adapter.get_roadmap_status, 供 F174 页)同读 roadmap_status。
- 只读既有真相, 不另记进度: 路线图文件(roadmap.load_roadmap)、卡挂接(roadmap.card_links, 游离判定 card_link_status 不另写)、
  门记录(next_resolver._read_task_records: 裁决 / 补审裁决 / 认领 / 交回 / 结案)、卡面 rework_rounds、运行证据
  (artifact_ingest.runtime_evidence_requirement + parse_runtime_evidence_section)、loop 运行记录(loop_run_record.read_run)、
  decision_log 指针(decision_log_pointers.pointer_index)。
- 阈值读 project.json roadmap_drift(唯一读取口 workspace_config.roadmap_drift_thresholds, 缺省见 config.schema); 文案 / 信号种类 /
  严重度 / 建议命令读 verbs.schema lybra_roadmap.status(status_declaration 唯一读取口), 禁写死。
- 任何项目同一组命令: 本模块不写死项目 ID / 路径 / 卡号前缀。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: 信号种类(与 verbs.schema lybra_roadmap.status.signals 键同集; 声明读取处核对)
SIGNAL_KINDS = ("unlinked_ratio", "stalled", "uncovered", "rework", "overdue", "roadmap_changed")
_STATUS_KEYS = ("progress_bar_width", "satisfied_queue_states", "excluded_queue_states", "verify_pending", "unmet_reasons",
                "status_mismatch", "severity_labels", "signals", "next_steps", "texts")


# ---------------------------------------------------------------------------
# 声明(唯一读取口; fail-closed)
# ---------------------------------------------------------------------------

def status_declaration() -> dict[str, Any]:
    """verbs.schema lybra_roadmap.status。缺键 / 信号种类不恰为 SIGNAL_KINDS / 严重度不在 severity_labels = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = (((load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get("lybra_roadmap") or {}).get("status"))
    if not isinstance(decl, dict) or any(k not in decl for k in _STATUS_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_roadmap.status 未声明齐 {list(_STATUS_KEYS)}")
    signals = decl["signals"]
    if not isinstance(signals, dict) or set(signals) != set(SIGNAL_KINDS):
        raise SchemaLoadError(f"verbs.lybra_roadmap.status.signals 须恰为 {list(SIGNAL_KINDS)}, 得到 {sorted(signals or [])}")
    for kind, spec in signals.items():
        if (not isinstance(spec, dict) or spec.get("severity") not in decl["severity_labels"]
                or not all(isinstance(spec.get(k), str) and spec[k] for k in ("label", "reason", "advice"))):
            raise SchemaLoadError(f"verbs.lybra_roadmap.status.signals.{kind} 须有 label / reason / advice 与已声明的 severity")
    from tools.aipos_cli.roadmap import roadmap_declaration

    verify_enum = roadmap_declaration()["verify"]["enum"]
    pending = decl["verify_pending"]
    if not isinstance(pending, dict) or not set(pending) < set(verify_enum):
        raise SchemaLoadError(f"verbs.lybra_roadmap.status.verify_pending 键须为 verify 值域 {verify_enum} 的真子集(其余 = 可推导)")
    return decl


def _fmt(template: Any, where: str, **values: Any) -> str:
    """声明模板渲染; 占位缺值 = SchemaLoadError(声明与实现不符, 不猜)。"""
    from tools.schema_loader import SchemaLoadError

    try:
        return str(template).format_map(values)
    except (KeyError, IndexError, ValueError) as exc:
        raise SchemaLoadError(f"verbs.lybra_roadmap.status.{where} 模板占位无值: {exc}") from exc


# ---------------------------------------------------------------------------
# 时刻(卡面 / 门记录 / 运行记录 / 指针的 *_at 值)
# ---------------------------------------------------------------------------

def _as_utc(value: Any) -> datetime | None:
    """ISO 时刻 / 日期 / datetime → UTC datetime; 空或解析不了 = None(调用方定口径, 不猜)。"""
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _latest_at(mapping: Any) -> datetime | None:
    """映射里全部 *_at 键(顶层)的最晚时刻。"""
    if not isinstance(mapping, dict):
        return None
    stamps = [_as_utc(v) for k, v in mapping.items() if isinstance(k, str) and k.endswith("_at")]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def _iso(moment: datetime | None) -> str | None:
    from tools.aipos_cli.clock import iso_z

    return iso_z(moment) if moment else None


def _card_created(meta: dict[str, Any]) -> datetime | None:
    """出卡时刻: 卡面 created_at → draft_created_at(都无 = None, 不计入游离卡占比窗)。"""
    for key in ("created_at", "draft_created_at"):
        moment = _as_utc(meta.get(key))
        if moment is not None:
            return moment
    return None


# ---------------------------------------------------------------------------
# 每卡真相(门记录 + loop 运行记录; 读不出 = 点名进 errors, 不猜)
# ---------------------------------------------------------------------------

def _card_truth(root: Path, card: dict[str, Any], errors: list[dict[str, str]], cache: dict[str, Any]) -> dict[str, Any]:
    """{records, last_activity, rework_rounds, unreadable}。records = next_resolver._read_task_records(门记录唯一读取);
    最近推进 = 卡面 *_at / 返工节 created_at / 门记录 *_at / loop 运行记录 *_at 的最晚者。"""
    task_id = str(card.get("task_id") or "")
    if task_id in cache:
        return cache[task_id]
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecordError, _run_files, loop_runs_dir, read_run, run_record_declaration
    from tools.aipos_cli.next_resolver import _read_task_records

    meta = card.get("metadata") or {}
    rounds = meta.get("rework_rounds") if isinstance(meta.get("rework_rounds"), list) else []
    stamps = [_latest_at(meta)] + [_latest_at(r) for r in rounds]
    out: dict[str, Any] = {"records": None, "rework_rounds": len(rounds), "unreadable": None}
    try:
        records = _read_task_records(root, task_id)
        out["records"] = records
        for key, value in records.items():
            if key.startswith("latest_"):
                stamps.append(_latest_at(value))
        stamps += [_latest_at(e) for e in records.get("events") or []]
        rdecl = cache.setdefault("__run_decl__", run_record_declaration(load_loop_contract()))
        for path in _run_files(loop_runs_dir(root, task_id)):
            stamps.append(_latest_at(read_run(path, rdecl)))
    except (OSError, ValueError, LookupError, FrontmatterReadError, LoopRunRecordError) as exc:
        out["unreadable"] = f"{type(exc).__name__}: {exc}"
        errors.append({"source": f"卡 {task_id} 的门记录 / 运行记录", "error": out["unreadable"]})
    found = [s for s in stamps if s is not None]
    out["last_activity"] = max(found) if found else None
    cache[task_id] = out
    return out


def _verify_card(root: Path, card: dict[str, Any], truth: dict[str, Any], verify: str, decl: dict[str, Any]) -> tuple[bool, str]:
    """已结案挂接卡是否达到标准声明的 verify 要求 → (满足, 未满足原因)。
    auto: 最新常规审计裁决属 PASS 类、补审未判 FAIL、卡声明了运行证据时裁决报告有运行证据节;
    verify_pending 所列方式(artifact / owner_check): 本卡先按未满足显示原因(待 F175), 不臆断。"""
    from tools.aipos_cli.artifact_ingest import (
        RuntimeEvidenceDeclarationError, parse_runtime_evidence_section, runtime_evidence_requirement,
    )
    from tools.aipos_cli.audit_derivation import supplementary_audit_declaration
    from tools.aipos_cli.next_resolver import _PASS_VERDICTS

    reasons = decl["unmet_reasons"]
    task_id = str(card.get("task_id") or "")
    if truth.get("unreadable"):
        return False, _fmt(reasons["unreadable"], "unmet_reasons.unreadable", task_id=task_id, problem=truth["unreadable"])
    records = truth.get("records") or {}
    verdict = records.get("latest_verdict") or {}
    value = str(verdict.get("verdict") or "").upper()
    if value not in _PASS_VERDICTS:
        return False, _fmt(reasons["no_pass"], "unmet_reasons.no_pass", task_id=task_id, verdict=value or "无裁决记录")
    supp = records.get("latest_supplementary_verdict") or {}
    supp_value = str(supp.get("verdict") or "").upper()
    if supp_value and supp_value in {str(v).upper() for v in supplementary_audit_declaration()["fail_verdicts"]}:
        return False, _fmt(reasons["supplementary_fail"], "unmet_reasons.supplementary_fail", task_id=task_id, verdict=supp_value,
                           verdict_id=str(supp.get("verdict_id") or "?"))
    try:
        requirement = runtime_evidence_requirement(None, card.get("metadata") or {})
    except RuntimeEvidenceDeclarationError as exc:
        return False, _fmt(reasons["evidence_declaration"], "unmet_reasons.evidence_declaration", task_id=task_id, problem=str(exc))
    if requirement is not None:
        problem = "裁决记录无报告引用 evidence_refs"
        for ref in verdict.get("evidence_refs") or []:
            report = Path(str(ref)) if Path(str(ref)).is_absolute() else root / str(ref)
            if not report.is_file():
                problem = f"报告 {ref} 不存在"
                continue
            try:
                entries, problem = parse_runtime_evidence_section(report.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as exc:
                entries, problem = None, f"报告 {ref} 读不出: {exc}"
            if entries:
                problem = ""
                break
            problem = problem or f"报告 {ref} 运行证据节为空"
        if problem:
            return False, _fmt(reasons["no_evidence"], "unmet_reasons.no_evidence", task_id=task_id, problem=problem)
    pending = decl["verify_pending"].get(verify)
    if pending:
        return False, _fmt(reasons["pending"], "unmet_reasons.pending", task_id=task_id, pending=pending)
    return True, ""


# ---------------------------------------------------------------------------
# 推导
# ---------------------------------------------------------------------------

def _ids(cards: list[dict[str, Any]]) -> str:
    return ", ".join(str(c.get("task_id")) for c in cards) or "-"


def _sample(ids: list[str], limit: int = 8) -> str:
    return ", ".join(ids[:limit]) + (" …" if len(ids) > limit else "")


def _signal(decl: dict[str, Any], kind: str, *, governance_root: Path, anchor: str, subject: str, advisor: str | None,
            milestone: dict[str, Any] | None = None, criterion: str | None = None, **values: Any) -> dict[str, Any]:
    spec = decl["signals"][kind]
    fill = {"governance_root": str(governance_root), "milestone": (milestone or {}).get("id"), "criterion": criterion, **values}
    return {"kind": kind, "label": str(spec["label"]), "severity": str(spec["severity"]),
            "severity_label": str(decl["severity_labels"][spec["severity"]]), "milestone": (milestone or {}).get("id"),
            "lane": (milestone or {}).get("lane"), "criterion": criterion, "subject": subject, "anchor": anchor, "advisor": advisor,
            "reason": _fmt(spec["reason"], f"signals.{kind}.reason", **fill), "advice": _fmt(spec["advice"], f"signals.{kind}.advice", **fill),
            "facts": {k: v for k, v in values.items() if isinstance(v, (str, int, float, bool)) or v is None}}


def roadmap_status(governance_root: str | Path, *, lane: str | None = None, project: str | None = None,
                   now: datetime | None = None) -> dict[str, Any]:
    """路线图进度与偏差(唯一实现; 只读)。返回 {project, path, rel, exists, lane, today, thresholds, milestones, signals, next_steps,
    unlinked, invalid, unreadable_cards, errors, totals}。无路线图 = exists False(调用方给提示, 不报错)。路线图读不出 = RoadmapError;
    阈值不合 = RoadmapDriftThresholdError; 声明缺 = SchemaLoadError(都向上, 调用方点名)。某卡门记录读不出 = 进 errors, 相关标准按未满足、
    相关里程碑不判停滞(不猜)。"""
    from tools.aipos_cli.clock import utc_now
    from tools.aipos_cli.roadmap import LINK_INVALID, LINK_LINKED, LINK_UNLINKED, card_links, load_roadmap, roadmap_declaration
    from tools.aipos_cli.task_loader import task_card_lookup_scope
    from tools.aipos_cli.workspace_config import roadmap_drift_thresholds

    root = Path(governance_root)
    moment = now or utc_now()
    today = moment.date()
    decl = status_declaration()
    rdecl = roadmap_declaration()
    roadmap = load_roadmap(root)
    out: dict[str, Any] = {"project": project, "path": str(roadmap["path"]), "rel": roadmap["rel"], "exists": roadmap["exists"],
                           "lane": lane, "today": today.isoformat(), "thresholds": None, "milestones": [], "signals": [],
                           "next_steps": [], "unlinked": [], "invalid": [], "unreadable_cards": [], "errors": [],
                           "totals": {"met": 0, "total": 0}}
    if not roadmap["exists"]:
        return out
    thresholds = roadmap_drift_thresholds(root)
    out["thresholds"] = thresholds
    errors: list[dict[str, str]] = out["errors"]
    cache: dict[str, Any] = {}
    excluded = set(decl["excluded_queue_states"])
    satisfied_states = set(decl["satisfied_queue_states"])
    with task_card_lookup_scope(root):
        links = card_links(root, roadmap)
        live = [c for c in links["cards"] if c.get("queue_state") not in excluded]
        out["unlinked"] = [c["task_id"] for c in live if c["state"] == LINK_UNLINKED]
        out["invalid"] = [{"task_id": c["task_id"], "problems": c["problems"]} for c in live if c["state"] == LINK_INVALID]
        out["unreadable_cards"] = links["unreadable"]
        linked = [c for c in live if c["state"] == LINK_LINKED]

        from tools.aipos_cli.decision_log_pointers import pointer_index

        pointers = pointer_index(root)
        for text in pointers["unreadable"]:
            errors.append({"source": "decision_log 指针", "error": text})
        changes = sorted((e for e in pointers["entries"] if roadmap["rel"] in e["records"]),
                         key=lambda e: str(e.get("decided_at") or ""))
        last_editor = next((e["decided_by"] for e in reversed(changes) if e.get("decided_by")), None)

        def _advisor(cards: list[dict[str, Any]]) -> str | None:
            dated = sorted(((_card_created(c.get("metadata") or {}) or datetime.min.replace(tzinfo=timezone.utc), c) for c in cards),
                           key=lambda pair: pair[0])
            for _moment, card in reversed(dated):
                meta = card.get("metadata") or {}
                who = str(meta.get("created_by") or meta.get("draft_created_by") or "").strip()
                if who:
                    return who
            return last_editor

        default_status = rdecl["status"]["default"]
        active_seen = False
        for ms in roadmap["milestones"]:
            if lane and ms.get("lane") != lane:
                continue
            ms_id = str(ms["id"])
            status = str(ms.get("status") or default_status)
            active_seen = active_seen or status == "active"
            ms_cards = [c for c in linked if c.get("milestone") == ms_id]
            criteria_rows = []
            for crit in ms.get("criteria") or []:
                cid = str(crit["id"])
                crit_cards = [c for c in ms_cards if cid in (c.get("criteria") or [])]
                closed = [c for c in crit_cards if c.get("queue_state") in satisfied_states]
                met, reason, met_cards = False, "", []
                if not crit_cards:
                    reason = str(decl["unmet_reasons"]["no_card"])
                elif not closed:
                    reason = _fmt(decl["unmet_reasons"]["not_closed"], "unmet_reasons.not_closed", cards=_ids(crit_cards))
                else:
                    why: list[str] = []
                    for card in closed:
                        ok, text = _verify_card(root, card, _card_truth(root, card, errors, cache), str(crit["verify"]), decl)
                        if ok:
                            met_cards.append(card)
                        else:
                            why.append(text)
                    met = bool(met_cards)
                    reason = "" if met else "; ".join(why)
                rounds = sum(_card_truth(root, c, errors, cache)["rework_rounds"] for c in crit_cards) if crit_cards else 0
                criteria_rows.append({"id": cid, "statement": crit["statement"], "verify": crit["verify"], "satisfied": met,
                                      "reason": reason or None, "cards": [c["task_id"] for c in crit_cards],
                                      "met_cards": [c["task_id"] for c in met_cards], "rework_rounds": rounds,
                                      "closed_cards": [c["task_id"] for c in closed]})
            n_met = sum(1 for r in criteria_rows if r["satisfied"])
            n_total = len(criteria_rows)
            truths = [_card_truth(root, c, errors, cache) for c in ms_cards]
            activity = [t["last_activity"] for t in truths if t["last_activity"] is not None]
            last = max(activity) if activity else None
            # 里程碑声明状态与推导进度不一致(codes 供下一步建议, 文本供呈现)
            mm = decl["status_mismatch"]
            moving = [c for c in ms_cards if c.get("queue_state") != "pending"]
            codes: dict[str, str] = {}
            if status == "done" and n_met < n_total:
                codes["done_unmet"] = _fmt(mm["done_unmet"], "status_mismatch.done_unmet", met=n_met, total=n_total)
            if status == "planned" and moving:
                codes["planned_moving"] = _fmt(mm["planned_moving"], "status_mismatch.planned_moving", cards=_ids(moving))
            if status in ("planned", "active") and n_total and n_met == n_total:
                codes["met_not_done"] = _fmt(mm["met_not_done"], "status_mismatch.met_not_done", met=n_met, total=n_total)
            row = {"id": ms_id, "lane": ms["lane"], "title": ms["title"], "status": status, "target_date": ms.get("target_date"),
                   "progress": {"met": n_met, "total": n_total, "ratio": (n_met / n_total) if n_total else None},
                   "mismatch": list(codes.values()), "mismatch_codes": list(codes), "cards": [c["task_id"] for c in ms_cards],
                   "last_activity": _iso(last), "criteria": criteria_rows}
            out["milestones"].append(row)
            out["totals"]["met"] += n_met
            out["totals"]["total"] += n_total
            if status == "dropped":
                continue
            who = _advisor(ms_cards)
            base = {"governance_root": root, "advisor": who, "milestone": ms}
            # 逾期: target_date 已过且未 done
            target = _as_utc(ms.get("target_date"))
            if target is not None and status != "done" and today > target.date():
                days_over = (today - target.date()).days
                out["signals"].append(_signal(decl, "overdue", anchor=f"{ms_id}|{ms.get('target_date')}",
                                              subject=_inbox_subject("overdue", ms_id, None), target_date=ms.get("target_date"),
                                              days_over=days_over, status=status, met=n_met, total=n_total, **base))
            # 停滞: active、标准未全满足且挂接卡最近推进早于阈值(有卡读不出 = 不判; 已全满足只差标 done = 见状态不一致, 不算停滞)
            if (status == "active" and ms_cards and last is not None and not (n_total and n_met == n_total)
                    and not any(t.get("unreadable") for t in truths)):
                idle = (moment - last).days
                if moment - last > timedelta(days=int(thresholds["stall_days"])):
                    out["signals"].append(_signal(decl, "stalled", anchor=f"{ms_id}|{_iso(last)}",
                                                  subject=_inbox_subject("stalled", ms_id, None), cards=_ids(ms_cards),
                                                  last_at=_iso(last), idle=idle, threshold=thresholds["stall_days"],
                                                  met=n_met, total=n_total, **base))
            for crit_row in criteria_rows:
                # 无卡覆盖: active 里程碑下的标准无任何挂接卡
                if status == "active" and not crit_row["cards"]:
                    out["signals"].append(_signal(decl, "uncovered", anchor=crit_row["id"], criterion=crit_row["id"],
                                                  subject=_inbox_subject("uncovered", ms_id, crit_row["id"]),
                                                  statement=crit_row["statement"], met=n_met, total=n_total, **base))
                # 反复返工: 同标准挂接卡返工轮次合计 ≥ 阈值
                if crit_row["rework_rounds"] >= int(thresholds["rework_rounds"]):
                    out["signals"].append(_signal(decl, "rework", anchor=f"{crit_row['id']}|{crit_row['rework_rounds']}",
                                                  criterion=crit_row["id"], subject=f"标准 {crit_row['id']}",
                                                  cards=", ".join(crit_row["cards"]), rounds=crit_row["rework_rounds"],
                                                  threshold=thresholds["rework_rounds"], met=n_met, total=n_total, **base))

        # 游离卡占比(全项目; 审计卡不计: 审计卡由产品派生, 挂接看被审卡)
        from tools.aipos_cli.audit_derivation import is_audit_card

        window = int(thresholds["unlinked_window_days"])
        since = moment - timedelta(days=window)
        recent = [c for c in live if c["state"] in (LINK_LINKED, LINK_UNLINKED, LINK_INVALID)
                  and not is_audit_card(str(c.get("task_id") or ""), c.get("metadata") or {})
                  and (_card_created(c.get("metadata") or {}) or since) > since]
        free = [c for c in recent if c["state"] == LINK_UNLINKED]
        if recent:
            ratio = len(free) / len(recent)
            if ratio > float(thresholds["unlinked_ratio"]):
                out["signals"].append(_signal(
                    decl, "unlinked_ratio", governance_root=root, anchor=f"{today.isoformat()}", subject="游离卡占比",
                    advisor=last_editor, days=window, total=len(recent), unlinked=len(free), ratio=round(ratio * 100),
                    threshold=round(float(thresholds["unlinked_ratio"]) * 100), sample=_sample([str(c["task_id"]) for c in free])))
        # 路线图近期被改(decision_log 指针 Record = 路线图文件)
        change_days = int(thresholds["change_window_days"])
        recent_changes = [e for e in changes if (_as_utc(e.get("decided_at")) or datetime.min.replace(tzinfo=timezone.utc))
                          > moment - timedelta(days=change_days)]
        if recent_changes:
            last_change = recent_changes[-1]
            out["signals"].append(_signal(
                decl, "roadmap_changed", governance_root=root, anchor=str(last_change["path"]), subject="路线图变更", advisor=last_editor,
                days=change_days, count=len(recent_changes), last_at=last_change.get("decided_at") or "?",
                last_by=last_change.get("decided_by") or "?", last_summary=last_change.get("summary") or "?",
                last_path=last_change["path"]))
        out["next_steps"] = _next_steps(decl, out, root, active_seen)
    return out


def _inbox_subject(kind: str, milestone: str, criterion: str | None) -> str:
    """收件箱事项主体(声明 lybra_owner_inbox.categories.roadmap_drift.subject_by_kind; 非收件箱信号 = 种类 + 主体 id)。"""
    from tools.aipos_cli.owner_inbox import inbox_declaration

    table = inbox_declaration()["categories"]["roadmap_drift"]["subject_by_kind"]
    if kind in table:
        return _fmt(table[kind], f"owner_inbox.categories.roadmap_drift.subject_by_kind.{kind}", milestone=milestone,
                    criterion=criterion or "")
    return f"{kind} {criterion or milestone}"


def _next_steps(decl: dict[str, Any], view: dict[str, Any], root: Path, active_seen: bool) -> list[str]:
    steps = decl["next_steps"]
    out: list[str] = []
    gr = str(root)
    for ms in view["milestones"]:
        if ms["status"] == "dropped":
            continue
        unmet = [r for r in ms["criteria"] if not r["satisfied"]]
        moving = [r for r in unmet if r["cards"] and not r["closed_cards"]]
        waiting = [r for r in unmet if r["closed_cards"]]
        if ms["status"] == "active" and moving:
            out.append(_fmt(steps["push"], "next_steps.push", milestone=ms["id"], criteria=", ".join(r["id"] for r in moving),
                            cards=", ".join(dict.fromkeys(c for r in moving for c in r["cards"]))))
        if waiting:
            out.append(_fmt(steps["pending_verify"], "next_steps.pending_verify", milestone=ms["id"],
                            criteria=", ".join(r["id"] for r in waiting)))
        if ms["progress"]["total"] and not unmet and ms["status"] != "done":
            out.append(_fmt(steps["mark_done"], "next_steps.mark_done", milestone=ms["id"], governance_root=gr))
        if "planned_moving" in ms["mismatch_codes"]:
            out.append(_fmt(steps["mark_active"], "next_steps.mark_active", milestone=ms["id"], governance_root=gr))
    if not active_seen:
        planned = next((m for m in view["milestones"] if m["status"] == "planned"), None)
        if planned is not None:
            out.append(_fmt(steps["activate"], "next_steps.activate", milestone=planned["id"], governance_root=gr))
    for sig in view["signals"]:
        if sig["severity"] == "inbox":  # 严重偏差点名进下一步(提示类只在信号段给建议, 不重复)
            out.append(_fmt(steps["act_on_signal"], "next_steps.act_on_signal", label=sig["label"], subject=sig["subject"]))
    if not out:
        out.append(str(steps["none"]))
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# 渲染(CLI 文本; --json 原样输出 roadmap_status)
# ---------------------------------------------------------------------------

def _bar(met: int, total: int, width: int) -> str:
    filled = (width * met) // total if total else 0
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _threshold_text(thresholds: dict[str, Any]) -> str:
    source = thresholds.get("_source") or {}
    return ", ".join(f"{k}={v}{'' if source.get(k) == 'project.json' else '(缺省)'}" for k, v in thresholds.items() if k != "_source")


def render_status_text(view: dict[str, Any]) -> str:
    from tools.aipos_cli.roadmap import roadmap_texts

    if not view["exists"]:
        return roadmap_texts()["no_roadmap"].format(project=view.get("project") or "-", path=view["path"])
    decl = status_declaration()
    texts = decl["texts"]
    width = int(decl["progress_bar_width"])
    lane_part = _fmt(texts["lane_part"], "texts.lane_part", lane=view["lane"]) if view.get("lane") else ""
    lines = [_fmt(texts["header"], "texts.header", project=view.get("project") or "-", path=view["rel"], lane_part=lane_part,
                  milestones=len(view["milestones"]), met=view["totals"]["met"], total=view["totals"]["total"], today=view["today"])]
    for ms in view["milestones"]:
        target_part = _fmt(texts["target_part"], "texts.target_part", target_date=ms["target_date"]) if ms.get("target_date") else ""
        lines.append(_fmt(texts["milestone"], "texts.milestone", lane=ms["lane"], id=ms["id"], status=ms["status"],
                          target_part=target_part, title=ms["title"]))
        mismatch_part = "".join(_fmt(texts["mismatch_part"], "texts.mismatch_part", mismatch=m) for m in ms["mismatch"])
        lines.append(_fmt(texts["progress"], "texts.progress", bar=_bar(ms["progress"]["met"], ms["progress"]["total"], width),
                          met=ms["progress"]["met"], total=ms["progress"]["total"], mismatch_part=mismatch_part))
        for crit in ms["criteria"]:
            key = "criterion_met" if crit["satisfied"] else "criterion_unmet"
            lines.append(_fmt(texts[key], f"texts.{key}", id=crit["id"], verify=crit["verify"], statement=crit["statement"],
                              cards=", ".join(crit["met_cards"]), reason=crit["reason"] or ""))
    thresholds = _threshold_text(view["thresholds"] or {})
    if view["signals"]:
        lines.append(_fmt(texts["signals_header"], "texts.signals_header", count=len(view["signals"]), thresholds=thresholds))
        for sig in view["signals"]:
            lines.append(_fmt(texts["signal"], "texts.signal", **sig))
            lines.append(_fmt(texts["advice"], "texts.advice", advice=sig["advice"]))
    else:
        lines.append(_fmt(texts["no_signals"], "texts.no_signals", thresholds=thresholds))
    for bad in view["invalid"]:
        lines.append(f"  [挂接不合] {bad['task_id']}: {'; '.join(bad['problems'])}")
    for bad in view["unreadable_cards"]:
        lines.append(f"  [卡面读不出] {bad['path']}: {bad['reason']}")
    lines.append(str(texts["next_header"]))
    lines.extend(_fmt(texts["next_line"], "texts.next_line", line=step) for step in view["next_steps"])
    if view["errors"]:
        lines.append(_fmt(texts["errors_header"], "texts.errors_header", count=len(view["errors"])))
        lines.extend(_fmt(texts["error"], "texts.error", **err) for err in view["errors"])
    return "\n".join(lines)


def json_view(view: dict[str, Any]) -> dict[str, Any]:
    """--json / 看板 API 输出形(Path → 文本)。"""
    return {k: (str(v) if isinstance(v, Path) else v) for k, v in view.items()}


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
