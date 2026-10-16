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


class _Ctx:
    def __init__(self, decl: dict[str, Any], lang: str) -> None:
        self.decl = decl
        self.lang = lang

    def t(self, key: str, **values: Any) -> str:
        from web.board.app import _generate_text

        return _generate_text(key, self.lang, **values)

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
    if not commands:
        return ""
    items = "".join(f"<li>{_rich(c) if '`' in c else '<code>' + _ESC(c) + '</code>'}</li>" for c in commands)
    return f"<div class=\"rp-cmd\"><span class=\"rp-cmd-label\">{_ESC(ctx.t('rp_command'))}</span><ul>{items}</ul></div>"


def _bar(met: int, total: int, ratio: Any = None) -> str:
    pct = round(100 * (ratio if isinstance(ratio, (int, float)) else (met / total if total else 0)))
    return (f"<span class=\"rp-bar\" role=\"progressbar\" aria-valuemin=\"0\" aria-valuemax=\"{total}\" aria-valuenow=\"{met}\""
            f" data-met=\"{met}\" data-total=\"{total}\" data-percent=\"{pct}\"><span style=\"width:{pct}%\"></span></span>"
            f"<span class=\"rp-bar-text\">{met}/{total}</span>")


def _page(ctx: _Ctx, page: str, title: str, body: list[str]) -> str:
    decl = ctx.decl
    locale = decl["locale"]
    nav = [("rp_nav_overview", decl["pages"]["overview"]), ("rp_nav_roadmap", decl["pages"]["roadmap"]),
           ("rp_nav_onboarding", decl["pages"]["onboarding"]), ("rp_nav_inbox", decl["nav_links"]["inbox"]),
           ("rp_nav_envelopes", _envelope_page()), ("rp_nav_board", decl["nav_links"]["board_home"])]
    current = _CURRENT
    links = "".join(f"<a href=\"{_ESC(ctx.href(url) if url != decl['nav_links']['board_home'] else url)}\""
                    f"{current if url == decl['pages'].get(page) else ''}>{_ESC(ctx.t(key))}</a>" for key, url in nav)
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
        f"<body class=\"rp-page rp-{_ESC(page)}\" data-readonly-page=\"{_ESC(page)}\" data-lang=\"{_ESC(ctx.lang)}\">",
        f"<header class=\"rp-nav\"><nav>{links}</nav>{switch}</header>",
        f"<main><h1>{_ESC(title)}</h1>", f"<p class=\"rp-readonly-note\">{_ESC(ctx.t('rp_readonly_note'))}</p>",
        *body, "</main>", script, "</body></html>"])


def _js(value: Any) -> str:
    import json

    return json.dumps(value).replace("<", "\\u003c")


def _envelope_page() -> str:
    from tools.aipos_cli.envelope_board import board_envelopes_declaration

    return str(board_envelopes_declaration()["page_path"])


# ---------------------------------------------------------------------------
# 总览
# ---------------------------------------------------------------------------

def _overview_card(ctx: _Ctx, card: dict[str, Any]) -> str:
    name = str(card["project"])
    li: list[str] = []
    queue = card["queue"]
    if "error" in queue:
        li.append(f"<li class=\"rp-inflight rp-err\">{_ESC(ctx.t('rp_error', error=queue['error']))}</li>")
    else:
        q = queue["value"]
        states = " · ".join(f"{s} {n}" for s, n in q["inflight"].items())
        li.append(f"<li class=\"rp-inflight\" data-inflight=\"{q['inflight_total']}\" data-closed=\"{q['closed']}\">"
                  f"{_ESC(ctx.t('rp_ov_inflight', total=q['inflight_total']))} <span class=\"rp-sub\">({_ESC(states)})</span> · "
                  f"{_ESC(ctx.t('rp_ov_closed', count=q['closed']))}</li>")
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
    badge = f" <span class=\"rp-badge\">{_ESC(ctx.t('rp_paused_badge'))}</span>" if card["paused"] else ""
    detail = f"/workspace/{int(card['index'])}"
    return (f"<article class=\"rp-card\" data-project=\"{_ESC(name)}\" data-paused=\"{str(bool(card['paused'])).lower()}\">"
            f"<h2>{_ESC(name)}{badge}</h2><ul>{''.join(li)}</ul>"
            f"<p class=\"rp-card-foot\"><a href=\"{_ESC(detail)}\">{_ESC(ctx.t('rp_ov_detail'))}</a></p></article>")


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
    body = [f"<section class=\"rp-cards\" data-projects=\"{len(active)}\">" + "".join(_overview_card(ctx, c) for c in active) + "</section>"]
    if paused:
        body.append(f"<details class=\"rp-paused\" data-paused=\"{len(paused)}\"><summary>{_ESC(ctx.t('rp_paused_folded', count=len(paused)))}"
                    "</summary><section class=\"rp-cards\">" + "".join(_overview_card(ctx, c) for c in paused) + "</section></details>")
    return _page(ctx, "overview", ctx.t("rp_ov_title", count=len(data["projects"])), body)


# ---------------------------------------------------------------------------
# 路线图与验收
# ---------------------------------------------------------------------------

def _signal_html(ctx: _Ctx, sig: dict[str, Any], targets: list[str]) -> str:
    tell = ctx.tell(targets, "rp_rm_signal_ask", {"label": sig["label"], "subject": sig["subject"]})
    return (f"<div class=\"rp-signal rp-sev-{_ESC(sig['severity'])}\" data-kind=\"{_ESC(sig['kind'])}\" data-severity=\"{_ESC(sig['severity'])}\""
            f" data-milestone=\"{_ESC(str(sig.get('milestone') or ''))}\" data-criterion=\"{_ESC(str(sig.get('criterion') or ''))}\">"
            f"<p class=\"rp-signal-head\"><span class=\"rp-sev\">{_ESC(ctx.t('rp_sev_' + str(sig['severity'])))}</span> "
            f"<strong>{_ESC(sig['label'])}</strong></p><p class=\"rp-reason\">{_ESC(sig['reason'])}</p>"
            f"<p class=\"rp-tell\">{_ESC(tell)}</p>"
            f"<div class=\"rp-cmd\"><span class=\"rp-cmd-label\">{_ESC(ctx.t('rp_command'))}</span><p>{_rich(sig['advice'])}</p></div></div>")


def _milestone_item(ctx: _Ctx, project: str, ms: dict[str, Any], *, selected: bool, overdue: bool) -> str:
    from tools.aipos_cli.board_pages import roadmap_url

    prog = ms["progress"]
    target = f"<span class=\"roadmap-target\">{_ESC(ctx.t('rp_rm_target', date=ms['target_date']))}</span>" if ms.get("target_date") else ""
    late = f"<span class=\"rp-overdue\">{_ESC(ctx.t('rp_rm_overdue'))}</span>" if overdue else ""
    classes = "roadmap-ms" + (" rp-selected" if selected else "") + (" rp-is-overdue" if overdue else "") + \
        (" roadmap-dropped" if ms["status"] == "dropped" else "")
    return (f"<li class=\"{classes}\" data-milestone=\"{_ESC(ms['id'])}\" data-status=\"{_ESC(ms['status'])}\" data-overdue=\"{str(overdue).lower()}\">"
            f"<a class=\"roadmap-ms-head\" href=\"{_ESC(ctx.href(roadmap_url(ctx.decl, project, ms['id'])))}\">"
            f"<span class=\"roadmap-title\">{_ESC(ms['id'])} · {_ESC(ms['title'])}</span></a>"
            f"<div class=\"rp-ms-meta\"><span class=\"roadmap-status\">{_ESC(ctx.t('rp_rm_status_' + str(ms['status'])))}</span>{target}{late}</div>"
            f"{_bar(prog['met'], prog['total'], prog.get('ratio'))}</li>")


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
                        f" <span class=\"vb-ring-meta\">{_ESC(ctx.t('rp_rm_evidence_entry', exit_code=entry.get('exit_code'), worktree=entry.get('worktree') or '?'))}</span>"
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
    ctx = _Ctx(data["decl"], lang)
    sel = data["selected"]
    body: list[str] = []
    tabs = []
    for p in data["projects"]:
        current = sel is not None and p["project"] == sel["project"]
        badge = f" ({_ESC(ctx.t('rp_paused_badge'))})" if p["paused"] else ""
        tabs.append(f"<a href=\"{_ESC(ctx.href(p['url']))}\" data-project=\"{_ESC(p['project'])}\"{_CURRENT if current else ''}>"
                    f"{_ESC(p['project'])}{badge}</a>")
    body.append(f"<nav class=\"rp-projects\"><span>{_ESC(ctx.t('rp_rm_projects'))}</span>{''.join(tabs)}</nav>")
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
                    f"<p class=\"rp-tell\">{_ESC(tell)}</p><div class=\"rp-cmd\"><span class=\"rp-cmd-label\">{_ESC(ctx.t('rp_command'))}</span>"
                    f"<p>{_rich(sel['no_roadmap_hint'])}</p></div></section>")
        return _page(ctx, "roadmap", title, body)
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
    lanes: dict[str, list[dict[str, Any]]] = {}
    for ms in view["milestones"]:
        lanes.setdefault(str(ms["lane"]), []).append(ms)
    left = [f"<h2>{_ESC(ctx.t('rp_rm_milestones'))}</h2>"]
    for lane, items in lanes.items():
        left.append(f"<h3 class=\"roadmap-lane\" data-lane=\"{_ESC(lane)}\">{_ESC(ctx.t('rp_rm_lane', lane=lane))}</h3><ol class=\"roadmap-list\">"
                    + "".join(_milestone_item(ctx, sel["project"], ms, selected=ms["id"] == sel["milestone"], overdue=ms["id"] in overdue)
                              for ms in items) + "</ol>")
    right: list[str] = []
    picked = next((m for m in view["milestones"] if m["id"] == sel["milestone"]), None)
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
    extra = [f"<h2>{_ESC(ctx.t('rp_rm_next'))}</h2><ul>" + "".join(f"<li>{_rich(step)}</li>" for step in view["next_steps"]) + "</ul>"]
    if view["unlinked"]:
        extra.append(f"<p class=\"roadmap-unlinked\">{_ESC(ctx.t('rp_rm_unlinked', count=len(view['unlinked']), cards=', '.join(view['unlinked'])))}</p>")
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

def _checklist_html(ctx: _Ctx, proj: dict[str, Any]) -> str:
    items = []
    for row in proj["rows"]:
        details = "".join(f"<li>{_ESC(ctx.t(key, **values))}</li>" for key, values in row["details"])
        tell = f"<p class=\"rp-tell\">{_ESC(ctx.tell(row['tell'], row['ask'][0], row['ask'][1]))}</p>" if row.get("ask") else ""
        links = "".join(f"<a class=\"rp-link\" href=\"{_ESC(ctx.href(url))}\">{_ESC(ctx.t(key))}</a>" for key, url in row["links"])
        items.append(f"<li class=\"rp-ob-item rp-state-{_ESC(row['state'])}\" data-item=\"{_ESC(row['key'])}\" data-state=\"{_ESC(row['state'])}\">"
                     f"<p><span class=\"rp-state\">{_ESC(ctx.t('rp_ob_state_' + row['state']))}</span> "
                     f"<strong>{_ESC(ctx.t('rp_ob_item_' + row['key']))}</strong></p><ul class=\"rp-details\">{details}</ul>"
                     f"{tell}{_command_block(ctx, row['commands'])}{links}</li>")
    summary = proj["summary"]
    badge = f" <span class=\"rp-badge\">{_ESC(ctx.t('rp_paused_badge'))}</span>" if proj["paused"] else ""
    return (f"<section class=\"rp-ob-project\" id=\"{_ESC(proj['project'])}\" data-project=\"{_ESC(proj['project'])}\""
            f" data-done=\"{summary['done']}\" data-total=\"{summary['total']}\"><h2>{_ESC(proj['project'])}{badge} "
            f"<span class=\"rp-sub\">{_ESC(ctx.t('rp_ob_summary', done=summary['done'], total=summary['total']))}</span></h2>"
            f"<ul class=\"rp-upgrade-line\">{_upgrade_li(ctx, proj.get('upgrade'))}</ul>"
            f"<ol class=\"rp-checklist\">{''.join(items)}</ol></section>")


def render_onboarding(data: dict[str, Any], lang: str) -> str:
    ctx = _Ctx(data["decl"], lang)
    active = [p for p in data["projects"] if not p["paused"]]
    paused = [p for p in data["projects"] if p["paused"]]
    body = [_checklist_html(ctx, p) for p in active]
    if paused:
        body.append(f"<details class=\"rp-paused\" data-paused=\"{len(paused)}\"><summary>{_ESC(ctx.t('rp_paused_folded', count=len(paused)))}"
                    "</summary>" + "".join(_checklist_html(ctx, p) for p in paused) + "</details>")
    return _page(ctx, "onboarding", ctx.t("rp_ob_title", count=len(data["projects"])), body)


# ---------------------------------------------------------------------------
# 入口(web/board/app.py do_GET 调; 读不出 = 错误页, 拒因原文)
# ---------------------------------------------------------------------------

def render_error(lang: str, message: str) -> str:
    from web.board.app import _generate_text

    text = _generate_text("rp_page_error", lang, error=message)
    return (f"<!doctype html>\n<html lang=\"{_ESC(lang)}\"><head><meta charset=\"utf-8\"><title>board</title></head>"
            f"<body class=\"rp-page\"><main><p class=\"rp-err rp-page-error\">{_ESC(text)}</p></main></body></html>")


def render_page(page: str, home_root: str | Path | None, params: dict[str, list[str]]) -> str:
    """page ∈ board_pages.PAGES; params = 查询参数(parse_qs)。声明 / 推导读不出 = 错误页(不吞, 拒因原文)。"""
    from tools.aipos_cli import board_pages
    from tools.schema_loader import SchemaLoadError

    lang = "zh"
    try:
        decl = board_pages.pages_declaration()
        lang = resolve_lang(decl, params)
        if page == "overview":
            return render_overview(board_pages.collect_overview(home_root), lang)
        if page == "onboarding":
            return render_onboarding(board_pages.collect_onboarding(home_root), lang)
        if page == "roadmap":
            query = decl["query"]
            return render_roadmap(board_pages.collect_roadmap(home_root, project=(params.get(query["project"]) or [None])[0],
                                                              milestone=(params.get(query["milestone"]) or [None])[0]), lang)
        raise ValueError(f"unknown readonly page {page!r}")
    except (OSError, ValueError, LookupError, RuntimeError, SchemaLoadError) as exc:  # unreadable = error page naming the cause (not swallowed, connection kept)
        return render_error(lang, f"{type(exc).__name__}: {exc}"[:800])
