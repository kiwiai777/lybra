// AIPOS-F172 / AIPOS-F180: the one Owner signing client for the inbox and envelope pages (server-rendered, F174 skeleton).
// Flows: authorization request cards ("sign as suggested" / "edit, then sign" / "decline = tell the advisor") and the envelope
// page renew / new forms -> POST the single F172 write path (stage=preview, then stage=confirm with the session-bound token).
// Confirm shows a plain-language summary only; success shows a result card with return links; failure shows a plain reason and
// the next step. Raw preview / record paths stay folded under "technical details".
// All texts come from the page (body data-gr-texts JSON, board i18n channel); the server decides everything (Owner-only,
// workspace check, preview token, audit log). No credentials ever reach this page.
(function () {
  'use strict';
  var body = document.body;
  if (!body || body.getAttribute('data-owner') !== 'true') { return; }
  var writePath = body.getAttribute('data-write-path');
  var panel = document.querySelector('.gr-panel');
  var conf = {};
  try { conf = JSON.parse(body.getAttribute('data-gr-texts') || '{}'); } catch (e) { conf = {}; }
  if (!writePath || !panel || !conf.t) { return; }
  var heading = panel.querySelector('h2');
  var content = panel.querySelector('.gr-panel-body');
  var tech = panel.querySelector('.gr-panel-tech');
  var techPre = tech.querySelector('pre');
  var actions = panel.querySelector('.gr-panel-actions');

  function fill(template, values) {
    return String(template || '').replace(/\{(\w+)\}/g, function (all, name) {
      return Object.prototype.hasOwnProperty.call(values || {}, name) ? String(values[name]) : all;
    });
  }
  function t(key, values) { return fill(conf.t[key] || key, values); }
  function modeLabel(mode) { return (conf.modes || {})[mode] || conf.mode_any || mode; }

  function post(payload) {
    return fetch(writePath, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (resp) {
      return resp.json().then(function (data) { return { status: resp.status, data: data }; },
        function () { return { status: resp.status, data: {} }; });
    }, function (err) { return { status: 0, data: { message: String(err) } }; });
  }

  function show(state, title, lines, techLines) {
    panel.setAttribute('data-state', state);
    heading.textContent = title;
    content.textContent = '';
    lines.forEach(function (line) {
      var p = document.createElement('p');
      p.textContent = line;
      content.appendChild(p);
    });
    techPre.textContent = (techLines || []).join('\n');
    tech.hidden = !(techLines && techLines.length);
    tech.open = false;
    actions.textContent = '';
    panel.hidden = false;
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function button(label, onClick, primary) {
    var b = document.createElement('button');
    b.type = 'button';
    b.textContent = label;
    if (primary) { b.className = 'gr-primary'; }
    b.addEventListener('click', onClick);
    actions.appendChild(b);
    return b;
  }

  function link(label, href) {
    var a = document.createElement('a');
    a.className = 'gr-back';
    a.href = href;
    a.textContent = label;
    actions.appendChild(a);
  }

  function repoText(list) { return (list && list.length) ? list.join(', ') : t('gr_repos_any'); }

  function summary(params, request) {
    var lines = [t('gr_confirm_who', { agent: params.agent_or_role }),
      t('gr_confirm_what', { mode: modeLabel(params.task_mode) }),
      t('gr_confirm_repos', { repos: repoText(params.lane_repo) }),
      t('gr_confirm_count', { count: params.max_tasks }),
      t('gr_confirm_until', { date: String(params.expires_at || '').slice(0, 10) }),
      t('gr_confirm_project', { project: params.project })];
    if (request) {
      lines.push(t('gr_confirm_card', { task_id: request.task_id,
        title_part: request.title ? t('gr_title_part', { title: request.title }) : '' }));
    }
    return lines;
  }

  function failure(res) {
    var d = res.data || {};
    var pair = (conf.errors || {})[d.error] || [conf.t.gr_err_other, conf.t.gr_next_other];
    var values = { message: d.message || d.error || ('HTTP ' + res.status) };
    show('failed', t('gr_fail_heading'), [fill(pair[0], values), t('gr_fail_next', { next: fill(pair[1], values) })],
      ['HTTP ' + res.status + ' ' + (d.error || ''), d.message || JSON.stringify(d)]);
    button(t('gr_retry_btn'), function () { window.location.reload(); }, true);
    button(t('gr_cancel_btn'), function () { panel.hidden = true; });
  }

  function success(d, request) {
    var params = d.params || {};
    var policy = ((d.signed || [])[0] || {}).policy_id || params.policy_id;
    var lines = [request ? t('gr_ok_continue', { agent: params.agent_or_role, task_id: request.task_id })
      : t('gr_ok_generic', { agent: params.agent_or_role })];
    var techLines = (d.signed || []).map(function (s) { return s.policy_id + ' -> ' + (s.written || []).join(', '); });
    if (d.landing_line) { techLines.push(d.landing_line); }
    techLines.push('actor=' + d.actor + ' / ' + d.capture_surface + ' / ' + d.captured_by);
    show('signed', t('gr_ok_heading', { policy_id: policy }), lines, techLines);
    if (request && request.card) { request.card.hidden = true; }
    var links = conf.links || {};
    link(t('gr_back_inbox'), links.inbox);
    link(t('gr_back_envelopes'), links.envelopes);
    link(t('gr_back_overview'), links.overview);
  }

  function confirmStep(payload, preview, request) {
    show('working', t('gr_working'), [], []);
    post(Object.assign({}, payload, { stage: 'confirm', preview_token: preview.preview_token })).then(function (res) {
      if (res.status !== 200 || !res.data.ok) { failure(res); return; }
      success(res.data, request);
    });
  }

  function start(payload, request) {
    show('working', t('gr_working'), [], []);
    post(Object.assign({}, payload, { stage: 'preview' })).then(function (res) {
      if (res.status !== 200 || !res.data.ok) { failure(res); return; }
      var preview = res.data;
      var techLines = (preview.preview_lines || []).slice();
      techLines.push('');
      techLines.push(JSON.stringify(preview.dry_run, null, 2));
      show('confirm', t('gr_confirm_heading'), summary(preview.params || {}, request), techLines);
      button(t('gr_confirm_btn'), function () { confirmStep(payload, preview, request); }, true);
      button(t('gr_cancel_btn'), function () { panel.hidden = true; });
    });
  }

  function splitRepos(text) {
    return String(text || '').split(',').map(function (x) { return x.trim(); }).filter(function (x) { return x; });
  }

  // Authorization request cards (data-request = the suggestion the server derived; signing posts it unchanged).
  Array.prototype.forEach.call(document.querySelectorAll('.gr-card[data-request]'), function (card) {
    var request;
    try { request = JSON.parse(card.getAttribute('data-request')); } catch (e) { return; }
    request.card = card;
    var editForm = card.querySelector('form.gr-edit');
    var deny = card.querySelector('.gr-deny');
    card.addEventListener('click', function (event) {
      var action = event.target && event.target.getAttribute && event.target.getAttribute('data-gr-action');
      if (action === 'sign') { start(request.payload, request); }
      if (action === 'edit' && editForm) { editForm.hidden = !editForm.hidden; if (deny) { deny.hidden = true; } }
      if (action === 'deny' && deny) { deny.hidden = !deny.hidden; if (editForm) { editForm.hidden = true; } }
    });
    if (editForm) {
      editForm.addEventListener('submit', function (event) {
        event.preventDefault();
        var count = parseInt(editForm.elements.max_tasks.value, 10);
        var date = String(editForm.elements.expires_date.value || '').trim();
        if (!(count >= 1) || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
          show('failed', t('gr_fail_heading'), [t('gr_edit_invalid')], []);
          return;
        }
        var payload = Object.assign({}, request.payload, { max_tasks: count, expires_at: date + (conf.date_only_time || ''),
          lane_repo: splitRepos(editForm.elements.lane_repo.value) });
        start(payload, request);
      });
    }
  });

  // Envelope page renew / new forms: same humanized flow (no request card attached).
  Array.prototype.forEach.call(document.querySelectorAll('form.envelope-form'), function (form) {
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var payload = { mode: form.getAttribute('data-mode'), workspace: form.getAttribute('data-workspace') };
      var template = form.getAttribute('data-template');
      if (template) { payload.template_policy_id = template; }
      Array.prototype.forEach.call(form.querySelectorAll('input[name]'), function (input) { payload[input.name] = input.value; });
      start(payload, null);
    });
  });
}());
