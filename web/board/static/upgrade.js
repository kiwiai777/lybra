// AIPOS-F176: Owner project upgrade page (server-rendered /owner/upgrade) - preview -> confirm two-step client.
// All texts come from the server-rendered page (data-text-* attributes, declared in verbs.schema verbs.lybra_project_upgrade.board);
// the server decides everything (Owner-only, workspace check, session-bound preview token, audit log). No credentials reach this page.
(function () {
  'use strict';
  var body = document.body;
  if (!body || body.getAttribute('data-owner') !== 'true') { return; }
  var writePath = body.getAttribute('data-write-path');
  var panel = document.querySelector('.upgrade-preview');
  if (!writePath || !panel) { return; }
  var heading = panel.querySelector('h2');
  var pre = panel.querySelector('pre');
  var actions = panel.querySelector('.upgrade-actions');

  function text(key) { return body.getAttribute('data-text-' + key) || key; }

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
    button(text('cancel-button'), function () { window.location.reload(); });
  }

  function confirmStep(workspace, preview) {
    post({ workspace: workspace, stage: 'confirm', preview_token: preview.preview_token }).then(function (res) {
      if (res.status !== 200 || !res.data.ok) { failure(res); return; }
      var d = res.data;
      var lines = (d.result_lines || []).slice();
      lines.push('actor=' + d.actor + ' / ' + d.capture_surface);
      show(text('result-heading'), lines);
      button(text('cancel-button'), function () { window.location.reload(); });
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('button.upgrade-preview-button'), function (btn) {
    btn.addEventListener('click', function () {
      var workspace = btn.getAttribute('data-workspace');
      post({ workspace: workspace, stage: 'preview' }).then(function (res) {
        if (res.status !== 200 || !res.data.ok) { failure(res); return; }
        var preview = res.data;
        show(text('preview-heading'), (preview.preview_lines || []).slice());
        button(text('confirm-button'), function () { confirmStep(workspace, preview); });
        button(text('cancel-button'), function () { panel.hidden = true; });
      });
    });
  });
}());
