"""AIPOS-F173 — 路线图偏差推导(唯一实现; 只读适配器)。

一句话: 根据路线图声明与卡、门记录、loop 运行记录、审计裁决, 算出每个里程碑的进度(标准满足数 / 总数)与偏差信号
(游离卡占比 / 停滞 / 无卡覆盖的标准 / 反复返工 / 逾期 / 路线图近期被改), 给每条一句人话原因与给顾问的命令建议。

防碎片化(卡面硬约束):
- 本模块是推导的唯一实现: CLI `lybra roadmap status`(aipos_cli 薄壳)、Owner 收件箱 roadmap_drift 类别(owner_inbox._roadmap_items)、
  看板(board_adapter.get_roadmap_status; AIPOS-F174 只读页经 board_pages 同读)同读 roadmap_status; 每条标准的挂接卡审计证据
  (裁决 / 补审 / 运行证据摘录)由 card_evidence 随行带出(criteria[].evidence), 看板只渲染。
- 只读既有真相, 不另记进度: 路线图文件(roadmap.load_roadmap)、卡挂接(roadmap.card_links, 游离判定 card_link_status 不另写)、
  门记录(next_resolver._read_task_records: 裁决 / 补审裁决 / 认领 / 交回 / 结案)、卡面 rework_rounds、运行证据
  (artifact_ingest.runtime_evidence_requirement + parse_runtime_evidence_section)、loop 运行记录(loop_run_record.read_run)、
  decision_log 指针(decision_log_pointers.pointer_index)。
- 阈值读 project.json roadmap_drift(唯一读取口 workspace_config.roadmap_drift_thresholds, 缺省见 config.schema); 文案 / 信号种类 /
  严重度 / 建议命令读 verbs.schema lybra_roadmap.status(status_declaration 唯一读取口), 禁写死。
- 任何项目同一组命令: 本模块不写死项目 ID / 路径 / 卡号前缀。

AIPOS-F175 件①(验证状态五态并入本推导, 不另写): 每条标准的验证状态(未验证 / 自动验证通过 / 待 Owner 抽查 / Owner 已确认 / 不符)
在本模块 _criterion_verification 一处算出, 只读既有真相: 挂接卡审计证据(card_evidence)+ 卡面产物(roadmap.card_artifacts, 声明
card.schema card_artifacts)+ Owner 抽查记录(既有 owner_verifications 记录, 经 records.load_records 唯一读取; 写口 = `lybra roadmap verify`
即 roadmap.apply_owner_verification)。文案 / 状态名 / 满足口径读 verbs.schema lybra_roadmap.status.verification。CLI、收件箱 owner_check 类别、
看板路线图页同读本推导结果(criteria[].state / basis / verifications / fix_cards / evidence[].artifacts)。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: 信号种类(与 verbs.schema lybra_roadmap.status.signals 键同集; 声明读取处核对)
SIGNAL_KINDS = ("unlinked_ratio", "stalled", "uncovered", "rework", "overdue", "roadmap_changed")
#: AIPOS-F175: verification states (keys of verbs.schema lybra_roadmap.status.verification.states; checked where declared)
VERIFICATION_STATES = ("unverified", "auto_passed", "owner_pending", "owner_confirmed", "mismatch")
_BASIS_KEYS = ("passed", "evidence_part", "artifact_part", "owner_pending", "owner_record", "refix_pending", "fix_part", "fix_none",
               "fix_draft", "fix_card", "fix_pending", "corrected", "followup_part", "conclusion_passed")
#: AIPOS-F209: 顾问复核结案卡(结案口径 task_complexity.closure_state kind = conclusion)核对用的未满足原因键(verbs lybra_roadmap.status.unmet_reasons)
_CONCLUSION_REASON_KEYS = ("conclusion_block", "conclusion_needs_audit", "closure_unreadable")
_STATUS_KEYS = ("progress_bar_width", "satisfied_queue_states", "excluded_queue_states", "verification", "unmet_reasons",
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
    ver = decl["verification"]
    if (not isinstance(ver, dict) or set(ver.get("states") or {}) != set(VERIFICATION_STATES)
            or not isinstance(ver.get("satisfied_state"), dict) or set(ver["satisfied_state"]) != set(verify_enum)
            or not set(ver["satisfied_state"].values()) <= set(VERIFICATION_STATES)
            or not set(ver.get("needs_artifacts") or []) <= set(verify_enum)
            or any(not isinstance((ver.get("basis") or {}).get(k), str) for k in _BASIS_KEYS)
            or not isinstance(ver.get("independent_audit"), list) or not set(ver["independent_audit"]) <= set(verify_enum)):
        raise SchemaLoadError(f"verbs.lybra_roadmap.status.verification 须有 states(恰为 {list(VERIFICATION_STATES)})/ satisfied_state"
                              f"(键 = verify 值域 {verify_enum})/ needs_artifacts / independent_audit(⊆ verify 值域)/ basis{list(_BASIS_KEYS)}")
    if any(not isinstance((decl.get("unmet_reasons") or {}).get(k), str) for k in _CONCLUSION_REASON_KEYS):
        raise SchemaLoadError(f"verbs.lybra_roadmap.status.unmet_reasons 须有 {list(_CONCLUSION_REASON_KEYS)}(AIPOS-F209)")
    return decl


def _pass_verdicts() -> Any:
    """PASS 类裁决值域(next_resolver._PASS_VERDICTS 唯一声明的只读投影)。"""
    from tools.aipos_cli.next_resolver import _PASS_VERDICTS

    return _PASS_VERDICTS


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
    最近推进 = 卡面 *_at / 返工节 created_at / 门记录 *_at / loop 运行记录 *_at 的最晚者。
    AIPOS-F249 件③: 这里的 last_activity 不含 loop 运行记录(运行中每几秒一改): 运行记录的读得出与否经 _card_run_error(稳定), 其 *_at 由
    _with_run_activity 在里程碑一级并入(_card_run_stamps 同一读法), 结果与原来逐字节一致; 看板服务进程内两者逐卡缓存。"""
    task_id = str(card.get("task_id") or "")
    if task_id in cache:
        return cache[task_id]
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.loop_run_record import LoopRunRecordError
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
        run_error = _card_run_error(root, task_id)
    except (OSError, ValueError, LookupError, FrontmatterReadError, LoopRunRecordError) as exc:
        run_error = f"{type(exc).__name__}: {exc}"
    if run_error is not None:
        out["unreadable"] = run_error
        errors.append({"source": f"卡 {task_id} 的门记录 / 运行记录", "error": out["unreadable"]})
    found = [s for s in stamps if s is not None]
    out["last_activity"] = max(found) if found else None
    cache[task_id] = out
    return out


def _card_run_stamps(root: Path, task_id: str) -> dict[str, Any]:
    """AIPOS-F249 件③: 一张卡 loop 运行记录的 *_at(唯一读法): {latest: 读到的各运行记录 *_at 的最晚者(读不出那份之前读到的照计), error:
    第一份读不出的「异常类: 原文」| None}。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecordError, _run_files, loop_runs_dir, read_run, run_record_declaration

    stamps: list[datetime] = []
    error = None
    try:
        rdecl = run_record_declaration(load_loop_contract())
        for path in _run_files(loop_runs_dir(root, task_id)):
            stamp = _latest_at(read_run(path, rdecl))
            if stamp is not None:
                stamps.append(stamp)
    except (OSError, ValueError, LookupError, FrontmatterReadError, LoopRunRecordError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    return {"latest": max(stamps) if stamps else None, "error": error}


def _card_run_error(root: Path, task_id: str) -> str | None:
    """AIPOS-F249 件③: 运行记录读不出的原文(_card_run_stamps.error); 看板服务进程内逐卡缓存: 运行中改写只重判这张卡, 读得出 = 值不变,
    路线图的其余推导不重算。"""
    from tools.aipos_cli.task_loader import truth_cached

    return truth_cached(("roadmap_drift._card_run_error", str(root), str(task_id)), lambda: _card_run_stamps(root, task_id)["error"])


def _card_run_latest(root: Path, task_id: str) -> datetime | None:
    """AIPOS-F249 件③: 运行记录 *_at 的最晚者(_card_run_stamps.latest); 看板服务进程内逐卡缓存(运行中每次改写都会变, 只重读这张卡)。"""
    from tools.aipos_cli.task_loader import truth_cached

    return truth_cached(("roadmap_drift._card_run_latest", str(root), str(task_id)), lambda: _card_run_stamps(root, task_id)["latest"])


def card_evidence(root: Path, card: dict[str, Any], truth: dict[str, Any], criterion: str | None = None) -> dict[str, Any]:
    """挂接卡的审计证据(唯一读取; _verify_card 判定与看板「路线图与验收」页证据展示(AIPOS-F174)同读, 看板不另读报告):
    最新常规审计裁决 / 补审裁决(门记录 truth.records)+ 运行证据要求(artifact_ingest.runtime_evidence_requirement)+ 裁决报告运行证据节
    (evidence_refs 指向的报告经 parse_runtime_evidence_section 解析; 取第一份解析出非空运行记录的报告)。
    返回 {task_id, queue_state, verdict, verdict_id, verdict_at, supplementary_verdict, supplementary_verdict_id, unreadable,
    evidence_required(True / False / None = 声明不合), declaration_problem, report_ref, evidence(运行记录列表 | None), evidence_problem,
    artifacts}。AIPOS-F175: criterion 给出 = artifacts 为该卡卡面声明的该标准产物(roadmap.card_artifacts 唯一解析; 看板缩略图 / 摘录同读),
    否则 None。只读。"""
    from tools.aipos_cli.artifact_ingest import (
        RuntimeEvidenceDeclarationError, parse_runtime_evidence_section, runtime_evidence_requirement,
    )

    records = truth.get("records") or {}
    verdict = records.get("latest_verdict") or {}
    supp = records.get("latest_supplementary_verdict") or {}
    out: dict[str, Any] = {
        "task_id": str(card.get("task_id") or ""), "queue_state": card.get("queue_state"),
        "verdict": str(verdict.get("verdict") or "").upper() or None, "verdict_id": verdict.get("verdict_id"),
        "verdict_at": _iso(_as_utc(verdict.get("verdict_at"))) if verdict.get("verdict_at") else None,
        "supplementary_verdict": str(supp.get("verdict") or "").upper() or None, "supplementary_verdict_id": supp.get("verdict_id"),
        "unreadable": truth.get("unreadable"), "evidence_required": None, "declaration_problem": None, "report_ref": None,
        "evidence": None, "evidence_problem": None, "artifacts": None, "criterion": criterion,
    }
    if criterion is not None:
        from tools.aipos_cli.roadmap import card_artifacts

        out["artifacts"] = card_artifacts(root, card.get("metadata") or {"task_id": out["task_id"]}, criterion=criterion)
    if truth.get("unreadable"):
        return out
    try:
        out["evidence_required"] = runtime_evidence_requirement(None, card.get("metadata") or {}) is not None
    except RuntimeEvidenceDeclarationError as exc:
        out["declaration_problem"] = str(exc)
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
            out["report_ref"], out["evidence"], problem = str(ref), entries, ""
            break
        problem = problem or f"报告 {ref} 运行证据节为空"
    out["evidence_problem"] = problem or None
    return out


def card_closure(root: Path, card: dict[str, Any], errors: list[dict[str, str]], cache: dict[str, Any]) -> dict[str, Any] | None:
    """AIPOS-F209: 挂接卡的结案口径——唯一判定 task_complexity.closure_state(是否结案 / 结案种类 / 结论), 本模块不另判。
    卡文件已由 card_links 查到 = 作 located 传入(免重查)。读不出 = 点名进 errors, 返回 None(不当作已结案, 不猜)。"""
    task_id = str(card.get("task_id") or "")
    key = f"__closure__:{task_id}"
    if key in cache:
        return cache[key]
    from tools.aipos_cli.task_complexity import closure_state

    rel = card.get("path")
    located = (root / str(rel), card.get("queue_state")) if rel else None
    try:
        state: dict[str, Any] | None = closure_state(root, task_id, located=located)
    except (OSError, ValueError) as exc:  # FrontmatterReadError / AmbiguousTaskCard ⊂ ValueError
        state = None
        errors.append({"source": f"卡 {task_id} 的结案口径(task_complexity.closure_state)", "error": f"{type(exc).__name__}: {exc}"})
    cache[key] = state
    return state


def _verify_card(root: Path, card: dict[str, Any], truth: dict[str, Any], verify: str, decl: dict[str, Any],
                 evidence: dict[str, Any] | None = None, closure: dict[str, Any] | None = None) -> tuple[bool, str]:
    """已结案挂接卡是否达到自动验证要求 → (通过, 未通过原因)。
    全部方式: 最新常规审计裁决属 PASS 类、补审未判 FAIL、卡声明了运行证据时裁决报告有运行证据节;
    AIPOS-F209: 无 PASS 类裁决而结案口径(closure = task_complexity.closure_state)为顾问复核结案(kind = conclusion)= 按结论与标准
    verify 方式判——结论不满足(block)= conclusion_block; verify ∈ verification.independent_audit(须独立审计)= conclusion_needs_audit
    (明确提示补充审计, 不静默; 补充审计裁决属 PASS 类 = 已独立审计); 否则(结论 pass 且非独立审计标准, 或已补审 PASS)代替常规审计 PASS,
    常规裁决报告的运行证据节不适用, 产物 / 补审 FAIL 照判;
    AIPOS-F175: verification.needs_artifacts 所列方式(artifact / owner_check)另需卡面 artifacts 声明了该标准的产物且每个 path_glob 在卡产物
    落点至少匹配一个文件(缺什么写进原因)。owner_check 的 Owner 结论不在此判(见 _criterion_verification)。
    证据读取唯一口 card_evidence(evidence 已算好时直接传入, 不重读)。"""
    from tools.aipos_cli.audit_derivation import supplementary_audit_declaration
    from tools.aipos_cli.next_resolver import _PASS_VERDICTS

    reasons = decl["unmet_reasons"]
    task_id = str(card.get("task_id") or "")
    if truth.get("unreadable"):
        return False, _fmt(reasons["unreadable"], "unmet_reasons.unreadable", task_id=task_id, problem=truth["unreadable"])
    ev = evidence if evidence is not None else card_evidence(root, card, truth)
    value = ev["verdict"] or ""
    by_conclusion = False
    if value not in _PASS_VERDICTS:
        view = (closure or {}).get("conclusion") if (closure or {}).get("kind") == "conclusion" else None
        if view is None:
            return False, _fmt(reasons["no_pass"], "unmet_reasons.no_pass", task_id=task_id, verdict=value or "无裁决记录")
        if not view["satisfies"]:
            return False, _fmt(reasons["conclusion_block"], "unmet_reasons.conclusion_block", task_id=task_id, outcome=view["outcome"],
                               source=view["source"])
        supp_pass = (ev["supplementary_verdict"] or "") in _PASS_VERDICTS  # 补充审计 PASS = 已有独立审计(F162 出口)
        if verify in decl["verification"]["independent_audit"] and not supp_pass:
            return False, _fmt(reasons["conclusion_needs_audit"], "unmet_reasons.conclusion_needs_audit", task_id=task_id,
                               outcome=view["outcome"], verify=verify, criterion=str(ev.get("criterion") or "?"),
                               governance_root=str(root))
        by_conclusion = True
    supp_value = ev["supplementary_verdict"] or ""
    if supp_value and supp_value in {str(v).upper() for v in supplementary_audit_declaration()["fail_verdicts"]}:
        return False, _fmt(reasons["supplementary_fail"], "unmet_reasons.supplementary_fail", task_id=task_id, verdict=supp_value,
                           verdict_id=str(ev["supplementary_verdict_id"] or "?"))
    if ev["declaration_problem"]:
        return False, _fmt(reasons["evidence_declaration"], "unmet_reasons.evidence_declaration", task_id=task_id,
                           problem=ev["declaration_problem"])
    if ev["evidence_required"] and ev["evidence_problem"] and not by_conclusion:
        return False, _fmt(reasons["no_evidence"], "unmet_reasons.no_evidence", task_id=task_id, problem=ev["evidence_problem"])
    if verify in decl["verification"]["needs_artifacts"]:
        art = ev.get("artifacts") or {}
        if art.get("problems"):
            return False, _fmt(reasons["artifact_invalid"], "unmet_reasons.artifact_invalid", task_id=task_id,
                               problem="; ".join(art["problems"]))
        if not art.get("entries"):
            return False, _fmt(reasons["no_artifacts"], "unmet_reasons.no_artifacts", task_id=task_id,
                               criterion=str(ev.get("criterion") or "?"), verify=verify)
        missing = [f"[{e['kind']}] {e['path_glob']}" for e in art["entries"] if not e["files"]]
        if missing:
            return False, _fmt(reasons["artifact_missing"], "unmet_reasons.artifact_missing", task_id=task_id,
                               dir=str(art.get("dir_rel") or art.get("dir")), missing=", ".join(missing))
    return True, ""


# ---------------------------------------------------------------------------
# AIPOS-F175 件①: 验证状态五态(唯一实现; 只读)
# ---------------------------------------------------------------------------

def _owner_records(root: Path, task_id: str, criterion: str, results: dict[str, Any], errors: list[dict[str, str]]) -> tuple[list[dict[str, Any]], str | None]:
    """该卡针对该标准的 Owner 抽查记录(既有 owner_verifications 记录类型, records.load_records 唯一读取)→ (按裁决时刻旧→新, 读不出原因)。
    记录读不出 = 点名进 errors 且返回原因(调用方不当作已确认, 不猜)。"""
    from tools.aipos_cli.records import load_records

    try:
        recs = load_records(root, groups=["owner_verifications"], task_id=task_id).get("owner_verifications") or []
    except (OSError, ValueError) as exc:
        problem = f"{type(exc).__name__}: {exc}"
        errors.append({"source": f"卡 {task_id} 的 Owner 抽查记录", "error": problem})
        return [], problem
    out: list[dict[str, Any]] = []
    problem = None
    for rec in recs:
        meta = rec.get("metadata") or {}
        if rec.get("parse_errors"):
            problem = f"{rec.get('path')}: {'; '.join(rec['parse_errors'])}"
            errors.append({"source": f"卡 {task_id} 的 Owner 抽查记录", "error": problem})
            continue
        if str(meta.get("criterion") or "") != criterion or str(meta.get("result") or "") not in results:
            continue
        out.append({"record_id": rec.get("record_id"), "path": rec.get("path"), "task_id": task_id, "result": str(meta["result"]),
                    "label": str(results[str(meta["result"])]["label"]), "decided_at": str(meta.get("decided_at") or ""),
                    "decided_by": meta.get("decided_by"), "relayed_by": meta.get("relayed_by"), "owner_words": meta.get("owner_words"),
                    "note": meta.get("note"), "fix_task_id": meta.get("fix_task_id")})
    out.sort(key=lambda r: (r["decided_at"], str(r["record_id"])))
    return out, problem


def _closed_at(card: dict[str, Any]) -> datetime:
    meta = card.get("metadata") or {}
    return _as_utc(meta.get("completed_at")) or _card_created(meta) or datetime.min.replace(tzinfo=timezone.utc)


def _fix_cards(root: Path, latest: dict[str, Any], all_cards: list[dict[str, Any]], basis: dict[str, Any]) -> list[dict[str, Any]]:
    """不符记录的修复卡: 记录 fix_task_id + 卡面 owner_check_ref = 该记录 id 的卡; 在队列 = 队列状态, 只在草稿 = 草稿待发布,
    都不在(自动起草被拒, AIPOS-F180C)= 待出。"""
    from tools.aipos_cli.draft_validator import expected_draft_relative_path

    by_id = {str(c.get("task_id")): c for c in all_cards}
    ids = [str(latest["fix_task_id"])] if latest.get("fix_task_id") else []
    ids += [str(c.get("task_id")) for c in all_cards
            if str((c.get("metadata") or {}).get("owner_check_ref") or "") == str(latest.get("record_id"))]
    out = []
    for task_id in dict.fromkeys(ids):
        card = by_id.get(task_id)
        if card is not None:
            out.append({"task_id": task_id, "queue_state": card.get("queue_state"), "draft": False,
                        "text": _fmt(basis["fix_card"], "verification.basis.fix_card", task_id=task_id, queue_state=card.get("queue_state"))})
        elif (root / expected_draft_relative_path(task_id)).is_file():
            out.append({"task_id": task_id, "queue_state": None, "draft": True,
                        "text": _fmt(basis["fix_draft"], "verification.basis.fix_draft", task_id=task_id)})
        else:
            out.append({"task_id": task_id, "queue_state": None, "draft": False, "pending": True,
                        "text": _fmt(basis["fix_pending"], "verification.basis.fix_pending", task_id=task_id)})
    return out


def _criterion_verification(root: Path, crit: dict[str, Any], crit_cards: list[dict[str, Any]], closed: list[dict[str, Any]],
                            evidence: dict[str, dict[str, Any]], all_cards: list[dict[str, Any]], decl: dict[str, Any],
                            errors: list[dict[str, str]], cache: dict[str, Any]) -> dict[str, Any]:
    """一条标准的验证状态(五态唯一判定)。返回 {satisfied, state, state_label, reason, basis, met_cards, qualified_cards, verify_target,
    owner_verification, verifications, fix_cards}。qualified = 已结案且自动验证通过(含产物)的挂接卡; owner_check 再看 Owner 抽查记录。"""
    from tools.aipos_cli.roadmap import verify_declaration

    ver = decl["verification"]
    basis_t = ver["basis"]
    reasons = decl["unmet_reasons"]
    verify = str(crit["verify"])
    cid = str(crit["id"])
    out: dict[str, Any] = {"satisfied": False, "state": "unverified", "state_label": "", "reason": None, "basis": None,
                           "met_cards": [], "qualified_cards": [], "verify_target": None, "verify_target_by": None, "owner_verification": None,
                           "verifications": [], "fix_cards": []}
    qualified: list[dict[str, Any]] = []
    passed: list[str] = []
    why: list[str] = []
    corrections: list[tuple[dict[str, Any], str]] = []  # AIPOS-F207 件②: (Owner 纠正争议标记, 依据文本)
    for card in crit_cards:  # AIPOS-F209: 结案口径读不出的挂接卡点名(不当作已结案, 不猜)
        if card_closure(root, card, errors, cache) is None:
            why.append(_fmt(reasons["closure_unreadable"], "unmet_reasons.closure_unreadable", task_id=str(card.get("task_id") or "")))
    if not crit_cards:
        why.append(str(reasons["no_card"]))
    elif not closed:
        why.append(_fmt(reasons["not_closed"], "unmet_reasons.not_closed", cards=_ids(crit_cards)))
    for card in closed:
        tid = str(card["task_id"])
        disputes = _card_disputes(root, tid, errors)
        if disputes:  # AIPOS-F207 件②: Owner 纠正过的结案不再算验证通过(原记录不改; 争议标记 = 新输入)
            text = _fmt(basis_t["corrected"], "verification.basis.corrected", task_id=tid, decided_at=disputes[-1].get("marked_at") or "?",
                        relayed_by=disputes[-1].get("relayed_by") or "?", owner_words=disputes[-1].get("owner_words") or "",
                        decision_id=disputes[-1].get("decision_id") or "?")
            corrections.append((disputes[-1], text))  # 依据只进 detail 一次(下方按状态拼承接卡), 不另进 why
            continue
        ev = evidence[tid]
        closure = card_closure(root, card, errors, cache)
        ok, text = _verify_card(root, card, _card_truth(root, card, errors, cache), verify, decl, ev, closure)
        if not ok:
            why.append(text)
            continue
        qualified.append(card)
        files = [f["rel"] for e in ((ev.get("artifacts") or {}).get("entries") or []) for f in e["files"]]
        artifact_part = (_fmt(basis_t["artifact_part"], "verification.basis.artifact_part", files=", ".join(files)) if files else "")
        if (ev.get("verdict") or "") not in _pass_verdicts():  # AIPOS-F209: 经顾问复核结案(结论 pass)满足
            view = (closure or {}).get("conclusion") or {}
            passed.append(_fmt(basis_t["conclusion_passed"], "verification.basis.conclusion_passed", task_id=tid,
                               outcome=view.get("outcome") or "?", note=str(view.get("note") or "")[:120], artifact_part=artifact_part))
            continue
        passed.append(_fmt(basis_t["passed"], "verification.basis.passed", task_id=tid, verdict=ev.get("verdict") or "?",
                           verdict_id=ev.get("verdict_id") or "?",
                           evidence_part=_fmt(basis_t["evidence_part"], "verification.basis.evidence_part", report_ref=ev["report_ref"])
                           if ev.get("report_ref") else "",
                           artifact_part=artifact_part))
    out["qualified_cards"] = [str(c["task_id"]) for c in qualified]
    state = "unverified"
    detail: list[str] = list(passed)
    if not qualified and corrections:
        # AIPOS-F207 件②: 唯一的结案被 Owner 纠正 = 不符·待承接(承接卡 = 争议标记 followup_task_id 或卡面 owner_check_ref = 决策 id 的卡)
        state = "mismatch"
        latest_fix, text = corrections[-1]
        out["fix_cards"] = _fix_cards(root, {"fix_task_id": latest_fix.get("followup_task_id"), "record_id": latest_fix.get("decision_id")},
                                      all_cards, basis_t)
        fix = ", ".join(f["text"] for f in out["fix_cards"])
        detail.append(text + (_fmt(basis_t["followup_part"], "verification.basis.followup_part", fix=fix) if fix else ""))
    elif qualified and verify != "owner_check":
        state = "auto_passed"
        detail += [text for _fix, text in corrections]
    elif qualified:
        detail += [text for _fix, text in corrections]
        results = verify_declaration()["results"]
        records: list[dict[str, Any]] = []
        unchecked: list[dict[str, Any]] = []
        unreadable: list[str] = []
        for card in qualified:
            got, problem = _owner_records(root, str(card["task_id"]), cid, results, errors)
            records += got
            if problem:
                unreadable.append(_fmt(reasons["verification_unreadable"], "unmet_reasons.verification_unreadable",
                                       task_id=str(card["task_id"]), problem=problem))
            if not got:
                unchecked.append(card)
        # same-second records: the one on the later-closed card (the newer deliverable) counts as the latest
        closed_at = {str(c["task_id"]): _closed_at(c) for c in qualified}
        records.sort(key=lambda r: (r["decided_at"], closed_at[r["task_id"]], str(r["record_id"])))
        out["verifications"] = records
        latest = records[-1] if records else None
        out["owner_verification"] = latest
        target_card = max(unchecked or qualified, key=_closed_at)
        target_meta = target_card.get("metadata") or {}
        out["verify_target"] = str(target_card["task_id"])
        out["verify_target_by"] = str(target_meta.get("created_by") or target_meta.get("draft_created_by") or "") or None
        if latest is None or (latest["result"] == "mismatch" and unchecked):
            state = "owner_pending"
            key = "owner_pending" if latest is None else "refix_pending"
            detail.append(_fmt(basis_t[key], f"verification.basis.{key}", cards=_ids(unchecked)))
        else:
            state = str(results[latest["result"]]["state"])
            record_text = _fmt(basis_t["owner_record"], "verification.basis.owner_record", label=latest["label"],
                               decided_at=latest["decided_at"] or "?", relayed_by=latest.get("relayed_by") or "?",
                               owner_words=latest.get("owner_words") or "", record=latest.get("path") or "?")
            if latest["result"] == "mismatch":
                out["fix_cards"] = _fix_cards(root, latest, all_cards, basis_t)
                fix = ", ".join(f["text"] for f in out["fix_cards"])
                record_text += (_fmt(basis_t["fix_part"], "verification.basis.fix_part", fix=fix) if fix else str(basis_t["fix_none"]))
            detail.append(record_text)
        detail += unreadable
    out["state"] = state
    out["state_label"] = str(ver["states"][state])
    out["satisfied"] = state == ver["satisfied_state"][verify]
    out["met_cards"] = out["qualified_cards"] if out["satisfied"] else []
    out["basis"] = "; ".join(detail) or None
    if not out["satisfied"]:
        extra = [] if state == "unverified" else [d for d in detail[len(passed):] if d not in why]
        out["reason"] = "; ".join(why + extra) or None
    return out


def _card_disputes(root: Path, task_id: str, errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """AIPOS-F207 件②: 卡的 Owner 纠正争议标记(task_complexity.closure_disputes 唯一读取)。读不出 = 点名进 errors 且按「有争议」处理
    (返回一条占位, 不当作已验证, 不猜)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.task_complexity import closure_disputes

    try:
        return closure_disputes(root, task_id)
    except (OSError, ValueError, FrontmatterReadError) as exc:
        problem = f"{type(exc).__name__}: {exc}"
        errors.append({"source": f"卡 {task_id} 的 Owner 纠正争议标记", "error": problem})
        return [{"decision_id": None, "followup_task_id": None, "owner_words": problem, "relayed_by": None, "marked_at": None}]



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
    unlinked(AIPOS-F181: 只含该 lane 建路线图之后出的游离卡), unlinked_before_roadmap(之前的历史游离卡数), invalid, unreadable_cards, errors, totals}。无路线图 = exists False(调用方给提示, 不报错)。路线图读不出 = RoadmapError;
    阈值不合 = RoadmapDriftThresholdError; 声明缺 = SchemaLoadError(都向上, 调用方点名)。某卡门记录读不出 = 进 errors, 相关标准按未满足、
    相关里程碑不判停滞(不猜)。"""
    from tools.aipos_cli.task_loader import lookup_scope_memo

    root = Path(governance_root)
    if now is None:
        # AIPOS-F177 件③: 只读作用域(请求级 / 视图级)内同一治理根同一 lane / project 只推导一次, 各调用方拿深拷贝; 作用域外照旧每次现算
        # AIPOS-F245 件②: 看板服务进程内另按真相指纹缓存(task_loader.truth_cached, 分钟粒度): 别的项目 / 卡的真相变了, 本项目路线图不重推导
        from tools.aipos_cli.task_loader import truth_cached

        return truth_cached(("roadmap_drift.roadmap_status", str(root), lane, project), lambda: lookup_scope_memo(
            root, f"roadmap_drift.roadmap_status:{lane!r}:{project!r}", lambda: _roadmap_view(root, lane, project, None), copy_out=True))
    return _roadmap_view(root, lane, project, now)


def roadmap_progress(governance_root: str | Path, *, lane: str | None = None, project: str | None = None) -> dict[str, Any]:
    """AIPOS-F249 件③: roadmap_status(同一推导)去掉各里程碑的 last_activity——loop 在跑时运行记录每几秒改写, 只这一项随之变(由它判出的
    停滞信号照留)。收件箱(路线图偏差 / 待抽查)与看板首页 / 项目页只读本投影: 看板服务进程内经真相指纹缓存, 运行记录改写重算路线图但投影
    不变 = 它们不重算。"""
    from tools.aipos_cli.task_loader import truth_cached

    root = Path(governance_root)

    def compute() -> dict[str, Any]:
        view = roadmap_status(root, project=project) if lane is None else roadmap_status(root, lane=lane, project=project)
        for ms in view["milestones"]:
            ms.pop("last_activity", None)
        return view

    return truth_cached(("roadmap_drift.roadmap_progress", str(root), lane, project), compute)


#: AIPOS-F249 件③: the milestone stall judgment needs the cards' loop run record instants (rewritten every few seconds while a loop runs):
#: _roadmap_core leaves this placeholder in signals and _with_run_activity replaces it (same judgment, same place in the order)
_STALL_PENDING = "__stall_pending__"


def _roadmap_view(root: Path, lane: str | None, project: str | None, now: datetime | None) -> dict[str, Any]:
    """roadmap_status body: everything but the loop run record instants (_roadmap_core; board server = cached on its own, so a run record
    rewrite does not re-derive it) + those instants per milestone (_with_run_activity). now None = the current moment (judged)."""
    from tools.aipos_cli.clock import judged_now
    from tools.aipos_cli.task_loader import truth_cached

    if now is None:
        core = truth_cached(("roadmap_drift._roadmap_core", str(root), lane, project),
                            lambda: _roadmap_core(root, lane=lane, project=project, now=judged_now()))
        return _with_run_activity(root, core, judged_now(), _card_run_latest)
    return _with_run_activity(root, _roadmap_core(root, lane=lane, project=project, now=now), now,
                              lambda r, t: _card_run_stamps(r, t)["latest"])


def _with_run_activity(root: Path, view: dict[str, Any], moment: datetime, run_latest: Any) -> dict[str, Any]:
    """AIPOS-F249 件③: fold the cards' loop run record instants into the core view: each milestone row's last_activity, the stall signal
    in its place (placeholder from _roadmap_core; judgment unchanged: active, criteria not all met, no unreadable card, last activity older
    than stall_days), then the next steps (they read the signals)."""
    from tools.aipos_cli.clock import holds_count, holds_until

    pending = view.pop("_activity", None)
    if pending is None:
        return view
    decl = status_declaration()
    lasts: dict[str, datetime | None] = {}
    for slot in pending["milestones"]:
        found = [x for x in [slot["core_last"], *(run_latest(root, t) for t in slot["cards"])] if x is not None]
        last = max(found) if found else None
        lasts[slot["id"]] = last
        view["milestones"][slot["row"]]["last_activity"] = _iso(last)
    signals: list[dict[str, Any]] = []
    stall_days = int(view["thresholds"]["stall_days"]) if view.get("thresholds") else 0
    for sig in view["signals"]:
        if sig.get("kind") != _STALL_PENDING:
            signals.append(sig)
            continue
        last = lasts.get(sig["milestone_id"])
        if sig["eligible"] and last is not None:
            idle = (moment - last).days
            holds_count(moment, last, 86400, idle)
            holds_until(moment, last + timedelta(days=stall_days, microseconds=1))
            if moment - last > timedelta(days=stall_days):
                signals.append(_signal(decl, "stalled", anchor=f"{sig['milestone_id']}|{_iso(last)}",
                                       subject=_inbox_subject("stalled", sig["milestone_id"], None), cards=sig["cards"],
                                       last_at=_iso(last), idle=idle, threshold=view["thresholds"]["stall_days"],
                                       met=sig["met"], total=sig["total"], governance_root=root, advisor=sig["advisor"], milestone=sig["milestone"]))
    view["signals"] = signals
    view["next_steps"] = _next_steps(decl, view, root, pending["active_seen"])
    return view


def _roadmap_core(governance_root: str | Path, *, lane: str | None, project: str | None, now: datetime) -> dict[str, Any]:
    """roadmap_status without the loop run record instants (AIPOS-F249 件③): milestone rows' last_activity and the stall signal are left
    to _with_run_activity (the view carries their inputs under the private key _activity; signals carry a placeholder)."""
    from tools.aipos_cli.clock import holds_until
    from tools.aipos_cli.roadmap import LINK_INVALID, LINK_LINKED, LINK_UNLINKED, card_links, load_roadmap, roadmap_declaration
    from tools.aipos_cli.task_loader import task_card_lookup_scope
    from tools.aipos_cli.workspace_config import roadmap_drift_thresholds

    root = Path(governance_root)
    moment = now
    today = moment.date()
    # AIPOS-F249: every time judgment below reports when it flips (the date: next UTC midnight; idle days; stall / window thresholds)
    holds_until(moment, datetime(today.year, today.month, today.day, tzinfo=timezone.utc) + timedelta(days=1))
    decl = status_declaration()
    rdecl = roadmap_declaration()
    roadmap = load_roadmap(root)
    out: dict[str, Any] = {"project": project, "path": str(roadmap["path"]), "rel": roadmap["rel"], "exists": roadmap["exists"],
                           "lane": lane, "today": today.isoformat(), "thresholds": None, "milestones": [], "signals": [],
                           "next_steps": [], "unlinked": [], "unlinked_before_roadmap": 0, "invalid": [], "unreadable_cards": [], "errors": [],
                           "totals": {"met": 0, "total": 0}}
    if not roadmap["exists"]:
        return out
    out["_activity"] = activity_slots = {"milestones": [], "active_seen": False}
    thresholds = roadmap_drift_thresholds(root)
    out["thresholds"] = thresholds
    errors: list[dict[str, str]] = out["errors"]
    cache: dict[str, Any] = {}
    excluded = set(decl["excluded_queue_states"])
    with task_card_lookup_scope(root):
        links = card_links(root, roadmap)
        live = [c for c in links["cards"] if c.get("queue_state") not in excluded]
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
                # AIPOS-F209: 「已结案」= 结案口径唯一判定 task_complexity.closure_state.concluded(card_closure; 读不出 = 不算, 点名进 errors)
                closed = [c for c in crit_cards if (card_closure(root, c, errors, cache) or {}).get("concluded")]
                # AIPOS-F174: 每张挂接卡的审计证据(裁决 / 补审 / 运行证据摘录)随行带出, 看板逐条标准展示只读此字段;
                # AIPOS-F175: 另带该卡卡面声明的本标准产物(evidence[].artifacts)
                evidence = {str(c["task_id"]): card_evidence(root, c, _card_truth(root, c, errors, cache), cid) for c in crit_cards}
                verdict_row = _criterion_verification(root, crit, crit_cards, closed, evidence, links["cards"], decl, errors, cache)
                rounds = sum(_card_truth(root, c, errors, cache)["rework_rounds"] for c in crit_cards) if crit_cards else 0
                criteria_rows.append({"id": cid, "statement": crit["statement"], "verify": crit["verify"], **verdict_row,
                                      "cards": [c["task_id"] for c in crit_cards], "rework_rounds": rounds,
                                      "closed_cards": [c["task_id"] for c in closed],
                                      "evidence": [evidence[str(c["task_id"])] for c in crit_cards]})
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
                   "last_activity": None, "criteria": criteria_rows}  # AIPOS-F249: last_activity filled by _with_run_activity
            activity_slots["milestones"].append({"id": ms_id, "row": len(out["milestones"]), "core_last": last,
                                                 "cards": [str(c.get("task_id") or "") for c in ms_cards]})
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
            # AIPOS-F249 件③: the last activity includes the loop run record instants = judged by _with_run_activity in this place
            out["signals"].append({"kind": _STALL_PENDING, "milestone_id": ms_id, "cards": _ids(ms_cards), "advisor": who, "milestone": ms,
                                   "met": n_met, "total": n_total,
                                   "eligible": bool(status == "active" and ms_cards and not (n_total and n_met == n_total)
                                                    and not any(t.get("unreadable") for t in truths))})
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

        # 游离卡占比(全项目; 审计卡不计: 审计卡由产品派生, 挂接看被审卡; AIPOS-F181: 游离卡清单同样不列审计卡)
        from tools.aipos_cli.audit_derivation import is_audit_card

        window = int(thresholds["unlinked_window_days"])
        since = moment - timedelta(days=window)
        # AIPOS-F181 item 3: only cards created once their lane had a roadmap count (start = that lane's first milestone declaration,
        # lanes without a milestone = the project's first one; read from the decision_log pointers); no pointer at all = no lower bound
        started = _roadmap_start(root, roadmap, pointers["entries"])
        own = [c for c in live if not is_audit_card(str(c.get("task_id") or ""), c.get("metadata") or {})]
        after = [c for c in own if started(c)]
        out["unlinked"] = [c["task_id"] for c in after if c["state"] == LINK_UNLINKED]
        out["unlinked_before_roadmap"] = sum(1 for c in own if c["state"] == LINK_UNLINKED and not started(c))
        recent = [c for c in after if c["state"] in (LINK_LINKED, LINK_UNLINKED, LINK_INVALID)
                  and (_card_created(c.get("metadata") or {}) or since) > since]
        holds_until(moment, *(_card_created(c.get("metadata") or {}) + timedelta(days=window) for c in recent))
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
        holds_until(moment, *(_as_utc(e.get("decided_at")) + timedelta(days=change_days) for e in recent_changes))
        if recent_changes:
            last_change = recent_changes[-1]
            out["signals"].append(_signal(
                decl, "roadmap_changed", governance_root=root, anchor=str(last_change["path"]), subject="路线图变更", advisor=last_editor,
                days=change_days, count=len(recent_changes), last_at=last_change.get("decided_at") or "?",
                last_by=last_change.get("decided_by") or "?", last_summary=last_change.get("summary") or "?",
                last_path=last_change["path"]))
        activity_slots["active_seen"] = active_seen  # AIPOS-F249: next steps read the signals = made by _with_run_activity
    return out


def _roadmap_start(root: Path, roadmap: dict[str, Any], entries: list[dict[str, Any]]) -> Any:
    """AIPOS-F181: predicate "card created after its lane had a roadmap". Lane of a card = machine_zone.lane_of_card (single lane
    derivation; milestone lane = the same key, e.g. a repo name); per lane start = earliest add-milestone pointer of its milestones
    (roadmap.milestone_declared_at), other lanes = the earliest of all milestones (else the earliest pointer to the roadmap file).
    No pointer at all = every card counts (behaviour unchanged). A card without a creation instant = before (history)."""
    from tools.aipos_cli.machine_zone import lane_of_card
    from tools.aipos_cli.roadmap import milestone_declared_at

    declared = milestone_declared_at(roadmap, entries)
    by_lane: dict[str, datetime] = {}
    for ms in (roadmap.get("data") or {}).get("milestones") or []:
        moment = _as_utc(declared["milestones"].get(str(ms.get("id"))))
        moment = moment.replace(microsecond=0) if moment is not None else None  # card instants are whole seconds
        lane = str(ms.get("lane") or "")
        if moment is not None and (lane not in by_lane or moment < by_lane[lane]):
            by_lane[lane] = moment
    first = _as_utc(declared["first_pointer"])
    project_start = min(by_lane.values()) if by_lane else (first.replace(microsecond=0) if first is not None else None)
    if project_start is None:
        return lambda card: True

    def started(card: dict[str, Any]) -> bool:
        meta = card.get("metadata") or {}
        created = _card_created(meta)
        if created is None:
            return False
        lane = str(lane_of_card(meta, root)["lane"]) if by_lane else ""
        return created >= by_lane.get(lane, project_start)

    return started


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
        # AIPOS-F175: closed-but-unmet criteria split by verification state
        by_state: dict[str, list[dict[str, Any]]] = {}
        for r in waiting:
            by_state.setdefault(str(r.get("state") or "unverified"), []).append(r)
        if by_state.get("unverified"):
            out.append(_fmt(steps["pending_verify"], "next_steps.pending_verify", milestone=ms["id"],
                            criteria=", ".join(r["id"] for r in by_state["unverified"])))
        if by_state.get("owner_pending"):
            out.append(_fmt(steps["owner_check"], "next_steps.owner_check", milestone=ms["id"], governance_root=gr,
                            criteria=", ".join(r["id"] for r in by_state["owner_pending"])))
        if by_state.get("mismatch"):
            fixes = ", ".join(f["text"] for r in by_state["mismatch"] for f in r.get("fix_cards") or [])
            basis = decl["verification"]["basis"]
            out.append(_fmt(steps["mismatch"], "next_steps.mismatch", milestone=ms["id"],
                            criteria=", ".join(r["id"] for r in by_state["mismatch"]),
                            fix_part=_fmt(basis["fix_part"], "verification.basis.fix_part", fix=fixes) if fixes else str(basis["fix_none"])))
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
                              cards=", ".join(crit["met_cards"]), reason=crit["reason"] or "", state_label=crit["state_label"],
                              basis=crit.get("basis") or ""))
            # AIPOS-F175: declared artifacts of each linked card (files found in the card artifact dir)
            for ev in crit.get("evidence") or []:
                art = ev.get("artifacts") or {}
                for entry in art.get("entries") or []:
                    files = ", ".join(f["rel"] for f in entry["files"]) or _fmt(texts["artifact_files_none"], "texts.artifact_files_none",
                                                                                  dir=art.get("dir_rel") or "?")
                    lines.append(_fmt(texts["criterion_artifact"], "texts.criterion_artifact", task_id=ev["task_id"], kind=entry["kind"],
                                      path_glob=entry["path_glob"], files=files))
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
