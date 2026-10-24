"""AIPOS-F172 — 看板「信封」页 + Owner 在看板授权签发 / 续签信封(看板只读的写例外①)。

一句话: 按项目看清所有信封的用量与到期, Owner 用自己的登录会话续签或新签, 预演 → 确认两步, 自动落账。

防碎片化(卡面硬约束):
- 信封状态只读唯一推导 autonomy_policy.envelope_overview(收件箱 F168 / loop status 预警 F166 / brief 同读); 本模块不另判状态。
- 项目 = home 根注册项目唯一枚举 owner_inbox.home_projects(看板项目列表 F171 / 收件箱同调); workspace 不在其中 = 拒(防串项目)。
- 签发 = envelope mint 唯一实现 envelope_mint(build_mint_payload / mint_preview = --dry-run / mint_via_gate = --confirm, 含 F158 自动落账);
  续签 policy_id / 缺省到期 = F166 renew_plan(经 envelope_overview 行); 新签缺省 policy_id = F166 next_policy_id。本模块只是调用方。
- 文案、状态色、错误码与状态码、预演令牌时效、审计日志落点全部读 verbs.schema board_owner_envelopes(board_envelopes_declaration 唯一读取口)。

安全(卡面逐条): 只有 Owner 角色会话可调用(其它 403); 必带 workspace 且须为 home 根推导出的项目; 确认须携本会话预演令牌(绑定会话指纹 +
参数摘要, 过期失效, 单次即焚); Origin 与 Host 不符 = 拒; 每次预演 / 确认(含被拒)写看板审计日志, 确认前先写不进 = 拒签; 凭据只在服务端由
envelope_mint 经 load_role_token 读, 应答与日志不含凭据原文(日志只记指纹)。
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

DECL_KEY = "board_owner_envelopes"
_DECL_KEYS = ("page_path", "api_path", "write_path", "script_path", "preview_ttl_seconds", "audit_log_file", "capture_surface", "token_role",
              "owner_role", "captured_by_template", "rationale_template", "decision_summary", "status_labels", "status_tone", "texts", "errors")
_TONES = ("alert", "ok", "muted")
_STAGES = ("preview", "confirm")
_MODES = ("renew", "new")
#: AIPOS-F180: inbox item id shape (verbs.schema lybra_owner_inbox.item_id: prefix + hex)
_REQUEST_ITEM_RE = re.compile(r"[A-Z]{1,8}-[0-9a-f]{6,40}")


class MintRequestError(Exception):
    """看板签发请求被拒: code ∈ 声明 errors(状态码与文案读声明)。"""

    def __init__(self, code: str, **values: Any) -> None:
        super().__init__(code)
        self.code = code
        self.values = values


# ---------------------------------------------------------------------------
# 声明(唯一读取口)
# ---------------------------------------------------------------------------

def board_envelopes_declaration() -> dict[str, Any]:
    """verbs.schema board_owner_envelopes(fail-closed): 键齐; status_labels / status_tone 键恰为 autonomy_policy.ENVELOPE_DISPLAY_STATUSES、
    色值 ∈ alert/ok/muted; errors 每码有 int status 与 message; 预演令牌时效为正整数。"""
    from tools.aipos_cli.autonomy_policy import ENVELOPE_DISPLAY_STATUSES
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = load_schema("verbs", code_repo_schema_root()).get(DECL_KEY)
    if not isinstance(decl, dict) or any(k not in decl for k in _DECL_KEYS):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY} 未声明齐 {list(_DECL_KEYS)}")
    statuses = set(ENVELOPE_DISPLAY_STATUSES)
    if set(decl["status_labels"]) != statuses or set(decl["status_tone"]) != statuses \
            or any(v not in _TONES for v in decl["status_tone"].values()):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.status_labels / status_tone 键须恰为 {sorted(statuses)}, 色值 ∈ {list(_TONES)}")
    ttl = decl["preview_ttl_seconds"]
    if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl < 1:
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.preview_ttl_seconds 须为正整数")
    for code, spec in decl["errors"].items():
        if not isinstance(spec, dict) or not isinstance(spec.get("status"), int) or not str(spec.get("message") or "").strip():
            raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.errors.{code} 须有 int status 与非空 message")
    if set(decl["decision_summary"]) != set(_MODES):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.decision_summary 键须恰为 {list(_MODES)}")
    _check_tabs(decl.get("tabs"), statuses)
    return decl


_TAB_KEYS = ("param", "signed_param", "order", "in_use_statuses", "renew_statuses", "after_sign", "texts")
_TABS = ("pending", "current", "history")
_TAB_TEXTS = ("tab_pending", "tab_current", "tab_history", "refresh", "signed_banner", "signed_view", "refreshing", "renew_heading",
              "renew_toggle", "pending_none", "current_none", "history_none", "drivers_none")


def _check_tabs(tabs: Any, statuses: set[str]) -> None:
    """AIPOS-F217: board_owner_envelopes.tabs(fail-closed): 键齐; order 恰为三个标签; 状态集 ⊆ 展示状态且 renew ⊆ in_use; after_sign 键恰为
    request + 签发模式、值 ∈ 标签; 文案语言恰为看板 locale.supported 且每种语言 _TAB_TEXTS 齐(F201 relay 话术声明同一形)。"""
    from tools.aipos_cli.board_pages import pages_declaration
    from tools.schema_loader import SchemaLoadError

    where = f"verbs.schema.json {DECL_KEY}.tabs"
    if not isinstance(tabs, dict) or any(k not in tabs for k in _TAB_KEYS):
        raise SchemaLoadError(f"{where} 未声明齐 {list(_TAB_KEYS)}")
    if not isinstance(tabs["order"], list) or sorted(tabs["order"]) != sorted(_TABS):
        raise SchemaLoadError(f"{where}.order 须恰为 {list(_TABS)}")
    in_use, renew = set(tabs["in_use_statuses"]), set(tabs["renew_statuses"])
    if not in_use or not in_use <= statuses or not renew <= in_use:
        raise SchemaLoadError(f"{where}.in_use_statuses 须为展示状态非空子集, renew_statuses ⊆ in_use_statuses")
    after = tabs["after_sign"]
    if not isinstance(after, dict) or set(after) != {"request", *_MODES} or any(v not in _TABS for v in after.values()):
        raise SchemaLoadError(f"{where}.after_sign 键须恰为 {['request', *_MODES]}, 值 ∈ {list(_TABS)}")
    for key in ("param", "signed_param"):
        if not isinstance(tabs[key], str) or not tabs[key]:
            raise SchemaLoadError(f"{where}.{key} 须为非空字符串")
    texts = tabs["texts"]
    langs = set(pages_declaration()["locale"]["supported"])
    if not isinstance(texts, dict) or set(texts) != langs or any(
            not isinstance(texts[lang], dict) or any(not str(texts[lang].get(k) or "").strip() for k in _TAB_TEXTS) for lang in langs):
        raise SchemaLoadError(f"{where}.texts 语言须恰为 {sorted(langs)}, 每种语言须有 {list(_TAB_TEXTS)}")


def tab_texts(lang: str | None = None) -> dict[str, str]:
    """AIPOS-F217: 一种语言的标签页文案(未知 / 缺省 = 看板缺省语言)。"""
    from tools.aipos_cli.board_pages import pages_declaration

    table = board_envelopes_declaration()["tabs"]["texts"]
    default = str(pages_declaration()["locale"]["default"])
    return table[lang] if lang in table else table[default]


def _fmt(template: Any, where: str, **values: Any) -> str:
    from tools.schema_loader import SchemaLoadError

    try:
        return str(template).format_map(values)
    except (KeyError, IndexError, ValueError) as exc:
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.{where} 模板占位无值: {exc}") from exc


def error_response(err: MintRequestError, decl: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """拒因 → (HTTP 状态码, 应答); 码不在声明 = SchemaLoadError(实现与声明不符)。"""
    from tools.schema_loader import SchemaLoadError

    d = decl or board_envelopes_declaration()
    spec = d["errors"].get(err.code)
    if spec is None:
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.errors 未声明 {err.code}")
    return int(spec["status"]), {"ok": False, "verdict": "BLOCK", "error": err.code,
                                 "message": _fmt(spec["message"], f"errors.{err.code}", **err.values)}


def board_envelope_url(project: str, policy_id: str | None = None) -> str:
    """看板信封页链接(收件箱信封项「裁决」指向此处); 路径读声明 page_path。"""
    path = str(board_envelopes_declaration()["page_path"])
    return f"{path}?project={quote(str(project))}" + (f"#{quote(str(policy_id))}" if policy_id else "")


# ---------------------------------------------------------------------------
# 只读视图(信封页 / GET api)
# ---------------------------------------------------------------------------

_PUBLIC_ROW_KEYS = ("policy_id", "agent_or_role", "driver_role", "state", "status", "current", "superseded_by", "why", "used", "max_tasks",
                    "remaining", "active_from", "expires_at", "task_mode", "lane_repos", "launch_harnesses", "project_selector", "task_ids",
                    "next_policy_id", "renew_note")


def collect_envelopes(home_root: str | Path | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    """跨 home 根全部已注册项目的信封视图(项目枚举 owner_inbox.home_projects; 每项目状态 autonomy_policy.envelope_overview)。
    某项目读不出 = 该项目 error 点名(不猜), 其余照列。行只出展示字段(不含门侧原始 usage / policy 对象)。"""
    from tools.aipos_cli.autonomy_policy import envelope_overview
    from tools.aipos_cli.owner_inbox import home_projects
    from tools.schema_loader import SchemaLoadError

    enumerated = home_projects(home_root)
    projects: list[dict[str, Any]] = []
    for p in enumerated["projects"]:
        entry: dict[str, Any] = {"project": p["name"], "governance_root": str(p["root"]), "paused": p["status"] == "paused",
                                 "status": p["status"], "status_error": p["status_error"], "envelopes": [], "drivers": [], "error": None}
        try:
            overview = envelope_overview(p["root"], now=now)
        except SchemaLoadError:
            raise
        except (OSError, ValueError, LookupError) as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            projects.append(entry)
            continue
        entry["envelopes"] = [{k: row.get(k) for k in _PUBLIC_ROW_KEYS} for row in overview["envelopes"]]
        entry["drivers"] = overview["drivers"]
        try:  # AIPOS-F224 件③: 「项目授权」主视图 + 迁移草案(读不出 = 点名, 信封照列)
            entry["authorizations"], entry["migration"] = _authorization_view(p["root"], now=now)
        except (OSError, ValueError, LookupError) as exc:
            entry["authorizations"], entry["migration"] = [], None
            entry["authorization_error"] = f"{type(exc).__name__}: {exc}"
        projects.append(entry)
    return {"home_root": str(enumerated["home_root"]), "home_source": enumerated["home_source"], "projects": projects}


_AUTH_PUBLIC_KEYS = ("authorization_id", "revision", "state", "status", "used", "max_tasks", "remaining", "expires_at", "lanes", "hosts",
                     "launch_harnesses", "zones_open", "supersedes_policies", "notices", "revisions", "owner_approval_ref")


def authorization_board_declaration() -> dict[str, Any]:
    """verbs.schema board_owner_envelopes.authorization(AIPOS-F224 件③, fail-closed): actions / audit_action / texts 齐。"""
    from tools.schema_loader import SchemaLoadError

    decl = board_envelopes_declaration().get("authorization")
    keys = ("heading", "none", "usage_line", "lane_line", "hosts_line", "launch_manual", "zones_none", "notice_line", "migrated_line",
            "revision_line", "revisions_toggle", "migrate_heading", "migrate_button", "extend_heading", "field_lane", "field_host",
            "field_open_zone", "field_max_tasks", "field_expires_at", "extend_button", "revoke_heading", "field_reason", "revoke_button",
            "summary_lines")
    if (not isinstance(decl, dict) or set(decl.get("actions") or []) != {"migrate", "extend", "revoke"}
            or not str(decl.get("audit_action") or "").strip() or not isinstance(decl.get("texts"), dict)
            or any(k not in decl["texts"] for k in keys) or not isinstance(decl["texts"]["summary_lines"], list)):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.authorization 未声明齐(actions{{migrate, extend, revoke}} / audit_action / texts {list(keys)})")
    return decl


def _authorization_view(root: Path, *, now: datetime | None) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """一个项目的「项目授权」行(autonomy_policy.authorization_overview, 展示字段)+ 迁移草案摘要(在用而未迁入的信封; 无 = None)。"""
    from tools.aipos_cli.autonomy_policy import authorization_overview

    rows = [{k: row.get(k) for k in _AUTH_PUBLIC_KEYS} for row in authorization_overview(root, now=now)]
    draft = authorization_draft_from_envelopes(root, now=now)
    migration = None
    if draft["block"] is not None:
        migration = {"envelopes": [e["policy_id"] for e in draft["envelopes"]], "max_tasks": draft["block"].get("max_tasks"),
                     "expires_at": draft["block"].get("expires_at"), "notes": draft["notes"]}
    return rows, migration


def _authorization_html(proj: dict[str, Any], *, owner: bool) -> str:
    """「项目授权」主视图(每份授权: 修订 / 状态 / 用量 / 车道 / 主机 / 禁区 / 告知 / 修订史 / 迁入的旧信封)+ Owner 表单(迁移 / 扩大 / 撤销,
    走本页同一写路由 mode = authorization)。全部值经 html.escape。"""
    from tools.aipos_cli.autonomy_policy import project_authorization_declaration

    esc = html.escape
    t = authorization_board_declaration()["texts"]
    name = str(proj["project"])
    out = [f"<div class=\"authorization-view\" data-project=\"{esc(name)}\" data-authorizations=\"{len(proj.get('authorizations') or [])}\">"]
    if proj.get("authorization_error"):
        out.append(f"<p class=\"envelope-error\">{esc(proj['authorization_error'])}</p>")
    rows = proj.get("authorizations") or []
    if not rows:
        out.append(f"<p class=\"envelope-none authorization-none\">{esc(t['none'])}</p>")
    for row in rows:
        out.append(f"<section class=\"authorization\" id=\"{esc(str(row['authorization_id']))}\" data-state=\"{esc(str(row.get('state')))}\">"
                   f"<h3>{esc(_fmt(t['heading'], 'authorization.texts.heading', **row))}</h3>")
        if row.get("state") == "missing":
            out.append("</section>")
            continue
        lines = [_fmt(t["usage_line"], "authorization.texts.usage_line", **row)]
        lines += [_fmt(t["lane_line"], "authorization.texts.lane_line", repo=l["repo"], task_modes=", ".join(l["task_modes"]),
                       drivers=", ".join(l["drivers"])) for l in row.get("lanes") or []]
        lines.append(_fmt(t["hosts_line"], "authorization.texts.hosts_line", hosts=", ".join(row.get("hosts") or []),
                          launch=", ".join(row.get("launch_harnesses") or []) or t["launch_manual"],
                          zones=", ".join(row.get("zones_open") or []) or t["zones_none"]))
        lines += [_fmt(t["notice_line"], "authorization.texts.notice_line", why=why) for why in row.get("notices") or []]
        if row.get("supersedes_policies"):
            lines.append(_fmt(t["migrated_line"], "authorization.texts.migrated_line", policies=", ".join(row["supersedes_policies"])))
        out.append("<ul>" + "".join(f"<li>{esc(line)}</li>" for line in lines) + "</ul>")
        revs = "".join(f"<li>{esc(_fmt(t['revision_line'], 'authorization.texts.revision_line', **{k: rev.get(k) for k in ('revision', 'action', 'status', 'revised_at', 'owner_approval_ref')}))}</li>"
                       for rev in row.get("revisions") or [])
        out.append(f"<details class=\"authorization-revisions\"><summary>{esc(_fmt(t['revisions_toggle'], 'authorization.texts.revisions_toggle', count=len(row.get('revisions') or [])))}</summary><ul>{revs}</ul></details>")
        if owner and row.get("status") == "active":
            zones = ", ".join(project_authorization_declaration()["zones"])
            fields = [("lane", t["field_lane"]), ("host", t["field_host"]),
                      ("open_zone", _fmt(t["field_open_zone"], "authorization.texts.field_open_zone", zones=zones)),
                      ("max_tasks", t["field_max_tasks"]), ("expires_at", t["field_expires_at"])]
            inputs = "".join(f"<label>{esc(label)} <input name=\"{n}\" type=\"text\" value=\"\"></label>" for n, label in fields)
            out.append(f"<details class=\"authorization-extend\"><summary>{esc(t['extend_heading'])}</summary>"
                       f"<form class=\"envelope-form\" data-mode=\"authorization\" data-action=\"extend\" data-workspace=\"{esc(name)}\">{inputs}"
                       f"<button type=\"submit\">{esc(t['extend_button'])}</button></form></details>")
            out.append(f"<details class=\"authorization-revoke\"><summary>{esc(t['revoke_heading'])}</summary>"
                       f"<form class=\"envelope-form\" data-mode=\"authorization\" data-action=\"revoke\" data-workspace=\"{esc(name)}\">"
                       f"<label>{esc(t['field_reason'])} <input name=\"reason\" type=\"text\" value=\"\"></label>"
                       f"<button type=\"submit\">{esc(t['revoke_button'])}</button></form></details>")
        out.append("</section>")
    mig = proj.get("migration")
    if mig and owner:
        out.append(f"<form class=\"envelope-form authorization-migrate\" data-mode=\"authorization\" data-action=\"migrate\" data-workspace=\"{esc(name)}\">"
                   f"<p>{esc(_fmt(t['migrate_heading'], 'authorization.texts.migrate_heading', count=len(mig['envelopes']), max_tasks=mig['max_tasks'], expires_at=mig['expires_at']))}</p>"
                   f"<button type=\"submit\">{esc(t['migrate_button'])}</button></form>")
    out.append("</div>")
    return "".join(out)


def _scope_text(row: dict[str, Any], texts: dict[str, Any]) -> str:
    return _fmt(texts["scope_line"], "texts.scope_line", task_mode=row.get("task_mode") or texts["scope_any"],
                lane_repos=", ".join(row.get("lane_repos") or []) or texts["scope_any"],
                launch_harnesses=", ".join(row.get("launch_harnesses") or []) or texts["scope_manual"])


def _row_html(row: dict[str, Any], decl: dict[str, Any], *, project: str, owner: bool, tt: dict[str, str] | None = None,
              signed: str | None = None) -> str:
    """One envelope row. AIPOS-F217: the renew form is folded behind one "renew" toggle (tt = tab texts of the page language); the row
    just signed (signed = policy_id from the page query) is highlighted."""
    esc = html.escape
    texts = decl["texts"]
    status = str(row["status"])
    tone = decl["status_tone"][status]
    label = decl["status_labels"][status]
    if row.get("superseded_by"):
        label += " · " + _fmt(texts["superseded_by"], "texts.superseded_by", policy_id=row["superseded_by"])
    if row.get("why"):
        label += f" · {row['why']}"
    cells = [esc(str(row["policy_id"])), esc(str(row.get("agent_or_role") or "")), esc(_scope_text(row, texts)),
             esc(f"{row.get('used')}/{row.get('max_tasks')}"), esc(str(row.get("expires_at") or "")), esc(label)]
    hit = bool(signed) and str(row["policy_id"]) == signed
    out = [f"<tr id=\"{esc(str(row['policy_id']))}\" class=\"envelope-row envelope-tone-{tone}{' envelope-row-signed' if hit else ''}\""
           f" data-policy-id=\"{esc(str(row['policy_id']))}\" data-status=\"{esc(status)}\" data-tone=\"{tone}\""
           f" data-current=\"{str(bool(row.get('current'))).lower()}\"{' data-signed=\"true\"' if hit else ''}>"]
    out.extend(f"<td>{c}</td>" for c in cells)
    if owner and tt and row.get("current") and row.get("next_policy_id"):
        default_exp = _default_expiry(decl)
        note = (f"<p class=\"envelope-note\">{esc(_fmt(texts['renew_note'], 'texts.renew_note', note=row['renew_note']))}</p>"
                if row.get("renew_note") else "")
        out.append(
            f"<td><details class=\"envelope-renew\"><summary>{esc(tt['renew_toggle'])}</summary>"
            "<form class=\"envelope-form\" data-mode=\"renew\""
            f" data-workspace=\"{esc(project)}\" data-template=\"{esc(str(row['policy_id']))}\">"
            f"<label>{esc(texts['field_max_tasks'])} <input name=\"max_tasks\" type=\"number\" min=\"1\" value=\"{esc(str(row.get('max_tasks') or 1))}\"></label>"
            f"<label>{esc(texts['field_expires_at'])} <input name=\"expires_at\" value=\"{esc(default_exp)}\"></label>"
            f"<button type=\"submit\">{esc(_fmt(texts['renew_button'], 'texts.renew_button', policy_id=row['next_policy_id']))}</button>"
            f"{note}</form></details></td>")
    else:
        out.append("<td></td>")
    out.append("</tr>")
    return "".join(out)


def _default_expiry(decl: dict[str, Any]) -> str:
    """续签 / 新签表单预填到期 = 现在 + F166 续签天数(verbs.schema lybra_loop.envelope.renew.renew_days, renew_plan 同一来源)。"""
    from datetime import timedelta

    from tools.aipos_cli.autonomy_policy import envelope_renew_declaration
    from tools.aipos_cli.clock import iso_z, utc_now

    return iso_z(utc_now() + timedelta(days=int(envelope_renew_declaration()["renew_days"])))


def _table(rows: list[dict[str, Any]], decl: dict[str, Any], *, project: str, owner: bool, css: str, tt: dict[str, str] | None = None,
           signed: str | None = None) -> str:
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in decl["texts"]["columns"]) + "<th></th>"
    body = "".join(_row_html(r, decl, project=project, owner=owner, tt=tt, signed=signed) for r in rows)
    return f"<table class=\"{css}\"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


# ---------------------------------------------------------------------------
# AIPOS-F217: tabs (to sign / in use / history) — one placement judgment
# ---------------------------------------------------------------------------

#: the display status (autonomy_policy.ENVELOPE_DISPLAY_STATUSES) of a used-up envelope
_EXHAUSTED = "exhausted"


def envelope_placement(row: dict[str, Any], rows: list[dict[str, Any]], decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F217 唯一判定: 一个信封的归属页 tab ∈ {current, history} 与是否待签 pending(页面 / 标签计数 / 横幅 / 测试同调)。
    rows = 同一项目 envelope_overview 的全部行(展示字段即可: policy_id / agent_or_role / status / current / superseded_by)。
    在用(current)= envelope_overview 判当前(未被接替)且展示状态 ∈ tabs.in_use_statuses(生效 / 将尽 / 将到期 / 已签待生效); 其余 = 历史。
    待签 = 在用且展示状态 ∈ tabs.renew_statuses(将尽 / 将到期), 或 额度用尽而未被接替、且同驱动方(agent_or_role)没有在用信封(无可用接替者)。"""
    tabs = (decl or board_envelopes_declaration())["tabs"]
    in_use_statuses = set(tabs["in_use_statuses"])

    def in_use(r: dict[str, Any]) -> bool:
        return bool(r.get("current")) and not r.get("superseded_by") and str(r.get("status")) in in_use_statuses

    status = str(row.get("status"))
    if in_use(row):
        return {"tab": "current", "pending": status in set(tabs["renew_statuses"])}
    exhausted_alone = (status == _EXHAUSTED and not row.get("superseded_by")
                       and not any(in_use(r) for r in rows if r.get("agent_or_role") == row.get("agent_or_role")))
    return {"tab": "history", "pending": exhausted_alone}


def envelope_tabs(view: dict[str, Any], decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every project's envelopes split by envelope_placement: {projects: {name: {pending, current, history}}, counts: {pending, current,
    history}} (pending = envelopes only; the page adds the authorization requests). Each row is in exactly one of current / history."""
    d = decl or board_envelopes_declaration()
    split: dict[str, dict[str, list[dict[str, Any]]]] = {}
    counts = {tab: 0 for tab in _TABS}
    for proj in view["projects"]:
        rows = proj.get("envelopes") or []
        parts: dict[str, list[dict[str, Any]]] = {tab: [] for tab in _TABS}
        for row in rows:
            place = envelope_placement(row, rows, d)
            parts[place["tab"]].append(row)
            if place["pending"]:
                parts["pending"].append(row)
        for tab in _TABS:
            counts[tab] += len(parts[tab])
        split[str(proj["project"])] = parts
    return {"projects": split, "counts": counts}


def resolve_tab(params: dict[str, list[str]] | None, *, pending_count: int, relays: int) -> str:
    """The tab to render: the query parameter when it names a declared tab, else the default (to sign when it has content, else in use)."""
    tabs = board_envelopes_declaration()["tabs"]
    asked = ((params or {}).get(str(tabs["param"])) or [""])[0]
    if asked in tabs["order"]:
        return asked
    return "pending" if pending_count or relays else "current"


def signed_policy(params: dict[str, list[str]] | None) -> str | None:
    """The just-signed policy id from the page query (banner + highlight); not a policy id shape = ignored."""
    from tools.aipos_cli.autonomy_policy import POLICY_ID_PATTERN

    value = ((params or {}).get(str(board_envelopes_declaration()["tabs"]["signed_param"])) or [""])[0].strip()
    return value if value and POLICY_ID_PATTERN.fullmatch(value) else None


def envelopes_page_parts(view: dict[str, Any], *, owner: bool, tab: str = "current", lang: str | None = None,
                         signed: str | None = None, split: dict[str, Any] | None = None) -> dict[str, Any]:
    """看板「信封」页正文(服务端渲染; 全部值经 html.escape)。owner=False(非 Owner 会话)= 只读, 不出任何表单 / 脚本。
    AIPOS-F180: 页面骨架(顶部导航 / 样式 / 「待授权请求」段)由看板 F174 骨架 web/board/readonly_pages.render_envelopes 套上;
    本函数只出 {title, h1_attrs, sections}(Owner 写例外的 body 属性与脚本由骨架按会话角色统一给)。
    AIPOS-F217: 只出 tab 这一页的信封段(envelope_tabs 同一切分): pending = 需要续签的信封; current = 在用表(续签折叠)+ 无在用信封的驱动方
    汇总一行折叠 + 新签表单; history = 历史表(新的在前)。项目读不出 = 各页都点名。"""
    esc = html.escape
    decl = board_envelopes_declaration()
    texts = decl["texts"]
    tt = tab_texts(lang)
    split = split or envelope_tabs(view, decl)
    title = _fmt(texts["title"], "texts.title", projects=len(view["projects"]), home_root=view["home_root"])
    parts: list[str] = []
    if not owner:
        parts.append(f"<p class=\"envelope-readonly\">{esc(texts['readonly_hint'])}</p>")
    if tab == "pending" and split["counts"]["pending"]:
        parts.append(f"<h2 class=\"envelope-renew-heading\">{esc(_fmt(tt['renew_heading'], 'tabs.texts.renew_heading', count=split['counts']['pending']))}</h2>")
    shown = 0
    for proj in view["projects"]:
        name = str(proj["project"])
        rows = split["projects"].get(name, {}).get(tab, [])
        if not proj.get("error") and tab != "current" and not rows:
            continue  # to sign / history: only projects with rows there (in use lists every project: its new-sign form lives there)
        shown += 1
        heading = _fmt(texts["project_heading"], "texts.project_heading", project=name) + (texts["project_paused"] if proj.get("paused") else "")
        parts.append(f"<section class=\"envelope-project\" id=\"project-{esc(name)}\" data-project=\"{esc(name)}\" data-tab=\"{esc(tab)}\""
                     f" data-rows=\"{len(rows)}\"><h2>{esc(heading)}</h2>")
        if proj.get("error"):
            parts.append(f"<p class=\"envelope-error\">{esc(_fmt(texts['page_error'], 'texts.page_error', error=proj['error']))}</p></section>")
            continue
        if tab == "pending":
            parts.append(_table(rows, decl, project=name, owner=owner, css="envelope-current envelope-pending", tt=tt, signed=signed))
        elif tab == "history":
            parts.append(_table(rows, decl, project=name, owner=False, css="envelope-folded envelope-history", signed=signed))
        else:
            parts.append(_authorization_html(proj, owner=owner))  # AIPOS-F224 件③: 「项目授权」主视图在前, 信封表在后(迁入的旧信封落「历史」)
            all_rows = proj["envelopes"]
            if not all_rows:
                parts.append(f"<p class=\"envelope-none\">{esc(texts['none'])}</p>")
            elif not rows:
                parts.append(f"<p class=\"envelope-none\">{esc(tt['current_none'])}</p>")
            else:
                parts.append(_table(rows, decl, project=name, owner=owner, css="envelope-current", tt=tt, signed=signed))
            parts.append(_drivers_none_html(proj, rows, decl, tt))
            if owner:
                parts.append(_new_form_html(name, decl))
        parts.append("</section>")
    if not shown and tab == "history":  # (to sign: the page says "nothing to sign" when requests / relays are empty too)
        parts.append(f"<p class=\"envelope-none envelope-tab-empty\">{esc(tt['history_none'])}</p>")
    return {"title": title, "h1_attrs": f" data-envelope-projects=\"{len(view['projects'])}\"", "sections": parts}


def _drivers_none_html(proj: dict[str, Any], in_use: list[dict[str, Any]], decl: dict[str, Any], tt: dict[str, str]) -> str:
    """Drivers with no envelope in use, one folded line (names inside). A driver whose latest envelope was superseded by another
    envelope already has its successor (not named)."""
    esc = html.escape
    by_id = {r["policy_id"]: r for r in proj["envelopes"]}
    using = {r.get("agent_or_role") for r in in_use}
    names = [drv for drv in proj.get("drivers") or []
             if drv["agent_or_role"] not in using and not (by_id.get(drv["latest"]) or {}).get("superseded_by")]
    if not names:
        return ""
    items = "".join(f"<li>{esc(_fmt(decl['texts']['driver_none'], 'texts.driver_none', **drv))}</li>" for drv in names)
    return (f"<details class=\"envelope-drivers-none\" data-drivers=\"{len(names)}\"><summary>"
            f"{esc(_fmt(tt['drivers_none'], 'tabs.texts.drivers_none', count=len(names)))}</summary><ul>{items}</ul></details>")


def _new_form_html(project: str, decl: dict[str, Any]) -> str:
    esc = html.escape
    t = decl["texts"]
    fields = [("agent_or_role", "", "text"), ("task_mode", "code", "text"), ("lane_repo", "", "text"), ("launch_harness", "", "text"),
              ("max_tasks", "20", "number"), ("expires_at", _default_expiry(decl), "text"), ("policy_id", "", "text")]
    inputs = "".join(f"<label>{esc(t['field_' + n])} <input name=\"{n}\" type=\"{typ}\" value=\"{esc(v)}\"></label>" for n, v, typ in fields)
    return (f"<details class=\"envelope-new\"><summary>{esc(_fmt(t['new_heading'], 'texts.new_heading', project=project))}</summary>"
            f"<form class=\"envelope-form\" data-mode=\"new\" data-workspace=\"{esc(project)}\">{inputs}"
            f"<button type=\"submit\">{esc(t['preview_button'])}</button></form></details>")


# ---------------------------------------------------------------------------
# 写例外: Owner 续签 / 新签(预演 → 确认)
# ---------------------------------------------------------------------------

def session_ref(session_id: str) -> str:
    """会话指纹(会话 secret 的 sha256 前 16 hex; 预演令牌绑定与审计日志用, 不出会话 secret 本身)。"""
    return "sha256:" + hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:16]


def _split_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    return [str(x).strip() for x in items if str(x).strip()]


def _parse_expiry(value: Any, now: datetime) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MintRequestError("PARAMS_INVALID", detail=f"expires_at 不是 ISO8601 时刻: {raw!r}") from exc
    if parsed.tzinfo is None:
        raise MintRequestError("PARAMS_INVALID", detail=f"expires_at 须带时区(如 Z): {raw!r}")
    parsed = parsed.astimezone(timezone.utc)
    if parsed <= now:
        raise MintRequestError("PARAMS_INVALID", detail=f"expires_at {raw} 不在未来")
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_max_tasks(value: Any) -> int:
    if isinstance(value, bool):
        raise MintRequestError("PARAMS_INVALID", detail="max_tasks 须为正整数")
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise MintRequestError("PARAMS_INVALID", detail=f"max_tasks 须为正整数: {value!r}") from exc
    if n < 1:
        raise MintRequestError("PARAMS_INVALID", detail=f"max_tasks 须为正整数: {value!r}")
    return n


def resolve_workspace(body: dict[str, Any], home_root: str | Path | None) -> tuple[str, Path]:
    """请求 workspace(项目名)→ (项目名, 治理根)。只认 home 根注册项目唯一枚举 owner_inbox.home_projects; 缺 / 不在其中 = 拒。治理根取自枚举, 不取请求。"""
    from tools.aipos_cli.owner_inbox import home_projects

    name = str(body.get("workspace") or "").strip()
    if not name:
        raise MintRequestError("WORKSPACE_REQUIRED")
    enumerated = home_projects(home_root)
    by_name = {p["name"]: Path(p["root"]) for p in enumerated["projects"]}
    if name not in by_name:
        raise MintRequestError("WORKSPACE_NOT_IN_HOME", workspace=name, home_root=str(enumerated["home_root"]), projects=sorted(by_name))
    return name, by_name[name]


def normalize_mint_params(body: dict[str, Any], *, project: str, governance_root: Path, now: datetime) -> dict[str, Any]:
    """表单 → 签发参数(预演与确认同一归一; 摘要据此算)。续签 = 模板须为该项目当前在用信封(envelope_overview), 驱动方 / 范围照抄模板,
    policy_id = 模板行 next_policy_id(F166 renew_plan), 只可改额度与到期; 新签 = 表单给范围, policy_id 缺省 = F166 next_policy_id。"""
    from tools.aipos_cli.autonomy_policy import POLICY_ID_PATTERN, envelope_overview, next_policy_id

    decl = board_envelopes_declaration()
    mode = str(body.get("mode") or "").strip()
    if mode not in _MODES:
        raise MintRequestError("PARAMS_INVALID", detail=f"mode 须为 {list(_MODES)}")
    max_tasks = _parse_max_tasks(body.get("max_tasks"))
    expires_at = _parse_expiry(body.get("expires_at"), now)
    template = ""
    if mode == "renew":
        template = str(body.get("template_policy_id") or "").strip()
        row = next((r for r in envelope_overview(governance_root, now=now)["envelopes"] if r["policy_id"] == template), None)
        if row is None or not row["current"] or not row.get("next_policy_id"):
            raise MintRequestError("TEMPLATE_NOT_CURRENT", template_policy_id=template)
        agent, task_mode = str(row["agent_or_role"]), str(row["task_mode"] or "")
        lane, harness, policy_id = list(row["lane_repos"]), list(row["launch_harnesses"]), str(row["next_policy_id"])
    else:
        agent = str(body.get("agent_or_role") or "").strip()
        if not agent or any(ch.isspace() for ch in agent):
            raise MintRequestError("PARAMS_INVALID", detail="agent_or_role 须为非空且不含空白的实例名或角色名")
        task_mode = str(body.get("task_mode") or "").strip()
        lane, harness = _split_list(body.get("lane_repo")), _split_list(body.get("launch_harness"))
        policy_id = str(body.get("policy_id") or "").strip() or next_policy_id(governance_root, identities={agent}, project=project)
        if not POLICY_ID_PATTERN.fullmatch(policy_id):
            raise MintRequestError("PARAMS_INVALID", detail=f"policy_id 形不合: {policy_id!r}")
    summary = _fmt(decl["decision_summary"][mode], f"decision_summary.{mode}", template_policy_id=template, policy_id=policy_id,
                   agent_or_role=agent, max_tasks=max_tasks, expires_at=expires_at)
    # AIPOS-F180: signing from an inbox authorization request carries its item id (traceability only; same mint path)
    request = str(body.get("request_item") or "").strip()
    if request:
        if not _REQUEST_ITEM_RE.fullmatch(request):
            raise MintRequestError("PARAMS_INVALID", detail=f"request_item 形不合: {request!r}")
        summary += _fmt(decl["texts"]["request_part"], "texts.request_part", request_item=request)
    return {"project": project, "governance_root": str(governance_root), "mode": mode, "template_policy_id": template,
            "policy_id": policy_id, "agent_or_role": agent, "task_mode": task_mode, "lane_repo": lane, "launch_harness": harness,
            "max_tasks": max_tasks, "expires_at": expires_at, "decision_summary": summary, "request_item": request}


def params_digest(params: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(params, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def session_provenance(decl: dict[str, Any], session: dict[str, Any]) -> dict[str, str]:
    values = {"role": session["role"], "token_ref": session.get("token_ref") or "-", "session_ref": session["session_ref"],
              "login_method": session.get("login_method") or "-", "source_ip": session.get("source_ip") or "-"}
    return {"capture_surface": str(decl["capture_surface"]),
            "captured_by": _fmt(decl["captured_by_template"], "captured_by_template", **values)[:160],
            "decision_rationale": _fmt(decl["rationale_template"], "rationale_template", **values)}


def _payload(params: dict[str, Any], decl: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    from tools.aipos_cli.envelope_mint import build_mint_payload

    return build_mint_payload(policy_id=params["policy_id"], agent_or_role=params["agent_or_role"], max_tasks=params["max_tasks"],
                              task_mode=params["task_mode"] or None, expires_at=params["expires_at"],
                              decision_summary=params["decision_summary"], actor=str(session["role"]),  # actor = 登录会话的 Owner 角色(已校验)
                              launch_harnesses=params["launch_harness"], lane_repo=params["lane_repo"] or None,
                              provenance=session_provenance(decl, session))


def preview_lines(params: dict[str, Any], payload: dict[str, Any], verdict: str, decl: dict[str, Any]) -> list[str]:
    texts = decl["texts"]
    renew_part = _fmt(texts["renew_part"], "texts.renew_part", **params) if params["mode"] == "renew" else ""
    values = {**params, "renew_part": renew_part, "verdict": verdict, "actor": payload["actor"], "captured_by": payload.get("captured_by", ""),
              "task_mode": params["task_mode"] or texts["scope_any"], "lane_repos": ", ".join(params["lane_repo"]) or texts["scope_any"],
              "launch_harnesses": ", ".join(params["launch_harness"]) or texts["scope_manual"]}
    return [_fmt(line, "texts.preview_lines", **values) for line in texts["preview_lines"]]


def append_board_audit(log_path: Path | None, record: dict[str, Any]) -> None:
    """看板审计日志追加一条(JSONL; 只记身份指纹 / 参数 / 结果, 不含任何凭据原文)。落点缺 / 写不进 = OSError 向上(调用方定拒签)。"""
    from tools.aipos_cli.clock import iso_z

    if log_path is None:
        raise OSError("看板审计日志落点未定(服务进程无工作区根)")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": iso_z(), **record}, ensure_ascii=False, sort_keys=True) + "\n")


def resolve_board_audit_log_path(repo_root: Path | None) -> Path | None:
    """看板审计日志落点 = <服务进程工作区根>/.lybra/<声明 audit_log_file>(与登录留痕 auth-log 同目录); 无工作区根 = None(签发拒)。"""
    if repo_root is None:
        return None
    return Path(repo_root).expanduser() / ".lybra" / str(board_envelopes_declaration()["audit_log_file"])


def handle_owner_write(
    body: dict[str, Any],
    *,
    decl: dict[str, Any],
    action: str,
    session: dict[str, Any] | None,
    home_root: str | Path | None,
    preview_store: Any,
    audit_log_path: Path | None,
    prepare: Any,
    preview: Any,
    confirm: Any,
    pre_confirm: Any = None,
    origin: str | None = None,
    host: str | None = None,
    content_type: str | None = None,
    now: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """看板 Owner 写例外的唯一安全框架(AIPOS-F172 写例外①信封签发首用; AIPOS-F176 写例外②项目升级同调, 不另写):
    Owner 角色会话(角色名读 board_owner_envelopes.owner_role)/ Origin 与 Host 相符 / JSON 请求体 / stage ∈ preview|confirm /
    workspace = home 根注册项目(resolve_workspace)/ 预演发本会话一次性令牌(绑定会话指纹 + 参数摘要, 令牌表与时效 = F172 同一张)/
    确认核令牌六类(缺 / 错 / 已用 / 过期 / 他会话 / 改参数)/ 每次预演与确认(含被拒)写看板审计日志, 确认前先写不进 = 拒(fail-closed)。

    decl = 本写例外的声明(取 errors 码表: 状态码与文案); action = 审计日志 action 名。各写例外只给三个回调:
      prepare(body, project, root, now, session) -> (params, audit_fields, context): 归一参数(摘要据此算, 预演与确认同一归一)
      preview(params, context) -> 应答附加键(dict); 不可预演 = 抛 MintRequestError("PREVIEW_BLOCKED", detail=…)
      confirm(params, context) -> (audit_fields, response, error): error 非 None = 执行未成(其审计照记, 再按 error 码拒)
      pre_confirm(params, context)(可选): 令牌核过后、留痕前的前置拒(如签发凭据库缺)。
    返回 (HTTP 状态码, 应答)。session = {role, token_ref, session_ref, login_method, source_ip} 或 None(无会话 = 非 Owner)。"""
    from tools.aipos_cli.clock import utc_now

    frame = board_envelopes_declaration()
    ttl = int(frame["preview_ttl_seconds"])
    now = now or utc_now()
    stage = str(body.get("stage") or "").strip()
    audit: dict[str, Any] = {"action": action, "stage": stage or None, "workspace": str(body.get("workspace") or "") or None,
                             "role": (session or {}).get("role"), "token_ref": (session or {}).get("token_ref"),
                             "session_ref": (session or {}).get("session_ref"), "login_method": (session or {}).get("login_method"),
                             "source_ip": (session or {}).get("source_ip")}

    def _reject(err: MintRequestError) -> tuple[int, dict[str, Any]]:
        status, payload = error_response(err, decl)
        try:
            append_board_audit(audit_log_path, {**audit, "outcome": err.code, "message": payload["message"]})
            payload["audit_logged"] = True
        except OSError as exc:  # refusal wrote nothing; report the audit failure (confirm has its own fail-closed audit check)
            payload["audit_logged"] = False
            payload["audit_error"] = f"{type(exc).__name__}: {exc}"
        return status, payload

    try:
        if not session or session.get("role") != frame["owner_role"]:
            raise MintRequestError("OWNER_ONLY")
        if origin and host and urlparse(origin).netloc != host:
            raise MintRequestError("ORIGIN_MISMATCH")
        if "application/json" not in str(content_type or "").lower():
            raise MintRequestError("PARAMS_INVALID", detail="Content-Type 须为 application/json")
        if stage not in _STAGES:
            raise MintRequestError("STAGE_INVALID")
        project, root = resolve_workspace(body, home_root)
        params, audit_fields, context = prepare(body, project, root, now, session)
        audit.update(audit_fields)
        digest = params_digest(params)
        if stage == "preview":
            extra = preview(params, context)
            token = preview_store.issue(session_ref=session["session_ref"], digest=digest)
            audit_error = None
            try:
                append_board_audit(audit_log_path, {**audit, "outcome": "previewed", "digest": digest})
            except OSError as exc:  # preview writes nothing; report the audit failure (confirm has its own fail-closed check)
                audit_error = f"{type(exc).__name__}: {exc}"
            return 200, {"ok": True, "stage": "preview", "preview_token": token, "expires_in": ttl, **extra,
                         "audit_logged": audit_error is None, "audit_error": audit_error}
        status, entry = preview_store.take(str(body.get("preview_token") or "").strip() or None)
        if status == "missing":
            raise MintRequestError("PREVIEW_TOKEN_REQUIRED")
        if status == "expired":
            raise MintRequestError("PREVIEW_TOKEN_EXPIRED", ttl=ttl)
        if status != "ok" or entry is None:
            raise MintRequestError("PREVIEW_TOKEN_INVALID")
        if entry.get("session_ref") != session["session_ref"]:
            raise MintRequestError("PREVIEW_SESSION_MISMATCH")
        if entry.get("digest") != digest:
            raise MintRequestError("PREVIEW_PARAMS_MISMATCH")
        if pre_confirm is not None:
            pre_confirm(params, context)
        # 每次执行须留痕: 执行前先写一条「确认中」, 写不进 = 拒(fail-closed)
        try:
            append_board_audit(audit_log_path, {**audit, "outcome": "confirming", "digest": digest})
        except OSError as exc:
            raise MintRequestError("AUDIT_LOG_UNAVAILABLE", detail=f"{type(exc).__name__}: {exc}") from exc
        result_fields, response, error = confirm(params, context)
        audit_error = None
        try:
            append_board_audit(audit_log_path, {**audit, "digest": digest, **result_fields})
        except OSError as exc:  # the write already took effect; report the result-audit failure (the confirming line is logged)
            audit_error = f"{type(exc).__name__}: {exc}"
        if error is not None:
            raise error
        return 200, {**response, "audit_logged": audit_error is None, "audit_error": audit_error}
    except MintRequestError as err:
        return _reject(err)


def handle_mint_request(
    body: dict[str, Any],
    *,
    session: dict[str, Any] | None,
    home_root: str | Path | None,
    preview_store: Any,
    connection_json: Path | None,
    audit_log_path: Path | None,
    origin: str | None = None,
    host: str | None = None,
    content_type: str | None = None,
    now: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """写例外①(信封签发): 安全判定全在 handle_owner_write(同一框架); 本函数只给签发的归一 / 预演 / 确认回调。
    preview_store = 预演令牌表(issue(session_ref, digest) / take(token) → (status, entry), web/board/auth_otc.PreviewTokenStore)。
    AIPOS-F224 件③: mode = authorization(项目授权迁移 / 扩大 / 撤销)同一路由、同一安全框架, 回调见 _authorization_callbacks。"""
    decl = board_envelopes_declaration()
    if str(body.get("mode") or "").strip() == "authorization":
        prepare_a, preview_a, confirm_a, pre_a = _authorization_callbacks(decl, connection_json)
        return handle_owner_write(body, decl=decl, action=str(authorization_board_declaration()["audit_action"]), session=session,
                                  home_root=home_root, preview_store=preview_store, audit_log_path=audit_log_path, prepare=prepare_a,
                                  preview=preview_a, confirm=confirm_a, pre_confirm=pre_a, origin=origin, host=host,
                                  content_type=content_type, now=now)

    def prepare(body: dict[str, Any], project: str, root: Path, now: datetime, session: dict[str, Any]) -> tuple[dict, dict, dict]:
        params = normalize_mint_params(body, project=project, governance_root=root, now=now)
        audit_fields = {"project": project, "mode": params["mode"], "policy_id": params["policy_id"],
                        "template_policy_id": params["template_policy_id"] or None, "max_tasks": params["max_tasks"],
                        "expires_at": params["expires_at"], "agent_or_role": params["agent_or_role"]}
        return params, audit_fields, {"payload": _payload(params, decl, session)}

    def preview(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return _preview(params, context["payload"], decl)

    def pre_confirm(params: dict[str, Any], context: dict[str, Any]) -> None:
        from tools.aipos_cli.autonomy_policy import render_mint_command

        if connection_json is None:
            raise MintRequestError("CREDENTIAL_UNAVAILABLE", command=render_mint_command(
                policy_id=params["policy_id"], agent_or_role=params["agent_or_role"], max_tasks=params["max_tasks"],
                task_mode=params["task_mode"], expires_at=params["expires_at"], decision_summary=params["decision_summary"],
                governance_root=params["governance_root"], lane_repos=params["lane_repo"], launch_harnesses=params["launch_harness"]))

    def confirm(params: dict[str, Any], context: dict[str, Any]) -> tuple[dict, dict, MintRequestError | None]:
        return _confirm(params, context["payload"], decl, connection_json)

    return handle_owner_write(body, decl=decl, action="envelope_mint", session=session, home_root=home_root, preview_store=preview_store,
                              audit_log_path=audit_log_path, prepare=prepare, preview=preview, confirm=confirm, pre_confirm=pre_confirm,
                              origin=origin, host=host, content_type=content_type, now=now)


def normalize_authorization_params(body: dict[str, Any], *, project: str, governance_root: Path, now: datetime) -> dict[str, Any]:
    """AIPOS-F224 件③: 看板「项目授权」表单 / 授权请求卡 → 参数(预演与确认同一归一, 摘要据此算; 不含随时刻变化的值)。
    action ∈ 声明 actions; extend = 车道(仓:类型,…:驱动方,…, 分号分隔)/ 主机 / 禁区(逗号分隔)/ 总卡数 / 到期, 或授权请求带来的建议授权块 block;
    revoke = 原因必填。"""
    from tools.aipos_cli.autonomy_policy import authorization_ids

    actions = authorization_board_declaration()["actions"]
    action = str(body.get("action") or "").strip()
    if action not in actions:
        raise MintRequestError("PARAMS_INVALID", detail=f"action 须为 {list(actions)}")
    params: dict[str, Any] = {"project": project, "governance_root": str(governance_root), "mode": "authorization", "action": action}
    if action != "migrate" and not authorization_ids(governance_root):
        raise MintRequestError("PARAMS_INVALID", detail=f"项目 {project} 还没有项目授权, 不能 {action}(先迁移或经 CLI lybra project grant sign)")
    request = str(body.get("request_item") or "").strip()
    if request and not _REQUEST_ITEM_RE.fullmatch(request):
        raise MintRequestError("PARAMS_INVALID", detail=f"request_item 形不合: {request!r}")
    params["request_item"] = request
    if action == "revoke":
        reason = " ".join(str(body.get("reason") or "").split())
        if not reason:
            raise MintRequestError("PARAMS_INVALID", detail="撤销须写原因")
        params["reason"] = reason
    elif action == "extend":
        if isinstance(body.get("block"), dict):  # 授权请求「按建议签发」: 建议授权块原样(门侧 build_authorization_decision 校验)
            params["block"] = body["block"]
            return params
        lanes = []
        for spec in [x.strip() for x in str(body.get("lane") or "").split(";") if x.strip()]:
            parts = spec.split(":")
            if len(parts) != 3 or not parts[0].strip() or not _split_list(parts[1]) or not _split_list(parts[2]):
                raise MintRequestError("PARAMS_INVALID", detail=f"车道须为 仓:类型,…:驱动方,…: {spec!r}")
            lanes.append({"repo": parts[0].strip(), "task_modes": _split_list(parts[1]), "drivers": _split_list(parts[2])})
        params.update({"lanes": lanes, "hosts": _split_list(body.get("host")), "zones_open": _split_list(body.get("open_zone")),
                       "max_tasks": _parse_max_tasks(body["max_tasks"]) if str(body.get("max_tasks") or "").strip() else None,
                       "expires_at": _parse_expiry(body["expires_at"], now) if str(body.get("expires_at") or "").strip() else None})
        if not (lanes or params["hosts"] or params["zones_open"] or params["max_tasks"] or params["expires_at"]):
            raise MintRequestError("PARAMS_INVALID", detail="扩大授权至少给一项(车道 / 主机 / 禁区 / 总卡数 / 到期)")
    return params


def _authorization_payload(params: dict[str, Any], decl: dict[str, Any], session: dict[str, Any], now: datetime) -> dict[str, Any]:
    """参数 → owner_decision_record payload(project_authorization 块唯一构造 autonomy_policy.authorization_block / 迁移草案
    authorization_draft_from_envelopes; 来源字段 = 看板会话)。"""
    from tools.aipos_cli.autonomy_policy import authorization_block

    root = Path(params["governance_root"])
    if params["action"] == "migrate":
        draft = authorization_draft_from_envelopes(root, now=now)
        if draft["block"] is None:
            raise MintRequestError("PREVIEW_BLOCKED", detail="; ".join(draft["notes"]))
        block = draft["block"]
    elif params["action"] == "revoke":
        block = authorization_block(root, action="revoke", now=now, notes=params["reason"])
    elif isinstance(params.get("block"), dict):
        block = params["block"]
    else:
        block = authorization_block(root, action="extend", now=now, lanes=params["lanes"], hosts=params["hosts"], zones_open=params["zones_open"],
                                    max_tasks=params["max_tasks"], expires_at=params["expires_at"])
    payload = {"decision_id": f"grant-{block.get('authorization_id')}-r{int(block.get('base_revision') or 0) + 1:04d}",
               "project_authorization": block, "actor": str(session["role"]), "decided_by_ref": str(session["role"]),
               **session_provenance(decl, session)}
    if params.get("request_item"):
        payload["decision_summary"] = (f"board {params['action']} project grant {block.get('authorization_id')} "
                                       + _fmt(decl["texts"]["request_part"], "texts.request_part", request_item=params["request_item"]))[:320]
    return payload


def _authorization_callbacks(decl: dict[str, Any], connection_json: Path | None) -> tuple[Any, Any, Any, Any]:
    """mode = authorization 的 prepare / preview / confirm / pre_confirm(安全判定全在 handle_owner_write)。"""
    def prepare(body: dict[str, Any], project: str, root: Path, now: datetime, session: dict[str, Any]) -> tuple[dict, dict, dict]:
        params = normalize_authorization_params(body, project=project, governance_root=root, now=now)
        payload = _authorization_payload(params, decl, session, now)
        audit = {"project": project, "mode": "authorization", "grant_action": params["action"],
                 "policy_id": payload["project_authorization"].get("authorization_id"), "request_item": params.get("request_item") or None}
        return params, audit, {"payload": payload}

    def preview(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        from tools.aipos_cli.envelope_mint import mint_preview

        payload = context["payload"]
        try:
            result = mint_preview([payload], governance_root=Path(params["governance_root"]), actor=str(payload["actor"]))[0]
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise MintRequestError("PREVIEW_BLOCKED", detail=f"{type(exc).__name__}: {exc}") from exc
        if str(result.get("verdict") or "") == "BLOCK" or not result.get("ok"):
            raise MintRequestError("PREVIEW_BLOCKED", detail=json.dumps(result.get("blocking_reasons") or result.get("errors")
                                                                        or result.get("message"), ensure_ascii=False)[:800])
        record = (result.get("data") or {}).get("authorization_record") or {}
        t = authorization_board_declaration()["texts"]
        values = {"project": params["project"], "authorization_id": record.get("authorization_id"), "revision": record.get("revision"),
                  "action": record.get("action"), "max_tasks": record.get("max_tasks"), "expires_at": record.get("expires_at"),
                  "lanes": "; ".join(f"{l['repo']}({', '.join(l['task_modes'])} / {', '.join(l['drivers'])})" for l in record.get("lanes") or []),
                  "hosts": ", ".join(record.get("hosts") or []), "launch": ", ".join(record.get("launch_harnesses") or []) or t["launch_manual"],
                  "zones": ", ".join(record.get("zones_open") or []) or t["zones_none"],
                  "supersedes": ", ".join(record.get("supersedes_policies") or []) or "-", "verdict": result.get("verdict")}
        lines = [_fmt(line, "authorization.texts.summary_lines", **values) for line in t["summary_lines"]]
        dry = {k: v for k, v in result.items() if k != "dry_run_token"}
        dry["data"] = {k: v for k, v in (result.get("data") or {}).items() if k != "dry_run_token"}
        return {"params": {**params, "policy_id": record.get("authorization_id")}, "summary_lines": lines, "preview_lines": lines, "dry_run": dry}

    def pre_confirm(params: dict[str, Any], context: dict[str, Any]) -> None:
        from tools.aipos_cli.autonomy_policy import render_authorization_command

        if connection_json is None:
            raise MintRequestError("CREDENTIAL_UNAVAILABLE", command=render_authorization_command(
                params["governance_root"], "draft --from-envelopes" if params["action"] == "migrate" else params["action"], []))

    def confirm(params: dict[str, Any], context: dict[str, Any]) -> tuple[dict, dict, MintRequestError | None]:
        return _confirm(params, context["payload"], decl, connection_json)

    return prepare, preview, confirm, pre_confirm


def _preview(params: dict[str, Any], payload: dict[str, Any], decl: dict[str, Any]) -> dict[str, Any]:
    from tools.aipos_cli.envelope_mint import mint_preview

    try:
        result = mint_preview([payload], governance_root=Path(params["governance_root"]), actor=str(payload["actor"]))[0]
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise MintRequestError("PREVIEW_BLOCKED", detail=f"{type(exc).__name__}: {exc}") from exc
    verdict = str(result.get("verdict") or "")
    if verdict == "BLOCK" or not result.get("ok"):
        raise MintRequestError("PREVIEW_BLOCKED", detail=json.dumps(result.get("blocking_reasons") or result.get("errors")
                                                                    or result.get("message"), ensure_ascii=False)[:800])
    dry = {k: v for k, v in result.items() if k != "dry_run_token"}
    dry["data"] = {k: v for k, v in (result.get("data") or {}).items() if k != "dry_run_token"}
    return {"params": params, "preview_lines": preview_lines(params, payload, verdict, decl), "dry_run": dry}


def _confirm(params: dict[str, Any], payload: dict[str, Any], decl: dict[str, Any],
             connection_json: Path | None) -> tuple[dict[str, Any], dict[str, Any], MintRequestError | None]:
    from tools.aipos_cli.envelope_mint import mint_via_gate

    report = mint_via_gate([payload], governance_root=Path(params["governance_root"]), actor=str(payload["actor"]),
                           connection_json=str(connection_json), token_role=str(decl["token_role"]))
    landing = report.get("landing") or {}
    landing_view = {k: landing.get(k) for k in ("landed", "verdict", "committed", "pushed", "commit", "exit_code") if k in landing}
    result = {"outcome": "signed" if report["signed"] else "mint_rejected", "credential_fingerprint": report.get("credential_fingerprint"),
              "signed": report["signed"], "error": report.get("error"), "landing": landing_view}
    if not report["signed"]:
        return result, {}, MintRequestError("MINT_REJECTED", detail=str(report.get("error") or report.get("exit_code")))
    return result, {"ok": True, "stage": "confirm", "params": params, "actor": payload["actor"], "captured_by": payload.get("captured_by"),
                    "capture_surface": payload.get("capture_surface"), "signed": report["signed"], "landing": landing_view,
                    "landing_line": report.get("landing_line"), "exit_code": report["exit_code"], "error": report.get("error")}, None


def authorization_draft_from_envelopes(governance_root: Path | str, *, now: datetime | None = None,
                                       max_tasks: int | None = None) -> dict[str, Any]:
    """AIPOS-F224 件③ 迁移草案(唯一实现; 只读预演, 声明 verbs.schema lybra_loop.envelope.project_authorization.migration; 放在看板 / CLI 层模块,
    不进 autonomy_policy, 免 finalize 合并后导入闭包经 validation_root 膨胀): 项目当前在用信封(envelope_overview current, 未被接替且未迁入)
    合并成一份 grant 授权块——车道 = lane 仓并集(不限 lane = project.json repos 全部仓 / 无清单 = 缺省仓)× 任务类型(不限 = enums task_mode 全部)
    × 驱动方(agent_or_role); 拉起并集; 期限最晚; 总卡数 = 剩余额度和(max_tasks 给出 = 用给出的); 主机 = 队列在途卡执行 / 审计实例 host 段 ∪
    validation_roots.hosts ∪ 本机; 禁区全禁; supersedes_policies = 这些信封。返回 {block, envelopes, notes, remaining_sum}; 无在用信封 = block None。"""
    from tools.aipos_cli.autonomy_policy import (
        authorization_block,
        authorization_superseded_policies,
        card_host,
        envelope_overview,
    )
    from tools.aipos_cli.clock import utc_now
    from tools.aipos_cli.frontmatter import read_markdown_frontmatter
    from tools.aipos_cli.validation_root import local_host_label, project_validation_hosts
    from tools.aipos_cli.workspace_config import project_repos
    from tools.schema_loader import get_enum_values

    root = Path(governance_root)
    now = now or utc_now()
    migrated = authorization_superseded_policies(root)
    rows = [r for r in envelope_overview(root, now=now)["envelopes"] if r["current"] and r["policy_id"] not in migrated]
    notes: list[str] = []
    if not rows:
        return {"block": None, "envelopes": [], "notes": ["没有在用信封可迁移"], "remaining_sum": 0}
    repos_decl = project_repos(root)
    all_repos = list((repos_decl.get("items") or {})) or ([str(repos_decl["code_repo"])] if repos_decl.get("code_repo") else [])
    all_modes = list(get_enum_values("task_mode"))
    lanes: list[dict[str, Any]] = []
    for row in rows:
        repos = list(row.get("lane_repos") or []) or all_repos
        modes = [row["task_mode"]] if row.get("task_mode") else all_modes
        if not repos:
            notes.append(f"{row['policy_id']}: 推不出车道仓(project.json 无 repos / code_repo), 未并入车道")
        lanes += [{"repo": r, "task_modes": modes, "drivers": [row["agent_or_role"]]} for r in repos if row.get("agent_or_role")]
        extra = [f"{k}={row[k]!r}" for k in ("project_selector", "task_ids") if row.get(k)]
        if extra:
            notes.append(f"{row['policy_id']} 另有不可表达的选择器 {', '.join(extra)}(授权按车道 × 任务类型覆盖, 范围以草案为准)")
    remaining = sum(int(r.get("remaining") or 0) for r in rows)
    hosts = [local_host_label()]
    try:
        hosts += list(project_validation_hosts(root))
    except ValueError as exc:  # 验证根主机段形不合 = 不并入(原文进 notes, 不猜)
        notes.append(f"validation_roots.hosts 读不出, 未并入主机: {exc}")
    from tools.aipos_cli.task_loader import QUEUE_STATES, QUEUE_TERMINAL_STATES, iter_queue_task_paths

    for path in iter_queue_task_paths(root, states=tuple(s for s in QUEUE_STATES if s not in QUEUE_TERMINAL_STATES)):
        try:
            card, _body, _warn, _fence = read_markdown_frontmatter(path)
        except OSError as exc:  # 读不出的卡不并入主机(原文进 notes)
            notes.append(f"卡 {path.name} 读不出, 未并入主机: {exc}")
            continue
        host = card_host(card)
        if host:
            hosts.append(host)
    expiry = max((str(r["expires_at"]) for r in rows if r.get("expires_at")), default="")
    total = int(max_tasks) if max_tasks is not None else remaining
    if total <= 0:
        notes.append("在用信封剩余额度和为 0: 须 Owner 显式给 --max-tasks(总卡数硬上限必填)")
    block = authorization_block(root, action="grant", now=now, lanes=lanes, hosts=list(dict.fromkeys(hosts)),
                                launch_harnesses=sorted({h for r in rows for h in r.get("launch_harnesses") or []}), zones_open=[],
                                max_tasks=total if total > 0 else None, expires_at=expiry or None,
                                supersedes_policies=[r["policy_id"] for r in rows],
                                notes=f"migrated from envelopes {', '.join(r['policy_id'] for r in rows)}")
    return {"block": block, "envelopes": [{k: r.get(k) for k in ("policy_id", "agent_or_role", "task_mode", "lane_repos", "used", "max_tasks",
                                                               "remaining", "expires_at", "launch_harnesses")} for r in rows],
            "notes": notes, "remaining_sum": remaining}


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
