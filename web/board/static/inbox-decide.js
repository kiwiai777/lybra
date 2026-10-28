// AIPOS-F226: Owner inbox one-click ruling (board write exception 3). Every ".ib-decide" block (inbox items, and "decline" on
// authorization request cards) has one button per option plus an optional one-line Owner sentence. Click -> POST the single declared
// write path (body data-ib-write-path) with stage=preview -> show the server's preview -> confirm with the session-bound token ->
// the whole page reloads from the server (response.redirect = the inbox page with the ruled item, whose banner the server derives
// from the truth). All texts come from the page (body data-ib-texts JSON, board i18n channel); the server decides everything
// (Owner-only, workspace check, preview token, audit log, the ruling itself = owner_inbox.decide). No credentials reach this page.
(function () {
  'use strict';
  var body = document.body;
  if (!body || body.getAttribute('data-ib-owner') !== 'true') { return; }
  var writePath = body.getAttribute('data-ib-write-path');
  var panel = document.querySelector('.ib-panel');
  var conf = {};
  try { conf = JSON.parse(body.getAttribute('data-ib-texts') || '{}'); } catch (e) { conf = {}; }
  if (!writePath || !panel) { return; }
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
  function t(key, values) { return fill(conf[key] || key, values); }

  function post(payload) {
    return fetch(writePath, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (resp) {
      // not logged in / session gone: the auth gate answers with a redirect to the login page (no JSON)
      if (resp.redirected) { return { status: resp.status, login: true, data: {} }; }
      return resp.json().then(function (data) { return { status: resp.status, data: data }; },
        function () { return { status: resp.status, login: resp.status === 200, data: {} }; });
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

  function failure(res) {
    var d = res.data || {};
    if (res.login) {
      show('failed', t('ib_dc_fail_heading'), [t('ib_dc_login'), t('ib_dc_login_next')], ['HTTP ' + res.status]);
    } else {
      show('failed', t('ib_dc_fail_heading'), [d.message || d.error || ('HTTP ' + res.status), t('ib_dc_fail_next')],
        ['HTTP ' + res.status + ' ' + (d.error || ''), JSON.stringify(d, null, 2)]);
    }
    button(t('ib_dc_retry_btn'), function () { window.location.reload(); }, true);
    button(t('ib_dc_cancel_btn'), function () { panel.hidden = true; });
  }

  function confirmStep(payload, preview, block) {
    show('working', t('ib_dc_working'), [], []);
    post(Object.assign({}, payload, { stage: 'confirm', preview_token: preview.preview_token })).then(function (res) {
      if (res.status !== 200 || !res.data.ok) { failure(res); return; }
      var d = res.data;
      var techLines = (d.result && d.result.written || []).slice();
      if (d.landing_line) { techLines.push(d.landing_line); }
      techLines.push('actor=' + d.actor + ' / ' + d.capture_surface + ' / ' + d.captured_by);
      show('ruled', t('ib_dc_ok_heading'), [], techLines);
      if (d.redirect) { window.location.assign(d.redirect); } else { window.location.reload(); }
    });
  }

  function start(block, choice, label) {
    var words = block.querySelector('input[name="owner_words"]');
    var payload = { workspace: block.getAttribute('data-ib-workspace'), item_id: block.getAttribute('data-ib-item'), choice: choice,
      owner_words: words ? String(words.value || '').trim() : '' };
    show('working', t('ib_dc_working'), [], []);
    post(Object.assign({}, payload, { stage: 'preview' })).then(function (res) {
      if (res.status !== 200 || !res.data.ok) { failure(res); return; }
      var preview = res.data;
      var params = preview.params || {};
      var lines = [t('ib_dc_item', { what: block.getAttribute('data-ib-what') || payload.item_id }),
        t('ib_dc_choice', { label: label }), t('ib_dc_words', { words: params.owner_words || '' }),
        t('ib_dc_after', { advisor: preview.advisor || block.getAttribute('data-ib-advisor') || '' })];
      show('confirm', t('ib_dc_confirm_heading'), lines, (preview.preview_lines || []).slice());
      button(t('ib_dc_confirm_btn'), function () { confirmStep(payload, preview, block); }, true);
      button(t('ib_dc_cancel_btn'), function () { panel.hidden = true; });
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('.ib-decide[data-ib-item]'), function (block) {
    block.addEventListener('click', function (event) {
      // AIPOS-F248: the recommended button carries a small tag; a click on it still means its button
      var target = event.target && event.target.closest ? event.target.closest('[data-ib-choice]') : event.target;
      var choice = target && target.getAttribute && target.getAttribute('data-ib-choice');
      if (!choice) { return; }
      start(block, choice, target.getAttribute('data-ib-label') || choice);
    });
  });
}());
