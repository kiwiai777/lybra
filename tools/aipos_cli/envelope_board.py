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
    return decl


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
                                 "status_error": p["status_error"], "envelopes": [], "drivers": [], "error": None}
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
        projects.append(entry)
    return {"home_root": str(enumerated["home_root"]), "home_source": enumerated["home_source"], "projects": projects}


def _scope_text(row: dict[str, Any], texts: dict[str, Any]) -> str:
    return _fmt(texts["scope_line"], "texts.scope_line", task_mode=row.get("task_mode") or texts["scope_any"],
                lane_repos=", ".join(row.get("lane_repos") or []) or texts["scope_any"],
                launch_harnesses=", ".join(row.get("launch_harnesses") or []) or texts["scope_manual"])


def _row_html(row: dict[str, Any], decl: dict[str, Any], *, project: str, owner: bool) -> str:
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
    out = [f"<tr id=\"{esc(str(row['policy_id']))}\" class=\"envelope-row envelope-tone-{tone}\" data-policy-id=\"{esc(str(row['policy_id']))}\""
           f" data-status=\"{esc(status)}\" data-tone=\"{tone}\" data-current=\"{str(bool(row.get('current'))).lower()}\">"]
    out.extend(f"<td>{c}</td>" for c in cells)
    if owner and row.get("current") and row.get("next_policy_id"):
        default_exp = _default_expiry(decl)
        note = (f"<p class=\"envelope-note\">{esc(_fmt(texts['renew_note'], 'texts.renew_note', note=row['renew_note']))}</p>"
                if row.get("renew_note") else "")
        out.append(
            "<td><form class=\"envelope-form\" data-mode=\"renew\""
            f" data-workspace=\"{esc(project)}\" data-template=\"{esc(str(row['policy_id']))}\">"
            f"<label>{esc(texts['field_max_tasks'])} <input name=\"max_tasks\" type=\"number\" min=\"1\" value=\"{esc(str(row.get('max_tasks') or 1))}\"></label>"
            f"<label>{esc(texts['field_expires_at'])} <input name=\"expires_at\" value=\"{esc(default_exp)}\"></label>"
            f"<button type=\"submit\">{esc(_fmt(texts['renew_button'], 'texts.renew_button', policy_id=row['next_policy_id']))}</button>"
            f"{note}</form></td>")
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


def _table(rows: list[dict[str, Any]], decl: dict[str, Any], *, project: str, owner: bool, css: str) -> str:
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in decl["texts"]["columns"]) + "<th></th>"
    body = "".join(_row_html(r, decl, project=project, owner=owner) for r in rows)
    return f"<table class=\"{css}\"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def envelopes_page_parts(view: dict[str, Any], *, owner: bool) -> dict[str, Any]:
    """看板「信封」页正文(服务端渲染; 全部值经 html.escape)。owner=False(非 Owner 会话)= 只读, 不出任何表单 / 脚本。
    AIPOS-F180: 页面骨架(顶部导航 / 样式 / 「待授权请求」段)由看板 F174 骨架 web/board/readonly_pages.render_envelopes 套上;
    本函数只出 {title, h1_attrs, sections}(Owner 写例外的 body 属性与脚本由骨架按会话角色统一给)。"""
    esc = html.escape
    decl = board_envelopes_declaration()
    texts = decl["texts"]
    title = _fmt(texts["title"], "texts.title", projects=len(view["projects"]), home_root=view["home_root"])
    parts: list[str] = []
    if not owner:
        parts.append(f"<p class=\"envelope-readonly\">{esc(texts['readonly_hint'])}</p>")
    for proj in view["projects"]:
        name = str(proj["project"])
        heading = _fmt(texts["project_heading"], "texts.project_heading", project=name) + (texts["project_paused"] if proj.get("paused") else "")
        parts.append(f"<section class=\"envelope-project\" id=\"project-{esc(name)}\" data-project=\"{esc(name)}\"><h2>{esc(heading)}</h2>")
        if proj.get("error"):
            parts.append(f"<p class=\"envelope-error\">{esc(_fmt(texts['page_error'], 'texts.page_error', error=proj['error']))}</p></section>")
            continue
        rows = proj["envelopes"]
        if not rows:
            parts.append(f"<p class=\"envelope-none\">{esc(texts['none'])}</p>")
        current = [r for r in rows if r.get("current")]
        history = [r for r in rows if not r.get("current")]
        if rows:
            parts.append(f"<h3>{esc(texts['current_heading'])}</h3>")
            parts.append(_table(current, decl, project=name, owner=owner, css="envelope-current"))
            by_id = {r["policy_id"]: r for r in rows}
            for drv in proj.get("drivers") or []:
                # 无在用信封的驱动方点名一行(其最新信封已被别的信封接替 = 已有着落, 不点名)
                if not drv["current"] and not (by_id.get(drv["latest"]) or {}).get("superseded_by"):
                    parts.append(f"<p class=\"envelope-driver-none\">{esc(_fmt(texts['driver_none'], 'texts.driver_none', **drv))}</p>")
        if history:
            summary = _fmt(texts["history_heading"], "texts.history_heading", count=len(history))
            parts.append(f"<details class=\"envelope-history\"><summary>{esc(summary)}</summary>"
                         + _table(history, decl, project=name, owner=False, css="envelope-folded") + "</details>")
        if owner:
            parts.append(_new_form_html(name, decl))
        parts.append("</section>")
    return {"title": title, "h1_attrs": f" data-envelope-projects=\"{len(view['projects'])}\"", "sections": parts}


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
    preview_store = 预演令牌表(issue(session_ref, digest) / take(token) → (status, entry), web/board/auth_otc.PreviewTokenStore)。"""
    decl = board_envelopes_declaration()

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


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
