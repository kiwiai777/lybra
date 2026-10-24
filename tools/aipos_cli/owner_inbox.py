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

AIPOS-F180(gap #175 / #170): loop 类事项事由 ∈ grant_request.reasons(no_envelope / envelope_renew)= 授权请求: 附结构化建议 grant(唯一推导
suggest_grant: 驱动方 / 卡面模式 / 卡面仓 / 同驱动方上一信封或项目缺省额度 / 项目缺省到期 / 卡标题 / F166 next_policy_id; 顾问 loop note --grant-*
逐字段覆盖), CLI 一行人话(grant_line)、看板「待授权请求」卡与「按建议签发」(F172 唯一写路由)同读; 驱动方已有覆盖本卡的信封(loop 同一判据
loop_driver.find_envelope)= 已自行解除, 不列。

AIPOS-F201(授权类小事看板闭环): 件① needs_owner pending 卡的 release 选项 =「放行继续」(既有 amend 出口, 修订理由带决策记录 ID 与 Owner 原话;
已认领 / 已阻塞的卡选 release = 声明拒因 choice_refused_by_queue_state); 件② 授权建议对派 pi 工位的卡(workspace_config.card_execution_mode)给
launch_harnesses=[pi]; 件③「告诉哪位顾问做什么」话术(声明 lybra_owner_inbox.relay, 中英): 每项转达对象 relay_target、看板每个选项的话术
choice_script、已签发 / 已裁决而顾问未接着做的「已定待转达」relays(从既有真相推导, 按顾问合并 relay_groups / relay_lines, CLI 与看板同文)。

AIPOS-F202(审计连续 BLOCK 的正式恢复入口): loop 类事由 audit_blocked_repeated 的事项选项含 reaudit(声明 pinned_options_by_reason: 顾问 loop note
写了自定义选项也恒列); 选 reaudit = 既有 decide 同一写路径落决策记录, 推导核以该裁决为界重算连续 BLOCK(next_resolver.audit_block_state)。
reaudit_hint: 此前「等修复」类裁决(after_choices)之后运行记录部署提交已变 = 事项附提示行(CLI 与看板同读 item.reaudit_hint)。

AIPOS-F225(Owner 介入分级): 三档声明唯一读取口 tier_declaration(verbs.schema lybra_owner_inbox.tiers, 加载守卫: loop 可判 owner_needed 的每个事由与
收件箱每个类别都须归档, 缺 = SchemaLoadError); loop 下一动作按档归类 apply_owner_tier(loop status 每个运行同调); 收件箱 items 只列第一档类别, 第二档
进 notices; 第二档自动放行记录写读口 write_auto_release / auto_release_records(既有事件记录), 撤回 = auto_released 事项选 revoke_auto, 卡停住判定
auto_release_stop; 顾问升 Owner 的第一档事由校验 require_owner_reason(loop note / needs_owner 卡同读)。

AIPOS-F175 件③: owner_check 类别 = 路线图推导(roadmap_drift.roadmap_status, 唯一实现)里验证状态 = 待 Owner 抽查的标准, 附产物 / 步骤 /
「告诉顾问」话术; 结论经顾问 `lybra roadmap verify`(roadmap.apply_owner_verification)记录, 不经 decide(对本类敲 decide = 用法错, 拒因指路)。
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
CATEGORIES = ("loop_owner_needed", "needs_owner_card", "rework_limit", "supplementary_fail", "envelope_warning", "roadmap_drift",
              "owner_check", "auto_released")
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
        for table in ("options_by_reason", "options_by_queue_state", "pinned_options_by_reason"):  # AIPOS-F202: pinned
            for keys in (cat.get(table) or {}).values():
                referenced |= set(keys)
        hint = cat.get("reaudit_hint")
        if hint is not None:  # AIPOS-F202: reaudit hint after a "wait for the fix" ruling
            if (not isinstance(hint, dict) or not isinstance(hint.get("reasons"), list) or not isinstance(hint.get("after_choices"), list)
                    or not str(hint.get("text") or "").strip() or not isinstance(hint.get("commit_chars"), int) or hint["commit_chars"] < 1):
                raise SchemaLoadError(f"verbs.{VERB}.categories.{name}.reaudit_hint 须有 reasons / after_choices(列表)、非空 text 与 ≥1 整数 commit_chars")
            referenced |= set(hint["after_choices"])
        referenced |= set(cat.get("choice_refused_by_queue_state") or {})  # AIPOS-F201: refusal texts are keyed by option
    ask_spec = decl["categories"]["loop_owner_needed"].get("advisor_ask")  # AIPOS-F222 件②
    if (not isinstance(ask_spec, dict) or not str(ask_spec.get("reason") or "").strip() or not str(ask_spec.get("impact") or "").strip()
            or not isinstance(ask_spec.get("pinned_when_rework_exhausted"), list)):
        raise SchemaLoadError(f"verbs.{VERB}.categories.loop_owner_needed.advisor_ask 须有非空 reason / impact 与列表 pinned_when_rework_exhausted")
    referenced |= set(ask_spec["pinned_when_rework_exhausted"])
    rl = decl["categories"]["rework_limit"]  # AIPOS-F222 件①: 锚按卡型措辞
    review = rl.get("advisor_review")
    if (not isinstance(review, dict) or any(not str(review.get(k) or "").strip() for k in ("ask", "impact"))
            or not str(rl.get("extension_part") or "").strip()):
        raise SchemaLoadError(f"verbs.{VERB}.categories.rework_limit 须有 advisor_review.ask / impact 与 extension_part")
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
    _check_tiers(decl)  # AIPOS-F225 件①: 档位映射守卫(未归档的事由 / 类别 = 加载即拒)
    return decl


# ---------------------------------------------------------------------------
# AIPOS-F225 Owner 介入分级: 三档声明(唯一读取口)+ 加载守卫
# ---------------------------------------------------------------------------

TIER_LEVELS = (1, 2, 3)
_TIER_KEYS = ("tier1", "tier2", "tier3", "loop_reasons", "categories", "needs_owner_reason_field", "grant_owner_reason", "texts")
_TIER_TEXTS = ("tier3_detail", "escalate_hint", "auto_reaudit_changed", "auto_reaudit_unchanged", "change_deploy", "change_validation_roots",
               "owner_reason_part", "needs_owner_reason_missing", "notices_header", "notice_item")
_AUTO_RELEASE_KEYS = ("event_type", "id_template", "record_fields", "revoke_option", "revoked_end_reason", "body")


def _owner_needed_reasons(next_decl: dict[str, Any], decl: dict[str, Any]) -> set[str]:
    """loop 顾问下一动作可判 owner_needed 的全部事由: by_end_reason 判 owner_needed 的结束原因(含按执行方式覆盖成 owner_needed 者)+
    owner_reasons 全部键 + 顾问 loop note 事由(categories.loop_owner_needed.advisor_ask.reason)。"""
    reasons = {str(r) for r, a in (next_decl.get("by_end_reason") or {}).items() if a == "owner_needed"}
    for rule in (((next_decl.get("by_execution_mode") or {}).get("modes")) or {}).values():
        reasons |= {str(r) for r, a in ((rule or {}).get("by_end_reason") or {}).items() if a == "owner_needed"}
    reasons |= {str(r) for r in (next_decl.get("owner_reasons") or {})}
    reasons.add(str(decl["categories"]["loop_owner_needed"]["advisor_ask"]["reason"]))
    return reasons


def _check_tiers(decl: dict[str, Any]) -> None:
    """lybra_owner_inbox.tiers 形校验(fail-closed, inbox_declaration 每次加载即调): 键齐; loop_reasons 恰覆盖 loop 可判 owner_needed 的全部事由
    (_owner_needed_reasons; 缺 = 新增事由未归档, 多 = 声明残留), categories 恰覆盖 CATEGORIES; 每条档位 ∈ TIER_LEVELS: 第一档 owner_reason ∈
    tier1.reasons(或 owner_reason_from = owner_ask), 第二档 kind ∈ tier2.kinds 且 action ∈ tier3.actions(未自动放行时的去向), 第三档 action ∈
    tier3.actions 且 why 非空。"""
    from tools.aipos_cli.loop_run_record import status_contract
    from tools.schema_loader import SchemaLoadError

    where = f"verbs.{VERB}.tiers"
    tiers = decl.get("tiers")
    if not isinstance(tiers, dict) or any(k not in tiers for k in _TIER_KEYS):
        raise SchemaLoadError(f"{where} 未声明齐 {list(_TIER_KEYS)}(AIPOS-F225)")
    reasons = (tiers["tier1"] or {}).get("reasons") if isinstance(tiers["tier1"], dict) else None
    kinds = (tiers["tier2"] or {}).get("kinds") if isinstance(tiers["tier2"], dict) else None
    actions = (tiers["tier3"] or {}).get("actions") if isinstance(tiers["tier3"], dict) else None
    if (not isinstance(reasons, dict) or not reasons or any(not str((v or {}).get("label") or "").strip() for v in reasons.values())
            or not isinstance(kinds, dict) or not kinds or not isinstance(actions, list) or not actions):
        raise SchemaLoadError(f"{where}: tier1.reasons(键 → label)/ tier2.kinds / tier3.actions 须为非空")
    if set(tiers["tier1"].get("inbox_last") or []) - set(reasons):
        raise SchemaLoadError(f"{where}.tier1.inbox_last 须 ⊆ tier1.reasons")
    auto = tiers["tier2"].get("auto_release")
    if not isinstance(auto, dict) or any(k not in auto for k in _AUTO_RELEASE_KEYS) or str(auto["revoke_option"]) not in decl["options"]:
        raise SchemaLoadError(f"{where}.tier2.auto_release 须声明齐 {list(_AUTO_RELEASE_KEYS)}, revoke_option 须为 options 声明的选项")
    if any(not str(tiers["texts"].get(k) or "").strip() for k in _TIER_TEXTS):
        raise SchemaLoadError(f"{where}.texts 须声明齐 {list(_TIER_TEXTS)}")
    if str(tiers["grant_owner_reason"]) not in reasons:
        raise SchemaLoadError(f"{where}.grant_owner_reason 须 ∈ tier1.reasons")
    expected = _owner_needed_reasons(status_contract()["next_action"], decl)
    expected.add(str(auto["revoked_end_reason"]))
    table = tiers["loop_reasons"]
    if not isinstance(table, dict) or set(table) != expected:
        got = set(table) if isinstance(table, dict) else set()
        raise SchemaLoadError(f"{where}.loop_reasons 须恰覆盖 loop 可判 owner_needed 的全部事由: 未归档 {sorted(expected - got)}; "
                              f"多出 {sorted(got - expected)}(AIPOS-F225: 新增事由须先归档)")
    cats = tiers["categories"]
    if not isinstance(cats, dict) or set(cats) != set(CATEGORIES):
        got = set(cats) if isinstance(cats, dict) else set()
        raise SchemaLoadError(f"{where}.categories 须恰覆盖收件箱类别: 未归档 {sorted(set(CATEGORIES) - got)}; 多出 {sorted(got - set(CATEGORIES))}")
    entries = [*(("loop_reasons", k, v, True) for k, v in table.items()), *(("categories", k, v, False) for k, v in cats.items())]
    for group, key, spec, is_loop in entries:
        name = f"{group}.{key}"
        level = spec.get("tier") if isinstance(spec, dict) else None
        bad = level not in TIER_LEVELS or isinstance(level, bool)
        if not bad and level == 1:
            reason = spec.get("owner_reason")
            bad = not ((reason is None and (not is_loop or spec.get("owner_reason_from") == "owner_ask")) or str(reason) in reasons)
        elif not bad and level == 2:
            bad = str(spec.get("kind")) not in kinds or (is_loop and str(spec.get("action")) not in actions)
        elif not bad:
            bad = not is_loop or str(spec.get("action")) not in actions or not str(spec.get("why") or "").strip()
        if bad:
            raise SchemaLoadError(f"{where}.{name} 档位不合: 第一档须 owner_reason ∈ tier1.reasons, 第二档须 kind ∈ tier2.kinds(loop 事由另须 action), "
                                  f"第三档(仅 loop 事由)须 action ∈ tier3.actions 且 why 非空; 得到 {spec!r}")


def tier_declaration(decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """lybra_owner_inbox.tiers(经 inbox_declaration 加载守卫)。"""
    return (decl or inbox_declaration())["tiers"]


def category_tier(category: str, decl: dict[str, Any] | None = None) -> int:
    """收件箱类别的档位(tiers.categories)。"""
    return int(tier_declaration(decl)["categories"][category]["tier"])


def owner_reason_entry(key: Any, decl: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """第一档事由键 → {key, label}; 不在清单 = None。"""
    reasons = tier_declaration(decl)["tier1"]["reasons"]
    text = str(key or "").strip()
    return {"key": text, "label": str(reasons[text]["label"])} if text in reasons else None


class OwnerReasonRefused(ValueError):
    """升 Owner 缺第一档事由 / 事由不在清单(loop note 出口 owner_reason_refused; 发布预演 = 阻断理由)。"""


def require_owner_reason(value: Any, *, grant: bool = False, decl: dict[str, Any] | None = None) -> str:
    """AIPOS-F225 件③ 唯一校验(loop note --owner-reason / 卡面 needs_owner 的 owner_reason 同读): 键 ∈ tiers.tier1.reasons → 键;
    缺 + grant(只给授权建议)= tiers.grant_owner_reason; 缺 / 不在清单 = OwnerReasonRefused(消息 = 合法键清单 + 给的值)。"""
    tiers = tier_declaration(decl)
    text = str(value or "").strip()
    if not text and grant:
        return str(tiers["grant_owner_reason"])
    if text in tiers["tier1"]["reasons"]:
        return text
    raise OwnerReasonRefused(text)


def owner_reason_keys(decl: dict[str, Any] | None = None) -> str:
    return "|".join(tier_declaration(decl)["tier1"]["reasons"])


def apply_owner_tier(governance_root: Path, view: dict[str, Any], na: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F225 件①③: loop status 每个运行的下一动作(已经 apply_owner_ruling)按档归类(唯一实现, 判据 tiers.loop_reasons)。
    非 owner_needed = 原样。第一档 = 原样 + owner_tier 1 / owner_reason {key, label}(事由来自 owner_ask 的 = 运行记录 owner_ask.owner_reason)。
    第二档 auto_reaudit = next_resolver.auto_reaudit_check(loop 自动重新派审同一判定): 有变化 / 无变化 = 声明 action(advisor_fix), detail 写明;
    该 BLOCK 已有被撤回的自动放行 = 第一档(revoked_end_reason 的事由)。第三档 = 改判声明 action, detail = why + 原判, 附 escalate_hint。"""
    if not isinstance(na, dict) or na.get("action") != "owner_needed":
        return na
    tiers = tier_declaration()
    reason = str(na.get("reason") or "")
    spec = tiers["loop_reasons"].get(reason)
    task_id = str(view.get("task_id") or "")
    fill = {"task_id": task_id, "governance_root": str(governance_root), "reasons": owner_reason_keys()}
    if spec is None:  # 守卫已保证全覆盖; 到这里 = 运行时出现了未声明的事由, 不猜(原判照旧, 标未归档)
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"verbs.{VERB}.tiers.loop_reasons 未归档事由 {reason!r}(AIPOS-F225: 新增事由须先归档)")
    level = int(spec["tier"])
    if level == 1:
        key = spec.get("owner_reason")
        if spec.get("owner_reason_from") == "owner_ask":
            key = ((view.get("owner_ask") or {}) if isinstance(view.get("owner_ask"), dict) else {}).get("owner_reason")
        entry = owner_reason_entry(key)
        return {**na, "owner_tier": 1, "owner_reason": entry}
    if level == 2:
        from tools.aipos_cli.next_resolver import auto_reaudit_check

        check = auto_reaudit_check(Path(governance_root), task_id)
        if check.get("revoked"):
            entry = owner_reason_entry(tiers["loop_reasons"][str(tiers["tier2"]["auto_release"]["revoked_end_reason"])]["owner_reason"])
            return {**na, "owner_tier": 1, "owner_reason": entry, "auto_reaudit": check}
        key = "auto_reaudit_changed" if check.get("eligible") else "auto_reaudit_unchanged"
        detail = _fmt(tiers["texts"][key], f"tiers.texts.{key}", verdict_id=check.get("verdict_id") or "?", changes=check.get("changes") or "",
                      why=check.get("why") or "", detail=str(na.get("detail") or ""))
        hint = str(na.get("hint") or "")
        if check.get("eligible"):
            from tools.aipos_cli.loop_run_record import _resume_fill, status_contract

            hint = _fmt(status_contract()["next_action"]["routine_rejections"]["resume_hint"], "resume_hint",
                        **_resume_fill(view, Path(governance_root)))
        out = {**na, "action": str(spec["action"]), "owner_tier": 2, "tier_from": {"action": "owner_needed", "reason": reason},
               "detail": detail, "hint": hint, "auto_reaudit": check,
               "escalate_hint": _fmt(tiers["texts"]["escalate_hint"], "tiers.texts.escalate_hint", **fill)}
    else:
        detail = _fmt(tiers["texts"]["tier3_detail"], "tiers.texts.tier3_detail", why=str(spec["why"]), detail=str(na.get("detail") or ""))
        out = {**na, "action": str(spec["action"]), "owner_tier": 3, "tier_from": {"action": "owner_needed", "reason": reason},
               "detail": detail, "escalate_hint": _fmt(tiers["texts"]["escalate_hint"], "tiers.texts.escalate_hint", **fill)}
    # 归顾问的运行上顾问已写升级请求(owner_ask)= 与非等 Owner 运行同一判定(owner_ask_destination: 进收件箱 / 已裁决改写)
    return _apply_owner_ask(Path(governance_root), view, out)


# ---------------------------------------------------------------------------
# AIPOS-F225 件②: 第二档自动放行记录(既有事件记录, 不另立存储)+ 撤回后卡停住
# ---------------------------------------------------------------------------

def auto_release_declaration(decl: dict[str, Any] | None = None) -> dict[str, Any]:
    return tier_declaration(decl)["tier2"]["auto_release"]


def auto_release_records(governance_root: Path, task_id: str | None = None) -> list[dict[str, Any]]:
    """本卡(缺省 = 全项目)第二档自动放行记录(records/events/<卡>/<event_type>_*.md), 按 released_at 旧→新; 每条 = frontmatter + _path。
    读不出 = FrontmatterReadError 向上(fail-closed)。"""
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.record_writer import record_dir

    spec = auto_release_declaration()
    root = Path(governance_root)
    base = record_dir(root, "events")
    dirs = [base / task_id] if task_id else (sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else [])
    out: list[dict[str, Any]] = []
    for directory in dirs:
        for path in sorted(directory.glob(f"{spec['event_type']}_*.md")) if directory.is_dir() else []:
            fm, _body = require_frontmatter(path)
            if str(fm.get("event_type") or "") == str(spec["event_type"]):
                out.append({**fm, "_path": str(path)})
    return sorted(out, key=lambda r: (str(r.get("released_at") or ""), str(r.get("auto_release_id") or "")))


def planned_auto_release_id(governance_root: Path, task_id: str, kind: str) -> str:
    """下一条自动放行记录 ID(声明 id_template; seq = 本卡同 kind 已有记录数 + 1)。"""
    spec = auto_release_declaration()
    seq = 1 + sum(1 for r in auto_release_records(Path(governance_root), task_id) if str(r.get("kind") or "") == kind)
    return _fmt(spec["id_template"], "tiers.tier2.auto_release.id_template", task_id=task_id, kind=kind, seq=seq)


def write_auto_release(governance_root: Path, task_id: str, kind: str, *, released_by: str, basis: str, record_id: str | None = None,
                       round_no: int | None = None, verdict_ref: str | None = None, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F225 件② 写口唯一实现: 一次第二档自动放行 = 一条事件记录(record_writer.write_records_atomic kind event; 字段读声明 record_fields)。
    返回 {auto_release_id, path}。写不进 = 异常向上(调用方 fail-closed)。"""
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.record_writer import render_markdown, write_records_atomic
    from tools.schema_constants import RecordType

    spec = auto_release_declaration()
    rid = record_id or planned_auto_release_id(Path(governance_root), task_id, kind)
    values = {"record_type": RecordType.TASK_PROGRESS_EVENT, "event_type": str(spec["event_type"]), "auto_release_id": rid, "task_id": task_id,
              "kind": kind, "tier": 2, "released_at": iso_z(), "released_by": released_by, "basis": basis, "round": round_no,
              "verdict_ref": verdict_ref, "evidence": dict(evidence or {})}
    meta = {key: values[key] for key in spec["record_fields"]}  # 字段只读声明(声明多出未知字段 = KeyError, fail-closed)
    body = _fmt(spec["body"], "tiers.tier2.auto_release.body", kind=kind, task_id=task_id, basis=basis)
    written = write_records_atomic(Path(governance_root), [("event", rid, render_markdown(meta, body, list(meta)), task_id)])
    paths = list(written.get("paths") or [])
    return {"auto_release_id": rid, "path": paths[0] if paths else None, "paths": paths}


def _auto_release_subject(task_id: str, rid: str, decl: dict[str, Any]) -> str:
    return _fmt(decl["categories"]["auto_released"]["subject"], "categories.auto_released.subject", task_id=task_id, auto_release_id=rid)


def auto_release_item_id(governance_root: Path, task_id: str, rid: str, decl: dict[str, Any] | None = None) -> str:
    """自动放行事项 ID(收件箱 _auto_release_items 同一构造: 项目 = 治理根目录名, 锚点 = 记录 ID)。"""
    decl = decl or inbox_declaration()
    return item_id(Path(governance_root).resolve().name, "auto_released", _auto_release_subject(task_id, rid, decl), rid, decl)


def auto_release_ruling(governance_root: Path, task_id: str, rid: str, decl: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """该自动放行事项的 Owner 裁决(撤回 / 知悉)或 None。读不出 = 向上。"""
    return read_ruling(Path(governance_root), auto_release_item_id(governance_root, task_id, rid, decl), decl)


def revoked_auto_release_ids(governance_root: Path, task_id: str) -> set[str]:
    """本卡已被 Owner 撤回(选 revoke_option)的自动放行记录 ID。"""
    decl = inbox_declaration()
    revoke = str(auto_release_declaration(decl)["revoke_option"])
    out: set[str] = set()
    for rec in auto_release_records(Path(governance_root), task_id):
        rid = str(rec.get("auto_release_id") or "")
        ruling = auto_release_ruling(Path(governance_root), task_id, rid, decl) if rid else None
        if ruling is not None and str(ruling.get("allowed_next_action") or "") == revoke:
            out.add(rid)
    return out


def auto_release_stop(governance_root: Path, task_id: str) -> dict[str, Any] | None:
    """AIPOS-F225 件② 卡停住唯一判定(推导核 next_resolver 与 loop 同读): 本卡某条自动放行已被 Owner 撤回, 且撤回之后本卡没有 Owner 收件箱新裁决
    (rulings_for_task, decided_at 不早于撤回: 秒级时刻同秒算之后; 撤回本身除外)= {auto_release_id, decision_id, decided_at, end_reason}; 否则 None。读不出 = 向上。"""
    decl = inbox_declaration()
    spec = auto_release_declaration(decl)
    revoke = str(spec["revoke_option"])
    latest: dict[str, Any] | None = None
    for rec in auto_release_records(Path(governance_root), task_id):
        rid = str(rec.get("auto_release_id") or "")
        ruling = auto_release_ruling(Path(governance_root), task_id, rid, decl) if rid else None
        if ruling is not None and str(ruling.get("allowed_next_action") or "") == revoke:
            if latest is None or str(ruling.get("decided_at") or "") > str(latest["decided_at"]):
                latest = {"auto_release_id": rid, "decision_id": str(ruling.get("decision_id") or ""), "decided_at": str(ruling.get("decided_at") or ""),
                          "kind": rec.get("kind"), "end_reason": str(spec["revoked_end_reason"])}
    if latest is None:
        return None
    later = [r for r in rulings_for_task(Path(governance_root), task_id)
             if str(r.get("decision_id") or "") != latest["decision_id"] and str(r.get("decided_at") or "") >= latest["decided_at"]]
    return None if later else latest


def _auto_release_items(root: Path, project: str, decl: dict[str, Any], home_root: Path | None,
                        errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """⑧ AIPOS-F225: 第二档自动放行记录每条一个事项(锚点 = 记录 ID; 已裁决 = 不再出现)。卡已结案 / 撤回 = 不列。"""
    from tools.aipos_cli.task_loader import find_task_card

    cat = decl["categories"]["auto_released"]
    out: list[dict[str, Any]] = []
    for rec in auto_release_records(root):
        task_id, rid = str(rec.get("task_id") or ""), str(rec.get("auto_release_id") or "")
        if not task_id or not rid:
            errors.append({"project": project, "source": f"自动放行记录 {rec.get('_path')}", "error": "缺 task_id / auto_release_id"})
            continue
        _path, state = find_task_card(root, task_id)
        if state in ("completed", "withdrawn"):
            continue
        kind = str(rec.get("kind") or "")
        rel = Path(str(rec["_path"])).resolve()
        fill = {"task_id": task_id, "auto_release_id": rid, "kind_label": str((cat.get("kind_labels") or {}).get(kind) or kind),
                "basis": str(rec.get("basis") or ""), "released_at": str(rec.get("released_at") or "?"),
                "record_path": rel.relative_to(root.resolve()).as_posix() if rel.is_relative_to(root.resolve()) else str(rel)}
        out.append(_base_item(
            decl, project=project, governance_root=root, category="auto_released", rank=int(cat["rank"]),
            subject=_auto_release_subject(task_id, rid, decl), anchor=rid, source=str(rec.get("released_by") or ""),
            ask=_fmt(cat["ask"], "categories.auto_released.ask", **fill), recommend=None,
            impact=_fmt(cat["impact"], "categories.auto_released.impact", **fill), options=_option_list(list(cat["options"]), decl),
            since=str(rec.get("released_at") or "") or None, task_id=task_id, home_root=home_root,
            facts={"auto_release_id": rid, "kind": kind, "round": rec.get("round"), "verdict_ref": rec.get("verdict_ref"),
                   "record_path": fill["record_path"]}))
    return out


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
            "decide_command": command, "facts": dict(facts or {}), "home_root": str(home_root) if home_root else None}


# ---------------------------------------------------------------------------
# AIPOS-F180 件①: 授权请求结构化建议(唯一推导 suggest_grant; CLI 人话行 / 看板「待授权请求」卡 / 按建议签发同读)
# ---------------------------------------------------------------------------

_GRANT_KEYS = ("reasons", "date_only_time", "mode_labels", "mode_any", "repos_any", "sources", "texts")
_GRANT_TEXTS = ("line", "title_part", "repos_part", "launch_part", "decide_board", "unavailable")
#: owner_ask.grant 字段(顾问 loop note --grant-* 写入; 逐字段覆盖推导值)
GRANT_FIELDS = ("task_mode", "lane_repos", "max_tasks", "expires_at", "reason")


def grant_declaration(decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """verbs.schema lybra_owner_inbox.grant_request(fail-closed): 键齐; reasons ⊆ loop 下一动作事由; mode_labels 键恰为 enums.schema task_mode。"""
    from tools.schema_loader import SchemaLoadError, get_enum_values

    g = (decl or inbox_declaration()).get("grant_request")
    if not isinstance(g, dict) or any(k not in g for k in _GRANT_KEYS) or any(k not in (g.get("texts") or {}) for k in _GRANT_TEXTS):
        raise SchemaLoadError(f"verbs.{VERB}.grant_request 未声明齐 {list(_GRANT_KEYS)} / texts {list(_GRANT_TEXTS)}")
    if set(g["mode_labels"]) != set(get_enum_values("task_mode")):
        raise SchemaLoadError(f"verbs.{VERB}.grant_request.mode_labels 键须恰为 enums.schema task_mode 值域 {get_enum_values('task_mode')}")
    if not isinstance(g["reasons"], list) or not g["reasons"]:
        raise SchemaLoadError(f"verbs.{VERB}.grant_request.reasons 须为非空列表")
    return g


def parse_grant_expires(value: Any, *, now: datetime | None = None, decl: dict[str, Any] | None = None) -> str:
    """授权到期 → ISO8601 UTC(Z)。只给日期(YYYY-MM-DD)= 当日 + 声明 date_only_time; 带时刻须带时区; 须在未来。不合 = ValueError(原文点名)。"""
    import re

    from tools.aipos_cli.clock import iso_z, utc_now

    raw = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raw = raw + str(grant_declaration(decl)["date_only_time"])
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"到期 {value!r} 须为 YYYY-MM-DD 或带时区的 ISO 时刻") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"到期 {value!r} 须带时区(如 Z)或只给日期 YYYY-MM-DD")
    if parsed <= (now or utc_now()):
        raise ValueError(f"到期 {value!r} 不在未来")
    return iso_z(parsed)


def normalize_grant_fields(*, task_mode: str | None = None, repos: Any = None, max_tasks: Any = None, expires: str | None = None,
                           reason: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """顾问 --grant-* → owner_ask.grant(只含给出的字段; 唯一校验实现, loop note 调)。不合 = ValueError(点名)。"""
    from tools.aipos_cli.workspace_config import repo_name_set
    from tools.schema_loader import get_enum_values

    out: dict[str, Any] = {}
    if task_mode is not None and str(task_mode).strip():
        mode = str(task_mode).strip()
        if mode not in get_enum_values("task_mode"):
            raise ValueError(f"--grant-mode {mode!r} 不在 task_mode 值域 {get_enum_values('task_mode')}")
        out["task_mode"] = mode
    if repos:
        items = repos if isinstance(repos, list) else [repos]
        names = repo_name_set([part for text in items for part in str(text).split(",")])
        if names:
            out["lane_repos"] = names
    if max_tasks is not None and str(max_tasks).strip():
        try:
            n = int(str(max_tasks).strip())
        except ValueError as exc:
            raise ValueError(f"--grant-max-tasks 须为正整数: {max_tasks!r}") from exc
        if n < 1 or isinstance(max_tasks, bool):
            raise ValueError(f"--grant-max-tasks 须为正整数: {max_tasks!r}")
        out["max_tasks"] = n
    if expires is not None and str(expires).strip():
        out["expires_at"] = parse_grant_expires(expires, now=now)
    if reason is not None and str(reason).strip():
        text = " ".join(str(reason).split())
        out["reason"] = text
    return out


def suggest_grant(root: Path, project: str, *, task_id: str, card_fm: dict[str, Any], driver: str,
                  advisor_grant: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """件① 授权建议唯一推导(声明 verbs.schema lybra_owner_inbox.grant_request): 驱动方 = 运行记录 driver; task_mode = 卡面; 仓 = 卡面 lane.repo
    (未写 = project.json repos 中指向卡所在仓的仓名); 额度(AIPOS-F192: 业务卡张数, 不再照抄上一信封)= project.json grant_request.max_tasks
    显式声明者, 否则本卡所需(autonomy_policy.envelope_card_need, 与 loop 续签同一推导); 到期 = 现在 + 项目缺省天数; 理由 = 卡标题;
    拉起授权 = 本卡执行阶段或审计阶段要拉 pi(AIPOS-F201 件②; AIPOS-F211 件③: 两阶段 harness 都经 loop_driver.card_harness 唯一推导——
    缺省由 workspace_config.card_execution_mode 推出, 审计卡恒 pi; 卡走独立审计(next_resolver.card_independent_audit)才计审计阶段)= [pi],
    否则同驱动方上一信封; policy_id = F166 next_policy_id。顾问 owner_ask.grant 逐字段覆盖。
    返回 {..., sources: {字段: 来源键}}。"""
    from datetime import timedelta

    from tools.aipos_cli.autonomy_policy import card_lane_refs, envelope_card_need, envelope_overview, envelope_usage, next_policy_id
    from tools.aipos_cli.clock import iso_z, utc_now
    from tools.aipos_cli.workspace_config import project_grant_defaults, project_repos

    defaults = project_grant_defaults(root)
    now = now or utc_now()
    previous = next((r for r in envelope_overview(root)["envelopes"] if r["agent_or_role"] == driver and r.get("max_tasks")), None)
    sources: dict[str, str] = {}
    lane = card_fm.get("lane") if isinstance(card_fm.get("lane"), dict) else {}
    raw_repo = str(lane.get("repo") or "").strip()
    if raw_repo:
        repos, sources["lane_repos"] = [raw_repo], "card"
    else:
        names = set((project_repos(root).get("items") or {}))
        repos = [r for r in card_lane_refs(root, card_fm)["refs"] if r in names]
        sources["lane_repos"] = "derived"
    # AIPOS-F192: 额度 = 业务卡张数——项目显式声明优先, 否则本卡所需(本卡对上一信封的放行状态; 无上一信封 = 新卡 1 张)
    explicit = (defaults.get("_source") or {}).get("max_tasks") == "project.json"
    need = envelope_card_need(root, envelope_usage(root, previous["policy_id"], now=now) if previous else {}, task_id=task_id)
    routed_pi = _card_routes_to_pi(root, card_fm, task_id=task_id)
    grant: dict[str, Any] = {
        "agent_or_role": driver, "task_mode": str(card_fm.get("task_mode") or "").strip(), "lane_repos": repos,
        "launch_harnesses": [routed_pi] if routed_pi else list((previous or {}).get("launch_harnesses") or []),
        "max_tasks": int(defaults["max_tasks"]) if explicit else int(need["max_tasks"]),
        "expires_at": iso_z(now + timedelta(days=int(defaults["days"]))),
        "reason": str(card_fm.get("title") or "").strip() or task_id,
    }
    sources.update({"agent_or_role": "derived", "task_mode": "card", "reason": "card", "expires_at": "project_default",
                    "launch_harnesses": "card_execution" if routed_pi else ("previous_envelope" if previous else "derived"),
                    "max_tasks": "project_default" if explicit else "card_need"})
    for key in GRANT_FIELDS:
        value = (advisor_grant or {}).get(key)
        if value not in (None, "", []):
            grant[key], sources[key] = (list(value) if isinstance(value, list) else value), "advisor"
    grant["policy_id"] = next_policy_id(root, identities={driver}, previous=(previous or {}).get("policy_id"), project=project)
    grant.update({"task_id": task_id, "title": str(card_fm.get("title") or "").strip() or None, "project": project,
                  "previous_policy_id": (previous or {}).get("policy_id"), "sources": sources, "quota_basis": need["basis"]})
    return grant


def _card_routes_to_pi(root: Path, card_fm: dict[str, Any], *, task_id: str = "") -> str | None:
    """AIPOS-F201 件②: 本卡推进要 loop 拉起 pi 工位 → 拉起授权 harness 名(EXECUTION_MODE_PI), 否则 None。
    AIPOS-F211 件③(F208 P1-5): 两阶段都问 loop 拉起同一推导 loop_driver.card_harness(卡面值, 缺 = workspace_config.card_execution_mode 推出;
    项目未声明 execution = card.schema default_by_task_mode)——①执行阶段 = 本卡 harness(审计卡本身恒 pi); ②审计阶段 = 本卡走独立审计
    (next_resolver.card_independent_audit)时其审计卡的 harness(audit_card=True, 审计恒为独立 pi 审计工位)。原只问执行阶段且不传 audit_card,
    子 agent 执行的代码卡授权建议不含 pi, 审计阶段因信封未授权 pi 停手工(F208 E18)。execution 段形不合 = ExecutionDeclarationError(ValueError)向上,
    授权建议点名推不出(不猜)。"""
    from tools.aipos_cli.audit_derivation import is_audit_card
    from tools.aipos_cli.loop_driver import CardHarnessUnresolved, card_harness
    from tools.aipos_cli.next_resolver import card_independent_audit
    from tools.aipos_cli.workspace_config import EXECUTION_MODE_PI

    tid = task_id or str(card_fm.get("task_id") or "")
    try:
        stages = [card_harness(card_fm, root, task_id=tid)]
    except CardHarnessUnresolved:
        # 只在执行方式 = 子 agent 时抛(该执行者无接入登记的会话 harness): 执行阶段确定不拉 pi, 具体 harness 与授权建议无关
        stages = []
    if not is_audit_card(tid, card_fm) and card_independent_audit(card_fm):
        stages.append(card_harness({"task_mode": "audit", "reviewed_task_id": tid}, root, task_id=tid, audit_card=True))
    return EXECUTION_MODE_PI if EXECUTION_MODE_PI in stages else None


def grant_line(item: dict[str, Any], decl: dict[str, Any] | None = None) -> str:
    """授权请求一行人话(CLI 收件箱 / 收件箱页事项行同用; 文案读 grant_request.texts)。"""
    g = grant_declaration(decl)
    t = g["texts"]
    grant = item["grant"]
    repos = ", ".join(grant["lane_repos"])
    return _fmt(t["line"], "grant_request.texts.line", advisor=grant["agent_or_role"], task_id=grant["task_id"],
                title_part=_fmt(t["title_part"], "grant_request.texts.title_part", title=grant["title"]) if grant.get("title") else "",
                mode_label=g["mode_labels"].get(grant["task_mode"]) or g["mode_any"],
                repos_part=_fmt(t["repos_part"], "grant_request.texts.repos_part", repos=repos) if repos else g["repos_any"],
                launch_part=(_fmt(t["launch_part"], "grant_request.texts.launch_part", harnesses=", ".join(grant["launch_harnesses"]))
                             if grant.get("launch_harnesses") else ""),
                max_tasks=grant["max_tasks"], expires_date=str(grant["expires_at"])[:10], reason=grant["reason"])


def _attach_grant(item: dict[str, Any], root: Path, project: str, *, card_fm: dict[str, Any], driver: str,
                  advisor_grant: dict[str, Any] | None, decl: dict[str, Any]) -> None:
    """授权请求事项附 grant(建议)/ grant_error(推不出, 原因点名)+ 看板链接与裁决行。"""
    from tools.aipos_cli.board_pages import pages_declaration
    from tools.aipos_cli.workspace_config import GrantRequestDefaultsError

    g = grant_declaration(decl)
    try:
        if not driver:
            raise ValueError("运行记录无驱动方(driver)")
        item["grant"] = suggest_grant(root, project, task_id=str(item["task_id"]), card_fm=card_fm, driver=driver,
                                      advisor_grant=advisor_grant)
    except (GrantRequestDefaultsError, OSError, ValueError, LookupError) as exc:  # named in the item (not guessed), item still listed
        item["grant_error"] = f"{type(exc).__name__}: {exc}"
        return
    item["board_url"] = f"{pages_declaration()['nav_links']['inbox']}#{item['item_id']}"
    item["decide_line"] = _fmt(g["texts"]["decide_board"], "grant_request.texts.decide_board", board_url=item["board_url"],
                               command=item["decide_command"])


def _covered_now(root: Path, task_id: str, card_fm: dict[str, Any], driver: str) -> dict[str, Any] | None:
    """授权请求已自行解除: 驱动方对本卡已有覆盖信封(loop 同一判据 loop_driver.find_envelope → autonomy_policy.select_envelope)= 该信封, 否则 None。
    逐谓词调试轨迹照 F142 唯一开关(发射处 trace_envelope 一处判定; 本处不另开关)。"""
    from tools.aipos_cli.loop_driver import find_envelope

    if not driver:
        return None
    policy, _reasons = find_envelope(root, task_id=task_id, task_fm=card_fm, driver_actor=driver)
    return policy


def _loop_items(root: Path, project: str, decl: dict[str, Any], home_root: Path | None, errors: list[dict[str, str]],
                relays: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """① 每卡最近一次 loop 运行的顾问下一动作 = owner_needed(唯一判定 loop_run_record.loop_status)。卡已结案 / 撤回 = 已自行解除, 不列;
    冻结卡 = 四视图同一口径不列。运行记录 owner_ask(lybra loop note 补写)优先于声明缺省文案。
    AIPOS-F180: 事由 ∈ grant_request.reasons = 授权请求: 驱动方已有覆盖本卡的信封 = 已自行解除不列; 否则附 grant 建议(suggest_grant)。
    AIPOS-F201 件③: 已自行解除(签发生效)而本卡最近运行仍停在该请求 = 顾问未续跑 → relays 收一条「已签发」转达(窗口内)。"""
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
        if na.get("action") == "advisor_fix" and na.get("reason") == "owner_ruled" and not view.get("frozen"):
            # AIPOS-F201 item 3 (a): ruled (loop status already reads the ruling) but this run is still the latest = not re-run yet
            # AIPOS-F222: an owner_ask merged into the rework-limit item is relayed by that item; one answered by a later ruling on
            # another item is relayed there (no second paragraph for the same decision)
            ask_mark = na.get("owner_ask_inbox") or {}
            own = not ask_mark or (ask_mark.get("category") != "rework_limit" and ask_mark.get("resolved_by") != "later")
            entry = (_ruled_loop_relay(root, project, task_id=task_id, view=view, na=na, decl=decl)
                     if relays is not None and own else None)
            if entry is not None:
                relays.append(entry)
            continue
        # AIPOS-F222 件②: 顾问 loop note 补写的 owner_ask 落在下一动作非等 Owner 的运行上(loop status 经 owner_ask_destination 标 owner_ask_inbox)
        # = 照列(事由 advisor_ask); 判归返工上限事项 = 已并入该事项(_merge_owner_ask), 不另列
        marker = na.get("owner_ask_inbox") if na.get("action") != "owner_needed" else None
        if isinstance(marker, dict) and marker.get("category") == "rework_limit":
            continue
        advisor_ask = isinstance(marker, dict) and bool(marker.get("item_id"))
        if (na.get("action") != "owner_needed" and not advisor_ask) or view.get("frozen"):
            continue
        ask_spec = cat["advisor_ask"]
        reason = str(ask_spec["reason"]) if advisor_ask else str(na.get("reason") or "")
        ask_rec = view.get("owner_ask") if isinstance(view.get("owner_ask"), dict) else {}
        if ask_rec and not advisor_ask and _owner_ask_answered(root, task_id, ask_rec, project, errors):
            ask_rec = {}  # AIPOS-F222: 补写之后本卡已有 Owner 裁决 = 该请求已被回应, 不再拿旧请求文案当本事项
        driver = str(view.get("driver") or "")
        grant_request = reason in grant_declaration(decl)["reasons"]
        card_fm: dict[str, Any] = {}
        if advisor_ask:  # AIPOS-F222: 卡面(返工额度 / 标题)
            card_fm = _card_frontmatter(card_path, errors, project) or {}
        if grant_request:
            card_fm = _card_frontmatter(card_path, errors, project) or {}
            try:
                covering = _covered_now(root, task_id, card_fm, driver) if card_fm else None
            except (OSError, ValueError, LookupError) as exc:  # coverage unreadable = named, item still listed (not guessed resolved)
                covering = None
                errors.append({"project": project, "source": f"授权请求 {task_id} 信封覆盖判定", "error": f"{type(exc).__name__}: {exc}"})
            if covering is not None:  # AIPOS-F180: envelope now covers this card for the driver = self-resolved, not listed
                if relays is not None:  # AIPOS-F201 item 3: signed but the advisor has not re-run the loop yet = relay
                    entry = _envelope_relay(root, project, task_id=task_id, driver=driver, policy=covering, decl=decl)
                    if entry is not None:
                        relays.append(entry)
                continue
        # AIPOS-F225: an advisor escalation of a tier-2 / tier-3 run keeps the run's own reason for the declared option / advice tables
        basis = str(view.get("end_reason") or reason) if advisor_ask else reason
        if ask_rec.get("options"):
            options = [{"key": str(o.get("key")), "label": str(o.get("label"))} for o in ask_rec["options"] if isinstance(o, dict)]
        else:
            options = _option_list(list((cat.get("options_by_reason") or {}).get(reason) or (cat.get("options_by_reason") or {}).get(basis)
                                        or cat["default_options"]), decl)
        # AIPOS-F202: options pinned for this reason (e.g. reaudit on audit_blocked_repeated) stay offered after the advisor's own;
        # AIPOS-F225: an advisor escalation (advisor_ask) on such a run keeps them too (manual fallback of the tier-2 auto path)
        pinned_by = cat.get("pinned_options_by_reason") or {}
        pinned = [*(pinned_by.get(reason) or []), *((pinned_by.get(basis) or []) if advisor_ask else [])]
        options += _option_list([k for k in dict.fromkeys(pinned) if k not in {o["key"] for o in options}], decl)
        if advisor_ask and _rework_exhausted(root, task_id, card_fm, project, errors):  # AIPOS-F222: 返工额度已尽 = 可授权追加一轮
            options += _option_list([k for k in ask_spec["pinned_when_rework_exhausted"] if k not in {o["key"] for o in options}], decl)
        recommend = ask_rec.get("recommend") or None
        if not recommend and basis in (cat.get("recommend_by_reason") or {}):
            recommend = _fmt(cat["recommend_by_reason"][basis], f"categories.loop_owner_needed.recommend_by_reason.{basis}",
                             hint=str(na.get("hint") or ""), task_id=task_id)
        subject = _fmt(cat["subject"], "categories.loop_owner_needed.subject", task_id=task_id)
        title = (str(card_fm.get("title") or "").strip() or None) if card_fm else _card_title(card_path, errors, project)
        item = _base_item(
            decl, project=project, governance_root=root, category="loop_owner_needed", rank=int(cat["rank"]), subject=subject,
            anchor=str(view.get("run_id") or ""), source=driver,
            ask=str(ask_rec.get("ask") or na.get("detail") or reason), recommend=recommend,
            impact=(_fmt(ask_spec["impact"], "categories.loop_owner_needed.advisor_ask.impact", task_id=task_id,
                         end_reason=str(view.get("end_reason") or na.get("reason") or "?")) if advisor_ask
                    else _fmt(cat["impact"], "categories.loop_owner_needed.impact", task_id=task_id, reason=reason)),
            options=options, since=view.get("ended_at") or view.get("started_at"), task_id=task_id, home_root=home_root, title=title,
            facts={"run_id": view.get("run_id"), "reason": reason, "driver": view.get("driver"), "envelope": view.get("envelope"),
                   "hint": na.get("hint"), "record_path": view.get("record_path"), "owner_ask_by": ask_rec.get("noted_by")})
        item["relay_to"] = (str(ask_rec.get("noted_by") or "") if advisor_ask else "") or driver or None  # AIPOS-F201: the loop driver is the advisor to tell
        # AIPOS-F225: first-tier reason (loop status apply_owner_tier; advisor_ask = the note's owner_reason)
        item["owner_reason"] = (owner_reason_entry((view.get("owner_ask") or {}).get("owner_reason"), decl)
                                if advisor_ask and isinstance(view.get("owner_ask"), dict) else na.get("owner_reason"))
        try:
            item["reaudit_hint"] = reaudit_hint(root, project, task_id=task_id, view=view, reason=basis, decl=decl)
        except (OSError, ValueError, LookupError, RuntimeError) as exc:  # hint unreadable = named in errors, item still listed
            item["reaudit_hint"] = None
            errors.append({"project": project, "source": f"重新派审提示 {task_id}", "error": f"{type(exc).__name__}: {exc}"})
        if grant_request:
            if card_fm:
                _attach_grant(item, root, project, card_fm={**card_fm, "task_id": task_id}, driver=driver,
                              advisor_grant=ask_rec.get("grant") if isinstance(ask_rec.get("grant"), dict) else None, decl=decl)
            else:
                item["grant_error"] = f"卡面 {task_id} 读不出"
        out.append(item)
    return out


def _owner_ask_answered(root: Path, task_id: str, ask: dict[str, Any], project: str, errors: list[dict[str, str]]) -> bool:
    """AIPOS-F222 件②: owner_ask 补写(noted_at)之后本卡已有任一 Owner 收件箱裁决 = 已被回应。读不出 = 点名进 errors, False(照旧用请求文案)。"""
    noted = str(ask.get("noted_at") or "")
    if not noted:
        return False
    try:
        return any(str(r.get("decided_at") or "") >= noted for r in rulings_for_task(root, task_id))
    except (OSError, ValueError) as exc:
        errors.append({"project": project, "source": f"Owner 裁决记录 {task_id}", "error": f"{type(exc).__name__}: {exc}"})
        return False


def _rework_exhausted(root: Path, task_id: str, card_fm: dict[str, Any], project: str, errors: list[dict[str, str]]) -> bool:
    """AIPOS-F222 件②: 本卡返工额度已尽(轮数 ≥ queue_mutation.rework_round_budget limit 且无未消费 Owner 追加裁决)。读不出 = 点名进 errors, False。"""
    from tools.aipos_cli.queue_mutation import rework_round_budget

    rounds = card_fm.get("rework_rounds") if isinstance(card_fm, dict) else None
    if not isinstance(rounds, list) or not rounds:
        return False
    try:
        budget = rework_round_budget(root, task_id, rounds)
    except (OSError, ValueError, LookupError) as exc:
        errors.append({"project": project, "source": f"返工额度 {task_id}", "error": f"{type(exc).__name__}: {exc}"})
        return False
    return len(rounds) >= budget["limit"] and not budget["unconsumed"]


def reaudit_hint(root: Path, project: str, *, task_id: str, view: dict[str, Any], reason: str, decl: dict[str, Any]) -> str | None:
    """AIPOS-F202: hint line on a loop item whose reason ∈ reaudit_hint.reasons — the card's latest ruling with a choice in
    after_choices ("wait for the fix") was made on an earlier run, and this run's recorded deploy commit (run record deploy.commit)
    differs from that run's = "the fix may be live, re-audit can be chosen". Either commit not recorded = None (not guessed).
    Ruled run = the run whose loop item id (item_id, same construction as _loop_items) is the ruling's external_ref.
    Records unreadable = raised (caller names it in errors)."""
    spec = decl["categories"]["loop_owner_needed"].get("reaudit_hint")
    if not isinstance(spec, dict) or reason not in spec["reasons"]:
        return None
    current = str((view.get("deploy") or {}).get("commit") or "")
    if not current:
        return None
    after = {str(c) for c in spec["after_choices"]}
    rulings = [r for r in _all_rulings(root, decl) if str(r.get("task_id") or "") == task_id and str(r.get("allowed_next_action") or "") in after]
    if not rulings:
        return None
    cat = decl["categories"]["loop_owner_needed"]
    subject = _fmt(cat["subject"], "categories.loop_owner_needed.subject", task_id=task_id)
    runs = {item_id(project, "loop_owner_needed", subject, str(m.get("run_id") or ""), decl): m for m in _run_metas(root, task_id)}
    prefix = str(decl["decision"]["external_ref_prefix"])
    ruling = rulings[-1]
    ext = str(ruling.get("external_ref") or "")
    ruled = runs.get(ext[len(prefix):]) if ext.startswith(prefix) else None
    if ruled is None or str(ruled.get("run_id") or "") == str(view.get("run_id") or ""):
        return None
    before = str((ruled.get("deploy") or {}).get("commit") or "")
    if not before or before == current:
        return None
    choice = str(ruling.get("allowed_next_action") or "")
    n = int(spec["commit_chars"])
    return _fmt(spec["text"], "categories.loop_owner_needed.reaudit_hint.text",
                label=str((decl["options"].get(choice) or {}).get("label") or choice), decision_id=str(ruling.get("decision_id") or "?"),
                decided_at=str(ruling.get("decided_at") or "?"), from_commit=before[:n], to_commit=current[:n])


def _card_frontmatter(path: Path | None, errors: list[dict[str, str]], project: str) -> dict[str, Any] | None:
    """卡面 frontmatter; 卡面读不出 = 点名进 errors, None(不猜)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

    if path is None:
        return None
    try:
        fm, _body = require_frontmatter(Path(path))
    except FrontmatterReadError as exc:
        errors.append({"project": project, "source": f"卡面 {Path(path).name}", "error": str(exc)})
        return None
    return fm


def _card_title(path: Path | None, errors: list[dict[str, str]], project: str) -> str | None:
    """卡面 title(遗留视图每项一行用); 卡面读不出 = 点名进 errors, 标题缺省(不猜)。"""
    fm = _card_frontmatter(path, errors, project)
    return (str(fm.get("title") or "").strip() or None) if fm else None


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
        # AIPOS-F225 件③: needs_owner 卡须带第一档事由(卡面 tiers.needs_owner_reason_field); 存量缺 = 照列并标缺(不隐藏阻塞)
        reason_entry = owner_reason_entry(meta.get(str(tier_declaration(decl)["needs_owner_reason_field"])), decl)
        if reason_entry is None:
            ask = f"{ask} " + _fmt(tier_declaration(decl)["texts"]["needs_owner_reason_missing"], "tiers.texts.needs_owner_reason_missing",
                                   field=str(tier_declaration(decl)["needs_owner_reason_field"]), reasons=owner_reason_keys(decl))
        out.append(_base_item(
            decl, project=project, governance_root=root, category="needs_owner_card", rank=int(cat["rank_by_queue_state"][state]),
            subject=subject, anchor=anchor,
            source=str(meta.get("blocked_by") or meta.get("created_by") or meta.get("draft_created_by") or ""),
            ask=ask, recommend=None, impact=str(cat["impact_by_queue_state"][state]),
            options=_option_list(list(cat["options_by_queue_state"][state]), decl),
            since=str(meta.get("blocked_at") or meta.get("claimed_at") or meta.get("created_at") or meta.get("draft_created_at") or "") or None,
            task_id=task_id, home_root=home_root, title=meta.get("title"),
            facts={"queue_state": state, "flags": flags, "path": task.get("path")}))
        out[-1]["owner_reason"] = reason_entry
    return out


def _rework_items(root: Path, project: str, decl: dict[str, Any], home_root: Path | None,
                  errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """④ 在途卡返工轮次达上限(门 queue_rework 同一额度 queue_mutation.rework_round_budget)且锚未过、无针对它的返工节。
    AIPOS-F222 件①: 锚按卡型(审计裁决 / 非代码快车道顾问复核, 唯一判定 rework_limit_core); 件②: 本卡最近一次运行顾问补写的 owner_ask
    (下一动作非等 Owner)并入本事项(_merge_owner_ask), 不另列 loop 事项。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.next_resolver import _read_frontmatter
    from tools.aipos_cli.task_loader import iter_queue_task_paths

    warnings: list[str] = []
    out: list[dict[str, Any]] = []
    for path in iter_queue_task_paths(root, states=("claimed",)):
        try:
            fm = _read_frontmatter(path)
        except FrontmatterReadError as exc:
            errors.append({"project": project, "source": f"卡面 {path.name}", "error": str(exc)})
            continue
        task_id = str(fm.get("task_id") or "")
        try:
            item = rework_limit_core(root, task_id, fm, project=project, decl=decl, home_root=home_root, warnings=warnings)
        except (OSError, ValueError, FrontmatterReadError) as exc:
            errors.append({"project": project, "source": f"裁决 / 复核记录 {task_id}", "error": f"{type(exc).__name__}: {exc}"})
            continue
        if item is None:
            continue
        _merge_owner_ask(root, item, decl, errors)
        out.append(item)
    for text in dict.fromkeys(warnings):
        errors.append({"project": project, "source": "返工轮次上限声明", "error": text})
    return out


def rework_limit_core(root: Path, task_id: str, fm: dict[str, Any] | None, *, project: str | None = None,
                      decl: dict[str, Any] | None = None, home_root: Path | None = None,
                      warnings: list[str] | None = None) -> dict[str, Any] | None:
    """AIPOS-F222 件①: 一张在途卡的「返工轮次达上限」事项(不含 owner_ask 并入; 收件箱 _rework_items、owner_ask_destination 同读, 禁第二份)。
    达上限 = 返工轮数 ≥ queue_mutation.rework_round_budget 的 limit(max_rounds + 已消费 Owner 追加裁决数)。锚: 非代码快车道卡
    (next_resolver.fast_lane_review)= 最新顾问复核记录(advisor_review_anchor); 其余 = 最新常规裁决(非 PASS 族)。锚已有针对它的返工轮
    (rework_round_for_verdict)= 不算。不成立 = None。卡面不是 claimed 交给调用方筛(本函数只读真相)。记录读不出 = 向上。"""
    from tools.aipos_cli.next_resolver import (_PASS_VERDICTS, _read_task_records, advisor_review_anchor, fast_lane_review,
                                               rework_round_for_verdict)
    from tools.aipos_cli.queue_mutation import rework_round_budget

    decl = decl or inbox_declaration()
    fm = fm if isinstance(fm, dict) else {}
    rounds = fm.get("rework_rounds")
    if not isinstance(rounds, list) or not task_id:
        return None
    root = Path(root)
    budget = rework_round_budget(root, task_id, rounds, warnings)
    if len(rounds) < budget["limit"]:
        return None
    cat = decl["categories"]["rework_limit"]
    if fast_lane_review(root, task_id, fm):
        anchor = advisor_review_anchor(root, task_id)
        if anchor is None:
            return None
        kind, view = "advisor_review", anchor[1]
        texts = cat["advisor_review"]
    else:
        kind, view = "audit_verdict", _read_task_records(root, task_id).get("latest_verdict") or {}
        texts = cat
        value = str(view.get("verdict") or "").upper()
        if not value or value in _PASS_VERDICTS:
            return None
    if rework_round_for_verdict(fm, view) is not None:
        return None
    consumed = len(budget["consumed"])
    vid = str(view.get("verdict_id") or "")
    value = str(view.get("verdict") or "").upper()
    ext_part = _fmt(cat["extension_part"], "categories.rework_limit.extension_part", consumed=consumed) if consumed else ""
    subject = _fmt(cat["subject"], "categories.rework_limit.subject", task_id=task_id)
    where = "categories.rework_limit" + (".advisor_review" if kind == "advisor_review" else "")
    return _base_item(
        decl, project=project or root.resolve().name, governance_root=root.resolve(), category="rework_limit", rank=int(cat["rank"]),
        subject=subject, anchor=f"{len(rounds)}|{vid}", source=str(fm.get("created_by") or fm.get("draft_created_by") or ""),
        ask=_fmt(texts["ask"], f"{where}.ask", rounds=len(rounds), max_rounds=budget["limit"], verdict=value, verdict_id=vid,
                 extension_part=ext_part),
        recommend=None, impact=str(texts["impact"]), options=_option_list(list(cat["options"]), decl),
        since=str(view.get("verdict_at") or "") or None, task_id=task_id, home_root=home_root, title=fm.get("title"),
        facts={"rounds": len(rounds), "max_rounds": budget["max_rounds"], "limit": budget["limit"], "verdict_id": vid,
               "anchor_kind": kind, "consumed_extensions": list(budget["consumed"])})


def _merge_owner_ask(root: Path, item: dict[str, Any], decl: dict[str, Any], errors: list[dict[str, str]]) -> None:
    """AIPOS-F222 件②: 本卡最近一次运行顾问补写的 owner_ask 判归本事项(loop status 同一判定: 下一动作 owner_ask_inbox.item_id = 本事项)=
    顾问建议 = owner_ask.recommend, 选项 = 声明选项后追加 owner_ask 自定义选项(去重), 记 facts.owner_ask_run / owner_ask_by。
    运行记录读不出 = 点名进 errors, 事项照列(不并入, 不猜)。"""
    from tools.aipos_cli.loop_run_record import LoopRunRecordError, loop_status

    task_id = str(item.get("task_id") or "")
    try:
        runs = loop_status(root, task_id).get("runs") or []
    except (OSError, ValueError, LookupError, RuntimeError, LoopRunRecordError) as exc:
        errors.append({"project": item["project"], "source": f"返工上限事项并入顾问请求 {task_id}", "error": f"{type(exc).__name__}: {exc}"})
        return
    if not runs:
        return
    view = runs[-1]
    marker = (view.get("next_action") or {}).get("owner_ask_inbox") or {}
    if marker.get("item_id") != item["item_id"]:
        return
    ask = view.get("owner_ask") if isinstance(view.get("owner_ask"), dict) else {}
    keys = {o["key"] for o in item["options"]}
    item["options"] = item["options"] + [{"key": str(o.get("key")), "label": str(o.get("label"))} for o in ask.get("options") or []
                                         if isinstance(o, dict) and str(o.get("key")) not in keys]
    if ask.get("recommend"):
        item["recommend"] = str(ask["recommend"])
    item["facts"].update({"owner_ask_run": view.get("run_id"), "owner_ask_by": ask.get("noted_by"), "owner_ask": ask.get("ask")})
    if ask.get("noted_by"):
        item["relay_to"] = str(ask["noted_by"])


def owner_ask_destination(root: Path, task_id: str, view: dict[str, Any], na: dict[str, Any], *,
                          decl: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """AIPOS-F222 件②: 一次运行上顾问补写的 owner_ask 进哪个收件箱事项(唯一判定: 收件箱 _loop_items / _merge_owner_ask、loop note 回显与拒写、
    loop status 裁决回查同读)。na = 该运行未经裁决改写的下一动作(loop_run_record.next_action)。
    返回 None = 运行无 owner_ask; 否则 {item_id, category, merged, reason(不列的原因键 | None), decision_id, choice, ...}:
    下一动作 owner_needed = 该运行的 loop 事项(既有路径); 否则同卡有返工上限事项(rework_limit_core)= 并入它(merged), 无 = loop 事项
    (事由 categories.loop_owner_needed.advisor_ask.reason)。不列: 卡已结案 / 撤回(card_closed)、冻结(frozen)、该事项已有 Owner 裁决或
    owner_ask 补写之后本卡已有任一 Owner 收件箱裁决(ruled; 已被 Owner 回应)。记录读不出 = 向上(调用方点名)。"""
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.task_loader import find_task_card

    ask = view.get("owner_ask") if isinstance(view.get("owner_ask"), dict) else {}
    if not str(ask.get("ask") or "").strip():
        return None
    decl = decl or inbox_declaration()
    root = Path(root)
    out: dict[str, Any] = {"item_id": None, "category": "loop_owner_needed", "merged": False, "reason": None, "decision_id": None,
                           "choice": None, "run_id": view.get("run_id"), "state": None}
    path, state = find_task_card(root, task_id)
    out["state"] = state
    if path is None or state in ("completed", "withdrawn"):
        out["reason"] = "card_closed"
        return out
    if view.get("frozen"):
        out["reason"] = "frozen"
        return out
    loop_id = loop_item_id(root, task_id, str(view.get("run_id") or ""), decl)
    out["item_id"] = loop_id
    if na.get("action") != "owner_needed":
        fm = require_frontmatter(Path(path))[0] if state == "claimed" else {}
        core = rework_limit_core(root, task_id, fm, decl=decl) if fm else None
        if core is not None:
            out.update({"item_id": core["item_id"], "category": "rework_limit", "merged": True})
    ruling = read_ruling(root, out["item_id"], decl)
    if ruling is None and na.get("action") != "owner_needed":
        noted = str(ask.get("noted_at") or "")
        later = [r for r in rulings_for_task(root, task_id) if noted and str(r.get("decided_at") or "") >= noted]
        ruling = later[-1] if later else None
        out["resolved_by"] = "later" if ruling is not None else None
    elif ruling is not None:
        out["resolved_by"] = "item"
    if ruling is not None:
        out.update({"reason": "ruled", "decision_id": ruling.get("decision_id"), "choice": ruling.get("allowed_next_action"),
                    "ruling": ruling})
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


def _owner_check_items(root: Path, project: str, decl: dict[str, Any], home_root: Path | None,
                       errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """⑦ AIPOS-F175: 验证状态 = owner_pending 的标准(推导唯一实现 roadmap_drift.roadmap_status), 每条一个事项; 锚点 = 标准 + 已自动验证通过的
    挂接卡 + 已有抽查记录数(新交付 / 新记录 = 新事项)。推导读不出处已由 roadmap_drift 类别点名(不重复进 errors)。项目无路线图 = 无事项。"""
    from tools.aipos_cli.board_pages import pages_declaration, roadmap_url
    from tools.aipos_cli.roadmap_drift import roadmap_status

    cat = decl["categories"]["owner_check"]
    view = roadmap_status(root, project=project)
    out: list[dict[str, Any]] = []
    for ms in view["milestones"]:
        if ms["status"] == "dropped":
            continue
        for row in ms["criteria"]:
            if row.get("state") != "owner_pending":
                continue
            cid = str(row["id"])
            evidence = [ev for ev in row["evidence"] if ev["task_id"] in row["qualified_cards"]]
            lines = []
            for ev in evidence:
                for entry in (ev.get("artifacts") or {}).get("entries") or []:
                    lines.append(_fmt(cat["artifact_line"], "categories.owner_check.artifact_line", task_id=ev["task_id"], kind=entry["kind"],
                                      files=", ".join(f["rel"] for f in entry["files"]) or str(cat["no_files"])))
            advisor = str(row.get("verify_target_by") or "")
            verify_command = _fmt(cat["verify_command"], "categories.owner_check.verify_command", governance_root=str(root), criterion=cid)
            fill = {"criterion": cid, "statement": row["statement"], "cards": ", ".join(row["qualified_cards"]),
                    "artifacts": "; ".join(lines) or str(cat["no_files"]), "milestone": ms["id"], "lane": ms["lane"],
                    "met": ms["progress"]["met"], "total": ms["progress"]["total"], "advisor": advisor or str(decl["texts"]["unknown_source"])}
            target_ev = next((ev for ev in evidence if ev["task_id"] == row.get("verify_target")), None)
            item = _base_item(
                decl, project=project, governance_root=root, category="owner_check", rank=int(cat["rank"]),
                subject=_fmt(cat["subject"], "categories.owner_check.subject", **fill),
                anchor=f"{cid}|{','.join(sorted(row['qualified_cards']))}|{len(row.get('verifications') or [])}", source=advisor,
                ask=_fmt(cat["ask"], "categories.owner_check.ask", **fill),
                recommend=_fmt(cat["recommend"], "categories.owner_check.recommend", **fill),
                impact=_fmt(cat["impact"], "categories.owner_check.impact", **fill),
                options=_option_list(list(cat["options"]), decl), since=(target_ev or {}).get("verdict_at"), task_id=row.get("verify_target"),
                home_root=home_root, facts={"milestone": ms["id"], "criterion": cid, "lane": ms["lane"], "verify_command": verify_command,
                                            "artifacts": [f["rel"] for ev in evidence for e in (ev.get("artifacts") or {}).get("entries") or []
                                                          for f in e["files"]]})
            item["decide_line"] = _fmt(cat["decide_line"], "categories.owner_check.decide_line", verify_command=verify_command)
            item["board_url"] = roadmap_url(pages_declaration(), project, str(ms["id"]))
            out.append(item)
    return out


_COLLECTORS = (("loop_owner_needed", _loop_items), ("needs_owner_card", _needs_owner_items), ("rework_limit", _rework_items),
               ("supplementary_fail", _supplementary_items), ("envelope_warning", _envelope_items), ("roadmap_drift", _roadmap_items),
               ("owner_check", _owner_check_items), ("auto_released", _auto_release_items))


def collect_project_inbox(governance_root: Path, *, project: str | None = None, home_root: Path | None = None,
                          decl: dict[str, Any] | None = None, categories: tuple[str, ...] | None = None) -> dict[str, Any]:
    """一个项目治理根的待决事项(未裁决者)+ 读不出处。项目名缺省 = 治理根目录名(home 根下的注册名)。
    categories = 只算这些类别(AIPOS-F180: 信封页「待授权请求」只要 loop 类; 缺省 = 全部)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.task_loader import task_card_lookup_scope
    from tools.schema_loader import SchemaLoadError

    decl = decl or inbox_declaration()
    root = Path(governance_root).resolve()
    name = project or root.name
    errors: list[dict[str, str]] = []
    items: list[dict[str, Any]] = []
    notices: list[dict[str, Any]] = []  # AIPOS-F225: 第二档类别(tiers.categories)事项, 不计待定数
    decided: list[dict[str, Any]] = []
    relays: list[dict[str, Any]] = []
    with task_card_lookup_scope(root):
        for category, collect in _COLLECTORS:
            if categories is not None and category not in categories:
                continue
            try:
                found = collect(root, name, decl, home_root, errors, relays) if collect is _loop_items else collect(root, name, decl, home_root, errors)
            except SchemaLoadError:
                raise  # 声明坏 = 整个命令拒(不是某项目的真相问题)
            except (OSError, ValueError, LookupError, RuntimeError, FrontmatterReadError) as exc:
                errors.append({"project": name, "source": decl["categories"][category]["label"], "error": f"{type(exc).__name__}: {exc}"})
                continue
            for item in found:
                if "owner_reason" not in item:  # AIPOS-F225: 类别级第一档事由(tiers.categories.<类别>.owner_reason; 无 = None)
                    item["owner_reason"] = owner_reason_entry(tier_declaration(decl)["categories"][category].get("owner_reason"), decl)
                if "relay_to" not in item:  # AIPOS-F201 item 3: the advisor to tell (card driver / item source)
                    item["relay_to"] = _relay_target(root, item, errors, decl)
                try:
                    ruling = read_ruling(root, item["item_id"], decl)
                except FrontmatterReadError as exc:
                    errors.append({"project": name, "source": f"裁决记录 {item['decision_id']}", "error": str(exc)})
                    continue
                tier = category_tier(category, decl)
                item["owner_tier"] = tier
                (decided if ruling else items if tier == 1 else notices).append(item)
                if ruling:  # AIPOS-F201 item 3 (a): ruled, but the truth still stands = the advisor has not continued
                    entry = _decision_relay(root, item, ruling, decl)
                    if entry is not None:
                        relays.append(entry)
        if categories is None or "needs_owner_card" in categories:
            relays += _released_relays(root, name, decl, errors)  # AIPOS-F201 item 3 (b)
            relays += _correction_relays(root, name, decl, errors)  # AIPOS-F207 件②: 纠正已记、承接卡未开跑
            # AIPOS-F213 件②: 选撤换 / 纠正的收件箱裁决尚未执行 = 转达带 --decision-id 的命令(与上面已有转达同一裁决不重复)
            seen = {str(r.get("decision_id")) for r in relays if r.get("decision_id")}
            relays += [r for r in _queue_ruling_relays(root, name, decl, errors) if str(r.get("decision_id")) not in seen]
    return {"project": name, "governance_root": str(root), "items": items, "notices": notices, "decided_hidden": len(decided),
            "errors": errors, "relays": relays}


def _sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """AIPOS-F225: 第一档事由 ∈ tiers.tier1.inbox_last(验收抽查)的事项排末尾(可攒着批量看); 其余按 rank / 发生时刻。"""
    last = set(tier_declaration()["tier1"].get("inbox_last") or [])
    reason = str((item.get("owner_reason") or {}).get("key") or "")
    return (reason in last, int(item["rank"]), str(item.get("since") or "9999"), item["project"], item["item_id"])


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


def collect_inbox(home_root: str | Path | None = None, *, project: str | None = None, include_paused: bool = False,
                  categories: tuple[str, ...] | None = None) -> dict[str, Any]:
    """跨 home 根全部已注册项目(workspace_config._project_candidates 唯一枚举)。返回 {home_root, home_source, projects, items(已排序),
    decided_hidden, paused_hidden, errors}。--project 不在注册清单 = ValueError(用法错, 不猜)。
    AIPOS-F170 件②: project.json status = paused(唯一读取口 workspace_config.project_status)的项目缺省不列(include_paused / 显式 --project
    照列), 名单进 paused_hidden; status 读不出 / 值不合 = 该项目照列并在 errors 点名(不猜成暂停)。
    AIPOS-F177 件③: 全程一个请求级只读作用域(task_loader.read_request_scope): 同一项目在各类别间共用卡索引与整根推导(路线图 / 信封)。"""
    from tools.aipos_cli.task_loader import read_request_scope

    with read_request_scope():
        return _collect_inbox(home_root, project=project, include_paused=include_paused, categories=categories)


def grant_requests(inbox: dict[str, Any]) -> list[dict[str, Any]]:
    """AIPOS-F180 件②: 收件箱数据中的授权请求(带 grant 建议的事项; 收件箱页 / 信封页「待授权请求」同读)。"""
    return [item for item in inbox["items"] if item.get("grant")]


# ---------------------------------------------------------------------------
# AIPOS-F201 件①③: 「告诉哪位顾问、做什么」话术(声明 verbs.schema lybra_owner_inbox.relay, 中英; CLI 与看板同读同文)
# ---------------------------------------------------------------------------

_RELAY_KEYS = ("window_days", "skip_choices", "driver_fields", "commands", "texts")
_RELAY_COMMANDS = ("decide", "resume_no_task")
_RELAY_TEXTS = ("section_heading", "section_note", "cli_heading", "tell", "lead", "entry_decision", "words_part", "entry_envelope",
                "launch_part", "notes", "choice_heading", "choice_summary", "choice_body", "choice_owner_words", "unknown_advisor")
#: 卡面「要 Owner 定」标志(与 needs_owner 类事项 flags 同一组; 仍为真 = 事项仍在, 不算已放行)
_OWNER_FLAGS = ("needs_owner", "owner_review_required", "approval_required")


def relay_declaration(decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """verbs.schema lybra_owner_inbox.relay(fail-closed): 键齐; window_days 正整数; skip_choices ⊆ options; driver_fields 非空字符串列表;
    命令模板齐; 文案语言恰为看板 locale.supported 且每种语言 _RELAY_TEXTS 齐(F198 话术声明同一形)。"""
    from tools.aipos_cli.board_pages import pages_declaration
    from tools.schema_loader import SchemaLoadError

    d = decl or inbox_declaration()
    r = d.get("relay")
    where = f"verbs.{VERB}.relay"
    if not isinstance(r, dict) or any(k not in r for k in _RELAY_KEYS):
        raise SchemaLoadError(f"{where} 未声明齐 {list(_RELAY_KEYS)}")
    if not isinstance(r["window_days"], int) or isinstance(r["window_days"], bool) or r["window_days"] < 1:
        raise SchemaLoadError(f"{where}.window_days 须为正整数")
    if not isinstance(r["skip_choices"], list) or not set(r["skip_choices"]) <= set(d["options"]):
        raise SchemaLoadError(f"{where}.skip_choices 须为 options 内的选项键列表")
    if not isinstance(r["driver_fields"], list) or not r["driver_fields"] or not all(isinstance(f, str) and f for f in r["driver_fields"]):
        raise SchemaLoadError(f"{where}.driver_fields 须为非空字段名列表")
    if not all(isinstance((r["commands"] or {}).get(k), str) and r["commands"][k] for k in _RELAY_COMMANDS):
        raise SchemaLoadError(f"{where}.commands 未声明齐 {list(_RELAY_COMMANDS)}")
    supported = list(pages_declaration()["locale"]["supported"])
    if sorted(r["texts"]) != sorted(supported):
        raise SchemaLoadError(f"{where}.texts 语言须恰为看板 locale.supported {supported}")
    for lang, table in r["texts"].items():
        missing = [k for k in _RELAY_TEXTS if not isinstance((table or {}).get(k), str)]
        if missing:
            raise SchemaLoadError(f"{where}.texts.{lang} 缺 {missing}")
    return r


def relay_texts(lang: str | None = None) -> dict[str, str]:
    """一种语言的话术文案(未知 / 缺省 = 看板缺省语言; CLI 用缺省语言)。"""
    from tools.aipos_cli.board_pages import pages_declaration

    table = relay_declaration()["texts"]
    default = str(pages_declaration()["locale"]["default"])
    return table[lang] if lang in table else table[default]


def _run_metas(root: Path, task_id: str) -> list[dict[str, Any]]:
    """本卡 loop 运行记录(旧→新; owner_ask_labels 同一读法)。读不出 = LoopRunRecordError 向上(不吞)。"""
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import _run_files, loop_runs_dir, read_run, run_record_declaration

    directory = loop_runs_dir(Path(root), task_id)
    if not directory.is_dir():
        return []
    rdecl = run_record_declaration(load_loop_contract())
    return sorted((read_run(p, rdecl) for p in _run_files(directory)),
                  key=lambda m: (str(m.get("started_at") or ""), str(m.get("run_id") or "")))


def card_driver(root: Path, task_id: str, card_fm: dict[str, Any] | None, decl: dict[str, Any] | None = None) -> str | None:
    """卡的驱动顾问(话术转达对象): 本卡最近一次 loop 运行的 driver; 无运行 = 卡面 relay.driver_fields 依序首个非空; 都无 = None。"""
    runs = _run_metas(Path(root), task_id) if task_id else []
    driver = next((str(m.get("driver") or "").strip() for m in reversed(runs) if str(m.get("driver") or "").strip()), "")
    if driver:
        return driver
    fm = card_fm or {}
    return next((str(fm.get(f) or "").strip() for f in relay_declaration(decl)["driver_fields"] if str(fm.get(f) or "").strip()), None)


def _relay_target(root: Path, item: dict[str, Any], errors: list[dict[str, str]], decl: dict[str, Any]) -> str | None:
    """事项的转达对象: 有卡 = card_driver(卡面 / 运行记录读不出 = 点名进 errors, 退回事项来源); 无卡 = 事项来源。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.task_loader import find_task_card

    source = str(item.get("source") or "").strip() or None
    task_id = str(item.get("task_id") or "")
    if not task_id:
        return source
    try:
        path, _state = find_task_card(root, task_id)
        fm = require_frontmatter(Path(path))[0] if path else {}
        return card_driver(root, task_id, fm, decl) or source
    except (OSError, ValueError, LookupError, RuntimeError, FrontmatterReadError) as exc:
        errors.append({"project": item["project"], "source": f"转达对象 {task_id}", "error": f"{type(exc).__name__}: {exc}"})
        return source


def _resume_for(root: Path, task_id: str | None, advisor: str | None, envelope: str | None = None) -> str:
    """顾问续跑命令: 有卡 = loop status 续跑提示同一模板(routine_rejections.resume_hint, 占位同 loop_run_record._resume_fill);
    无卡 = relay.commands.resume_no_task。"""
    from tools.aipos_cli.loop_run_record import _resume_fill, status_contract

    if not task_id:
        return _fmt(relay_declaration()["commands"]["resume_no_task"], "relay.commands.resume_no_task", governance_root=str(root))
    view = {"task_id": task_id, "driver": advisor or "", "envelope": envelope or ""}
    return _fmt(status_contract()["next_action"]["routine_rejections"]["resume_hint"], "resume_hint", **_resume_fill(view, Path(root)))


def option_next(decl: dict[str, Any], choice: str, label: str, *, resume: str, task_id: str | None, governance_root: Path,
                decision_id: str | None = None) -> str:
    """选项的顾问下一步(options.<键>.advisor_next; 自定义选项 = custom_option.advisor_next)。裁决行 ruling_view 与话术同用。
    AIPOS-F213 件②: decision_id = 该裁决的决策记录 id(撤换 / 纠正选项的命令带 --decision-id 引用它, 不另记裁决)。"""
    spec = decl["options"].get(choice)
    template = (spec or {}).get("advisor_next") or decl["custom_option"]["advisor_next"]
    return _fmt(template, "options.advisor_next", resume=resume, task_id=task_id or "", governance_root=str(governance_root), label=label,
                decision_id=decision_id or "<decision_id>")


def _within_window(text: Any, decl: dict[str, Any]) -> bool:
    """发生时刻在 relay.window_days 天内; 时刻读不出 = True(照列, 不猜成过期)。"""
    from tools.aipos_cli.clock import utc_now

    when = _parse_utc(text)
    return when is None or when >= utc_now() - timedelta(days=int(relay_declaration(decl)["window_days"]))


def _decision_relay(root: Path, item: dict[str, Any], ruling: dict[str, Any], decl: dict[str, Any]) -> dict[str, Any] | None:
    """(a) 已裁决而真相仍在的事项 → 一条转达(skip_choices / 窗口外 = None)。"""
    choice = str(ruling.get("allowed_next_action") or "")
    if choice in relay_declaration(decl)["skip_choices"] or not _within_window(ruling.get("decided_at"), decl):
        return None
    facts = item.get("facts") or {}
    advisor = item.get("relay_to")
    labels = {o["key"]: o["label"] for o in item["options"]}
    label = str((decl["options"].get(choice) or {}).get("label") or labels.get(choice) or choice)
    resume = _resume_for(root, item.get("task_id"), advisor, facts.get("envelope") if item["category"] == "loop_owner_needed" else None)
    return {"kind": "decision", "advisor": advisor, "project": item["project"], "governance_root": str(root),
            "task_id": item.get("task_id"), "subject": item["subject"], "item_id": item["item_id"],
            "decision_id": ruling.get("decision_id") or item["decision_id"], "choice": choice, "label": label,
            "owner_words": ruling.get("owner_words"), "done_at": ruling.get("decided_at"),
            "advisor_next": option_next(decl, choice, label, resume=resume, task_id=item.get("task_id"), governance_root=root,
                                        decision_id=ruling.get("decision_id") or item["decision_id"])}


def _ruled_loop_relay(root: Path, project: str, *, task_id: str, view: dict[str, Any], na: dict[str, Any],
                      decl: dict[str, Any]) -> dict[str, Any] | None:
    """(a) loop 类: 本卡最近运行的下一动作已是 advisor_fix(owner_ruled)(loop_run_record → apply_owner_ruling 同一判定)= Owner 已裁决、驱动方未续跑
    → 一条转达; 下一步 = 该裁决的 advisor_next(loop status hint 同文)。skip_choices / 窗口外 = None。"""
    rv = na.get("owner_ruling") if isinstance(na.get("owner_ruling"), dict) else {}
    choice = str(rv.get("choice") or "")
    if not choice or choice in relay_declaration(decl)["skip_choices"] or not _within_window(rv.get("decided_at"), decl):
        return None
    return {"kind": "decision", "advisor": str(view.get("driver") or "") or None, "project": project, "governance_root": str(root),
            "task_id": task_id, "subject": _fmt(decl["categories"]["loop_owner_needed"]["subject"], "categories.loop_owner_needed.subject",
                                                task_id=task_id),
            "item_id": na.get("inbox_item_id"), "decision_id": rv.get("decision_id"), "choice": choice, "label": rv.get("label") or choice,
            "owner_words": rv.get("owner_words"), "done_at": rv.get("decided_at"), "advisor_next": str(rv.get("advisor_next") or "")}


def _all_rulings(root: Path, decl: dict[str, Any]) -> list[dict[str, Any]]:
    """收件箱裁决记录全部(decision_type = 声明 decision_type), 按裁决时刻旧→新。读不出 = 向上。"""
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.record_writer import record_root

    d = decl["decision"]
    directory = Path(root) / record_root("owner_decisions")
    out: list[dict[str, Any]] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob(f"{d['decision_id_prefix']}*.md")):
        fm, body = require_frontmatter(path)
        if str(fm.get("decision_type") or "") == d["decision_type"]:
            out.append(_ruling_record(path, fm, body))
    out.sort(key=lambda r: str(r.get("decided_at") or ""))
    return out


def _released_relays(root: Path, project: str, decl: dict[str, Any], errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """(b) 选项 effect = release_needs_owner 的裁决(窗口内)已解除标志、卡仍 pending、裁决后本卡无新 loop 运行 = 顾问未推进 → 转达。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.task_loader import find_task_card

    release = {k for k, v in decl["options"].items() if v.get("effect") == "release_needs_owner"}
    out: list[dict[str, Any]] = []
    try:
        rulings = _all_rulings(root, decl)
    except (OSError, ValueError, FrontmatterReadError) as exc:
        errors.append({"project": project, "source": "已放行转达: 裁决记录", "error": f"{type(exc).__name__}: {exc}"})
        return out
    cat = decl["categories"]["needs_owner_card"]
    for ruling in rulings:
        choice = str(ruling.get("allowed_next_action") or "")
        task_id = str(ruling.get("task_id") or "")
        if choice not in release or not task_id or not _within_window(ruling.get("decided_at"), decl):
            continue
        try:
            path, state = find_task_card(root, task_id)
            if state != "pending" or path is None:
                continue
            fm = require_frontmatter(Path(path))[0]
            if any(fm.get(f) is True for f in _OWNER_FLAGS):
                continue
            decided = _parse_utc(ruling.get("decided_at"))
            runs = _run_metas(root, task_id)
            if decided is not None and any((_parse_utc(m.get("started_at")) or decided) >= decided for m in runs):
                continue  # a loop run started at / after the ruling = the advisor has continued (second-precision clocks)
            advisor = card_driver(root, task_id, fm, decl)
        except (OSError, ValueError, LookupError, RuntimeError, FrontmatterReadError) as exc:
            errors.append({"project": project, "source": f"已放行转达 {task_id}", "error": f"{type(exc).__name__}: {exc}"})
            continue
        label = str(decl["options"][choice]["label"])
        resume = _resume_for(root, task_id, advisor)
        out.append({"kind": "decision", "advisor": advisor, "project": project, "governance_root": str(root), "task_id": task_id,
                    "subject": _fmt(cat["subject"], "categories.needs_owner_card.subject", task_id=task_id, queue_state=state),
                    "item_id": None, "decision_id": ruling.get("decision_id"), "choice": choice, "label": label,
                    "owner_words": ruling.get("owner_words"), "done_at": ruling.get("decided_at"),
                    "advisor_next": option_next(decl, choice, label, resume=resume, task_id=task_id, governance_root=root,
                                                decision_id=ruling.get("decision_id"))})
    return out


def _correction_relays(root: Path, project: str, decl: dict[str, Any], errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """AIPOS-F207 件②(已定待转达): Owner 纠正已结案卡的裁决(选项 = transitions queue_mutations.correct.decision_choice, 窗口内)已记、
    其争议标记(task_complexity.closure_disputes)所列承接卡仍 pending 且裁决后承接卡无 loop 运行 = 顾问未推进 → 转达(下一步 = 承接卡续跑命令)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.queue_mutation import correct_declaration
    from tools.aipos_cli.task_complexity import closure_disputes
    from tools.aipos_cli.task_loader import find_task_card

    choice = str(correct_declaration()["decision_choice"])
    out: list[dict[str, Any]] = []
    try:
        rulings = [r for r in _all_rulings(root, decl) if str(r.get("allowed_next_action") or "") == choice]
    except (OSError, ValueError, FrontmatterReadError) as exc:
        errors.append({"project": project, "source": "纠正转达: 裁决记录", "error": f"{type(exc).__name__}: {exc}"})
        return out
    for ruling in rulings:
        task_id = str(ruling.get("task_id") or "")
        if not task_id or not _within_window(ruling.get("decided_at"), decl):
            continue
        try:
            dispute = next((d for d in closure_disputes(root, task_id) if d.get("decision_id") == ruling.get("decision_id")), None)
            followup = str((dispute or {}).get("followup_task_id") or "")
            if not followup:
                continue
            path, state = find_task_card(root, followup)
            if state != "pending" or path is None:
                continue  # 未发布(纠正输出已给手工发布命令)/ 已认领推进 = 不转达
            decided = _parse_utc(ruling.get("decided_at"))
            if decided is not None and any((_parse_utc(m.get("started_at")) or decided) >= decided for m in _run_metas(root, followup)):
                continue
            advisor = card_driver(root, followup, require_frontmatter(Path(path))[0], decl)
        except (OSError, ValueError, LookupError, RuntimeError, FrontmatterReadError) as exc:
            errors.append({"project": project, "source": f"纠正转达 {task_id}", "error": f"{type(exc).__name__}: {exc}"})
            continue
        label = str(decl["options"][choice]["label"])
        out.append({"kind": "decision", "advisor": advisor, "project": project, "governance_root": str(root), "task_id": task_id,
                    "subject": f"{task_id} → {followup}", "item_id": None, "decision_id": ruling.get("decision_id"), "choice": choice,
                    "label": label, "owner_words": ruling.get("owner_words"), "done_at": ruling.get("decided_at"),
                    "advisor_next": option_next(decl, choice, label, resume=_resume_for(root, followup, advisor), task_id=task_id,
                                                governance_root=root, decision_id=ruling.get("decision_id"))})
    return out


def _queue_ruling_actions() -> dict[str, dict[str, Any]]:
    """AIPOS-F213 件②: 可引用收件箱裁决的队列写动作 → {选项: {action, from_states}}(声明 transitions queue_mutations.transitions.supersede /
    queue_mutations.correct 的 decision_choice 与源状态; 唯一读取口 queue_mutation.supersede_declaration / correct_declaration)。"""
    from tools.aipos_cli.queue_mutation import correct_declaration, supersede_declaration

    sup, cor = supersede_declaration(), correct_declaration()
    return {str(sup["decision_choice"]): {"action": "queue supersede", "from_states": tuple(sup["from_states"])},
            str(cor["decision_choice"]): {"action": "queue correct", "from_states": tuple(cor["from_states"])}}


def ruling_consumers(governance_root: Path, decision_id: str, task_id: str | None = None) -> list[str]:
    """AIPOS-F213 件②: 已用过该裁决的动作(人读点名)。判据 = 任一队列卡卡面 supersede.card_fields.decision_id(撤换)或
    correct.followup.ref_field(纠正承接卡)记了该裁决, 或所裁卡已有引用该裁决的争议标记(task_complexity.closure_disputes)。
    读不出 = 向上(fail-closed, 不猜成未用过)。"""
    from tools.aipos_cli.queue_mutation import correct_declaration, supersede_declaration
    from tools.aipos_cli.task_complexity import closure_disputes
    from tools.aipos_cli.task_loader import load_all_tasks

    wanted = str(decision_id or "").strip()
    fields = {str(supersede_declaration()["card_fields"]["decision_id"]): "queue supersede",
              str(correct_declaration()["followup"]["ref_field"]): "queue correct"}
    out: list[str] = []
    for task in load_all_tasks(Path(governance_root)):
        meta = task.get("metadata") or {}
        for field, action in fields.items():
            if str(meta.get(field) or "").strip() == wanted:
                out.append(f"{action}: {meta.get('task_id')}[{task.get('queue_state')}] {field}")
    if task_id:
        out += [f"queue correct: {task_id} {Path(str(d.get('path') or '')).name}".rstrip()
                for d in closure_disputes(Path(governance_root), str(task_id)) if str(d.get("decision_id") or "") == wanted]
    return out


def referenced_ruling(governance_root: Path, decision_id: str, *, choice: str, task_id: str, action: str,
                      decl: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """AIPOS-F213 件②(F208 P1-6): `queue supersede` / `queue correct` 带 --decision-id 引用收件箱已有裁决(不另记决策)时的唯一核验。
    返回 (裁决 _ruling_record | None, 拒因列表; 空 = 可引用)。判据见声明 verbs.lybra_owner_inbox.decision_reference(文案同处)。
    记录在但读不出 = FrontmatterReadError 向上(fail-closed)。"""
    from tools.aipos_cli.frontmatter import require_frontmatter

    d = decl or inbox_declaration()
    texts = (d.get("decision_reference") or {}).get("texts") or {}
    need = ("not_found", "not_inbox", "choice_mismatch", "task_mismatch", "consumed")
    if any(not isinstance(texts.get(k), str) for k in need):
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"verbs.{VERB}.decision_reference.texts 未声明齐 {list(need)}")
    did = str(decision_id or "").strip()
    path = _decision_path(Path(governance_root), did)
    if not did or not path.is_file():
        return None, [_fmt(texts["not_found"], "decision_reference.texts.not_found", decision_id=did or "<empty>", path=path)]
    ruling = _ruling_record(path, *require_frontmatter(path))
    problems: list[str] = []
    want_type = str(d["decision"]["decision_type"])
    if str(ruling.get("decision_type") or "") != want_type:
        problems.append(_fmt(texts["not_inbox"], "decision_reference.texts.not_inbox", decision_id=did,
                             got=ruling.get("decision_type"), want=want_type))
    got_choice = str(ruling.get("allowed_next_action") or "")
    if got_choice != str(choice):
        problems.append(_fmt(texts["choice_mismatch"], "decision_reference.texts.choice_mismatch", decision_id=did, got=got_choice or "-",
                             action=action, want=choice))
    got_task = str(ruling.get("task_id") or "")
    if got_task.upper() != str(task_id or "").upper():
        problems.append(_fmt(texts["task_mismatch"], "decision_reference.texts.task_mismatch", decision_id=did, got=got_task or "-",
                             task_id=task_id))
    used = ruling_consumers(Path(governance_root), did, got_task or task_id)
    if used:
        problems.append(_fmt(texts["consumed"], "decision_reference.texts.consumed", decision_id=did, by="; ".join(used)))
    return ruling, problems


def _queue_ruling_relays(root: Path, project: str, decl: dict[str, Any], errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """AIPOS-F213 件②(已定待转达): 选撤换 / 纠正(_queue_ruling_actions 的选项)的收件箱裁决(窗口内)尚未执行——未被任何动作用过
    (ruling_consumers 为空)且所裁卡仍在该动作的源状态 = 顾问未接着做 → 转达; 下一步 = 该选项 advisor_next(带 --decision-id 的命令)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.task_loader import find_task_card

    actions = _queue_ruling_actions()
    out: list[dict[str, Any]] = []
    try:
        rulings = [r for r in _all_rulings(root, decl) if str(r.get("allowed_next_action") or "") in actions]
    except (OSError, ValueError, FrontmatterReadError) as exc:
        errors.append({"project": project, "source": "撤换 / 纠正转达: 裁决记录", "error": f"{type(exc).__name__}: {exc}"})
        return out
    for ruling in rulings:
        choice = str(ruling.get("allowed_next_action") or "")
        task_id = str(ruling.get("task_id") or "")
        if not task_id or not _within_window(ruling.get("decided_at"), decl):
            continue
        try:
            path, state = find_task_card(root, task_id)
            if path is None or state not in actions[choice]["from_states"]:
                continue  # 已执行(撤换后进 withdrawn)/ 卡不在 = 不转达
            if ruling_consumers(root, str(ruling.get("decision_id") or ""), task_id):
                continue
            advisor = card_driver(root, task_id, require_frontmatter(Path(path))[0], decl)
        except (OSError, ValueError, LookupError, RuntimeError, FrontmatterReadError) as exc:
            errors.append({"project": project, "source": f"撤换 / 纠正转达 {task_id}", "error": f"{type(exc).__name__}: {exc}"})
            continue
        label = str(decl["options"][choice]["label"])
        out.append({"kind": "decision", "advisor": advisor, "project": project, "governance_root": str(root), "task_id": task_id,
                    "subject": f"{task_id} [{state}]", "item_id": None, "decision_id": ruling.get("decision_id"), "choice": choice,
                    "label": label, "owner_words": ruling.get("owner_words"), "done_at": ruling.get("decided_at"),
                    "advisor_next": option_next(decl, choice, label, resume=_resume_for(root, task_id, advisor), task_id=task_id,
                                                governance_root=root, decision_id=ruling.get("decision_id"))})
    return out


def _envelope_relay(root: Path, project: str, *, task_id: str, driver: str, policy: dict[str, Any],
                    decl: dict[str, Any]) -> dict[str, Any] | None:
    """(c) 授权请求已被签发的信封覆盖(窗口内按信封 active_from)而本卡最近运行仍停在该请求 → 一条「已签发」转达。"""
    if not _within_window(policy.get("active_from"), decl):
        return None
    pid = str(policy.get("policy_id") or "")
    return {"kind": "envelope", "advisor": driver or None, "project": project, "governance_root": str(root), "task_id": task_id,
            "policy_id": pid, "launch_harnesses": list(policy.get("launch_harnesses") or []), "done_at": policy.get("active_from"),
            "resume": _resume_for(root, task_id, driver, pid)}


def relay_groups(relays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按转达对象合并(同一顾问一段); 段按顾问名、段内按发生时刻 / 项目 / 卡排序。转达对象未知的单独一段(advisor None)。"""
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in relays:
        groups.setdefault(str(entry.get("advisor") or ""), []).append(entry)
    return [{"advisor": name or None,
             "entries": sorted(rows, key=lambda e: (str(e.get("done_at") or ""), e["project"], str(e.get("task_id") or "")))}
            for name, rows in sorted(groups.items())]


def relay_entry_line(entry: dict[str, Any], n: int, t: dict[str, str]) -> str:
    if entry["kind"] == "envelope":
        harnesses = ", ".join(entry.get("launch_harnesses") or [])
        return _fmt(t["entry_envelope"], "relay.texts.entry_envelope", n=n, project=entry["project"], task_id=entry["task_id"],
                    policy_id=entry["policy_id"], advisor=entry.get("advisor") or t["unknown_advisor"],
                    launch_part=_fmt(t["launch_part"], "relay.texts.launch_part", harnesses=harnesses) if harnesses else "",
                    resume=entry["resume"])
    words = entry.get("owner_words")
    return _fmt(t["entry_decision"], "relay.texts.entry_decision", n=n, project=entry["project"], subject=entry["subject"],
                label=entry["label"], decision_id=entry.get("decision_id") or "?",
                words_part=_fmt(t["words_part"], "relay.texts.words_part", owner_words=words) if words else "",
                advisor_next=entry["advisor_next"])


def relay_lines(group: dict[str, Any], lang: str | None = None) -> list[str]:
    """一段转达话术(CLI 收件箱段 / 看板「已定待转达」/ JSON 同一函数同一文字): 告诉 X + 引导 + 逐条 + 注意事项。"""
    t = relay_texts(lang)
    entries = group["entries"]
    lines = [_fmt(t["tell"], "relay.texts.tell", advisor=group.get("advisor") or t["unknown_advisor"]),
             _fmt(t["lead"], "relay.texts.lead", count=len(entries))]
    lines += [relay_entry_line(entry, n, t) for n, entry in enumerate(entries, 1)]
    lines.append(t["notes"])
    return lines


def choice_script(item: dict[str, Any], choice: str, lang: str | None = None) -> list[str]:
    """件①③ 看板选项话术: Owner 在看板选定该选项后复制给转达对象——owner decide 预演命令(转达人 = 转达对象, Owner 原话 = choice_owner_words)、
    --confirm 方法与该选项 advisor_next(续跑命令同 loop status)。只生成文字, 不写任何东西。"""
    import shlex

    decl = inbox_declaration()
    r = relay_declaration(decl)
    t = relay_texts(lang)
    label = next(o["label"] for o in item["options"] if o["key"] == choice)
    advisor = item.get("relay_to") or t["unknown_advisor"]
    words = _fmt(t["choice_owner_words"], "relay.texts.choice_owner_words", label=label)
    home = item.get("home_root") or str(Path(item["governance_root"]).parent)
    preview = _fmt(r["commands"]["decide"], "relay.commands.decide", item_id=item["item_id"], choice=shlex.quote(choice),
                   advisor=shlex.quote(advisor), owner_words=shlex.quote(words), home_root=shlex.quote(home))
    root = Path(item["governance_root"])
    resume = _resume_for(root, item.get("task_id"), item.get("relay_to"),
                         (item.get("facts") or {}).get("envelope") if item["category"] == "loop_owner_needed" else None)
    nxt = option_next(decl, choice, label, resume=resume, task_id=item.get("task_id"), governance_root=root, decision_id=item.get("decision_id"))
    return [_fmt(t["tell"], "relay.texts.tell", advisor=advisor),
            _fmt(t["choice_body"], "relay.texts.choice_body", project=item["project"], subject=item["subject"], label=label,
                 preview=preview, advisor_next=nxt),
            t["notes"]]


def choice_scripts_offered(item: dict[str, Any], decl: dict[str, Any] | None = None) -> bool:
    """看板给该事项逐选项话术: 授权请求(有请求卡)与经别处记录的类别(decide_redirect, 如验收抽查)除外。"""
    d = decl or inbox_declaration()
    return not item.get("grant") and not d["categories"][item["category"]].get("decide_redirect")


def _collect_inbox(home_root: str | Path | None, *, project: str | None, include_paused: bool,
                   categories: tuple[str, ...] | None = None) -> dict[str, Any]:
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
    notices: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    relays: list[dict[str, Any]] = []
    hidden = 0
    paused: list[str] = []
    # AIPOS-F181 item 3: statuses hidden by default = declaration status.inbox_hidden (paused + test); same flag shows them all
    from tools.aipos_cli.workspace_config import project_status_visibility

    hidden_statuses = project_status_visibility()["inbox_hidden"]
    for name in names:
        if not include_paused and not project:
            entry = by_name[name]
            status = entry["status"]
            if entry["status_error"]:  # 值不合 / project.json 读不出: 照列并点名
                errors.append({"project": name, "source": "project.json status", "error": entry["status_error"]})
            if status in hidden_statuses:
                paused.append(name)
                continue
        part = collect_project_inbox(home / name, project=name, home_root=home, decl=decl, categories=categories)
        items += part["items"]
        notices += part["notices"]
        errors += part["errors"]
        relays += part["relays"]
        hidden += part["decided_hidden"]
    items.sort(key=_sort_key)
    notices.sort(key=_sort_key)
    groups = relay_groups(relays)
    for group in groups:  # AIPOS-F201: JSON / API carry the paragraph in the default language (the CLI text, byte for byte)
        group["lines"] = relay_lines(group)
    return {"home_root": str(home), "home_source": source, "projects": [n for n in names if n not in paused], "items": items,
            "notices": notices, "notice_count": len(notices),
            "decided_hidden": hidden, "paused_hidden": paused, "errors": errors, "count": len(items), "relays": groups,
            "relay_count": len(groups)}


# ---------------------------------------------------------------------------
# 渲染(CLI 文本 / 看板 HTML 同一数据)
# ---------------------------------------------------------------------------

def item_lines(item: dict[str, Any], texts: dict[str, Any]) -> list[str]:
    """One inbox item as the CLI lines (AIPOS-F181: the board folds the same lines into an item's "show details")."""
    lines = [_fmt(texts["item"], "texts.item", **item), _fmt(texts["ask"], "texts.ask", ask=item["ask"])]
    if item.get("owner_reason"):  # AIPOS-F225: 第一档事由
        lines[-1] += _fmt(tier_declaration()["texts"]["owner_reason_part"], "tiers.texts.owner_reason_part", **item["owner_reason"])
    if item.get("grant"):  # AIPOS-F180: authorization request, one plain-language line (same data as the board request card)
        lines.append(grant_line(item))
    elif item.get("grant_error"):
        lines.append(_fmt(grant_declaration()["texts"]["unavailable"], "grant_request.texts.unavailable", why=item["grant_error"],
                          task_id=item.get("task_id") or ""))
    if item.get("recommend"):
        lines.append(_fmt(texts["recommend"], "texts.recommend", recommend=item["recommend"]))
    elif item["category"] == "loop_owner_needed":
        lines.append(_fmt(texts["no_recommend"], "texts.no_recommend", task_id=item.get("task_id") or ""))
    lines.append(_fmt(texts["impact"], "texts.impact", impact=item["impact"]))
    lines.append(_fmt(texts["options"], "texts.options", options=" / ".join(f"{o['key']}={o['label']}" for o in item["options"])))
    if item.get("since"):
        lines.append(_fmt(texts["since"], "texts.since", since=item["since"]))
    if item.get("reaudit_hint"):  # AIPOS-F202: a "wait for the fix" ruling, deploy changed since = re-audit can be chosen
        lines.append(_fmt(texts["hint"], "texts.hint", hint=item["reaudit_hint"]))
    if item.get("relay_to"):  # AIPOS-F201: the advisor the Owner tells (board: one message per option)
        lines.append(_fmt(texts["relay_to"], "texts.relay_to", advisor=item["relay_to"]))
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
        out.extend(item_lines(item, texts))
    notices = inbox.get("notices") or []
    if notices:  # AIPOS-F225: 第二档(已自动放行 / 告知)一行一项, 不计待定数
        tt = tier_declaration()["texts"]
        out.append("")
        out.append(_fmt(tt["notices_header"], "tiers.texts.notices_header", count=len(notices)))
        out.extend(_fmt(tt["notice_item"], "tiers.texts.notice_item", **n) for n in notices)
    groups = inbox.get("relays") or []
    if groups:  # AIPOS-F201 item 3: decided / signed, the advisor has not continued yet (same paragraphs as the board)
        rt = relay_texts()
        out.append("")
        out.append(_fmt(rt["cli_heading"], "relay.texts.cli_heading", count=len(groups)))
        for group in groups:
            out.append("")
            out.extend("  " + line for line in (group.get("lines") or relay_lines(group)))
    if inbox["errors"]:
        out.append("")
        out.append(_fmt(texts["errors_header"], "texts.errors_header", count=len(inbox["errors"])))
        out.extend(_fmt(texts["error"], "texts.error", **err) for err in inbox["errors"])
    return "\n".join(out)


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


def queue_ruling_payload(governance_root: Path, *, choice: str, task_id: str, anchor: str, subject: str, relayed_by: str,
                         owner_words: str, owner_ref: str | None, decided_at: str, note: str | None = None,
                         actor: str = "owner") -> dict[str, Any]:
    """队列写命令记 Owner 裁决的唯一构造(AIPOS-F184 supersede / AIPOS-F207 queue correct 共用): 所裁「事项」= 该写动作本身
    (项目 = 治理根目录名, 类别 = 选项键 choice, 主体 = subject, 锚点 = anchor, ID 经 item_id 唯一实现); 记录 = 既有
    owner_decision_record(与 decide 同一 payload 构造 build_decision_payload, 同一写路径 record_owner_decision + execute_dry_run);
    allowed_next_action = choice(lybra_owner_inbox.options 同名选项, 未声明 = SchemaLoadError); decided_by_ref = actor(缺省 owner),
    captured_by = relayed_by, Rationale = Owner 原话; note 进决策摘要; owner_ref 进 refs。"""
    decl = inbox_declaration()
    option = decl["options"].get(choice)
    if not isinstance(option, dict):
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"verbs.{VERB}.options.{choice} 未声明(队列写命令记 Owner 裁决引用)")
    root = Path(governance_root).resolve()
    project = root.name
    iid = item_id(project, choice, task_id, anchor, decl)
    item = {"item_id": iid, "project": project, "governance_root": str(root), "category": choice,
            "category_label": str(option["label"]), "subject": subject, "task_id": task_id, "policy_id": None,
            "ask": owner_words, "recommend": None, "impact": str(option["advisor_next"]), "anchor": anchor, "source": relayed_by,
            "options": [{"key": choice, "label": str(option["label"])}], "decision_id": decision_id_for(iid, decl)}
    payload = build_decision_payload(item, choice, note, actor, decided_at=decided_at, relayed_by=relayed_by,
                                     owner_words=owner_words, decl=decl)
    if owner_ref:
        payload["refs"] = list(payload["refs"]) + [str(owner_ref)]
        payload["owner_approval_evidence"]["refs"] = list(payload["owner_approval_evidence"]["refs"]) + [str(owner_ref)]
    return payload


def supersede_decision_payload(governance_root: Path, *, task_id: str, by: str, relayed_by: str, owner_words: str,
                               owner_ref: str | None, from_state: str, decided_at: str, actor: str = "owner") -> dict[str, Any]:
    """AIPOS-F184 件③: 撤换的 Owner 裁决(构造 = queue_ruling_payload; 类别 = transitions supersede.decision_choice, 主体 = 原卡,
    锚点 = 新卡)。"""
    from tools.aipos_cli.queue_mutation import supersede_declaration

    return queue_ruling_payload(governance_root, choice=str(supersede_declaration()["decision_choice"]), task_id=task_id, anchor=by,
                                subject=f"{task_id} [{from_state}] → {by}", relayed_by=relayed_by, owner_words=owner_words,
                                owner_ref=owner_ref, decided_at=decided_at, actor=actor)


def _effect(item: dict[str, Any], choice: str, decl: dict[str, Any]) -> str | None:
    spec = decl["options"].get(choice) if item["category"] == "needs_owner_card" else None
    return str(spec.get("effect")) if isinstance(spec, dict) and spec.get("effect") else None


def _ruling_reason(decision_ids: list[str], item_ids: list[str], owner_words: str | None = None) -> str:
    """解除动作的理由(修订 / 撤回 / 冻结记录): 决策记录 ID + 事项 ID; AIPOS-F201 件①: 带 Owner 原话(声明 decision.owner_words_prefix 起头)。"""
    base = f"Owner inbox ruling {', '.join(decision_ids)} ({', '.join(item_ids)})"
    words = str(owner_words or "").strip()
    return f"{base}; {inbox_declaration()['decision']['owner_words_prefix']}{words}" if words else base


def _release_preview(item: dict[str, Any], effect: str | None, actor: str, decision_id: str, *, dry_run: bool,
                     owner_words: str | None = None) -> dict[str, Any] | None:
    """逐卡解除动作 = 既有队列出口(不手改卡): release_needs_owner → board_adapter.amend_task(只收 pending, 改为真的 needs_owner 类标志);
    withdraw_card → board_adapter.withdraw_task(AIPOS-F170: 带 Owner 裁决引用 owner_ruling_ref = decision_id, blocked 卡凭此可撤)。
    freeze_legacy 按治理根成批, 见 _freeze_group。dry_run=True 预演、False 执行; 无 effect = None。"""
    from tools.aipos_cli.board_adapter import amend_task, withdraw_task

    if not effect or effect == "freeze_legacy":
        return None
    root = Path(item["governance_root"])
    reason = _ruling_reason([decision_id], [item["item_id"]], owner_words)
    if effect == "release_needs_owner":
        flags = list((item.get("facts") or {}).get("flags") or []) or ["needs_owner"]
        return amend_task(task_id=item["task_id"], actor=actor, amendments={f: False for f in flags}, amendment_reason=reason,
                          dry_run=dry_run, repo_root=root)
    if effect == "withdraw_card":
        return withdraw_task(task_id=item["task_id"], actor=actor, reason=reason, dry_run=dry_run, repo_root=root,
                             owner_ruling_ref=decision_id)
    from tools.schema_loader import SchemaLoadError

    raise SchemaLoadError(f"verbs.{VERB}.options.*.effect 未知: {effect!r}")


def _freeze_group(root: Path, entries: list[dict[str, Any]], actor: str, *, dry_run: bool, owner_words: str | None = None) -> dict[str, Any]:
    """AIPOS-F170 件①: 同一治理根内裁决为 freeze_legacy 的卡 = 一条存量冻结清单条目(既有口 legacy_freeze.plan_freeze_legacy 选卡 +
    legacy_baseline.write_freeze_entry 落条目; owner_policy_ref = 本批决策记录 ID)。返回 {ok, verdict, plan, written, blocking_reasons}。"""
    from tools.aipos_cli.legacy_baseline import LegacyBaselineError, write_freeze_entry
    from tools.aipos_cli.legacy_freeze import plan_freeze_legacy

    task_ids = [str(e["item"]["task_id"]) for e in entries]
    decision_ids = [str(e["item"]["decision_id"]) for e in entries]
    try:
        plan = plan_freeze_legacy(root, task_ids=task_ids)
        written = None if dry_run else write_freeze_entry(root, plan, reason=_ruling_reason(decision_ids, [e["item"]["item_id"] for e in entries],
                                                                                         owner_words),
                                                          actor=actor, owner_policy_ref=",".join(decision_ids))
    except (LegacyBaselineError, OSError) as exc:
        return {"ok": False, "verdict": "BLOCK", "plan": None, "written": None, "blocking_reasons": [f"{type(exc).__name__}: {exc}"],
                "task_ids": task_ids}
    return {"ok": True, "verdict": "PASS", "plan": {k: plan[k] for k in ("action", "selected", "already", "manifest_dir")},
            "written": written, "blocking_reasons": [], "task_ids": task_ids}


def _release_writes(resp: dict[str, Any] | None) -> list[str]:
    """逐卡解除动作写出的文件(冻结组的清单条目由 decide 按治理根另计)。AIPOS-F213 件①: 写出清单唯一实现 board_adapter.queue_write_paths
    (CLI / MCP 队列写同读)。"""
    from tools.aipos_cli.board_adapter import queue_write_paths

    return queue_write_paths(resp) if resp and resp.get("ok") else []


def _release_blocked(resp: dict[str, Any] | None) -> bool:
    return resp is not None and (not resp.get("ok") or resp.get("verdict") == "BLOCK")


def find_items(item_ids: list[str], *, home_root: str | Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """收件箱只算一次, 逐 ID 取事项; 任一不在 = InboxItemNotFound(点名全部缺的)。暂停项目照列(裁决口不受缺省视图影响)。"""
    inbox = collect_inbox(home_root, include_paused=True)
    # AIPOS-F225: 第二档事项(notices: 已自动放行 / 告知)不计待定数, 但仍可裁决(撤回 / 知悉 / 接受)
    by_id = {item["item_id"]: item for item in [*inbox["items"], *(inbox.get("notices") or [])]}
    missing = [i for i in item_ids if i not in by_id]
    if missing:
        raise InboxItemNotFound(f"收件箱中无事项 {', '.join(missing)}(已裁决 / 已自行解除 / ID 错; 当前 {inbox['count']} 项, "
                                f"lybra owner inbox --home-root {inbox['home_root']} 查看)")
    return [by_id[i] for i in item_ids], inbox


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
    from tools.aipos_cli.board_adapter import execute_dry_run, land_after_write, record_owner_decision
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN

    ids = [item_ids] if isinstance(item_ids, str) and "," not in item_ids else split_item_ids(None, item_ids)
    relayed, words = relay_fields(relayed_by, owner_words)
    decl = inbox_declaration()
    items, inbox = find_items(ids, home_root=home_root)
    for item in items:
        redirect = decl["categories"][item["category"]].get("decide_redirect")
        if redirect:  # AIPOS-F175: owner_check is recorded via `lybra roadmap verify`, never via decide
            raise InboxUsageError(_fmt(redirect, f"categories.{item['category']}.decide_redirect", item_id=item["item_id"],
                                       verify_command=(item.get("facts") or {}).get("verify_command") or ""))
    for item in items:
        keys = [o["key"] for o in item["options"]]
        if choice not in keys:
            # AIPOS-F201 item 1: an option this card's queue state cannot take = the declared reason (e.g. release on a claimed card)
            refused = (decl["categories"][item["category"]].get("choice_refused_by_queue_state") or {}).get(choice) or {}
            state = str((item.get("facts") or {}).get("queue_state") or "")
            if state in refused:
                raise InboxChoiceInvalid(_fmt(refused[state], f"categories.{item['category']}.choice_refused_by_queue_state.{choice}.{state}",
                                              task_id=item.get("task_id") or "", queue_state=state, options=", ".join(keys)))
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
        entry["release"] = _release_preview(item, effect, actor, item["decision_id"], dry_run=True, owner_words=words)
        if _release_blocked(entry["release"]):
            report["rc_outcome"] = "blocked"
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in report["entries"]:
        if entry["effect"] == "freeze_legacy":
            groups.setdefault(entry["item"]["governance_root"], []).append(entry)
    for root_text, entries in groups.items():
        group = _freeze_group(Path(root_text), entries, actor, dry_run=True, owner_words=words)
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
        entry["release"] = _release_preview(item, entry["effect"], actor, item["decision_id"], dry_run=False, owner_words=words)
        written += _release_writes(entry["release"])
        failed = failed or _release_blocked(entry["release"])
        entry["written"] = written
        by_root.setdefault(item["governance_root"], []).extend(written)
    report["freeze"] = []
    for root_text, entries in groups.items():
        ready = [e for e in entries if e["written"]]  # 决策记录已落的才冻结(引用须真实)
        if not ready:
            continue
        group = _freeze_group(Path(root_text), ready, actor, dry_run=False, owner_words=words)
        report["freeze"].append({"governance_root": root_text, **group})
        failed = failed or _release_blocked(group)
        written_entry = (group.get("written") or {})
        if written_entry.get("written"):
            from tools.aipos_cli.workspace_config import project_json_path

            extra = [str(written_entry["entry"])] + ([str(project_json_path(root_text))] if written_entry.get("project_json_declared") else [])
            by_root.setdefault(root_text, []).extend(extra)
    for root_text, written in by_root.items():
        ids_here = [e["item"]["item_id"] for e in report["entries"] if e["item"]["governance_root"] == root_text]
        # AIPOS-F213 件①: 写后落账唯一薄壳(与 CLI / MCP 队列写同一实现; 提交信息 = owner_decide)
        landing = land_after_write(Path(root_text), written, actor=actor, command="owner_decide", item_id=",".join(ids_here),
                                   choice=choice)
        report["landings"].append({"governance_root": root_text, **(landing or {})})
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
    advisor_next = option_next(decl, choice, label, resume=resume, task_id=task_id, governance_root=Path(governance_root),
                               decision_id=ruling.get("decision_id"))
    return {"choice": choice, "label": label, "note": note or None, "relayed_by": ruling.get("relayed_by"), "owner_words": words or None,
            "decided_at": ruling.get("decided_at"),
            "decision_id": ruling.get("decision_id"), "record": ruling.get("_path"), "line": line, "advisor_next": advisor_next}


def apply_owner_ruling(governance_root: Path, view: dict[str, Any], na: dict[str, Any]) -> dict[str, Any]:
    """loop status 每个运行的下一动作: owner_needed 且该运行对应事项已有 Owner 裁决 = advisor_fix(owner_ruled), detail = 裁决原文,
    hint = 该选项 advisor_next(含续跑命令, 占位与例行门拒同一实现 loop_run_record._resume_fill)。其余原样返回。"""
    if not isinstance(na, dict):
        return na
    if na.get("action") != "owner_needed":
        return _apply_owner_ask(Path(governance_root), view, na)
    decl = inbox_declaration()
    task_id = str(view.get("task_id") or "")
    iid = loop_item_id(Path(governance_root), task_id, str(view.get("run_id") or ""), decl)
    ruling = read_ruling(Path(governance_root), iid, decl)
    if ruling is None:
        return {**na, "inbox_item_id": iid}
    return _ruled_action(Path(governance_root), view, na, iid, ruling, decl)


def _apply_owner_ask(governance_root: Path, view: dict[str, Any], na: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F222 件②: 下一动作非等 Owner 的运行带顾问补写的 owner_ask(owner_ask_destination 唯一判定)= 已有 Owner 回应(该事项裁决 /
    补写后本卡新裁决)→ advisor_fix(owner_ruled), 同 owner_needed 已裁决同一构造; 待裁决 = 原下一动作附 owner_ask_inbox {item_id, category,
    merged}(收件箱据此列 / 并入, loop status 显示「已进收件箱」); 卡已结案 / 冻结 = 原样。"""
    if not isinstance(view.get("owner_ask"), dict):
        return na
    task_id = str(view.get("task_id") or "")
    dest = owner_ask_destination(governance_root, task_id, view, na)
    if dest is None or dest["reason"] in ("card_closed", "frozen"):
        return na
    marker = {"item_id": dest["item_id"], "category": dest["category"], "merged": dest["merged"], "resolved_by": dest.get("resolved_by")}
    if dest["reason"] == "ruled":
        return {**_ruled_action(governance_root, view, na, str(dest["item_id"]), dest["ruling"], inbox_declaration()),
                "owner_ask_inbox": marker}
    return {**na, "owner_ask_inbox": marker}


def _ruled_action(governance_root: Path, view: dict[str, Any], na: dict[str, Any], iid: str, ruling: dict[str, Any],
                  decl: dict[str, Any]) -> dict[str, Any]:
    """已裁决事项 → 顾问下一动作 advisor_fix(owner_ruled)(owner_needed 运行 / owner_ask 运行同一构造)。"""
    from tools.aipos_cli.loop_run_record import _resume_fill, status_contract

    task_id = str(view.get("task_id") or "")
    resume = _fmt(status_contract()["next_action"]["routine_rejections"]["resume_hint"], "resume_hint",
                  **_resume_fill(view, Path(governance_root)))
    ask = view.get("owner_ask") if isinstance(view.get("owner_ask"), dict) else {}
    labels = {str(o.get("key")): str(o.get("label")) for o in (ask.get("options") or []) if isinstance(o, dict)}
    rv = ruling_view(ruling, decl, resume=resume, task_id=task_id, governance_root=Path(governance_root), labels=labels)
    return {"action": "advisor_fix", "reason": "owner_ruled", "detail": f"{rv['line']}(原待决: {na.get('detail')})",
            "hint": rv["advisor_next"], "owner_ruling": rv, "inbox_item_id": iid,
            "ruled_from": {"reason": na.get("reason"), "action": na.get("action")}}


def rulings_for_task(governance_root: Path, task_id: str) -> list[dict[str, Any]]:
    """该卡的收件箱裁决(决策记录 decision_type = 声明 decision_type 且 task_id = 该卡), 按裁决时刻旧→新。读不出 = 向上。"""
    return [r for r in _all_rulings(Path(governance_root), inbox_declaration()) if str(r.get("task_id") or "") == str(task_id)]


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
