"""AIPOS-F174 — 看板只读页渲染(总览 / 路线图与验收 / 项目接入): 服务端渲染, 只渲染不另算。

数据只来自 tools/aipos_cli/board_pages.py(其各面只调既有唯一推导); 界面文字全部走看板 i18n 单通道 web/board/app.py
_I18N_TEMPLATES(_generate_text, zh / en 同键; 本文件不含界面文字字面量, 过 CJK 守卫)。推导给出的原因 / 建议 / 命令等内容原样显示
(内容不翻译, 与看板既有约定一致)。全部值经 html.escape; 反引号包住的命令渲染为 <code>。页面仅 GET, 无表单无按钮: 需 Owner 动作处
给「告诉 <顾问实例>: …」与顾问命令。DOM 沿用旧项目详情页的里程碑 / 验证台类名(roadmap-* / vb-*), 样式在 static/readonly-pages.css。
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

_ESC = html.escape
_CURRENT = ' aria-current="page"'
# AIPOS-F183: the brand mark of the top-bar lockup (official design-system asset, copied into static/assets)
_LOGO = "/assets/lybra-logo-black.png"


class _Ctx:
    def __init__(self, decl: dict[str, Any], lang: str) -> None:
        self.decl = decl
        self.lang = lang

    def t(self, key: str, **values: Any) -> str:
        from web.board.app import _generate_text

        return _generate_text(key, self.lang, **values)

    def raw(self, key: str) -> str:
        """Unformatted template (handed to the page script, which fills the placeholders); missing key = KeyError (same red line)."""
        from web.board.app import _I18N_TEMPLATES

        return str(_I18N_TEMPLATES[self.lang if self.lang in _I18N_TEMPLATES else "zh"][key])

    def href(self, url: str) -> str:
        """站内链接带上当前语言(缺省语言不带)。"""
        locale = self.decl["locale"]
        if self.lang == locale["default"]:
            return url
        base, _, frag = url.partition("#")
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}{urlencode({locale['param']: self.lang})}" + (f"#{frag}" if frag else "")

    def tell(self, targets: list[str], ask_key: str, values: dict[str, Any]) -> str:
        who = " / ".join(targets) if targets else self.t("rp_advisor_fallback")
        return self.t("rp_tell", advisor=who, ask=self.t(ask_key, **values))


def resolve_lang(decl: dict[str, Any], params: dict[str, list[str]]) -> str:
    locale = decl["locale"]
    asked = (params.get(locale["param"]) or [""])[0]
    return asked if asked in locale["supported"] else str(locale["default"])


def _rich(text: Any) -> str:
    """推导给出的文本原样显示; 反引号段渲染为 <code>(命令可直接复制)。先转义后变换。"""
    parts = str(text).split("`")
    out = []
    for i, part in enumerate(parts):
        out.append(f"<code>{_ESC(part)}</code>" if i % 2 and i < len(parts) - 1 else _ESC(part if i % 2 == 0 else "`" + part))
    return "".join(out)


def _command_block(ctx: _Ctx, commands: list[str]) -> str:
    """Advisor commands, folded (AIPOS-F181: command lines and server paths stay out of the page body)."""
    if not commands:
        return ""
    items = "".join(f"<li>{_rich(c) if '`' in c else '<code>' + _ESC(c) + '</code>'}</li>" for c in commands)
    return f"<details class=\"rp-cmd\"><summary class=\"rp-cmd-label\">{_ESC(ctx.t('rp_command'))}</summary><ul>{items}</ul></details>"


def _step_html(ctx: _Ctx, text: Any) -> str:
    """A derived step sentence: the words before its first command stay visible, the full sentence with the command is folded."""
    return _ESC(_words(text)) + (_command_text(ctx, text) if "`" in str(text) else "")


def _command_text(ctx: _Ctx, text: Any) -> str:
    """One advisor command paragraph (backticks = <code>), folded like _command_block."""
    return f"<details class=\"rp-cmd\"><summary class=\"rp-cmd-label\">{_ESC(ctx.t('rp_command'))}</summary><p>{_rich(text)}</p></details>"


def _bar(met: int, total: int, ratio: Any = None) -> str:
    pct = round(100 * (ratio if isinstance(ratio, (int, float)) else (met / total if total else 0)))
    return (f"<span class=\"rp-bar\" role=\"progressbar\" aria-valuemin=\"0\" aria-valuemax=\"{total}\" aria-valuenow=\"{met}\""
            f" data-met=\"{met}\" data-total=\"{total}\" data-percent=\"{pct}\"><span style=\"width:{pct}%\"></span></span>"
            f"<span class=\"rp-bar-text\">{met}/{total}</span>")


def _nav_urls(decl: dict[str, Any]) -> dict[str, str]:
    """AIPOS-F246 item 5: the top nav (declared board_readonly_pages.home.nav): home / project / history & audit (until F247 = the
    project page all-cards view) / engineering (collects the other pages; no page or route removed)."""
    from tools.aipos_cli.board_pages import page_url

    home = decl["home"]
    return {"home": str(decl["pages"]["home"]), "project": str(decl["pages"]["project"]),
            "history": page_url(decl, "project", view=home["cards_view_value"]), "engineering": str(decl["pages"]["engineering"])}


def _page(ctx: _Ctx, page: str, title: str, body: list[str], *, note_key: str | None = "rp_readonly_note", h1_attrs: str = "",
          body_attrs: str = "", scripts: tuple[str, ...] | list[str] = (), current_url: str | None = None,
          nav_current: str | None = None) -> str:
    """F174 page skeleton (top nav + stylesheet + language switch). AIPOS-F180: the Owner inbox / envelope pages reuse it with
    note_key=None (no read-only note: they host the F172 write exception), extra body attributes, page scripts and current_url.
    AIPOS-F181: the single skeleton of the whole board (the upgrade page too; the board root redirects to the home page); one nav.
    AIPOS-F246 item 5: the nav is home / project / history / engineering; every other page (inbox, envelopes, roadmap, onboarding,
    workstation, upgrade, the engineering overview) sits under engineering (nav_current overrides, e.g. the all-cards view = history)."""
    decl = ctx.decl
    locale = decl["locale"]
    urls = _nav_urls(decl)
    current = nav_current or (page if page in ("home", "project") else "engineering")
    links = "".join(f"<a href=\"{_ESC(ctx.href(urls[key]))}\" data-nav=\"{_ESC(key)}\"{_CURRENT if key == current else ''}>"
                    f"{_ESC(ctx.t('rp_nav_' + key))}</a>" for key in decl["home"]["nav"])
    other = next((code for code in locale["supported"] if code != ctx.lang), None)
    switch = ""
    if other:
        switch = (f"<a class=\"rp-lang\" data-set-lang=\"{_ESC(other)}\" href=\"?{_ESC(urlencode({locale['param']: other}))}\">"
                  f"{_ESC(ctx.t('rp_lang_other'))}</a>")
    script = (
        "<script>(function(){var k=" + _js(locale["storage_key"]) + ",p=" + _js(locale["param"]) + ",d=" + _js(locale["default"])
        + ";try{var q=new URLSearchParams(location.search),s=localStorage.getItem(k);"
        "document.querySelectorAll('[data-set-lang]').forEach(function(a){a.addEventListener('click',function(){"
        "try{localStorage.setItem(k,a.getAttribute('data-set-lang'))}catch(e){}})});"
        "if(!q.has(p)&&s&&s!==d&&" + _js(list(locale["supported"])) + ".indexOf(s)>=0){q.set(p,s);"
        "location.replace(location.pathname+'?'+q.toString()+location.hash)}}catch(e){}})();</script>")
    return "\n".join([
        "<!doctype html>", f"<html lang=\"{_ESC(ctx.lang)}\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>{_ESC(title)}</title><link rel=\"stylesheet\" href=\"{_ESC(str(decl['stylesheet']))}\"></head>",
        f"<body class=\"rp-page rp-{_ESC(page)}\" data-readonly-page=\"{_ESC(page)}\" data-lang=\"{_ESC(ctx.lang)}\"{body_attrs}>",
        f"<header class=\"rp-nav\"><span class=\"rp-brand\"><img class=\"rp-logo\" src=\"{_LOGO}\" alt=\"\">Lybra</span>"
        f"<nav>{links}</nav>{switch}</header>",
        f"<main><h1{h1_attrs}>{_ESC(title)}</h1>",
        *([f"<p class=\"rp-readonly-note\">{_ESC(ctx.t(note_key))}</p>"] if note_key else []),
        *body, "</main>", script, *(f"<script src=\"{_ESC(src)}\"></script>" for src in scripts), "</body></html>"])


def _tech(ctx: _Ctx, inner: str, *, css: str = "rp-tech") -> str:
    """AIPOS-F181: technical details (server paths, ids, raw lines) folded; the summary is the only visible part."""
    return f"<details class=\"{css}\"><summary>{_ESC(ctx.t('rp_tech'))}</summary>{inner}</details>"


def _toggle(ctx: _Ctx, hidden: list[str], show_all: bool, base_url: str, *, show_key: str = "rp_show_hidden",
            hide_key: str = "rp_hide_hidden") -> str:
    """AIPOS-F181: the page switch for projects hidden by status (replaces the command-line hint); base_url = this page with its
    other parameters, the switch adds / drops query.show."""
    decl = ctx.decl
    if show_all:
        return f"<p class=\"rp-toggle\" data-show=\"all\"><a href=\"{_ESC(ctx.href(base_url))}\">{_ESC(ctx.t(hide_key))}</a></p>"
    if not hidden:
        return ""
    sep = "&" if "?" in base_url else "?"
    url = f"{base_url}{sep}{urlencode({decl['query']['show']: decl['show_all_value']})}"
    return (f"<p class=\"rp-toggle\" data-hidden=\"{len(hidden)}\"><a href=\"{_ESC(ctx.href(url))}\">"
            f"{_ESC(ctx.t(show_key, count=len(hidden)))}</a></p>")


def _status_badge(ctx: _Ctx, item: dict[str, Any]) -> str:
    if item.get("paused"):
        return f" <span class=\"rp-badge\">{_ESC(ctx.t('rp_paused_badge'))}</span>"
    if item.get("hidden"):
        return f" <span class=\"rp-badge rp-badge-test\">{_ESC(ctx.t('rp_test_badge'))}</span>"
    return ""


def _js(value: Any) -> str:
    import json

    return json.dumps(value).replace("<", "\\u003c")


def _envelope_page() -> str:
    from tools.aipos_cli.envelope_board import board_envelopes_declaration

    return str(board_envelopes_declaration()["page_path"])


def _workstation_page(project: str | None = None) -> str:
    """AIPOS-F198: workstation page address (declared verbs.lybra_project_workstation_plan.board.page_path)."""
    from tools.aipos_cli.workstation_plan import page_url

    return page_url(project)


# ---------------------------------------------------------------------------
# 总览
# ---------------------------------------------------------------------------

def _state_parts(ctx: _Ctx, by_state: dict[str, int], states: list[str]) -> str:
    return " · ".join(ctx.t("rp_qs_count", state=ctx.t("rp_qs_" + s), count=by_state[s]) for s in states if by_state.get(s))


def _counts_li(ctx: _Ctx, queue: dict[str, Any]) -> list[str]:
    """AIPOS-F181 item 3: counts from machine_zone.card_stats (frozen legacy cards only as the history line)."""
    if "error" in queue:
        return [f"<li class=\"rp-inflight rp-err\">{_ESC(ctx.t('rp_error', error=queue['error']))}</li>"]
    stats = queue["value"]
    tot = stats["totals"]
    detail = _state_parts(ctx, stats["by_state"], stats["groups"]["inflight"])
    out = [f"<li class=\"rp-inflight\" data-inflight=\"{tot['inflight']}\" data-todo=\"{tot['todo']}\" data-closed=\"{tot['closed']}\""
           f" data-frozen=\"{stats['frozen']}\">{_ESC(ctx.t('rp_ov_counts', inflight=tot['inflight'], todo=tot['todo'], closed=tot['closed']))}"
           + (f" <span class=\"rp-sub\">({_ESC(detail)})</span>" if detail else "") + "</li>"]
    if stats["frozen_error"]:
        out.append(f"<li class=\"rp-frozen rp-err\">{_ESC(ctx.t('rp_ov_frozen_error', error=stats['frozen_error']))}</li>")
    elif stats["frozen"]:
        out.append(f"<li class=\"rp-frozen\" data-frozen=\"{stats['frozen']}\">{_ESC(ctx.t('rp_ov_frozen', count=stats['frozen']))}</li>")
    return out


def _subproject_table(ctx: _Ctx, project: str, stats: dict[str, Any]) -> str:
    """AIPOS-F181 item 2: project -> subproject counts (one row each, with its advisor); one subproject = one advisor line."""
    from tools.aipos_cli.board_pages import project_url

    subs = stats["subprojects"]
    if not subs:
        return ""
    if len(subs) == 1:
        who = subs[0]["advisor"] or ctx.t("rp_sub_no_advisor")
        return f"<p class=\"rp-sub-one\" data-subproject=\"{_ESC(subs[0]['key'])}\">{_ESC(ctx.t('rp_ov_one_advisor', advisor=who))}</p>"
    head = "".join(f"<th>{_ESC(ctx.t(k))}</th>" for k in ("rp_sub_head", "rp_sub_inflight", "rp_sub_todo", "rp_sub_closed", "rp_sub_advisor"))
    rows = []
    for s in subs:
        c = s["counts"]
        url = ctx.href(project_url(ctx.decl, project, s["key"]))
        rows.append(f"<tr data-subproject=\"{_ESC(s['key'])}\" data-inflight=\"{c['inflight']}\" data-todo=\"{c['todo']}\" data-closed=\"{c['closed']}\">"
                    f"<td><a href=\"{_ESC(url)}\" title=\"{_ESC(s['key'])}\">{_ESC(s['label'])}</a></td><td>{c['inflight']}</td><td>{c['todo']}</td>"
                    f"<td>{c['closed']}</td><td class=\"rp-sub\">{_ESC(s['advisor'] or ctx.t('rp_sub_no_advisor'))}</td></tr>")
    return f"<table class=\"rp-subs\" data-subprojects=\"{len(subs)}\"><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _overview_card(ctx: _Ctx, card: dict[str, Any]) -> str:
    name = str(card["project"])
    li: list[str] = _counts_li(ctx, card["queue"])
    inbox = card["inbox"]
    if inbox["error"]:
        li.append(f"<li class=\"rp-inbox rp-err\">{_ESC(ctx.t('rp_error', error=inbox['error']))}</li>")
    elif inbox["count"] is None:
        li.append(f"<li class=\"rp-inbox\" data-inbox-count=\"\">{_ESC(ctx.t('rp_ov_inbox_paused'))}</li>")
    else:
        url = ctx.href(str(ctx.decl["nav_links"]["inbox"]))
        li.append(f"<li class=\"rp-inbox{' rp-alert' if inbox['count'] else ''}\" data-inbox-count=\"{inbox['count']}\">"
                  f"<a href=\"{_ESC(url)}\">{_ESC(ctx.t('rp_ov_inbox', count=inbox['count']))}</a></li>")
    env = card["envelopes"]
    env_url = _ESC(ctx.href(env["url"]))
    if env["error"]:
        li.append(f"<li class=\"rp-envelope rp-err\" data-envelope-state=\"error\"><a href=\"{env_url}\">"
                  f"{_ESC(ctx.t('rp_error', error=env['error']))}</a></li>")
    else:
        parts = []
        if env["active"]:
            parts.append(ctx.t("rp_ov_envelope_ok", policies=", ".join(env["active"])))
        if env["low"]:
            parts.append(ctx.t("rp_ov_envelope_low", policies=", ".join(env["low"])))
        state = "low" if env["low"] else "ok" if env["active"] else "none"
        text = " · ".join(parts) or ctx.t("rp_ov_envelope_none")
        li.append(f"<li class=\"rp-envelope{' rp-alert' if state != 'ok' else ''}\" data-envelope-state=\"{state}\">"
                  f"<a href=\"{env_url}\">{_ESC(text)}</a></li>")
    rm = card["roadmap"]
    rm_url = _ESC(ctx.href(rm["url"]))
    if rm["error"]:
        li.append(f"<li class=\"rp-roadmap rp-err\"><a href=\"{rm_url}\">{_ESC(ctx.t('rp_error', error=rm['error']))}</a></li>")
    elif not rm["exists"]:
        li.append(f"<li class=\"rp-roadmap\" data-roadmap=\"none\"><a href=\"{rm_url}\">{_ESC(ctx.t('rp_ov_roadmap_none'))}</a></li>")
    else:
        text = ctx.t("rp_ov_roadmap", met=rm["met"], total=rm["total"], milestones=rm["milestones"], signals=rm["signals"], severe=rm["severe"])
        li.append(f"<li class=\"rp-roadmap{' rp-alert' if rm['severe'] else ''}\" data-roadmap=\"declared\" data-signals=\"{rm['signals']}\""
                  f" data-severe=\"{rm['severe']}\"><a href=\"{rm_url}\">{_ESC(text)}</a> {_bar(rm['met'], rm['total'])}</li>")
    li.append(_upgrade_li(ctx, card.get("upgrade")))
    ob = card["onboarding"]
    li.append(f"<li class=\"rp-onboarding\" data-done=\"{ob['done']}\" data-total=\"{ob['total']}\"><a href=\"{_ESC(ctx.href(ob['url']))}\">"
              f"{_ESC(ctx.t('rp_ov_onboarding', done=ob['done'], total=ob['total']))}</a></li>")
    subs = _subproject_table(ctx, name, card["queue"]["value"]) if "value" in card["queue"] else ""
    return (f"<article class=\"rp-card\" data-project=\"{_ESC(name)}\" data-paused=\"{str(bool(card['paused'])).lower()}\""
            f" data-hidden=\"{str(bool(card.get('hidden'))).lower()}\"><h2><a href=\"{_ESC(ctx.href(card['url']))}\">{_ESC(name)}</a>"
            f"{_status_badge(ctx, card)}</h2><ul>{''.join(li)}</ul>{subs}"
            f"<p class=\"rp-card-foot\"><a href=\"{_ESC(ctx.href(card['url']))}\">{_ESC(ctx.t('rp_ov_detail'))}</a></p></article>")


def _upgrade_li(ctx: _Ctx, up: dict[str, Any] | None) -> str:
    """AIPOS-F176: project upgrade badge (link to the upgrade page; count = pending mechanical steps)."""
    if not up:
        return ""
    url = _ESC(ctx.href(str(up["url"])))
    if up.get("error"):
        return f"<li class=\"rp-upgrade rp-err\" data-upgrade-pending=\"\"><a href=\"{url}\">{_ESC(ctx.t('rp_error', error=up['error']))}</a></li>"
    pending = int(up["pending"])
    text = ctx.t("rp_ov_upgrade", count=pending) if pending else ctx.t("rp_ov_upgrade_none")
    return (f"<li class=\"rp-upgrade{' rp-alert' if pending else ''}\" data-upgrade-pending=\"{pending}\"><a href=\"{url}\">"
            f"{_ESC(text)}</a></li>")


def render_overview(data: dict[str, Any], lang: str) -> str:
    ctx = _Ctx(data["decl"], lang)
    active = [c for c in data["projects"] if not c["paused"]]
    paused = [c for c in data["projects"] if c["paused"]]
    body = [_toggle(ctx, data.get("hidden") or [], bool(data.get("show_all")), str(ctx.decl["pages"]["overview"])),
            f"<section class=\"rp-cards\" data-projects=\"{len(active)}\">" + "".join(_overview_card(ctx, c) for c in active) + "</section>"]
    if paused:
        body.append(f"<details class=\"rp-paused\" data-paused=\"{len(paused)}\"><summary>{_ESC(ctx.t('rp_paused_folded', count=len(paused)))}"
                    "</summary><section class=\"rp-cards\">" + "".join(_overview_card(ctx, c) for c in paused) + "</section></details>")
    return _page(ctx, "overview", ctx.t("rp_ov_title", count=len(data["projects"])), body)


# ---------------------------------------------------------------------------
# AIPOS-F246: home (needs-you + one card per advisor) / project brief (per advisor) / engineering page
# Text written by advisors / card authors is wrapped in data-authored spans (shown as written; the home forbidden-word guard
# checks the interface text, board_home.forbidden_hits).
# ---------------------------------------------------------------------------

def _authored(text: Any) -> str:
    return f"<span data-authored>{_ESC(str(text))}</span>"


def _age_text(ctx: _Ctx, hours: Any) -> str:
    if hours is None:
        return ctx.t("hm_foot_none")
    if hours < 1:
        return ctx.t("hm_age_now")
    if hours < 24:
        return ctx.t("hm_age_hours", hours=int(hours))
    return ctx.t("hm_age_days", days=int(hours // 24))


def _headline_age(ctx: _Ctx, headline: dict[str, Any] | None) -> tuple[str, bool]:
    """(foot age text, stale)."""
    if not headline:
        return ctx.t("hm_foot_none"), False
    hours = headline.get("age_hours")
    if headline.get("stale"):
        return ctx.t("hm_stale", days=int((hours or 0) // 24)), True
    return _age_text(ctx, hours), False


def _meter(ctx: _Ctx, progress: dict[str, Any]) -> str:
    if progress["kind"] == "criteria":
        met, total = int(progress["met"]), int(progress["total"])
        pct = round(100 * met / total) if total else 0
        text = ctx.t("hm_progress_criteria", met=met, total=total)
    else:
        pct = 100 if progress["count"] else 0
        text = ctx.t("hm_progress_closed", days=progress["days"], count=progress["count"])
    return (f"<div class=\"hm-meter\" data-progress=\"{_ESC(progress['kind'])}\"><span class=\"hm-bar\" role=\"progressbar\""
            f" aria-valuenow=\"{pct}\" aria-valuemin=\"0\" aria-valuemax=\"100\"><i style=\"width:{pct}%\"></i></span>"
            f"<span>{_ESC(text)}</span></div>")


def _stuck_text(ctx: _Ctx, count: int, oldest: dict[str, Any] | None) -> str:
    if not count or not oldest:
        return _ESC(ctx.t("hm_stuck_none"))
    marker = "\u0000"
    key = "hm_stuck_line" if oldest.get("days") is not None else "hm_stuck_line_norecord"
    text = ctx.t(key, count=count, title=marker, days=oldest.get("days"))
    head, _, tail = text.partition(marker)
    return _ESC(head) + _authored(oldest.get("title") or "") + _ESC(tail)


def _home_card(ctx: _Ctx, card: dict[str, Any]) -> str:
    hl = card.get("headline") or {}
    age, stale = _headline_age(ctx, card.get("headline"))
    stage = f"<span class=\"hm-chip\" data-authored>{_ESC(hl['stage'])}</span>" if hl.get("stage") else ""
    paused = f"<span class=\"hm-chip hm-chip-q\">{_ESC(ctx.t('hm_paused_chip'))}</span>" if card.get("paused") else ""
    if card.get("headline"):
        rows_now = f"<div class=\"hm-row\"><b>{_ESC(ctx.t('hm_now'))}</b>{_authored(hl.get('now') or '')}</div>"
        rows_done = f"<div class=\"hm-row\"><b>{_ESC(ctx.t('hm_done'))}</b>{_authored(hl.get('done') or '')}</div>"
    else:
        rows_now = f"<div class=\"hm-row hm-none\"><b>{_ESC(ctx.t('hm_now'))}</b><span>{_ESC(ctx.t('hm_no_headline'))}</span></div>"
        rows_done = ""
    stuck = (f"<div class=\"hm-row{' hm-warn' if card['stalled'] else ''}\" data-stalled=\"{card['stalled']}\"><b>{_ESC(ctx.t('hm_stuck'))}</b>"
             f"<span>{_stuck_text(ctx, card['stalled'], card.get('stalled_oldest'))}</span></div>")
    name = _authored(card["display"]) if hl.get("title") else _ESC(card["display"])
    return (f"<a class=\"hm-proj\" href=\"{_ESC(ctx.href(card['url']))}\" data-project=\"{_ESC(card['project'])}\""
            f" data-pushing=\"{card['pushing']}\" data-stalled=\"{card['stalled']}\" data-stale=\"{str(stale).lower()}\""
            f" data-headline=\"{str(bool(card.get('headline'))).lower()}\">"
            f"<div class=\"hm-h\"><span class=\"hm-n\">{name}</span>{stage}{paused}</div>{_meter(ctx, card['progress'])}"
            f"{rows_now}{stuck}{rows_done}"
            f"<div class=\"hm-foot\"><span class=\"{'hm-stale' if stale else ''}\">"
            f"{_ESC(ctx.t('hm_foot', advisor=chr(0), age=age)).replace(chr(0), name)}</span>"
            f"<span>{_ESC(ctx.t('hm_detail'))}</span></div></a>")


def _ask_title_html(ctx: _Ctx, ask: dict[str, Any]) -> str:
    title = ctx.t(ask["key"], **{k: "\u0000" + k + "\u0000" for k in ask["values"]})
    parts = title.split("\u0000")
    return "".join(_authored(ask["values"][part]) if i % 2 else _ESC(part) for i, part in enumerate(parts))


def _button_label(ctx: _Ctx, button: dict[str, Any]) -> str:
    """Renewal / signing buttons are labelled by the board (\"renew N days\"); option buttons by the declared option label."""
    if button["kind"] == "extend_authorization" and not button.get("days"):  # expiry already later: only more cards
        return ctx.t("hm_btn_more", count=button.get("max_tasks") or "?")
    if button["kind"] in ("renew_envelope", "extend_authorization"):
        return ctx.t("hm_btn_renew", days=button.get("days") or "?")
    if button["kind"] == "sign_grant":
        return ctx.t("hm_btn_grant")
    return str(button["label"])


def _ask_button(ctx: _Ctx, ask: dict[str, Any], button: dict[str, Any], *, primary: bool) -> str:
    """AIPOS-F248: one one-click button (data only; static/owner-asks.js posts the declared write path, preview -> one confirm line ->
    confirm; every check is on the server). The confirm / done lines are rendered here (board i18n), {words} filled by the script."""
    import json

    advisor = ask.get("advisor") or ""
    kind = button["kind"]
    label = _button_label(ctx, button)
    if kind == "extend_authorization" and not button.get("days"):
        confirm = ctx.t("hm_cf_more", count=button.get("max_tasks") or "?", advisor=advisor)
        done = ctx.t("hm_done_more", count=button.get("max_tasks") or "?", advisor=advisor)
    elif kind in ("renew_envelope", "extend_authorization"):
        confirm = ctx.t("hm_cf_renew", days=button.get("days") or "?", count=button.get("max_tasks") or "?", advisor=advisor)
        done = ctx.t("hm_done_renew", days=button.get("days") or "?", advisor=advisor)
    elif kind == "sign_grant":
        confirm = ctx.t("hm_cf_grant", advisor=advisor)
        done = ctx.t("hm_done_grant", advisor=advisor)
    else:
        confirm = ctx.t("hm_cf_decide", label=label, advisor=advisor, words="{words}")  # {words} = the Owner sentence, filled by the script
        done = ctx.t("hm_done_decide", label=label, advisor=advisor)
    attrs = (f" data-ask-path=\"{_ESC(button['path'])}\" data-ask-kind=\"{_ESC(kind)}\""
             f" data-ask-payload=\"{_ESC(json.dumps(button['payload'], ensure_ascii=False))}\" data-ask-words=\"{_ESC(button['words'])}\""
             f" data-ask-confirm=\"{_ESC(confirm)}\" data-ask-done=\"{_ESC(done)}\"")
    if primary:
        return (f"<button type=\"button\" class=\"hm-btn\" data-ask-primary=\"true\"{attrs}><small>{_ESC(ctx.t('hm_rec_tag'))}</small>"
                f"{_ESC(label)}</button>")
    text = label + ("\u2026" if button["words"] == "required" else "")
    return f"<button type=\"button\" class=\"hm-alt\"{attrs}>{_ESC(text)}</button>"


def _ask_acts(ctx: _Ctx, ask: dict[str, Any], owner: bool) -> str:
    if not owner:
        return ""
    buttons = []
    if ask.get("primary"):
        buttons.append(_ask_button(ctx, ask, ask["primary"], primary=True))
    buttons += [_ask_button(ctx, ask, b, primary=False) for b in ask.get("alts") or []]
    if ask.get("scope_url"):
        buttons.append(f"<a class=\"hm-alt\" href=\"{_ESC(ctx.href(ask['scope_url']))}\">{_ESC(ctx.t('hm_alt_scope'))}</a>")
    return f"<div class=\"hm-acts\">{''.join(buttons)}</div>" if buttons else ""


def _ask_why(ctx: _Ctx, ask: dict[str, Any]) -> str:
    """The one "why you" sentence: the recommendation reason (advisor-written = authored text) or the spot-check statement."""
    rec = ask.get("rec")
    parts = []
    if ask.get("detail"):
        parts.append(_authored(ask["detail"]))
    if ask.get("artifacts"):
        parts.append(_ESC(ctx.t("hm_artifacts_count", count=ask["artifacts"])))
    if rec and rec.get("reason"):
        label = _button_label(ctx, ask["primary"]) if ask.get("primary") else str(rec.get("label") or "")
        head = (ctx.t("hm_rec_advisor", advisor=rec.get("by") or ctx.t("hm_advisor_generic"), label=label)
                if rec.get("source") == "advisor" else ctx.t("hm_rec_default", label=label))
        reason = _authored(rec["reason"]) if rec.get("source") == "advisor" else _ESC(rec["reason"])
        parts.append(f"<span class=\"hm-why\">{_ESC(head)}{reason}</span>")
    return " · ".join(parts)


def _ask_html(ctx: _Ctx, ask: dict[str, Any], owner: bool = False) -> str:
    if ask.get("group"):
        return _ask_group_html(ctx, ask, owner)
    waited = ctx.t("hm_ask_waited", days=ask["waited_days"]) if ask.get("waited_days") else ctx.t("hm_ask_today")
    why = _ask_why(ctx, ask)
    look = ask.get("look") or {"url": ask["url"], "key": "hm_ask_go"}
    return (f"<div class=\"hm-ask\" id=\"ask-{_ESC(ask['item_id'])}\" data-rank=\"{_ESC(str(ask.get('rank')))}\" data-project=\"{_ESC(ask['project'])}\""
            f" data-item=\"{_ESC(ask['item_id'])}\" data-recommended=\"{str(bool(ask.get('primary'))).lower()}\">"
            f"<div class=\"hm-what\"><div class=\"hm-t\">{_ask_title_html(ctx, ask)}</div><div class=\"hm-d\">"
            + (f"{why} · " if why else "")
            + f"{_ESC(str(ask.get('rank_label') or ''))} · {_ESC(waited)} · <a href=\"{_ESC(ctx.href(look['url']))}\">{_ESC(ctx.t(look['key']))}</a>"
            f"</div><div class=\"hm-bar-line\" hidden></div></div>{_ask_acts(ctx, ask, owner)}</div>")


def _ask_group_html(ctx: _Ctx, group: dict[str, Any], owner: bool) -> str:
    """AIPOS-F248: several spot checks of one milestone merged into one "N acceptance spot checks", expandable item by item."""
    waited = ctx.t("hm_ask_waited", days=group["waited_days"]) if group.get("waited_days") else ctx.t("hm_ask_today")
    marker = "\u0000"
    title = ctx.t("hm_group_title", milestone=marker, count=group["count"])
    head, _, tail = title.partition(marker)
    children = "".join(_ask_html(ctx, child, owner) for child in group["children"])
    return (f"<div class=\"hm-ask hm-group\" data-rank=\"{_ESC(str(group.get('rank')))}\" data-project=\"{_ESC(group['project'])}\""
            f" data-group=\"{group['count']}\"><div class=\"hm-what\"><div class=\"hm-t\">{_ESC(head)}{_authored(group['milestone_title'])}"
            f"{_ESC(tail)}</div><div class=\"hm-d\">{_ESC(str(group.get('rank_label') or ''))} · {_ESC(waited)} · "
            f"<a href=\"{_ESC(ctx.href(group['look']['url']))}\">{_ESC(ctx.t('hm_look_artifacts'))}</a></div>"
            f"<details class=\"hm-fold\"><summary>{_ESC(ctx.t('hm_group_open'))}</summary>{children}</details></div></div>")


_HM_JS_KEYS = ("hm_js_confirm", "hm_js_cancel", "hm_js_next", "hm_js_working", "hm_js_fail", "hm_js_login", "hm_js_words_required",
               "hm_js_words_optional", "hm_js_words_part")


def _asks_attrs(ctx: _Ctx, owner: bool) -> str:
    """Body attributes for static/owner-asks.js (Owner sessions only; texts in the page language)."""
    import json

    if not owner:
        return ""
    texts = json.dumps({key: ctx.raw(key) for key in _HM_JS_KEYS}, ensure_ascii=False)
    return f" data-asks-owner=\"true\" data-asks-texts=\"{_ESC(texts)}\""


def render_home(data: dict[str, Any], lang: str, owner: bool = False) -> str:
    ctx = _Ctx(data["decl"], lang)
    all_asks = bool(data.get("all_asks"))
    body = [] if all_asks else [_toggle(ctx, data.get("hidden") or [], bool(data.get("show_all")), str(ctx.decl["pages"]["home"]))]
    asks = [f"<section class=\"hm-asks\" data-asks=\"{len(data['asks'])}\" data-asks-total=\"{data['asks_total']}\">",
            f"<div class=\"hm-kicker\">{_ESC(ctx.t('hm_asks_kicker', count=data['asks_total']))}</div>"]
    if data.get("inbox_error"):
        asks.append(f"<p class=\"rp-err\">{_ESC(ctx.t('hm_inbox_error', error=data['inbox_error']))}</p>")
    elif not data["asks"]:
        asks.append(f"<p class=\"hm-quiet\">{_ESC(ctx.t('hm_asks_none'))}</p>")
    asks += [_ask_html(ctx, a, owner) for a in data["asks"]]
    more = int(data["asks_total"]) - len(data["asks"])
    if more > 0:
        asks.append(f"<a class=\"hm-more\" href=\"{_ESC(ctx.href(data.get('asks_url') or data['inbox_url']))}\">"
                    f"{_ESC(ctx.t('hm_ask_more', count=more))}</a>")
    asks.append("</section>")
    body += asks
    scripts = [str(data["home"]["asks_script"])] if owner and data["asks"] and data["home"].get("asks_script") else []
    if all_asks:
        return _page(ctx, "home", ctx.t("hm_title"), body, note_key=None, body_attrs=_asks_attrs(ctx, owner), scripts=scripts)
    active = [c for c in data["cards"] if not c["paused"]]
    paused = [c for c in data["cards"] if c["paused"]]
    body.append(f"<section class=\"hm-projects\" data-cards=\"{len(active)}\"><div class=\"hm-kicker\">{_ESC(ctx.t('hm_projects_kicker'))}</div>")
    if not data["cards"]:
        body.append(f"<p class=\"hm-quiet\">{_ESC(ctx.t('hm_no_cards'))}</p>")
    body.append("<div class=\"hm-grid\">" + "".join(_home_card(ctx, c) for c in active) + "</div>")
    if paused:
        body.append(f"<details class=\"hm-paused\" data-paused=\"{len(paused)}\"><summary>{_ESC(ctx.t('hm_paused_folded', count=len(paused)))}"
                    "</summary><div class=\"hm-grid\">" + "".join(_home_card(ctx, c) for c in paused) + "</div></details>")
    body.append("</section>")
    for err in data.get("errors") or []:
        body.append(f"<p class=\"rp-err hm-err\">{_ESC(ctx.t('hm_project_error', project=err['project'], error=err['error']))}</p>")
    return _page(ctx, "home", ctx.t("hm_title"), body, note_key=None, body_attrs=_asks_attrs(ctx, owner), scripts=scripts)


def _brief_rows(ctx: _Ctx, rows: list[dict[str, Any]]) -> str:
    items = []
    for r in rows:
        state = r["state"]
        label = ctx.t("pb_state_" + state)
        extra = ""
        if state == "stalled":
            extra = " · " + (ctx.t("pb_stalled_days", days=r["days"]) if r.get("days") is not None else ctx.t("pb_stalled_norecord"))
        items.append(f"<li data-state=\"{_ESC(state)}\" data-task-id=\"{_ESC(r['task_id'])}\"><span class=\"hm-st{' hm-st-w' if state == 'stalled' else ''}\">"
                     f"{_ESC(label)}</span><span>{_authored(r['title'])}{_ESC(extra)}</span><span class=\"hm-id\">{_ESC(r['task_id'])}</span></li>")
    return f"<ul class=\"hm-list\">{''.join(items)}</ul>"


def _brief_milestone(ctx: _Ctx, ms: dict[str, Any]) -> str:
    if ms["status"] == "done":
        tail = ctx.t("pb_ms_done")
    elif ms["overdue"]:
        tail = ctx.t("pb_ms_overdue", date=ms["target_date"])
    elif ms.get("target_date"):
        tail = ctx.t("pb_ms_target", date=ms["target_date"])
    else:
        tail = ""
    if ms.get("owner_pending") and ms["status"] != "done":
        tail = (tail + " · " if tail else "") + ctx.t("pb_ms_owner", count=ms["owner_pending"])
    pct = round(100 * ms["met"] / ms["total"]) if ms["total"] else 0
    css = "hm-ms" + (" hm-ms-done" if ms["status"] == "done" else "") + (" hm-ms-overdue" if ms["overdue"] else "")
    return (f"<div class=\"{css}\" data-milestone=\"{_ESC(ms['id'])}\" data-met=\"{ms['met']}\" data-total=\"{ms['total']}\">"
            f"<div class=\"hm-mt\">{_authored(ms['title'])}<span>{ms['met']}/{ms['total']}{(' · ' + _ESC(tail)) if tail else ''}</span></div>"
            f"<span class=\"hm-bar\"><i style=\"width:{pct}%\"></i></span></div>")


def render_project_brief(data: dict[str, Any], lang: str) -> str:
    """AIPOS-F246 item 4: the project page = one sheet per advisor; engineering details folded at the bottom."""
    ctx = _Ctx(data["decl"], lang)
    home = data["home"]
    sel = data["selected"]
    body = [f"<a class=\"hm-back\" href=\"{_ESC(ctx.href(str(ctx.decl['pages']['home'])))}\">{_ESC(ctx.t('pb_back'))}</a>",
            _project_tabs(ctx, data, sel["project"] if sel else None)]
    if data.get("unknown_project"):
        body.append(f"<p class=\"rp-err\">{_ESC(ctx.t('rp_rm_unknown_project', project=data['unknown_project']))}</p>")
    if sel is None:
        if not data["projects"]:
            body.append(f"<p>{_ESC(ctx.t('rp_rm_no_projects'))}</p>")
        return _page(ctx, "project", ctx.t("rp_nav_project"), body, note_key=None)
    for err in sel.get("errors") or []:
        body.append(f"<p class=\"rp-err\">{_ESC(ctx.t('rp_error', error=err))}</p>")
    adv = sel.get("advisor")
    if len(sel["advisors"]) > 1:
        links = "".join(f"<a href=\"{_ESC(ctx.href(a['url']))}\"{_CURRENT if adv and a['instance'] == adv['instance'] else ''}>{_ESC(a['display'])}</a>"
                        for a in sel["advisors"])
        body.append(f"<nav class=\"rp-subnav hm-advisors\"><span>{_ESC(ctx.t('pb_advisors'))}</span>{links}</nav>")
    if sel.get("unknown_advisor"):
        body.append(f"<p class=\"rp-err\">{_ESC(ctx.t('pb_unknown_advisor', advisor=sel['unknown_advisor'], shown=(adv or {}).get('display') or '-'))}</p>")
    title = sel["project"]
    if adv is None:
        body.append(f"<p class=\"hm-quiet\">{_ESC(ctx.t('pb_no_advisor'))}</p>")
    else:
        title = adv["display"]
        hl = adv.get("headline") or {}
        age, stale = _headline_age(ctx, adv.get("headline"))
        stage = (str(hl.get("stage")) + " · ") if hl.get("stage") else ""
        body.append(f"<p class=\"hm-sub{' hm-stale' if stale else ''}\">{_ESC(ctx.t('pb_sub', stage=stage, advisor=adv['display'], age=age))}"
                    f" · {_ESC(ctx.t('pb_counts', pushing=len(adv['pushing']), stalled=len(adv['stalled'])))}</p>")
        if adv.get("headline"):
            body.append(f"<div class=\"hm-headline\"><div class=\"hm-row\"><b>{_ESC(ctx.t('hm_now'))}</b>{_authored(hl.get('now') or '')}</div>"
                        f"<div class=\"hm-row\"><b>{_ESC(ctx.t('hm_done'))}</b>{_authored(hl.get('done') or '')}</div></div>")
        else:
            body.append(f"<p class=\"hm-quiet\">{_ESC(ctx.t('hm_no_headline'))}</p>")
        left = [f"<section class=\"hm-sec\" data-section=\"pushing\" data-count=\"{len(adv['pushing'])}\">"
                f"<div class=\"hm-kicker\">{_ESC(ctx.t('pb_pushing_kicker'))}</div>"
                + (_brief_rows(ctx, adv["pushing"]) if adv["pushing"] else f"<p class=\"hm-quiet\">{_ESC(ctx.t('pb_pushing_none'))}</p>")
                + "</section>"]
        if adv["stalled"]:
            left.append(f"<section class=\"hm-sec\" data-section=\"stalled\" data-count=\"{len(adv['stalled'])}\">"
                        f"<div class=\"hm-kicker\">{_ESC(ctx.t('pb_stalled_kicker', count=len(adv['stalled'])))}</div>"
                        f"{_brief_rows(ctx, adv['stalled'][:5])}"
                        + (f"<details class=\"hm-fold\"><summary>{_ESC(ctx.t('hm_ask_more', count=len(adv['stalled']) - 5))}</summary>"
                           f"{_brief_rows(ctx, adv['stalled'][5:])}</details>" if len(adv["stalled"]) > 5 else "") + "</section>")
        left.append(f"<section class=\"hm-sec\" data-section=\"milestones\" data-count=\"{len(adv['milestones'])}\">"
                    f"<div class=\"hm-kicker\">{_ESC(ctx.t('pb_ms_kicker'))}</div>"
                    + ("".join(_brief_milestone(ctx, m) for m in adv["milestones"]) if adv["milestones"]
                       else f"<p class=\"hm-quiet\">{_ESC(ctx.t('pb_ms_none'))}</p>") + "</section>")
        thumbs = [t for d in adv["deliverables"] for t in d["thumbs"]][: int(home["thumbs_limit"])]
        dl = [f"<section class=\"hm-sec\" data-section=\"deliverables\" data-count=\"{adv['recent_closed']}\">"
              f"<div class=\"hm-kicker\">{_ESC(ctx.t('pb_dl_kicker'))}</div>"]
        if thumbs:
            dl.append("<div class=\"hm-thumbs\">" + "".join(f"<a href=\"{_ESC(t['url'])}\"><img loading=\"lazy\" src=\"{_ESC(t['url'])}\""
                                                            f" alt=\"{_ESC(t['name'])}\"></a>" for t in thumbs) + "</div>")
        if adv["deliverables"]:
            dl.append("<ul class=\"hm-list\">" + "".join(
                f"<li data-task-id=\"{_ESC(d['task_id'])}\"><span class=\"hm-st\">{_ESC(d['closed_at'])}</span><span>{_authored(d['title'])}</span>"
                f"<span class=\"hm-id\">{_ESC(d['task_id'])}</span></li>" for d in adv["deliverables"]) + "</ul>")
            if adv["recent_closed"] > len(adv["deliverables"]):
                dl.append(f"<p class=\"hm-quiet\">{_ESC(ctx.t('pb_dl_more', days=home['recent_days'], count=adv['recent_closed']))}</p>")
        else:
            dl.append(f"<p class=\"hm-quiet\">{_ESC(ctx.t('pb_dl_none', days=home['recent_days']))}</p>")
        dl.append("</section>")
        asks = [f"<section class=\"hm-sec\" data-section=\"asks\" data-count=\"{len(adv['asks'])}\">"
                f"<div class=\"hm-kicker\">{_ESC(ctx.t('pb_asks_kicker', count=len(adv['asks'])))}</div>"]
        if adv.get("inbox_error"):
            asks.append(f"<p class=\"rp-err\">{_ESC(ctx.t('hm_inbox_error', error=adv['inbox_error']))}</p>")
        elif adv["asks"]:
            asks.append("<ul class=\"hm-list\">" + "".join(
                f"<li><span class=\"hm-st\">{_ESC(ctx.t('pb_state_check'))}</span><span>{_authored(a['title'])}</span>"
                f"<a class=\"hm-id\" href=\"{_ESC(ctx.href(a['url']))}\">{_ESC(ctx.t('pb_asks_go'))}</a></li>" for a in adv["asks"]) + "</ul>")
        else:
            asks.append(f"<p class=\"hm-quiet\">{_ESC(ctx.t('pb_asks_none'))}</p>")
        asks.append("</section>")
        body.append(f"<div class=\"hm-two\"><div>{''.join(left)}</div><div>{''.join(dl)}{''.join(asks)}</div></div>")
    links = sel["links"]
    eng = [("pb_eng_cards", links["cards"]), ("pb_eng_roadmap", links["roadmap"]), ("pb_eng_envelopes", links["envelopes"]),
           ("pb_eng_onboarding", links["onboarding"]), ("pb_eng_workstation", _workstation_page(sel["project"]))]
    items = "".join(f"<li><a href=\"{_ESC(ctx.href(url))}\">{_ESC(ctx.t(key))}</a></li>" for key, url in eng)
    body.append(f"<details class=\"hm-eng\"><summary>{_ESC(ctx.t('pb_eng'))}</summary><ul>{items}</ul>"
                f"<p>{_ESC(ctx.t('pb_eng_cleanup'))}</p>{_command_block(ctx, sel['cleanup_commands'])}</details>")
    return _page(ctx, "project", title, body, note_key=None)


def render_engineering(decl: dict[str, Any], lang: str) -> str:
    """AIPOS-F246 item 5: the engineering page collects every existing page (declared home.engineering_links; none removed)."""
    from tools.aipos_cli.board_pages import page_url
    from tools.aipos_cli.project_upgrade import board_declaration as upgrade_board_declaration

    ctx = _Ctx(decl, lang)
    urls = {"inbox": str(decl["nav_links"]["inbox"]), "overview": str(decl["pages"]["overview"]), "roadmap": str(decl["pages"]["roadmap"]),
            "envelopes": _envelope_page(), "onboarding": str(decl["pages"]["onboarding"]), "workstation": _workstation_page(),
            "upgrade": str(upgrade_board_declaration()["page_path"]),
            "cards": page_url(decl, "project", view=decl["home"]["cards_view_value"])}
    items = "".join(f"<li data-link=\"{_ESC(key)}\"><a href=\"{_ESC(ctx.href(urls[key]))}\">{_ESC(ctx.t('eng_' + key))}</a>"
                    f"<span class=\"rp-sub\"> {_ESC(ctx.t('eng_' + key + '_desc'))}</span></li>" for key in decl["home"]["engineering_links"])
    body = [f"<p class=\"hm-quiet\">{_ESC(ctx.t('eng_intro'))}</p>", f"<ul class=\"hm-eng-links\">{items}</ul>"]
    return _page(ctx, "engineering", ctx.t("eng_title"), body, note_key=None)


# ---------------------------------------------------------------------------
# AIPOS-F181 item 2: project page (cards grouped by subproject; in flight + to do listed, closed folded)
# ---------------------------------------------------------------------------

def _project_tabs(ctx: _Ctx, data: dict[str, Any], current: str | None) -> str:
    tabs = [f"<a href=\"{_ESC(ctx.href(p['url']))}\" data-project=\"{_ESC(p['project'])}\"{_CURRENT if p['project'] == current else ''}>"
            f"{_ESC(p['project'])}{(' (' + _ESC(ctx.t('rp_paused_badge')) + ')') if p['paused'] else ''}"
            f"{(' (' + _ESC(ctx.t('rp_test_badge')) + ')') if p.get('hidden') else ''}</a>" for p in data["projects"]]
    return f"<nav class=\"rp-projects\"><span>{_ESC(ctx.t('rp_rm_projects'))}</span>{''.join(tabs)}</nav>"


def _subproject_nav(ctx: _Ctx, subs: list[dict[str, Any]], current: str | None, url_for: Any) -> str:
    """Subproject filter links (same query.subproject on the project page and the roadmap page); one subproject = no filter."""
    if len(subs) < 2:
        return ""
    links = [f"<a href=\"{_ESC(ctx.href(url_for(None)))}\" data-subproject=\"\"{_CURRENT if current is None else ''}>{_ESC(ctx.t('rp_pj_all'))}</a>"]
    links += [f"<a href=\"{_ESC(ctx.href(url_for(s['key'])))}\" data-subproject=\"{_ESC(s['key'])}\" title=\"{_ESC(s['key'])}\""
              f"{_CURRENT if current == s['key'] else ''}>{_ESC(s['label'])}</a>" for s in subs]
    return f"<nav class=\"rp-subnav\"><span>{_ESC(ctx.t('rp_pj_filter'))}</span>{''.join(links)}</nav>"


def _card_rows(ctx: _Ctx, rows: list[dict[str, Any]]) -> str:
    items = []
    for r in rows:
        title = f" <span class=\"rp-row-title\">{_ESC(r['title'])}</span>" if r.get("title") else ""
        items.append(f"<li class=\"rp-row\" data-task-id=\"{_ESC(r['task_id'])}\" data-state=\"{_ESC(r['queue_state'])}\">"
                     f"<span class=\"rp-chip rp-chip-{_ESC(r['queue_state'])}\">{_ESC(ctx.t('rp_qs_' + r['queue_state']))}</span>"
                     f" <span class=\"rp-card-id\">{_ESC(r['task_id'])}</span>{title}</li>")
    return f"<ul class=\"rp-rows\">{''.join(items)}</ul>"


def _subproject_section(ctx: _Ctx, sub: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    mine = [r for r in rows if r["subproject"] == sub["key"]]
    inflight = [r for r in mine if r["group"] == "inflight"]
    todo = [r for r in mine if r["group"] == "todo"]
    closed = [r for r in mine if r["group"] == "closed"]
    parts = [f"<section class=\"rp-subproject\" data-subproject=\"{_ESC(sub['key'])}\" data-inflight=\"{len(inflight)}\""
             f" data-todo=\"{len(todo)}\" data-closed=\"{len(closed)}\"><h2 title=\"{_ESC(sub['key'])}\">{_ESC(sub['label'])}"
             f" <span class=\"rp-sub\">{_ESC(ctx.t('rp_ov_one_advisor', advisor=sub['advisor'] or ctx.t('rp_sub_no_advisor')))}</span></h2>"]
    if inflight:
        parts.append(f"<h3>{_ESC(ctx.t('rp_pj_inflight', count=len(inflight)))}</h3>{_card_rows(ctx, inflight)}")
    if todo:
        parts.append(f"<h3>{_ESC(ctx.t('rp_pj_todo', count=len(todo)))}</h3>{_card_rows(ctx, todo)}")
    if not inflight and not todo:
        parts.append(f"<p class=\"rp-sub\">{_ESC(ctx.t('rp_pj_nothing'))}</p>")
    if closed:
        parts.append(f"<details class=\"rp-closed\" data-closed=\"{len(closed)}\"><summary>{_ESC(ctx.t('rp_pj_closed', count=len(closed)))}"
                     f"</summary>{_card_rows(ctx, closed)}</details>")
    parts.append("</section>")
    return "".join(parts)


def render_project(data: dict[str, Any], lang: str) -> str:
    from tools.aipos_cli.board_pages import project_url, roadmap_url

    ctx = _Ctx(data["decl"], lang)
    sel = data["selected"]
    page_base = project_url(ctx.decl, sel["project"], view=data.get("view")) if sel else project_url(ctx.decl, "", view=data.get("view"))
    body = [_toggle(ctx, data.get("hidden") or [], bool(data.get("show_all")), page_base),
            _project_tabs(ctx, data, sel["project"] if sel else None)]
    if data.get("unknown_project"):
        body.append(f"<p class=\"rp-err\">{_ESC(ctx.t('rp_rm_unknown_project', project=data['unknown_project']))}</p>")
    if sel is None:
        if not data["projects"]:
            body.append(f"<p>{_ESC(ctx.t('rp_rm_no_projects'))}</p>")
        return _page(ctx, "project", ctx.t("rp_nav_project"), body, nav_current="history" if data.get("view") else None)
    title = ctx.t("rp_pj_title", project=sel["project"])
    if sel.get("error"):
        body.append(f"<p class=\"rp-err\">{_ESC(ctx.t('rp_error', error=sel['error']))}</p>")
        return _page(ctx, "project", title, body, nav_current="history" if data.get("view") else None)
    stats = sel["stats"]
    body.append("<ul class=\"rp-summary\">" + "".join(_counts_li(ctx, {"value": stats})) + "</ul>")
    if sel.get("unknown_subproject"):
        body.append(f"<p class=\"rp-err\">{_ESC(ctx.t('rp_pj_unknown_sub', subproject=sel['unknown_subproject']))}</p>")
    body.append(_subproject_nav(ctx, stats["subprojects"], sel["subproject"],
                                lambda key: project_url(ctx.decl, sel["project"], key, view=data.get("view"))))
    shown = [s for s in stats["subprojects"] if sel["subproject"] in (None, s["key"])]
    body.extend(_subproject_section(ctx, s, stats["rows"]) for s in shown)
    if not shown:
        body.append(f"<p class=\"rp-sub\">{_ESC(ctx.t('rp_pj_nothing'))}</p>")
    rm_url = roadmap_url(ctx.decl, sel["project"]) + (f"&{urlencode({ctx.decl['query']['subproject']: sel['subproject']})}"
                                                     if sel["subproject"] else "")
    body.append(f"<p class=\"rp-card-foot\"><a class=\"rp-link\" href=\"{_ESC(ctx.href(rm_url))}\">{_ESC(ctx.t('rp_pj_roadmap'))}</a>"
                f"<a class=\"rp-link\" href=\"{_ESC(ctx.href(sel['envelope_url']))}\">{_ESC(ctx.t('rp_pj_envelopes'))}</a>"
                f"<a class=\"rp-link rp-workstation-link\" href=\"{_ESC(ctx.href(_workstation_page(sel['project'])))}\">"
                f"{_ESC(ctx.t('rp_pj_workstation'))}</a>"
                f"<a class=\"rp-link rp-legacy\" href=\"{_ESC(sel['legacy_url'])}\">{_ESC(ctx.t('rp_pj_legacy'))}</a></p>")
    return _page(ctx, "project", title, body, nav_current="history" if data.get("view") else None)


# ---------------------------------------------------------------------------
# 路线图与验收
# ---------------------------------------------------------------------------

def _signal_html(ctx: _Ctx, sig: dict[str, Any], targets: list[str]) -> str:
    tell = ctx.tell(targets, "rp_rm_signal_ask", {"label": sig["label"], "subject": sig["subject"]})
    return (f"<div class=\"rp-signal rp-sev-{_ESC(sig['severity'])}\" data-kind=\"{_ESC(sig['kind'])}\" data-severity=\"{_ESC(sig['severity'])}\""
            f" data-milestone=\"{_ESC(str(sig.get('milestone') or ''))}\" data-criterion=\"{_ESC(str(sig.get('criterion') or ''))}\">"
            f"<p class=\"rp-signal-head\"><span class=\"rp-sev\">{_ESC(ctx.t('rp_sev_' + str(sig['severity'])))}</span> "
            f"<strong>{_ESC(sig['label'])}</strong></p><p class=\"rp-reason\">{_ESC(sig['reason'])}</p>"
            f"<p class=\"rp-tell\">{_ESC(tell)}</p>{_command_text(ctx, sig['advice'])}</div>")


def _milestone_item(ctx: _Ctx, project: str, ms: dict[str, Any], *, selected: bool, overdue: bool, subproject: str | None = None) -> str:
    from tools.aipos_cli.board_pages import roadmap_url

    prog = ms["progress"]
    target = f"<span class=\"roadmap-target\">{_ESC(ctx.t('rp_rm_target', date=ms['target_date']))}</span>" if ms.get("target_date") else ""
    late = f"<span class=\"rp-overdue\">{_ESC(ctx.t('rp_rm_overdue'))}</span>" if overdue else ""
    classes = "roadmap-ms" + (" rp-selected" if selected else "") + (" rp-is-overdue" if overdue else "") + \
        (" roadmap-dropped" if ms["status"] == "dropped" else "")
    return (f"<li class=\"{classes}\" data-milestone=\"{_ESC(ms['id'])}\" data-status=\"{_ESC(ms['status'])}\" data-overdue=\"{str(overdue).lower()}\">"
            f"<a class=\"roadmap-ms-head\" href=\"{_ESC(ctx.href(roadmap_url(ctx.decl, project, ms['id']) + _sub_param(ctx, subproject)))}\">"
            f"<span class=\"roadmap-title\">{_ESC(ms['id'])} · {_ESC(ms['title'])}</span></a>"
            f"<div class=\"rp-ms-meta\"><span class=\"roadmap-status\">{_ESC(ctx.t('rp_rm_status_' + str(ms['status'])))}</span>{target}{late}</div>"
            f"{_bar(prog['met'], prog['total'], prog.get('ratio'))}</li>")


def _sub_param(ctx: _Ctx, subproject: str | None) -> str:
    return f"&{urlencode({ctx.decl['query']['subproject']: subproject})}" if subproject else ""


def _artifacts_html(ctx: _Ctx, ev: dict[str, Any], project: str) -> str:
    """AIPOS-F175: declared artifacts of this card for the criterion (images = thumbnail via the read-only artifact route, text = excerpt)."""
    from tools.aipos_cli.board_pages import artifact_url

    art = ev.get("artifacts") or {}
    if not art.get("entries") and not art.get("problems"):
        return ""
    rows = []
    for entry in art.get("entries") or []:
        head = _ESC(ctx.t("rp_rm_artifact", kind=entry["kind"], path_glob=entry["path_glob"]))
        if not entry["files"]:
            rows.append(f"<li class=\"rp-artifact rp-err\" data-kind=\"{_ESC(entry['kind'])}\" data-files=\"0\">{head} · "
                        f"{_ESC(ctx.t('rp_rm_artifact_none', dir=art.get('dir_rel') or '?'))}</li>")
            continue
        shown = []
        for item in entry["files"]:
            if item["media"] == "image":
                url = _ESC(artifact_url(ctx.decl, project, ev["task_id"], item["name"]))
                shown.append(f"<figure class=\"rp-artifact-image\"><a href=\"{url}\" title=\"{_ESC(ctx.t('rp_rm_artifact_open'))}\">"
                             f"<img src=\"{url}\" alt=\"{_ESC(item['rel'])}\" loading=\"lazy\"></a>"
                             f"<figcaption><code>{_ESC(item['rel'])}</code></figcaption></figure>")
            elif item["media"] == "text":
                shown.append(f"<div class=\"rp-artifact-text\"><code>{_ESC(item['rel'])}</code>"
                             f"<pre class=\"rp-artifact-excerpt\">{_ESC(item.get('excerpt') or '')}</pre></div>")
            else:
                shown.append(f"<code class=\"rp-artifact-file\">{_ESC(item['rel'])}</code>")
        rows.append(f"<li class=\"rp-artifact\" data-kind=\"{_ESC(entry['kind'])}\" data-files=\"{len(entry['files'])}\">{head}"
                    + "".join(shown) + "</li>")
    rows.extend(f"<li class=\"rp-artifact rp-err\">{_ESC(ctx.t('rp_error', error=problem))}</li>" for problem in art.get("problems") or [])
    return f"<ul class=\"rp-artifacts\" data-dir=\"{_ESC(str(art.get('dir_rel') or ''))}\">" + "".join(rows) + "</ul>"


def _evidence_html(ctx: _Ctx, ev: dict[str, Any], workspace_index: int, project: str = "") -> str:
    head = [f"<span class=\"rp-card-id\">{_ESC(ctx.t('rp_rm_card', task_id=ev['task_id'], queue_state=ev.get('queue_state') or '?'))}</span>"]
    if ev.get("unreadable"):
        head.append(f"<span class=\"rp-err\">{_ESC(ctx.t('rp_error', error=ev['unreadable']))}</span>")
    elif ev.get("verdict"):
        verdict = str(ev["verdict"])
        head.append(f"<span class=\"vb-verdict is-{_ESC(verdict.split('_')[0])}\" data-verdict=\"{_ESC(verdict)}\">"
                    f"{_ESC(ctx.t('rp_rm_verdict', verdict=verdict))}</span>")
        if ev.get("verdict_id"):
            link = "/api/markdown-source?" + urlencode({"workspace": workspace_index, "record_id": ev["verdict_id"]})
            head.append(f"<a class=\"rp-record\" href=\"{_ESC(link)}\" data-verdict-id=\"{_ESC(str(ev['verdict_id']))}\">"
                        f"{_ESC(ctx.t('rp_rm_record_link'))}</a>")
    else:
        head.append(f"<span class=\"vb-verdict\" data-verdict=\"\">{_ESC(ctx.t('rp_rm_no_verdict'))}</span>")
    if ev.get("supplementary_verdict"):
        head.append(f"<span class=\"rp-supp\">{_ESC(ctx.t('rp_rm_supp', verdict=ev['supplementary_verdict'], verdict_id=ev.get('supplementary_verdict_id') or '?'))}</span>")
    body = ""
    if ev.get("evidence"):
        limit = int(ctx.decl["evidence_excerpt_chars"])
        rows = []
        for entry in ev["evidence"]:
            if not isinstance(entry, dict):
                rows.append(f"<li><pre class=\"vb-ring-body\">{_ESC(str(entry)[:limit])}</pre></li>")
                continue
            log = entry.get("log_excerpt") or entry.get("log_path") or ""
            log = str(log)
            clipped = log[:limit] + (" …" if len(log) > limit else "")
            rows.append(f"<li class=\"rp-ev-entry\" data-exit-code=\"{_ESC(str(entry.get('exit_code')))}\"><code>{_ESC(str(entry.get('command') or ''))}</code>"
                        f" <span class=\"vb-ring-meta\" title=\"{_ESC(str(entry.get('worktree') or ''))}\">"
                        f"{_ESC(ctx.t('rp_rm_evidence_entry', exit_code=entry.get('exit_code'), worktree=entry.get('worktree') or '?'))}</span>"
                        f"<pre class=\"vb-ring-body\">{_ESC(clipped)}</pre></li>")
        body = (f"<div class=\"rp-evidence\" data-report=\"{_ESC(str(ev.get('report_ref') or ''))}\"><p>"
                f"{_ESC(ctx.t('rp_rm_evidence', ref=ev.get('report_ref') or '?'))}</p><ul>{''.join(rows)}</ul></div>")
    elif ev.get("verdict") and (ev.get("evidence_required") or ev.get("declaration_problem")):
        problem = ev.get("declaration_problem") or ev.get("evidence_problem") or "?"
        body = f"<p class=\"rp-evidence rp-err\">{_ESC(ctx.t('rp_rm_evidence_missing', problem=problem))}</p>"
    return (f"<div class=\"vb-ring rp-card-evidence\" data-task-id=\"{_ESC(ev['task_id'])}\"><div class=\"vb-ring-head\">"
            + " ".join(head) + f"</div>{body}{_artifacts_html(ctx, ev, project)}</div>")


def _verification_html(ctx: _Ctx, crit: dict[str, Any], check: dict[str, Any] | None) -> str:
    """AIPOS-F175: five-state verification line + basis + latest Owner spot check + fix cards; awaiting check = tell + verify command."""
    vstate = str(crit.get("state") or "unverified")
    parts = [f"<p class=\"rp-crit-vstate rp-vstate-{_ESC(vstate)}\" data-state=\"{_ESC(vstate)}\">"
             f"{_ESC(ctx.t('rp_rm_state', state=ctx.t('rp_rm_state_' + vstate)))}</p>"]
    if crit.get("basis"):
        parts.append(f"<p class=\"rp-crit-basis\">{_ESC(ctx.t('rp_rm_basis', basis=crit['basis']))}</p>")
    rec = crit.get("owner_verification")
    if rec:
        parts.append(f"<p class=\"rp-owner-check\" data-result=\"{_ESC(str(rec.get('result') or ''))}\" data-record=\"{_ESC(str(rec.get('path') or ''))}\">"
                     f"{_ESC(ctx.t('rp_rm_owner_record', label=rec.get('label') or '?', decided_at=rec.get('decided_at') or '?', relayed_by=rec.get('relayed_by') or '?', owner_words=rec.get('owner_words') or ''))}"
                     f" <span class=\"rp-sub\">{_ESC(ctx.t('rp_rm_history', count=len(crit.get('verifications') or [])))}</span></p>")
    if crit.get("fix_cards"):
        parts.append(f"<p class=\"rp-fix\" data-fix=\"{_ESC(','.join(f['task_id'] for f in crit['fix_cards']))}\">"
                     f"{_ESC(ctx.t('rp_rm_fix', fix=', '.join(f['text'] for f in crit['fix_cards'])))}</p>")
    if check:
        parts.append(f"<p class=\"rp-tell\">{_ESC(ctx.tell(check['tell'], 'rp_rm_verify_ask', {'criterion': crit['id']}))}</p>")
        parts.append(_command_block(ctx, [check["command"]]))
    return "".join(parts)


def _criterion_html(ctx: _Ctx, crit: dict[str, Any], workspace_index: int, project: str = "",
                    check: dict[str, Any] | None = None) -> str:
    state = ctx.t("rp_rm_crit_met") if crit["satisfied"] else ctx.t("rp_rm_crit_unmet", reason=crit.get("reason") or "?")
    cards = "".join(_evidence_html(ctx, ev, workspace_index, project) for ev in crit.get("evidence") or [])
    return (f"<article class=\"vb-station rp-criterion{' rp-met' if crit['satisfied'] else ' rp-unmet'}\" data-criterion=\"{_ESC(crit['id'])}\""
            f" data-satisfied=\"{str(bool(crit['satisfied'])).lower()}\" data-verify=\"{_ESC(crit['verify'])}\""
            f" data-state=\"{_ESC(str(crit.get('state') or ''))}\">"
            f"<div class=\"vb-station-head\"><span class=\"vb-station-id\">{_ESC(crit['id'])}</span>"
            f"<span class=\"vb-station-title\">{_ESC(crit['statement'])}</span></div>"
            f"<p class=\"rp-crit-verify\">{_ESC(ctx.t('rp_rm_verify', verify=ctx.t('rp_rm_verify_' + str(crit['verify']))))}</p>"
            f"<p class=\"rp-crit-state\">{_ESC(state)}</p>{_verification_html(ctx, crit, check)}"
            f"<div class=\"vb-evidence\">{cards}</div></article>")


def render_roadmap(data: dict[str, Any], lang: str) -> str:
    from tools.aipos_cli.board_pages import roadmap_url

    ctx = _Ctx(data["decl"], lang)
    sel = data["selected"]
    page_base = roadmap_url(ctx.decl, sel["project"]) if sel else str(ctx.decl["pages"]["roadmap"])
    body: list[str] = [_toggle(ctx, data.get("hidden") or [], bool(data.get("show_all")), page_base),
                       _project_tabs(ctx, data, sel["project"] if sel else None)]
    if data.get("unknown_project"):
        body.append(f"<p class=\"rp-err\">{_ESC(ctx.t('rp_rm_unknown_project', project=data['unknown_project']))}</p>")
    if sel is None:
        if not data["projects"]:
            body.append(f"<p>{_ESC(ctx.t('rp_rm_no_projects'))}</p>")
        return _page(ctx, "roadmap", ctx.t("rp_nav_roadmap"), body)
    title = ctx.t("rp_rm_title", project=sel["project"])
    if sel.get("error"):
        body.append(f"<p class=\"rp-err roadmap-error\">{_ESC(ctx.t('rp_error', error=sel['error']))}</p>")
        return _page(ctx, "roadmap", title, body)
    view = sel["view"]
    if not view.get("exists"):
        tell = ctx.tell(sel["advisors"], "rp_rm_howto_ask", {"project": sel["project"]})
        body.append(f"<section class=\"rp-howto\" data-roadmap=\"none\"><h2>{_ESC(ctx.t('rp_rm_howto_title'))}</h2>"
                    f"<p class=\"rp-tell\">{_ESC(tell)}</p>{_command_text(ctx, sel['no_roadmap_hint'])}</section>")
        return _page(ctx, "roadmap", title, body)
    sub_key = sel.get("subproject")
    sub_q = ctx.decl["query"]["subproject"]

    def with_sub(url: str, key: str | None) -> str:
        return url + (f"&{urlencode({sub_q: key})}" if key else "")

    body.append(_subproject_nav(ctx, sel.get("subprojects") or [], sub_key, lambda key: with_sub(roadmap_url(ctx.decl, sel["project"]), key)))
    if sel.get("unknown_subproject"):
        body.append(f"<p class=\"rp-err\">{_ESC(ctx.t('rp_pj_unknown_sub', subproject=sel['unknown_subproject']))}</p>")
    totals = view["totals"]
    body.append(f"<p class=\"rp-source\">{_ESC(ctx.t('rp_rm_source', path=view['rel'], today=view['today'], met=totals['met'], total=totals['total']))}"
                f" {_bar(totals['met'], totals['total'])}</p>")
    signals = sorted(zip(view["signals"], sel.get("signal_targets") or [[]] * len(view["signals"])),
                     key=lambda pair: 0 if pair[0]["severity"] == "inbox" else 1)
    if signals:
        body.append(f"<section class=\"rp-signals\" data-signals=\"{len(signals)}\"><h2>{_ESC(ctx.t('rp_rm_signals', count=len(signals)))}</h2>"
                    + "".join(_signal_html(ctx, s, targets) for s, targets in signals) + "</section>")
    else:
        body.append(f"<section class=\"rp-signals\" data-signals=\"0\"><p>{_ESC(ctx.t('rp_rm_no_signals'))}</p></section>")
    if sel.get("unknown_milestone"):
        body.append(f"<p class=\"rp-err\">{_ESC(ctx.t('rp_rm_unknown_milestone', milestone=sel['unknown_milestone']))}</p>")
    overdue = {s.get("milestone") for s in view["signals"] if s["kind"] == "overdue"}
    allowed = sel.get("milestone_filter")
    shown = [ms for ms in view["milestones"] if allowed is None or ms["id"] in allowed]
    finished = ("done", "dropped")

    def items_by_lane(items: list[dict[str, Any]]) -> str:
        lanes: dict[str, list[dict[str, Any]]] = {}
        for ms in items:
            lanes.setdefault(str(ms["lane"]), []).append(ms)
        return "".join(
            f"<h3 class=\"roadmap-lane\" data-lane=\"{_ESC(lane)}\">{_ESC(ctx.t('rp_rm_lane', lane=lane))}</h3><ol class=\"roadmap-list\">"
            + "".join(_milestone_item(ctx, sel["project"], ms, selected=ms["id"] == sel["milestone"], overdue=ms["id"] in overdue,
                                      subproject=sub_key) for ms in group) + "</ol>" for lane, group in lanes.items())

    open_items = [ms for ms in shown if ms["status"] not in finished]
    done_items = [ms for ms in shown if ms["status"] in finished]
    left = [f"<h2>{_ESC(ctx.t('rp_rm_milestones'))}</h2>", items_by_lane(open_items)]
    if not shown:
        left.append(f"<p class=\"rp-sub\">{_ESC(ctx.t('rp_rm_none_for_sub'))}</p>")
    if done_items:
        is_open = " open" if any(ms["id"] == sel["milestone"] for ms in done_items) else ""
        left.append(f"<details class=\"rp-ms-folded\" data-folded=\"{len(done_items)}\"{is_open}><summary>"
                    f"{_ESC(ctx.t('rp_rm_folded', count=len(done_items)))}</summary>{items_by_lane(done_items)}</details>")
    right: list[str] = []
    picked = next((m for m in shown if m["id"] == sel["milestone"]), None)
    if picked is not None:
        right.append(f"<h2>{_ESC(ctx.t('rp_rm_criteria_title', id=picked['id'], title=picked['title']))}</h2>")
        right.extend(f"<p class=\"rp-mismatch\">{_ESC(ctx.t('rp_rm_mismatch', text=m))}</p>" for m in picked.get("mismatch") or [])
        if picked["criteria"]:
            checks = sel.get("owner_checks") or {}
            right.extend(_criterion_html(ctx, c, int(sel["index"]), sel["project"], checks.get(str(c["id"]))) for c in picked["criteria"])
        else:
            right.append(f"<p>{_ESC(ctx.t('rp_rm_no_criteria'))}</p>")
    body.append("<div class=\"rp-layout\"><aside class=\"rp-left\">" + "".join(left) + "</aside>"
                f"<section class=\"rp-right\" data-milestone=\"{_ESC(str(sel['milestone'] or ''))}\">" + "".join(right) + "</section></div>")
    extra = [f"<h2>{_ESC(ctx.t('rp_rm_next'))}</h2><ul>" + "".join(f"<li>{_step_html(ctx, step)}</li>" for step in view["next_steps"]) + "</ul>"]
    if view["unlinked"]:
        extra.append(f"<p class=\"roadmap-unlinked\" data-unlinked=\"{len(view['unlinked'])}\">"
                     f"{_ESC(ctx.t('rp_rm_unlinked', count=len(view['unlinked']), cards=', '.join(view['unlinked'])))}</p>")
    if view.get("unlinked_before_roadmap"):
        extra.append(f"<p class=\"roadmap-unlinked-before rp-sub\" data-before=\"{view['unlinked_before_roadmap']}\">"
                     f"{_ESC(ctx.t('rp_rm_unlinked_before', count=view['unlinked_before_roadmap']))}</p>")
    extra.extend(f"<p class=\"rp-err\">{_ESC(ctx.t('rp_rm_invalid', task_id=bad['task_id'], problems='; '.join(bad['problems'])))}</p>"
                 for bad in view["invalid"])
    if view["errors"]:
        extra.append(f"<h3>{_ESC(ctx.t('rp_rm_errors', count=len(view['errors'])))}</h3><ul>"
                     + "".join(f"<li class=\"rp-err\">{_ESC(e['source'])}: {_ESC(e['error'])}</li>" for e in view["errors"]) + "</ul>")
    body.append("<section class=\"rp-next\">" + "".join(extra) + "</section>")
    return _page(ctx, "roadmap", title, body)


# ---------------------------------------------------------------------------
# 项目接入
# ---------------------------------------------------------------------------

#: detail lines that only carry server paths: folded into the technical details (AIPOS-F181)
_TECH_DETAIL_KEYS = ("rp_ob_detail_governance_root",)


def _checklist_html(ctx: _Ctx, proj: dict[str, Any]) -> str:
    items = []
    for row in proj["rows"]:
        details = "".join(f"<li>{_ESC(ctx.t(key, **values))}</li>" for key, values in row["details"] if key not in _TECH_DETAIL_KEYS)
        tech = "".join(f"<p>{_ESC(ctx.t(key, **values))}</p>" for key, values in row["details"] if key in _TECH_DETAIL_KEYS)
        details += f"<li>{_tech(ctx, tech)}</li>" if tech else ""
        tell = f"<p class=\"rp-tell\">{_ESC(ctx.tell(row['tell'], row['ask'][0], row['ask'][1]))}</p>" if row.get("ask") else ""
        links = "".join(f"<a class=\"rp-link\" href=\"{_ESC(ctx.href(url))}\">{_ESC(ctx.t(key))}</a>" for key, url in row["links"])
        items.append(f"<li class=\"rp-ob-item rp-state-{_ESC(row['state'])}\" data-item=\"{_ESC(row['key'])}\" data-state=\"{_ESC(row['state'])}\">"
                     f"<p><span class=\"rp-state\">{_ESC(ctx.t('rp_ob_state_' + row['state']))}</span> "
                     f"<strong>{_ESC(ctx.t('rp_ob_item_' + row['key']))}</strong></p><ul class=\"rp-details\">{details}</ul>"
                     f"{tell}{_command_block(ctx, row['commands'])}{links}</li>")
    summary = proj["summary"]
    badge = _status_badge(ctx, proj)
    return (f"<section class=\"rp-ob-project\" id=\"{_ESC(proj['project'])}\" data-project=\"{_ESC(proj['project'])}\""
            f" data-done=\"{summary['done']}\" data-total=\"{summary['total']}\"><h2>{_ESC(proj['project'])}{badge} "
            f"<span class=\"rp-sub\">{_ESC(ctx.t('rp_ob_summary', done=summary['done'], total=summary['total']))}</span></h2>"
            f"<ul class=\"rp-upgrade-line\">{_upgrade_li(ctx, proj.get('upgrade'))}</ul>"
            f"<ol class=\"rp-checklist\">{''.join(items)}</ol></section>")


def render_onboarding(data: dict[str, Any], lang: str) -> str:
    ctx = _Ctx(data["decl"], lang)
    active = [p for p in data["projects"] if not p["paused"]]
    paused = [p for p in data["projects"] if p["paused"]]
    body = [_toggle(ctx, data.get("hidden") or [], bool(data.get("show_all")), str(ctx.decl["pages"]["onboarding"]))]
    body += [_checklist_html(ctx, p) for p in active]
    if paused:
        body.append(f"<details class=\"rp-paused\" data-paused=\"{len(paused)}\"><summary>{_ESC(ctx.t('rp_paused_folded', count=len(paused)))}"
                    "</summary>" + "".join(_checklist_html(ctx, p) for p in paused) + "</details>")
    return _page(ctx, "onboarding", ctx.t("rp_ob_title", count=len(data["projects"])), body)


# ---------------------------------------------------------------------------
# AIPOS-F180: Owner inbox / envelope pages on the F174 skeleton + "authorization requests" cards.
# Data = owner_inbox grant suggestion (one derivation, CLI line reads the same); signing reuses the F172 write path
# (mode=new, parameters = the suggestion); all page text goes through the board i18n channel (gr_* keys).
# ---------------------------------------------------------------------------

#: i18n templates the page script needs (filled client-side; texts never live in the script itself)
_GR_JS_KEYS = ("gr_confirm_heading", "gr_confirm_who", "gr_confirm_what", "gr_confirm_repos", "gr_confirm_count", "gr_confirm_until",
               "gr_confirm_card", "gr_confirm_project", "gr_confirm_btn", "gr_cancel_btn", "gr_working", "gr_tech", "gr_ok_heading",
               "gr_fail_heading", "gr_fail_next", "gr_retry_btn", "gr_repos_any", "gr_title_part", "gr_err_other", "gr_next_other",
               "gr_edit_invalid")


def _task_modes() -> list[str]:
    from tools.schema_loader import get_enum_values

    return list(get_enum_values("task_mode"))


def _mode_label(ctx: _Ctx, mode: str) -> str:
    return ctx.t("gr_mode_" + mode) if mode in _task_modes() else ctx.t("gr_mode_any")


def _script_texts(ctx: _Ctx, after_sign: dict[str, str]) -> str:
    """JSON handed to static/envelopes.js (templates, task-mode labels, per-error plain reason + next step). AIPOS-F217: after_sign =
    where the page goes once a signing is confirmed ({request, renew, new} -> server-rendered URL with a {policy_id} placeholder),
    refreshing = the line shown while it goes there."""
    import json

    from tools.aipos_cli.envelope_board import board_envelopes_declaration, tab_texts
    from tools.aipos_cli.owner_inbox import grant_declaration

    codes = list(board_envelopes_declaration()["errors"])
    data = {"t": {key: ctx.raw(key) for key in _GR_JS_KEYS},
            "modes": {mode: ctx.t("gr_mode_" + mode) for mode in _task_modes()}, "mode_any": ctx.t("gr_mode_any"),
            "errors": {code: [ctx.raw("gr_err_" + code), ctx.raw("gr_next_" + code)] for code in codes},
            "after_sign": after_sign, "refreshing": tab_texts(ctx.lang)["refreshing"],
            "date_only_time": str(grant_declaration()["date_only_time"])}
    return json.dumps(data, ensure_ascii=False)


def _owner_attrs(ctx: _Ctx, owner: bool, after_sign: dict[str, str] | None = None) -> str:
    from tools.aipos_cli.envelope_board import board_envelopes_declaration

    attrs = f" data-owner=\"{str(bool(owner)).lower()}\" data-write-path=\"{_ESC(str(board_envelopes_declaration()['write_path']))}\""
    return attrs + (f" data-gr-texts=\"{_ESC(_script_texts(ctx, after_sign or {}))}\"" if owner else "")


def _owner_script() -> str:
    from tools.aipos_cli.envelope_board import board_envelopes_declaration

    return str(board_envelopes_declaration()["script_path"])


def _panel(ctx: _Ctx) -> str:
    """The single sign panel (confirm / success / failure), filled by the page script; technical details folded."""
    return ("<section class=\"gr-panel\" hidden aria-live=\"polite\"><h2></h2><div class=\"gr-panel-body\"></div>"
            f"<details class=\"gr-tech gr-panel-tech\" hidden><summary>{_ESC(ctx.t('gr_tech'))}</summary><pre></pre></details>"
            "<div class=\"gr-panel-actions\"></div></section>")


def grant_sentence(ctx: _Ctx, grant: dict[str, Any]) -> str:
    """One plain-language sentence for an authorization request (board side; CLI line = owner_inbox.grant_line, same data)."""
    if grant.get("kind") == "authorization":  # AIPOS-F224: extend the project grant instead of a new envelope
        return ctx.t("gr_auth_sentence", advisor=grant["agent_or_role"], task_id=grant["task_id"],
                     title_part=ctx.t("gr_title_part", title=grant["title"]) if grant.get("title") else "",
                     dimension=grant.get("dimension_label") or grant.get("dimension"), authorization_id=grant["authorization_id"],
                     change=grant["change"], revision=grant["revision"])
    repos = ", ".join(grant["lane_repos"])
    return ctx.t("gr_sentence", advisor=grant["agent_or_role"], task_id=grant["task_id"],
                 title_part=ctx.t("gr_title_part", title=grant["title"]) if grant.get("title") else "",
                 mode=_mode_label(ctx, grant["task_mode"]), repos=ctx.t("gr_repos_part", repos=repos) if repos else ctx.t("gr_repos_any"),
                 launch=ctx.t("gr_launch_part", harnesses=", ".join(grant["launch_harnesses"])) if grant.get("launch_harnesses") else "",
                 count=grant["max_tasks"], date=str(grant["expires_at"])[:10], reason=grant["reason"])


def request_payload(item: dict[str, Any]) -> dict[str, Any]:
    """What "sign as suggested" posts to the F172 write path (single construction owner_inbox.grant_request_payload, AIPOS-F248)."""
    from tools.aipos_cli.owner_inbox import grant_request_payload

    return grant_request_payload(item)


def _grant_card(ctx: _Ctx, item: dict[str, Any], owner: bool) -> str:
    import json

    g = item["grant"]
    # AIPOS-F217: after signing the page reloads from the server; the paragraph to copy to the driving advisor is the server's
    # "decided, to relay" section (owner_inbox.relay_lines), no longer carried here
    data = {"payload": request_payload(item), "task_id": g["task_id"], "title": g.get("title") or "", "project": item["project"],
            "mode_label": _mode_label(ctx, g["task_mode"])}
    tech_lines = [f"item={item['item_id']} project={item['project']} policy_id={g['policy_id']}"]
    tech_lines += [f"{key}={g.get(key)!r} ({g['sources'].get(key, '-')})" for key in
                   ("agent_or_role", "task_mode", "lane_repos", "launch_harnesses", "max_tasks", "expires_at", "reason")]
    if g.get("previous_policy_id"):
        tech_lines.append(f"previous_envelope={g['previous_policy_id']}")
    if g.get("quota_basis"):
        tech_lines.append(f"quota_basis={g['quota_basis']}")  # AIPOS-F192: suggested count = business cards (envelope_card_need)
    parts = [f"<article class=\"gr-card\" id=\"{_ESC(item['item_id'])}\" data-request-item=\"{_ESC(item['item_id'])}\""
             f" data-request-project=\"{_ESC(item['project'])}\" data-request=\"{_ESC(json.dumps(data, ensure_ascii=False))}\">",
             f"<p class=\"gr-sentence\">{_ESC(grant_sentence(ctx, g))}</p>",
             f"<p class=\"rp-sub gr-meta\">{_ESC(ctx.t('gr_meta', project=item['project'], policy_id=g['policy_id'], since=item.get('since') or '?'))}</p>"]
    if owner:
        repos = ", ".join(g["lane_repos"])
        parts.append("<div class=\"gr-actions\">"
                     f"<button type=\"button\" class=\"gr-primary\" data-gr-action=\"sign\">{_ESC(ctx.t('gr_btn_sign'))}</button>"
                     f"<button type=\"button\" data-gr-action=\"edit\">{_ESC(ctx.t('gr_btn_edit'))}</button>"
                     f"<button type=\"button\" data-gr-action=\"deny\">{_ESC(ctx.t('gr_btn_deny'))}</button></div>")
        parts.append("<form class=\"gr-edit\" hidden>"
                     f"<label>{_ESC(ctx.t('gr_edit_max'))} <input name=\"max_tasks\" type=\"number\" min=\"1\" value=\"{_ESC(str(g['max_tasks']))}\"></label>"
                     f"<label>{_ESC(ctx.t('gr_edit_until'))} <input name=\"expires_date\" type=\"date\" value=\"{_ESC(str(g['expires_at'])[:10])}\"></label>"
                     f"<label>{_ESC(ctx.t('gr_edit_repos'))} <input name=\"lane_repo\" type=\"text\" value=\"{_ESC(repos)}\"></label>"
                     f"<button type=\"submit\">{_ESC(ctx.t('gr_edit_submit'))}</button></form>")
        tell = ctx.t("gr_deny_tell", advisor=g["agent_or_role"], task_id=g["task_id"])
        # AIPOS-F226: "decline" is recorded right here (write exception 3, choice deny_grant); the message + command stay as the fallback
        deny = _decide_block(ctx, item, ["deny_grant"], label_key="ib_deny_decide")
        parts.append(f"<div class=\"gr-deny\" hidden>{deny}<details class=\"ib-fallback\"><summary>{_ESC(ctx.t('ib_fallback'))}</summary>"
                     f"<p class=\"rp-tell\">{_ESC(tell)}</p>{_command_block(ctx, [item['decide_command']])}</details></div>")
    parts.append(f"<details class=\"gr-tech\"><summary>{_ESC(ctx.t('gr_tech'))}</summary><pre>{_ESC(chr(10).join(tech_lines))}</pre></details>")
    parts.append("</article>")
    return "".join(parts)


def _grant_section(ctx: _Ctx, requests: list[dict[str, Any]], owner: bool, errors: int = 0) -> list[str]:
    """"Authorization requests" block at the top of the inbox / envelope pages (nothing pending = nothing rendered)."""
    if not requests and not errors:
        return []
    out = [f"<section class=\"gr-requests\" data-requests=\"{len(requests)}\"><h2>{_ESC(ctx.t('gr_section_title', count=len(requests)))}</h2>"]
    if not owner and requests:
        out.append(f"<p class=\"rp-readonly-note\">{_ESC(ctx.t('gr_readonly'))}</p>")
    out.extend(_grant_card(ctx, item, owner) for item in requests)
    if errors:
        out.append(f"<p class=\"rp-err\"><a href=\"{_ESC(ctx.href(str(ctx.decl['nav_links']['inbox'])))}\">{_ESC(ctx.t('gr_errors_note', count=errors))}</a></p>")
    out.append("</section>")
    return out


def _words(text: Any) -> str:
    """The plain-language part of a derived sentence: everything before its first backtick command."""
    return str(text or "").partition("`")[0].strip().rstrip(":;,\uff1a\uff1b\uff0c")


def _decidable(item: dict[str, Any]) -> bool:
    """AIPOS-F226: the item can be ruled on the board (write exception 3): not an owner_check / decide_redirect category (declaration
    board.decide.excluded_categories); authorization requests are signed on their request card (decline = deny_grant button there)."""
    from tools.aipos_cli.owner_inbox import board_decide_declaration, inbox_declaration

    decl = inbox_declaration()
    spec = board_decide_declaration(decl)
    return (item["category"] not in spec["excluded_categories"]  # AIPOS-F248: spot checks are decidable here too (verify branch)
            and (item["category"] in spec["verify"]["categories"] or not decl["categories"][item["category"]].get("decide_redirect")))


def _decide_block(ctx: _Ctx, item: dict[str, Any], keys: list[str] | None = None, *, label_key: str | None = None) -> str:
    """AIPOS-F226: one button per option (or only `keys`) + an optional one-line Owner sentence; static/inbox-decide.js posts the declared
    write path (preview -> confirm). Data attributes only; every check is on the server."""
    from tools.aipos_cli.owner_inbox import board_decide_declaration

    spec = board_decide_declaration()
    options = [o for o in item.get("options") or [] if keys is None or o["key"] in keys]
    if not options or not _decidable(item):
        return ""
    what = ctx.t("ib_card_title", task_id=item["task_id"], title=item["title"]) if item.get("task_id") and item.get("title") else (
        ctx.t("ib_card", task_id=item["task_id"]) if item.get("task_id") else str(item.get("subject") or ""))
    # AIPOS-F248: the recommended option (item.recommended, same data as the home page) comes first as the tagged primary button;
    # no recommendation = options side by side (no primary)
    rec = item.get("recommended") if isinstance(item.get("recommended"), dict) and not label_key else None
    rec_key = str((rec or {}).get("choice") or "")
    if rec_key and any(o["key"] == rec_key for o in options):
        options = sorted(options, key=lambda o: o["key"] != rec_key)
    else:
        rec = None
    rec_attr = ' data-recommended="true"'
    buttons = "".join(
        f"<button type=\"button\" class=\"ib-decide-btn{' gr-primary' if (rec and o['key'] == rec_key) or (label_key and n == 0) else ''}\""
        f" data-ib-choice=\"{_ESC(o['key'])}\" data-ib-label=\"{_ESC(o['label'])}\""
        f"{rec_attr if rec and o['key'] == rec_key else ''}>"
        + (f"<small>{_ESC(ctx.t('hm_rec_tag'))}</small> " if rec and o["key"] == rec_key else "")
        + f"{_ESC(ctx.t(label_key) if label_key else o['label'])}</button>"
        for n, o in enumerate(options))
    if rec and rec.get("reason"):
        head = (ctx.t("hm_rec_advisor", advisor=rec.get("by") or ctx.t("hm_advisor_generic"), label=rec.get("label") or "")
                if rec.get("source") == "advisor" else ctx.t("hm_rec_default", label=rec.get("label") or ""))
        buttons = f"<p class=\"rp-sub ib-rec\">{_ESC(head)}{_authored(rec['reason'])}</p>" + buttons
    return (f"<div class=\"ib-decide\" data-ib-item=\"{_ESC(item['item_id'])}\" data-ib-workspace=\"{_ESC(item['project'])}\""
            f" data-ib-what=\"{_ESC(item['project'] + ' / ' + what)}\" data-ib-advisor=\"{_ESC(item.get('relay_to') or ctx.t('ib_ruled_unknown'))}\">"
            + ("" if label_key else f"<p class=\"rp-sub ib-decide-head\">{_ESC(ctx.t('ib_decide_heading'))}</p>")
            + f"<label class=\"ib-words\">{_ESC(ctx.t('ib_words_label'))} <input type=\"text\" name=\"owner_words\""
            f" maxlength=\"{int(spec['owner_words_max_chars'])}\"></label><div class=\"ib-decide-actions\">{buttons}</div></div>")


def _inbox_item_html(ctx: _Ctx, item: dict[str, Any], lines: list[str], owner: bool = False) -> str:
    """AIPOS-F181 item 4: one inbox item in plain words (project / card / what you are asked); the engineering lines (item id,
    options, decide command with the home root) are folded into "show details". An item that already has a plain-language request
    card above only points to it."""
    if item.get("task_id"):
        what = ctx.t("ib_card_title", task_id=item["task_id"], title=item["title"]) if item.get("title") else ctx.t("ib_card", task_id=item["task_id"])
    else:
        what = str(item.get("subject") or "")
    parts = [f"<section class=\"owner-inbox-item ib-item ib-rank-{item['rank']}\" id=\"item-{_ESC(item['item_id'])}\""
             f" data-item-id=\"{_ESC(item['item_id'])}\" data-category=\"{_ESC(item['category'])}\" data-rank=\"{item['rank']}\""
             f" data-project=\"{_ESC(item['project'])}\" data-grant=\"{str(bool(item.get('grant'))).lower()}\">",
             f"<h2>{_ESC(ctx.t('ib_heading', project=item['project'], what=what))}</h2>",
             f"<p class=\"ib-kind\"><span class=\"rp-chip ib-chip\">{_ESC(str(item['rank_label']))}</span> {_ESC(str(item['category_label']))}</p>"]
    if item.get("grant"):
        parts.append(f"<p class=\"ib-grant\"><a href=\"#{_ESC(item['item_id'])}\">{_ESC(ctx.t('ib_grant'))}</a></p>")
    else:
        # words before the first command stay visible; the command (with server paths) is in the folded details below
        parts.append(f"<p class=\"ib-ask\">{_ESC(ctx.t('ib_ask', ask=_words(item['ask'])))}</p>")
        if item.get("recommend") and _words(item["recommend"]):
            parts.append(f"<p class=\"ib-recommend\">{_ESC(ctx.t('ib_recommend', recommend=_words(item['recommend'])))}</p>")
        if item.get("reaudit_hint"):  # AIPOS-F202: fix may be live after a "wait for the fix" ruling (same line as the CLI)
            parts.append(f"<p class=\"rp-tell ib-hint\">{_ESC(item['reaudit_hint'])}</p>")
        if item.get("board_url"):
            parts.append(f"<p class=\"owner-inbox-board\"><a href=\"{_ESC(item['board_url'])}\">{_ESC(ctx.t('ib_board'))}</a></p>")
        if owner:  # AIPOS-F226: Owner session decides here; the per-option messages below are folded as the fallback
            parts.append(_decide_block(ctx, item))
        parts.append(_choice_messages(ctx, item))
    parts.append(f"<details class=\"ib-details\"><summary>{_ESC(ctx.t('ib_details'))}</summary>"
                 f"<pre>{_ESC(chr(10).join(line.strip() for line in lines))}</pre></details></section>")
    return "".join(parts)


def _choice_messages(ctx: _Ctx, item: dict[str, Any]) -> str:
    """AIPOS-F201 items 1 / 3: one copyable message per option for the advisor to tell (owner_inbox.choice_script, declared texts in the
    page language). Read-only: the Owner picks an option by copying its message; the named advisor records it with product commands."""
    from tools.aipos_cli.owner_inbox import choice_script, choice_scripts_offered, relay_texts

    if not choice_scripts_offered(item) or not item.get("options"):
        return ""
    t = relay_texts(ctx.lang)
    advisor = item.get("relay_to") or t["unknown_advisor"]
    rows = []
    for option in item["options"]:
        text = "\n".join(choice_script(item, option["key"], ctx.lang))
        rows.append(f"<details class=\"rp-cmd ib-choice\" data-choice=\"{_ESC(option['key'])}\"><summary class=\"rp-cmd-label\">"
                    f"{_ESC(t['choice_summary'].format(label=option['label'], advisor=advisor))}</summary>"
                    f"<pre class=\"upgrade-lines ib-choice-text\">{_ESC(text)}</pre></details>")
    # AIPOS-F226: folded by default ("message to copy if the advisor falls behind"); deciding happens with the buttons above
    return (f"<details class=\"ib-fallback\"><summary>{_ESC(ctx.t('ib_fallback'))}</summary>"
            f"<div class=\"ib-choices\" data-relay-to=\"{_ESC(item.get('relay_to') or '')}\">"
            f"<p class=\"rp-sub ib-choices-head\">{_ESC(t['choice_heading'].format(advisor=advisor))}</p>{''.join(rows)}</div></details>")


def _relay_section(ctx: _Ctx, inbox: dict[str, Any]) -> list[str]:
    """AIPOS-F201 item 3: "decided, to relay" (signed / ruled, the advisor has not continued yet), one paragraph per advisor; the same
    lines as the CLI section (owner_inbox.relay_lines). Nothing to relay = nothing rendered."""
    from tools.aipos_cli.owner_inbox import relay_lines, relay_texts

    groups = inbox.get("relays") or []
    if not groups:
        return []
    t = relay_texts(ctx.lang)
    out = [f"<section class=\"ib-relays\" data-relays=\"{len(groups)}\"><h2>{_ESC(t['section_heading'].format(count=len(groups)))}</h2>"
           f"<p class=\"rp-sub\">{_ESC(t['section_note'])}</p>"]
    for group in groups:
        lines = relay_lines(group, ctx.lang)
        out.append(f"<article class=\"ib-relay\" data-advisor=\"{_ESC(group.get('advisor') or '')}\" data-entries=\"{len(group['entries'])}\">"
                   f"<p class=\"rp-tell\">{_ESC(lines[0])}</p><pre class=\"upgrade-lines ib-relay-text\">{_ESC(chr(10).join(lines))}</pre></article>")
    out.append("</section>")
    return out


def _auto_released_section(ctx: _Ctx, inbox: dict[str, Any], owner: bool) -> list[str]:
    """AIPOS-F228 (design note 2026-10-24 section 5): the "auto-released" column = tier-2 items that can be revoked
    (owner_inbox.revocable_notices over the same collect_inbox data as the CLI). Owner sessions get one button per option (revoke /
    acknowledge) through the same F226 write path; other sessions see the list read-only. Nothing auto-released = nothing rendered."""
    from tools.aipos_cli.owner_inbox import inbox_declaration, item_lines, revocable_notices

    notices = revocable_notices(inbox)
    if not notices:
        return []
    texts = inbox_declaration()["texts"]
    out = [f"<section class=\"ib-auto-released\" data-auto-released=\"{len(notices)}\">"
           f"<h2>{_ESC(ctx.t('ib_auto_heading', count=len(notices)))}</h2><p class=\"rp-sub\">{_ESC(ctx.t('ib_auto_note'))}</p>"]
    if not owner and any(_decidable(item) for item in notices):
        out.append(f"<p class=\"rp-readonly-note\">{_ESC(ctx.t('ib_decide_readonly'))}</p>")
    out.extend(_inbox_item_html(ctx, item, item_lines(item, texts), owner) for item in notices)
    out.append("</section>")
    return ["".join(out)]


def _ruled_banner_html(ctx: _Ctx, banner: dict[str, Any] | None) -> list[str]:
    """AIPOS-F226: after a board ruling the page reloads with the declared ruled_param; the banner states only what the truth shows
    (owner_inbox.ruled_banner): waiting in "decided, waiting for the advisor" = names the advisor whose next loop picks it up."""
    if not banner:
        return []
    if banner["pending"]:
        text = ctx.t("ib_ruled_banner", label=banner.get("label") or "?", advisor=banner.get("advisor") or ctx.t("ib_ruled_unknown"))
    else:
        text = ctx.t("ib_ruled_done", label=banner.get("label") or "?", decision_id=banner.get("decision_id") or "?")
    return [f"<p class=\"env-banner ib-banner\" data-ruled=\"{_ESC(banner['item_id'])}\" data-pending=\"{str(bool(banner['pending'])).lower()}\">"
            f"{_ESC(text)}</p>"]


def _decide_panel(ctx: _Ctx) -> str:
    """AIPOS-F226: the single ruling panel (confirm / working / failure), filled by static/inbox-decide.js; technical details folded."""
    return ("<section class=\"gr-panel ib-panel\" hidden aria-live=\"polite\"><h2></h2><div class=\"gr-panel-body\"></div>"
            f"<details class=\"gr-tech gr-panel-tech\" hidden><summary>{_ESC(ctx.t('ib_dc_tech'))}</summary><pre></pre></details>"
            "<div class=\"gr-panel-actions\"></div></section>")


_IB_JS_KEYS = ("ib_dc_confirm_heading", "ib_dc_item", "ib_dc_choice", "ib_dc_words", "ib_dc_after", "ib_dc_confirm_btn", "ib_dc_cancel_btn",
               "ib_dc_working", "ib_dc_ok_heading", "ib_dc_fail_heading", "ib_dc_fail_next", "ib_dc_login", "ib_dc_login_next",
               "ib_dc_retry_btn")


def _decide_script() -> str:
    from tools.aipos_cli.owner_inbox import board_decide_declaration

    return str(board_decide_declaration()["script_path"])


def _decide_attrs(ctx: _Ctx) -> str:
    """AIPOS-F226: body attributes for static/inbox-decide.js (write path from the declaration, unformatted texts in the page language)."""
    import json

    from tools.aipos_cli.owner_inbox import board_decide_declaration

    texts = json.dumps({key: ctx.raw(key) for key in _IB_JS_KEYS}, ensure_ascii=False)
    return (f" data-ib-owner=\"true\" data-ib-write-path=\"{_ESC(str(board_decide_declaration()['write_path']))}\""
            f" data-ib-texts=\"{_ESC(texts)}\"")


def _inbox_body(ctx: _Ctx, inbox: dict[str, Any], show_all: bool, owner: bool = False) -> list[str]:
    from tools.aipos_cli.owner_inbox import inbox_declaration, item_lines

    texts = inbox_declaration()["texts"]
    base = str(ctx.decl["nav_links"]["inbox"])
    out = []
    hidden = list(inbox.get("paused_hidden") or [])
    if hidden:
        out.append(f"<p class=\"owner-inbox-paused rp-sub\">{_ESC(ctx.t('ib_hidden', projects=', '.join(hidden)))}</p>")
    out.append(_toggle(ctx, hidden, show_all, base, show_key="ib_show", hide_key="ib_hide"))
    if not owner and any(_decidable(item) for item in inbox["items"]):
        out.append(f"<p class=\"rp-readonly-note\">{_ESC(ctx.t('ib_decide_readonly'))}</p>")
    out.extend(_inbox_item_html(ctx, item, item_lines(item, texts), owner) for item in inbox["items"])
    if inbox["errors"]:
        rows = "".join(f"<li>{_ESC(ctx.t('ib_error', project=e['project'], source=e['source'], error=e['error']))}</li>" for e in inbox["errors"])
        out.append(f"<section class=\"owner-inbox-errors\"><h2>{_ESC(ctx.t('ib_errors', count=len(inbox['errors'])))}</h2>"
                   f"{_tech(ctx, '<ul>' + rows + '</ul>')}</section>")
    return out


def render_owner_inbox(home_root: str | Path | None, params: dict[str, list[str]], *, owner: bool) -> str:
    """GET /owner/inbox: inbox data (owner_inbox.collect_inbox via board_adapter.get_owner_inbox, same data as the API / CLI) rendered
    in plain words on the F174 skeleton, with the authorization requests on top; Owner sessions get the sign client only when there is
    something to sign. AIPOS-F181: the paused / test projects switch replaces the command-line hint.
    AIPOS-F245 item 2: the whole render is one request-level read scope (the inbox declaration is read and checked once per request)."""
    from tools.aipos_cli.task_loader import read_request_scope

    with read_request_scope():
        return _render_owner_inbox(home_root, params, owner=owner)


def _render_owner_inbox(home_root: str | Path | None, params: dict[str, list[str]], *, owner: bool) -> str:
    import json

    from tools.aipos_cli import board_pages
    from tools.aipos_cli.board_adapter import get_owner_inbox
    from tools.aipos_cli.owner_inbox import (board_decide_declaration, grant_requests, render_inbox_error_html, revocable_notices,
                                             ruled_banner)
    from tools.schema_loader import SchemaLoadError

    lang = "zh"
    try:
        decl = board_pages.pages_declaration()
        lang = resolve_lang(decl, params)
        ctx = _Ctx(decl, lang)
        show_all = board_pages.show_all_requested(decl, params)
        response = get_owner_inbox(home_root, include_paused=show_all)
        if not response.get("ok"):
            return render_inbox_error_html(json.dumps(response.get("errors") or response.get("message") or response, ensure_ascii=False)[:800])
        inbox = response["data"]
        requests = grant_requests(inbox)
        signing = owner and bool(requests)
        # AIPOS-F226: Owner session + something decidable = the ruling panel and its script (write exception 3)
        # AIPOS-F228: the "auto-released" column's revoke buttons use the same ruling panel / script / write path
        deciding = owner and any(_decidable(item) for item in [*inbox["items"], *revocable_notices(inbox)])
        spec = board_decide_declaration()
        banner = ruled_banner(inbox, (params.get(str(spec["ruled_param"])) or [""])[0], home_root)
        body = (_ruled_banner_html(ctx, banner) + ([_panel(ctx)] if signing else []) + ([_decide_panel(ctx)] if deciding else [])
                + _relay_section(ctx, inbox) + _grant_section(ctx, requests, owner) + _inbox_body(ctx, inbox, show_all, owner)
                + _auto_released_section(ctx, inbox, owner))
        title = ctx.t("ib_title" if inbox["items"] else "ib_empty", count=inbox["count"], projects=len(inbox["projects"]))
        # AIPOS-F217: after signing, reload this page (same switch / language); its "decided, to relay" section carries the paragraph
        here = str(decl["nav_links"]["inbox"]) + (f"?{urlencode({decl['query']['show']: decl['show_all_value']})}" if show_all else "")
        after_sign = {kind: ctx.href(here) for kind in _after_sign_kinds()}
        scripts = ([_owner_script()] if signing else []) + ([_decide_script()] if deciding else [])
        return _page(ctx, "inbox", title, body, note_key=None, h1_attrs=f" data-owner-inbox-count=\"{inbox['count']}\"",
                     body_attrs=_owner_attrs(ctx, signing, after_sign) + (_decide_attrs(ctx) if deciding else ""), scripts=scripts,
                     current_url=str(decl["nav_links"]["inbox"]))
    except (OSError, ValueError, LookupError, RuntimeError, SchemaLoadError) as exc:  # unreadable = error page naming the cause
        return render_error(lang, f"{type(exc).__name__}: {exc}"[:800])


def _after_sign_kinds() -> list[str]:
    from tools.aipos_cli.envelope_board import board_envelopes_declaration

    return list(board_envelopes_declaration()["tabs"]["after_sign"])


def _envelope_tab_url(ctx: _Ctx, tab: str, show_all: bool, *, lang: bool = True) -> str:
    """This page on one tab, keeping the test-project switch (and the language unless lang=False: _toggle adds it itself)."""
    from tools.aipos_cli.envelope_board import board_envelopes_declaration

    query = {str(board_envelopes_declaration()["tabs"]["param"]): tab}
    if show_all:
        query[ctx.decl["query"]["show"]] = ctx.decl["show_all_value"]
    url = f"{_envelope_page()}?{urlencode(query)}"
    return ctx.href(url) if lang else url


def _envelope_tabs_nav(ctx: _Ctx, tab: str, counts: dict[str, int], show_all: bool) -> str:
    """AIPOS-F217: the three tabs (server-rendered links with counts) + the refresh link (GET this tab again; no script needed)."""
    from tools.aipos_cli.envelope_board import board_envelopes_declaration, tab_texts

    tt = tab_texts(ctx.lang)
    links = "".join(
        f"<a class=\"env-tab\" data-tab=\"{_ESC(name)}\" data-count=\"{counts[name]}\" href=\"{_ESC(_envelope_tab_url(ctx, name, show_all))}\""
        f"{_CURRENT if name == tab else ''}>{_ESC(tt['tab_' + name].format(count=counts[name]))}</a>"
        for name in board_envelopes_declaration()["tabs"]["order"])
    refresh = f"<a class=\"env-refresh\" href=\"{_ESC(_envelope_tab_url(ctx, tab, show_all))}\">{_ESC(tt['refresh'])}</a>"
    return f"<nav class=\"env-tabs\" data-tab=\"{_ESC(tab)}\">{links}{refresh}</nav>"


def _signed_banner(ctx: _Ctx, signed: str, tab: str, show_all: bool) -> str:
    """AIPOS-F217: "signed <policy_id>" after the page script reloaded the page; links to the row when it lives on another tab."""
    from tools.aipos_cli.envelope_board import tab_texts

    tt = tab_texts(ctx.lang)
    view = (f" <a href=\"{_ESC(_envelope_tab_url(ctx, 'current', show_all) + '#' + signed)}\">{_ESC(tt['signed_view'])}</a>"
            if tab != "current" else "")
    return f"<p class=\"env-banner\" data-signed=\"{_ESC(signed)}\">{_ESC(tt['signed_banner'].format(policy_id=signed))}{view}</p>"


def render_envelopes(home_root: str | Path | None, params: dict[str, list[str]], *, owner: bool) -> str:
    """GET envelope page: F172 body (envelope_board.envelopes_page_parts) on the F174 skeleton; non-Owner sessions get no forms, no
    buttons and no script. AIPOS-F181: projects whose status is board_hidden (test) are left out unless the page switch is on.
    AIPOS-F217: three server-rendered tabs (query tabs.param; counts from envelope_board.envelope_tabs = envelope_placement): to sign =
    "decided, to relay" (owner_inbox.relay_lines) + authorization requests (owner_inbox loop category) + envelopes to renew; in use;
    history. A refresh link per tab; after a signing the page script lands here with tabs.signed_param (banner + highlighted row)."""
    from tools.aipos_cli import board_pages
    from tools.aipos_cli.envelope_board import (board_envelopes_declaration, collect_envelopes, envelope_tabs, envelopes_page_parts,
                                                resolve_tab, signed_policy, tab_texts)
    from tools.aipos_cli.owner_inbox import collect_inbox, grant_requests
    from tools.aipos_cli.workspace_config import project_status_visibility
    from tools.schema_loader import SchemaLoadError

    lang = "zh"
    try:
        decl = board_pages.pages_declaration()
        lang = resolve_lang(decl, params)
        ctx = _Ctx(decl, lang)
        show_all = board_pages.show_all_requested(decl, params)
        view = collect_envelopes(home_root)
        board_hidden = project_status_visibility()["board_hidden"]
        hidden = [p["project"] for p in view["projects"] if p.get("status") in board_hidden]
        if not show_all:
            view = {**view, "projects": [p for p in view["projects"] if p["project"] not in hidden]}
        split = envelope_tabs(view)
        inbox = collect_inbox(home_root, categories=("loop_owner_needed",), include_paused=show_all)
        requests = grant_requests(inbox)
        relays = inbox.get("relays") or []
        counts = {**split["counts"], "pending": len(requests) + split["counts"]["pending"]}
        tab = resolve_tab(params, pending_count=counts["pending"], relays=len(relays))
        signed = signed_policy(params)
        if signed and not any(r["policy_id"] == signed for p in view["projects"] for r in p.get("envelopes") or []):
            signed = None  # not an envelope on this page = no banner (never claim a signing the truth does not show)
        parts = envelopes_page_parts(view, owner=owner, tab=tab, lang=lang, signed=signed, split=split)
        body = ([_panel(ctx)] if owner else []) + [_toggle(ctx, [] if show_all else hidden, show_all,
                                                           _envelope_tab_url(ctx, tab, False, lang=False))]
        body.append(_envelope_tabs_nav(ctx, tab, counts, show_all))
        if signed:
            body.append(_signed_banner(ctx, signed, tab, show_all))
        deciding = owner and tab == "pending" and bool(requests)  # AIPOS-F226: "decline" on a request card = one-click deny_grant
        if deciding:
            body.append(_decide_panel(ctx))
        if tab == "pending":
            body += _relay_section(ctx, inbox) + _grant_section(ctx, requests, owner, len(inbox["errors"]))
            if not counts["pending"] and not relays and not inbox["errors"]:
                body.append(f"<p class=\"envelope-none envelope-tab-empty\">{_ESC(tab_texts(lang)['pending_none'])}</p>")
        body += parts["sections"]
        tabs = board_envelopes_declaration()["tabs"]
        after_sign = {kind: _envelope_tab_url(ctx, target, show_all) + f"&{tabs['signed_param']}={{policy_id}}#{{policy_id}}"
                      for kind, target in tabs["after_sign"].items()}
        return _page(ctx, "envelopes", parts["title"], body, note_key=None, h1_attrs=parts["h1_attrs"],
                     body_attrs=_owner_attrs(ctx, owner, after_sign) + (_decide_attrs(ctx) if deciding else ""),
                     scripts=([_owner_script()] if owner else []) + ([_decide_script()] if deciding else []), current_url=_envelope_page())
    except (OSError, ValueError, LookupError, RuntimeError, SchemaLoadError) as exc:  # unreadable = error page naming the cause
        return render_error(lang, f"{type(exc).__name__}: {exc}"[:800])


def render_upgrade(home_root: str | Path | None, params: dict[str, list[str]], *, owner: bool) -> str:
    """AIPOS-F181: project upgrade page (F176 write exception 2; body = project_upgrade.upgrade_page_parts) on the same skeleton."""
    from tools.aipos_cli import board_pages
    from tools.aipos_cli.project_upgrade import board_declaration, collect_upgrade_page, upgrade_page_parts
    from tools.aipos_cli.workspace_config import project_status_visibility
    from tools.schema_loader import SchemaLoadError

    lang = "zh"
    try:
        decl = board_pages.pages_declaration()
        lang = resolve_lang(decl, params)
        ctx = _Ctx(decl, lang)
        show_all = board_pages.show_all_requested(decl, params)
        project = (params.get(decl["query"]["project"]) or [None])[0]
        view = collect_upgrade_page(home_root, project=project)
        board_hidden = project_status_visibility()["board_hidden"]
        hidden = [] if project or show_all else [p["project"] for p in view["projects"] if p.get("status") in board_hidden]
        view = {**view, "projects": [p for p in view["projects"] if p["project"] not in hidden]}
        parts = upgrade_page_parts(view, owner=owner)
        page_path = str(board_declaration()["page_path"])
        body = [_toggle(ctx, hidden, show_all and not project, page_path)] + parts["sections"]
        return _page(ctx, "upgrade", parts["title"], body, note_key=None, h1_attrs=parts["h1_attrs"], body_attrs=parts["body_attrs"],
                     scripts=parts["scripts"], current_url=page_path)
    except (OSError, ValueError, LookupError, RuntimeError, SchemaLoadError) as exc:  # unreadable = error page naming the cause
        return render_error(lang, f"{type(exc).__name__}: {exc}"[:800])


# ---------------------------------------------------------------------------
# AIPOS-F198: workstation page (read-only; status / risks / routing / GET form for the target / plan steps / messages for the advisors)
# ---------------------------------------------------------------------------

def _ws_status(ctx: _Ctx, t: dict[str, Any], plan: dict[str, Any]) -> str:
    from tools.aipos_cli.workstation_plan import instance_parts, status_rows

    rows = []
    texts_by_key = dict(status_rows(plan, t))
    for key, view_key in (("row_subagent", "subagent"), ("row_pi_executor", "pi_executor"), ("row_auditor", "auditor")):
        parts = instance_parts(t, plan["status"][view_key])
        extra = (f" <span class=\"rp-sub\">{_ESC(parts['charter'])}</span>" if parts["charter"] else "")
        where = _tech(ctx, f"<code>{_ESC(parts['where'])}</code>") if parts["where"] else ""
        rows.append(f"<tr class=\"ws-row{'' if parts['ok'] else ' rp-alert'}\" data-row=\"{_ESC(view_key)}\""
                    f" data-enrollment=\"{_ESC(str(parts['enrollment'] or ''))}\" data-charter=\"{_ESC(str(parts['charter_state'] or ''))}\">"
                    f"<th>{_ESC(t[key])}</th><td>{_ESC(parts['who'])}{extra}{where}</td></tr>")
    for key in ("row_pi_modes", "row_hosts", "row_validation_roots", "row_advisors"):  # AIPOS-F235 item 4: advisor registrations
        rows.append(f"<tr class=\"ws-row\" data-row=\"{_ESC(key)}\"><th>{_ESC(t[key])}</th><td>{_ESC(texts_by_key[key])}</td></tr>")
    return (f"<section class=\"ws-status\"><h2>{_ESC(t['status_heading'])}</h2>"
            f"<table class=\"rp-subs ws-table\"><tbody>{''.join(rows)}</tbody></table></section>")


def _ws_form(t: dict[str, Any], plan: dict[str, Any], lang: str, query: dict[str, Any], locale: dict[str, Any]) -> str:
    from tools.aipos_cli.workstation_plan import declaration

    decl = declaration()
    names = decl["board"]["params"]
    tgt = plan["target"]
    st = plan["status"]
    boxes = "".join(f"<label class=\"ws-check\"><input type=\"checkbox\" name=\"{_ESC(names['pi_mode'])}\" value=\"{_ESC(m)}\""
                    f"{' checked' if m in tgt['pi_modes'] else ''}> {_ESC(t['mode_' + m])}</label>" for m in decl["offered_pi_modes"])
    insts = "".join(f"<option value=\"{_ESC(c['instance'])}\"></option>" for c in st["candidates"]["items"])
    dirs = "".join(f"<option value=\"{_ESC(c['dir'])}\"></option>" for c in st["candidates"]["items"])
    hosts = sorted(set(st["validation"]["hosts"]) | set(st["validation"]["used_hosts"]))
    host_opts = "".join(f"<option value=\"{_ESC(h)}\"></option>" for h in hosts)
    alias_opts = "".join(f"<option value=\"{_ESC(a)}\"></option>" for a in sorted(set(st["validation"]["hosts"].values())))

    def field(key: str, label: str, value: Any, datalist: str = "") -> str:
        listed = f" list=\"ws-list-{_ESC(key)}\"" if datalist else ""
        options = f"<datalist id=\"ws-list-{_ESC(key)}\">{datalist}</datalist>" if datalist else ""
        return (f"<label class=\"ws-field\"><span>{_ESC(label)}</span><input type=\"text\" name=\"{_ESC(names[key])}\""
                f" value=\"{_ESC(str(value or ''))}\"{listed}>{options}</label>")

    hidden = (f"<input type=\"hidden\" name=\"{_ESC(str(query['project']))}\" value=\"{_ESC(plan['project'])}\">"
              f"<input type=\"hidden\" name=\"{_ESC(names['submitted'])}\" value=\"1\">"
              + (f"<input type=\"hidden\" name=\"{_ESC(str(locale['param']))}\" value=\"{_ESC(lang)}\">"
                 if lang != locale["default"] else ""))
    return (f"<section class=\"ws-choose\"><h2>{_ESC(t['form_heading'])}</h2><p class=\"rp-sub\">{_ESC(t['form_note'])}</p>"
            f"<form class=\"ws-form\" method=\"get\" action=\"{_ESC(str(decl['board']['page_path']))}\">{hidden}"
            f"<fieldset class=\"ws-modes\"><legend>{_ESC(t['form_modes'])}</legend>{boxes}</fieldset>"
            + field("pi_executor", t["form_pi_executor"], tgt["pi_executor"], insts)
            + field("workspace", t["form_workspace"], tgt["workspace"], dirs)
            + field("host", t["form_host"], tgt["host"], host_opts)
            + field("ssh_alias", t["form_ssh_alias"], tgt["ssh_alias"], alias_opts)
            + field("owner_ref", t["form_owner_ref"], tgt["owner_ref"])
            + f"<p><button type=\"submit\">{_ESC(t['form_submit'])}</button></p></form></section>")


def _ws_steps(t: dict[str, Any], plan: dict[str, Any], ctx: _Ctx) -> str:
    from tools.aipos_cli.workstation_plan import step_lines

    items = []
    for n, step in enumerate(plan["steps"], 1):
        st = t["steps"][step["name"]]
        head = (f"<span class=\"rp-chip ws-chip ws-chip-{_ESC(step['state'])}\">{_ESC(t['state_' + step['state']])}</span> "
                f"<span class=\"ws-step-label\">{_ESC(st['label'])}</span>")
        more = ""
        if step["state"] in ("pending", "check"):
            more = (f"<p class=\"ws-why\">{_ESC(st['why'])}</p><details class=\"rp-cmd\"><summary class=\"rp-cmd-label\">"
                    f"{_ESC(ctx.t('rp_command'))}</summary><pre class=\"upgrade-lines\">"
                    f"{_ESC(chr(10).join(line.strip() for line in step_lines(plan, t, step, n)[1:]))}</pre></details>")
        elif step["detail"]:
            more = _tech(ctx, f"<p>{_ESC(step['detail'])}</p>")
        items.append(f"<li class=\"ws-step\" data-step=\"{_ESC(step['name'])}\" data-state=\"{_ESC(step['state'])}\">{head}{more}</li>")
    nothing = "" if plan["changes"] else f"<p class=\"ws-nothing\">{_ESC(t['nothing'])}</p>"
    return (f"<section class=\"ws-plan\" data-changes=\"{str(bool(plan['changes'])).lower()}\"><h2>{_ESC(t['plan_heading'])}</h2>"
            f"{nothing}<ol class=\"ws-steps\">{''.join(items)}</ol></section>")


def render_workstation(home_root: str | Path | None, params: dict[str, list[str]]) -> str:
    """AIPOS-F198: workstation page (read-only; data = tools/aipos_cli/workstation_plan.collect_page, the same derivation and the same
    full text as `lybra project workstation-plan`). The target division lives in the URL (GET form); nothing is written."""
    from tools.aipos_cli import board_pages
    from tools.aipos_cli import workstation_plan as wp
    from tools.aipos_cli.workstation_plan import risk_texts, routing_lines, target_text
    from tools.schema_loader import SchemaLoadError

    lang = "zh"
    try:
        decl = board_pages.pages_declaration()
        lang = resolve_lang(decl, params)
        ctx = _Ctx(decl, lang)
        t = wp.texts(lang)
        show_all = board_pages.show_all_requested(decl, params)
        data = wp.collect_page(home_root, params, lang=lang, show_all=show_all)
        plan = data["selected"]
        page_path = _workstation_page()
        base = _workstation_page(plan["project"]) if plan else page_path
        body = [_toggle(ctx, data.get("hidden") or [], bool(data.get("show_all")), base),
                _project_tabs(ctx, data, plan["project"] if plan else None), f"<p class=\"ws-intro\">{_ESC(t['intro'])}</p>"]
        if data.get("unknown_project"):
            body.append(f"<p class=\"rp-err\">{_ESC(t['unknown_project'].format(project=data['unknown_project']))}</p>")
        if plan is None:
            return _page(ctx, "workstation", t["title_none"], body, current_url=page_path)
        if data.get("input_error"):
            body.append(f"<p class=\"rp-err ws-input-error\">{_ESC(t['input_error'].format(error=data['input_error']))}</p>")
        body.append(_ws_status(ctx, t, plan))
        routing = [*routing_lines(plan["status"]["routing"], t)]
        auditor = (plan["status"]["execution"] or {}).get("auditor")
        if auditor:
            routing.append(t["audit_row"].format(auditor=auditor))
        body.append(f"<section class=\"ws-routing\"><h2>{_ESC(t['routing_heading'])}</h2><ul>"
                    + "".join(f"<li>{_ESC(line)}</li>" for line in routing) + "</ul></section>")
        risks = risk_texts(plan, t)
        body.append(f"<section class=\"ws-risks\" data-risks=\"{len(risks)}\"><h2>{_ESC(t['risks_heading'])}</h2><ul>"
                    + ("".join(f"<li class=\"rp-alert\">{_ESC(r)}</li>" for r in risks) or f"<li>{_ESC(t['none'])}</li>") + "</ul></section>")
        body.append(_ws_form(t, plan, lang, decl["query"], decl["locale"]))
        body.append(f"<section class=\"ws-target\"><h2>{_ESC(t['target_heading'])}</h2><p>{_ESC(target_text(plan, t))}</p></section>")
        body.append(_ws_steps(t, plan, ctx))
        prompts = [f"<section class=\"ws-prompts\"><p class=\"rp-tell\">{_ESC(t['board_note'])}</p>"]
        for key, heading in (("lead", "lead_heading"), ("specialist", "specialist_heading")):
            prompts.append(f"<h2>{_ESC(t[heading])}</h2><details class=\"rp-cmd ws-prompt\" data-prompt=\"{key}\"><summary class=\"rp-cmd-label\">"
                           f"{_ESC(t['copy_summary'])}</summary><pre class=\"upgrade-lines ws-prompt-text\">"
                           f"{_ESC(chr(10).join(plan['prompts'][key]))}</pre></details>")
        prompts.append(f"<details class=\"rp-tech ws-full\"><summary>{_ESC(t['plan_text_summary'])}</summary>"
                       f"<pre class=\"upgrade-lines ws-plan-text\">{_ESC(chr(10).join(plan['lines']))}</pre></details></section>")
        body.extend(prompts)
        return _page(ctx, "workstation", t["title"].format(project=plan["project"]), body, current_url=page_path)
    except (OSError, ValueError, LookupError, RuntimeError, SchemaLoadError) as exc:  # unreadable = error page naming the cause
        return render_error(lang, f"{type(exc).__name__}: {exc}"[:800])


# ---------------------------------------------------------------------------
# 入口(web/board/app.py do_GET 调; 读不出 = 错误页, 拒因原文)
# ---------------------------------------------------------------------------

def render_error(lang: str, message: str) -> str:
    from web.board.app import _generate_text

    text = _generate_text("rp_page_error", lang, error=message)
    return (f"<!doctype html>\n<html lang=\"{_ESC(lang)}\"><head><meta charset=\"utf-8\"><title>board</title></head>"
            f"<body class=\"rp-page\"><main><p class=\"rp-err rp-page-error\">{_ESC(text)}</p></main></body></html>")


def render_page(page: str, home_root: str | Path | None, params: dict[str, list[str]], owner: bool = False) -> str:
    """page ∈ board_pages.PAGES; params = 查询参数(parse_qs)。声明 / 推导读不出 = 错误页(不吞, 拒因原文)。
    AIPOS-F245 item 2: the whole render is one request-level read scope (declarations read once per request, also by the renderer).
    AIPOS-F248: owner = Owner session (the home page then carries the one-click buttons; the data is the same for every session)."""
    from tools.aipos_cli.task_loader import read_request_scope

    with read_request_scope():
        return _render_page(page, home_root, params, owner)


def _render_page(page: str, home_root: str | Path | None, params: dict[str, list[str]], owner: bool = False) -> str:
    from tools.aipos_cli import board_pages
    from tools.schema_loader import SchemaLoadError

    lang = "zh"
    try:
        decl = board_pages.pages_declaration()
        lang = resolve_lang(decl, params)
        show_all = board_pages.show_all_requested(decl, params)
        query = decl["query"]

        def arg(name: str) -> str | None:
            return (params.get(query[name]) or [None])[0]

        if page == "home":  # AIPOS-F246: the home page (one card per advisor)
            from tools.aipos_cli.board_home import collect_home

            all_asks = bool(decl["home"].get("asks_view_value")) and arg("view") == decl["home"]["asks_view_value"]
            return render_home(collect_home(home_root, show_all=show_all, all_asks=all_asks), lang, owner)
        if page == "engineering":
            return render_engineering(decl, lang)
        if page == "overview":
            return render_overview(board_pages.collect_overview(home_root, show_all=show_all), lang)
        if page == "onboarding":
            return render_onboarding(board_pages.collect_onboarding(home_root, show_all=show_all), lang)
        if page == "project":
            view = arg("view")
            if view == decl["home"]["cards_view_value"]:  # AIPOS-F246: the former list view = all task cards (engineering / history)
                return render_project(board_pages.collect_project(home_root, project=arg("project"), subproject=arg("subproject"),
                                                                  show_all=show_all, view=view), lang)
            from tools.aipos_cli.board_home import collect_project_brief

            return render_project_brief(collect_project_brief(home_root, project=arg("project"), advisor=arg("subproject"),
                                                              show_all=show_all), lang)
        if page == "roadmap":
            return render_roadmap(board_pages.collect_roadmap(home_root, project=arg("project"), milestone=arg("milestone"),
                                                              subproject=arg("subproject"), show_all=show_all), lang)
        raise ValueError(f"unknown readonly page {page!r}")
    except (OSError, ValueError, LookupError, RuntimeError, SchemaLoadError) as exc:  # unreadable = error page naming the cause (not swallowed, connection kept)
        return render_error(lang, f"{type(exc).__name__}: {exc}"[:800])
