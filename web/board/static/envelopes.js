// AIPOS-F172: Owner envelope page (server-rendered /owner/envelopes) — preview -> confirm two-step client.
// All texts come from the server-rendered page (data-text-* attributes, declared in verbs.schema board_owner_envelopes);
// the server decides everything (Owner-only, workspace check, session-bound preview token). No credentials ever reach this page.
(function () {
  'use strict';
  var body = document.body;
  if (!body || body.getAttribute('data-owner') !== 'true') { return; }
  var writePath = body.getAttribute('data-write-path');
  var panel = document.querySelector('.envelope-preview');
  if (!writePath || !panel) { return; }
  var heading = panel.querySelector('h2');
  var pre = panel.querySelector('pre');
  var actions = panel.querySelector('.envelope-actions');

  function text(key) { return body.getAttribute('data-text-' + key) || key; }

  function formBody(form) {
    var out = { mode: form.getAttribute('data-mode'), workspace: form.getAttribute('data-workspace') };
    var template = form.getAttribute('data-template');
    if (template) { out.template_policy_id = template; }
    Array.prototype.forEach.call(form.querySelectorAll('input[name]'), function (input) {
      out[input.name] = input.value;
    });
    return out;
  }

  function post(payload) {
    return fetch(writePath, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (resp) {
      return resp.json().then(function (data) { return { status: resp.status, data: data }; });
    });
  }

  function show(title, lines) {
    heading.textContent = title;
    pre.textContent = lines.join('\n');
    actions.textContent = '';
    panel.hidden = false;
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function button(label, onClick) {
    var b = document.createElement('button');
    b.type = 'button';
    b.textContent = label;
    b.addEventListener('click', onClick);
    actions.appendChild(b);
    return b;
  }

  function failure(res) {
    var d = res.data || {};
    show(text('result-heading'), ['HTTP ' + res.status + ' ' + (d.error || ''), d.message || JSON.stringify(d)]);
  }

  function confirmStep(payload, preview) {
    var confirmBody = Object.assign({}, payload, { stage: 'confirm', preview_token: preview.preview_token });
    post(confirmBody).then(function (res) {
      if (res.status !== 200 || !res.data.ok) { failure(res); return; }
      var d = res.data;
      var lines = (d.signed || []).map(function (s) { return s.policy_id + ' -> ' + (s.written || []).join(', '); });
      if (d.landing_line) { lines.push(d.landing_line); }
      lines.push('actor=' + d.actor + ' / ' + d.capture_surface + ' / ' + d.captured_by);
      show(text('result-heading'), lines);
      button(text('cancel-button'), function () { window.location.reload(); });
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('form.envelope-form'), function (form) {
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var payload = formBody(form);
      post(Object.assign({}, payload, { stage: 'preview' })).then(function (res) {
        if (res.status !== 200 || !res.data.ok) { failure(res); return; }
        var preview = res.data;
        var lines = (preview.preview_lines || []).slice();
        lines.push('');
        lines.push(JSON.stringify(preview.dry_run, null, 2));
        show(text('preview-heading'), lines);
        button(text('confirm-button'), function () { confirmStep(payload, preview); });
        button(text('cancel-button'), function () { panel.hidden = true; });
      });
    });
  });
}());
