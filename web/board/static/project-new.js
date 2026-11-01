// AIPOS-F269: board write exception 4 "new project" on the onboarding page. The Owner fills a project name (and an optional product repo
// absolute path) -> POST the single declared write path (body data-pn-write-path) with stage=preview -> show the server's rehearsal (the
// same implementation as `lybra project new`, run in a throw-away home root: tree + project.json + commit facts) -> confirm with the
// session-bound preview token -> show the result (created tree, commit line, advisor commands) and a link to the checklist.
// All texts come from the page (body data-pn-texts JSON, board i18n channel); the server decides everything (Owner-only, name check,
// preview token, audit log, the creation itself). No credentials reach this page.
(function () {
  'use strict';
  var body = document.body;
  if (!body || body.getAttribute('data-pn-owner') !== 'true') { return; }
  var writePath = body.getAttribute('data-pn-write-path');
  var form = document.querySelector('form.pn-form');
  var panel = document.querySelector('.pn-panel');
  var conf = {};
  try { conf = JSON.parse(body.getAttribute('data-pn-texts') || '{}'); } catch (e) { conf = {}; }
  if (!writePath || !form || !panel) { return; }
  var heading = panel.querySelector('h3');
  var content = panel.querySelector('.pn-panel-body');
  var actions = panel.querySelector('.pn-panel-actions');

  function fill(template, values) {
    return String(template || '').replace(/\{(\w+)\}/g, function (all, name) {
      return Object.prototype.hasOwnProperty.call(values || {}, name) ? String(values[name]) : all;
    });
  }
  function t(key, values) { return fill(conf[key] || key, values); }

  function post(payload) {
    return fetch(writePath, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (resp) {
      if (resp.redirected) { return { status: resp.status, login: true, data: {} }; }
      return resp.json().then(function (data) { return { status: resp.status, data: data }; },
        function () { return { status: resp.status, login: resp.status === 200, data: {} }; });
    }, function (err) { return { status: 0, data: { message: String(err) } }; });
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }
  function para(text) { content.appendChild(el('p', null, text)); }
  function pre(lines) { var p = el('pre', 'pn-pre'); p.textContent = lines.join('\n'); return p; }
  function folded(summary, node) {
    var d = el('details', 'rp-tech');
    d.appendChild(el('summary', null, summary));
    d.appendChild(node);
    content.appendChild(d);
  }
  function open(title) {
    heading.textContent = title;
    content.textContent = '';
    actions.textContent = '';
    panel.hidden = false;
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function button(label, onClick) {
    var b = el('button', null, label);
    b.type = 'button';
    b.addEventListener('click', onClick);
    actions.appendChild(b);
    return b;
  }
  function langSuffix(url) {
    var param = body.getAttribute('data-pn-lang-param');
    var lang = param ? new URLSearchParams(window.location.search).get(param) : null;
    if (!lang) { return url; }
    var hash = '';
    var at = url.indexOf('#');
    if (at >= 0) { hash = url.slice(at); url = url.slice(0, at); }
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + encodeURIComponent(param) + '=' + encodeURIComponent(lang) + hash;
  }

  function failure(res) {
    var d = res.data || {};
    open(t('pn_js_fail_heading'));
    if (res.login) { para(t('pn_js_login')); } else {
      var key = 'pn_js_err_' + (d.error || '');
      para(Object.prototype.hasOwnProperty.call(conf, key) ? t(key) : t('pn_js_err_other', { code: d.error || res.status, message: d.message || '' }));
    }
    folded(t('pn_js_tech'), pre(['HTTP ' + res.status + ' ' + (d.error || ''), d.message || JSON.stringify(d)]));
    button(t('pn_js_cancel_btn'), function () { panel.hidden = true; });
  }

  function showPlan(project, plan) {
    open(t('pn_js_preview_heading', { project: project }));
    var root = (plan.params || {}).governance_root;
    var p = plan.plan || {};
    para(t('pn_js_root', { root: root }));
    para(t('pn_js_registered_by', { actor: p.registered_by, surface: p.capture_surface }));
    if (!p.repo) { para(t('pn_js_repo_none')); } else {
      para(t('pn_js_repo', { path: p.repo.path, git: p.repo.git_root ? t('pn_js_repo_git', { root: p.repo.git_root }) : t('pn_js_repo_nogit') }));
    }
    var gov = p.governance_repo || {};
    para(gov.git ? t('pn_js_gov_git', { root: gov.repo_root }) : t('pn_js_gov_nogit', { command: p.non_git_exit || '' }));
    content.appendChild(el('h4', null, t('pn_js_tree')));
    content.appendChild(pre(p.tree || []));
    folded(t('pn_js_project_json'), pre([p.project_json || '']));
  }

  function showDone(project, d) {
    open(t('pn_js_done_heading', { project: project }));
    var landing = d.landing || {};
    para(landing.landed ? t('pn_js_landed', { commit: String(landing.commit_hash || '').slice(0, 12) })
      : t('pn_js_not_landed', { line: d.landing_line || landing.reason || '' }));
    content.appendChild(el('h4', null, t('pn_js_next_cmds')));
    var ul = el('ul', 'pn-cmds');
    (d.next_commands || []).forEach(function (cmd) { var li = el('li'); li.appendChild(el('code', null, cmd)); ul.appendChild(li); });
    content.appendChild(ul);
    folded(t('pn_js_tech'), pre([(d.created || {}).governance_root || ''].concat((d.created || {}).tree || [])));
    button(t('pn_js_go'), function () { window.location.href = langSuffix(d.redirect || window.location.pathname); });
  }

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var project = String((form.elements.workspace || {}).value || '').trim();
    var repo = String((form.elements.code_repo || {}).value || '').trim();
    if (!project) { open(t('pn_js_fail_heading')); para(t('pn_js_name_missing')); return; }
    var payload = { workspace: project, code_repo: repo || null, stage: 'preview' };
    open(t('pn_js_working'));
    post(payload).then(function (res) {
      if (res.status !== 200 || !res.data.ok) { failure(res); return; }
      var preview = res.data;
      showPlan(project, preview);
      button(t('pn_js_confirm_btn'), function () {
        actions.textContent = '';
        para(t('pn_js_working'));
        post({ workspace: project, code_repo: repo || null, stage: 'confirm', preview_token: preview.preview_token }).then(function (done) {
          if (done.status !== 200 || !done.data.ok) { failure(done); return; }
          showDone(project, done.data);
        });
      });
      button(t('pn_js_cancel_btn'), function () { panel.hidden = true; });
    });
  });
}());
