// AIPOS-F270: board write exception 5 "enrollment code" on the onboarding page. Next to an open advisor / executor / auditor row the Owner
// clicks "sign enrollment code" (prefilled role + instance) -> the panel moves under that row: role (the project's choices, data-ec-roles),
// instance, validity -> POST the single declared write path (body data-ec-write-path) with stage=preview -> show what the code will bind
// (the gate's own dry-run, same implementation as `lybra roles enroll-code`) -> confirm with the session-bound preview token -> the code is
// shown once in a box with a copy button and the one line to hand over; it is never stored (no storage, no URL), a reload loses it for good
// (void and sign again: the exit commands are shown with it). All texts come from the page (body data-ec-texts JSON, board i18n channel);
// the server decides everything (Owner-only, role / instance rules, preview token, audit log, the signing itself).
(function () {
  'use strict';
  var body = document.body;
  if (!body || body.getAttribute('data-ec-owner') !== 'true') { return; }
  var writePath = body.getAttribute('data-ec-write-path');
  var defaultTtl = body.getAttribute('data-ec-default-ttl') || '';
  var panel = document.getElementById('ec-panel');
  var conf = {};
  try { conf = JSON.parse(body.getAttribute('data-ec-texts') || '{}'); } catch (e) { conf = {}; }
  if (!writePath || !panel) { return; }
  var heading = panel.querySelector('h3');
  var content = panel.querySelector('.ec-panel-body');
  var actions = panel.querySelector('.ec-panel-actions');

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
  function pre(lines, cls) { var p = el('pre', cls || 'ec-pre'); p.textContent = lines.join('\n'); return p; }
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
  }
  function close() {
    content.textContent = '';
    actions.textContent = '';
    panel.hidden = true;
  }
  function button(label, onClick) {
    var b = el('button', null, label);
    b.type = 'button';
    b.addEventListener('click', onClick);
    actions.appendChild(b);
    return b;
  }

  function failure(res) {
    var d = res.data || {};
    open(t('ec_js_fail_heading'));
    if (res.login) { para(t('ec_js_login')); } else {
      var key = 'ec_js_err_' + (d.error || '');
      para(Object.prototype.hasOwnProperty.call(conf, key) ? t(key) : t('ec_js_err_other', { code: d.error || res.status, message: d.message || '' }));
    }
    folded(t('ec_js_tech'), pre(['HTTP ' + res.status + ' ' + (d.error || ''), d.message || JSON.stringify(d)]));
    button(t('ec_js_cancel_btn'), close);
  }

  function field(labelText, input) {
    var p = el('p');
    var label = el('label', null, labelText + ' ');
    label.appendChild(input);
    p.appendChild(label);
    content.appendChild(p);
    return input;
  }

  function showForm(project, roles, role, instance) {
    open(t('ec_js_heading', { project: project }));
    var select = el('select');
    select.name = 'role';
    roles.forEach(function (name) {
      var opt = el('option', null, name);
      opt.value = name;
      if (name === role) { opt.selected = true; }
      select.appendChild(opt);
    });
    field(t('ec_js_role'), select);
    var inst = el('input');
    inst.type = 'text';
    inst.name = 'instance';
    inst.value = instance || '';
    field(t('ec_js_instance'), inst);
    var ttl = el('input');
    ttl.type = 'number';
    ttl.min = '1';
    ttl.name = 'ttl';
    ttl.placeholder = defaultTtl;
    field(t('ec_js_ttl', { ttl: defaultTtl }), ttl);
    button(t('ec_js_preview_btn'), function () {
      var payload = { workspace: project, role: select.value, instance: inst.value.trim() || null,
                      ttl: ttl.value.trim() || null, stage: 'preview' };
      if (!payload.role) { para(t('ec_js_role_missing')); return; }
      open(t('ec_js_working'));
      post(payload).then(function (res) {
        if (res.status !== 200 || !res.data.ok) { failure(res); return; }
        showPreview(project, payload, res.data);
      });
    });
    button(t('ec_js_cancel_btn'), close);
  }

  function showPreview(project, payload, data) {
    open(t('ec_js_preview_heading', { project: project }));
    var pv = data.preview || {};
    var params = data.params || {};
    para(t('ec_js_bind_role', { role: pv.role || params.role }));
    para(t('ec_js_bind_instance', { instance: pv.instance || params.instance || t('ec_js_any_instance') }));
    para(t('ec_js_bind_gate', { gate_url: pv.gate_url || params.gate_url }));
    para(t('ec_js_bind_root', { root: pv.governance_root || params.governance_root }));
    para(t('ec_js_bind_ttl', { ttl: pv.ttl_seconds }));
    para(t('ec_js_bind_actor', { actor: params.actor, surface: data.capture_surface }));
    folded(t('ec_js_tech'), pre([JSON.stringify(pv, null, 1)]));
    button(t('ec_js_confirm_btn'), function () {
      actions.textContent = '';
      para(t('ec_js_working'));
      var confirmPayload = {};
      Object.keys(payload).forEach(function (k) { confirmPayload[k] = payload[k]; });
      confirmPayload.stage = 'confirm';
      confirmPayload.preview_token = data.preview_token;
      post(confirmPayload).then(function (done) {
        if (done.status !== 200 || !done.data.ok) { failure(done); return; }
        showCode(done.data);
      });
    });
    button(t('ec_js_cancel_btn'), close);
  }

  function showCode(data) {
    var code = data.code || {};
    open(t('ec_js_done_heading'));
    content.appendChild(el('p', 'ec-once', t('ec_js_once')));
    para(t('ec_js_handoff'));
    var box = pre([code.paste_text || ''], 'ec-code');
    content.appendChild(box);
    para(t('ec_js_expires', { time: code.expires_at, code_id: code.code_id, fingerprint: code.fingerprint }));
    var exits = data.exits || {};
    folded(t('ec_js_exits'), pre([exits.revoke || '', exits.void || '']));
    var copy = button(t('ec_js_copy'), function () {
      var text = box.textContent;
      var done = function () { copy.textContent = t('ec_js_copied'); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { selectBox(box); });
      } else { selectBox(box); }
    });
    // the code lives only in this panel: "done" drops it and reloads the server-rendered checklist ("signed, awaiting redemption")
    button(t('ec_js_done_btn'), function () { close(); window.location.reload(); });
  }

  function selectBox(node) {
    var range = document.createRange();
    range.selectNodeContents(node);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  document.querySelectorAll('button.ec-sign').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var section = btn.closest('section.rp-ob-project');
      if (!section) { return; }
      var roles = [];
      try { roles = JSON.parse(section.getAttribute('data-ec-roles') || '[]'); } catch (e) { roles = []; }
      var role = btn.getAttribute('data-ec-role');
      if (roles.indexOf(role) < 0) { roles.unshift(role); }
      var row = btn.closest('li.rp-ob-item') || btn.parentNode;
      row.appendChild(panel);
      showForm(section.getAttribute('data-project'), roles, role, btn.getAttribute('data-ec-instance'));
      panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  });
}());
