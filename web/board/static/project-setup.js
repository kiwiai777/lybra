// AIPOS-F272: board write exceptions 6 "first project grant" and 7 "execution mode / pi model" on the onboarding page. Next to an open
// checklist row the Owner clicks a button -> the panel moves under that row with a form prefilled from the project's current state
// (section data-ps-setup JSON: grant prefill / execution candidates / pi model candidates) -> POST the declared write path with
// stage=preview -> show what will be written (the grant revision in full, or the project.json diff; same implementation as the CLI)
// -> confirm with the session-bound preview token -> the result, then "done" reloads the server-rendered checklist.
// All texts come from the page (body data-ps-texts JSON, board i18n channel); the server decides everything (Owner-only, the CLI's own
// checks, preview token, audit log, the write and its landing). Nothing is kept in browser storage.
(function () {
  'use strict';
  var body = document.body;
  if (!body || body.getAttribute('data-ps-owner') !== 'true') { return; }
  var paths = { grant: body.getAttribute('data-ps-grant-path'), execution: body.getAttribute('data-ps-execution-path') };
  paths.model = paths.execution;
  var panel = document.getElementById('ps-panel');
  var conf = {};
  try { conf = JSON.parse(body.getAttribute('data-ps-texts') || '{}'); } catch (e) { conf = {}; }
  if (!paths.grant || !paths.execution || !panel) { return; }
  var heading = panel.querySelector('h3');
  var content = panel.querySelector('.ps-panel-body');
  var actions = panel.querySelector('.ps-panel-actions');

  function fill(template, values) {
    return String(template || '').replace(/\{(\w+)\}/g, function (all, name) {
      return Object.prototype.hasOwnProperty.call(values || {}, name) ? String(values[name]) : all;
    });
  }
  function t(key, values) { return fill(conf[key] || key, values); }

  function post(path, payload) {
    return fetch(path, {
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
  function para(text, cls) { content.appendChild(el('p', cls || null, text)); }
  function pre(lines) { var p = el('pre', 'ps-pre'); p.textContent = lines.join('\n'); return p; }
  function folded(summary, node, open) {
    var d = el('details', 'rp-tech');
    if (open) { d.open = true; }
    d.appendChild(el('summary', null, summary));
    d.appendChild(node);
    content.appendChild(d);
  }
  function show(title) {
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
  function field(labelText, input) {
    var p = el('p');
    var label = el('label', null, labelText + ' ');
    label.appendChild(input);
    p.appendChild(label);
    content.appendChild(p);
    return input;
  }
  function textInput(name, value) {
    var input = el('input');
    input.type = 'text';
    input.name = name;
    input.value = value || '';
    return input;
  }
  function list(value) { return (value || []).join(', '); }
  function split(value) { return String(value || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean); }
  function checkboxes(legend, name, choices, checked) {
    var fs = el('fieldset');
    fs.appendChild(el('legend', null, legend));
    choices.forEach(function (choice) {
      var label = el('label', null, ' ' + choice + ' ');
      var box = el('input');
      box.type = 'checkbox';
      box.name = name;
      box.value = choice;
      box.checked = checked.indexOf(choice) >= 0;
      label.insertBefore(box, label.firstChild);
      fs.appendChild(label);
    });
    content.appendChild(fs);
    return function () {
      return Array.prototype.map.call(fs.querySelectorAll('input:checked'), function (b) { return b.value; });
    };
  }
  function picker(labelText, name, options, value, allowNone) {
    if (!options.length) {
      para(t('ps_js_no_candidates'));
      return field(labelText, textInput(name, value));
    }
    var select = el('select');
    select.name = name;
    if (allowNone) {
      var none = el('option', null, t('ps_js_none_option'));
      none.value = '';
      select.appendChild(none);
    }
    options.forEach(function (opt) {
      var o = el('option', null, opt);
      o.value = opt;
      if (opt === value) { o.selected = true; }
      select.appendChild(o);
    });
    return field(labelText, select);
  }

  function failure(project, res) {
    var d = res.data || {};
    show(t('ps_js_fail_heading'));
    if (res.login) { para(t('ps_js_login')); } else {
      var key = 'ps_js_err_' + (d.error || '');
      para(Object.prototype.hasOwnProperty.call(conf, key) ? t(key) : t('ps_js_err_other', { code: d.error || res.status }));
      if (d.message) { para(t('ps_js_server_says', { message: d.message }), 'ps-server'); }
    }
    folded(t('ps_js_tech'), pre(['HTTP ' + res.status + ' ' + (d.error || ''), JSON.stringify(d, null, 1)]));
    button(t('ps_js_cancel_btn'), close);
  }

  function run(action, project, payload) {
    show(t('ps_js_working'));
    post(paths[action], payload).then(function (res) {
      if (res.status !== 200 || !res.data.ok) { failure(project, res); return; }
      showPreview(action, project, payload, res.data);
    });
  }

  function showPreview(action, project, payload, data) {
    show(t('ps_js_preview_heading', { project: project }));
    var params = data.params || {};
    if (action === 'grant') {
      (data.summary_lines || []).forEach(function (line) { para(line); });
      if (data.authorization_markdown) { folded(t('ps_js_full_text'), pre([data.authorization_markdown]), true); }
    } else {
      var plan = data.plan || {};
      content.appendChild(el('p', null, t('ps_js_diff')));
      content.appendChild(pre([plan.diff || '']));
      if (plan.affected && plan.affected.length) {
        folded(t('ps_js_affected'), pre(plan.affected.map(function (a) { return JSON.stringify(a); })), true);
      }
    }
    para(t('ps_js_actor', { actor: params.actor || '', surface: data.capture_surface || '' }));
    folded(t('ps_js_tech'), pre([JSON.stringify(data.dry_run || data.plan || {}, null, 1)]));
    button(t('ps_js_confirm_btn'), function () {
      actions.textContent = '';
      para(t('ps_js_working'));
      var confirmPayload = {};
      Object.keys(payload).forEach(function (k) { confirmPayload[k] = payload[k]; });
      confirmPayload.stage = 'confirm';
      confirmPayload.preview_token = data.preview_token;
      post(paths[action], confirmPayload).then(function (done) {
        if (done.status !== 200 || !done.data.ok) { failure(project, done); return; }
        showDone(project, done.data);
      });
    });
    button(t('ps_js_cancel_btn'), close);
  }

  function showDone(project, data) {
    show(t('ps_js_done_heading', { project: project }));
    var landing = data.landing || {};
    para(landing.landed === false ? t('ps_js_not_landed') : t('ps_js_landed'));
    if (data.plan && data.plan.diff) { folded(t('ps_js_diff'), pre([data.plan.diff])); }
    folded(t('ps_js_tech'), pre([data.landing_line || '', JSON.stringify(data.signed || data.plan || {}, null, 1)]));
    button(t('ps_js_done_btn'), function () { close(); window.location.reload(); });
  }

  function grantForm(project, setup) {
    var g = setup.grant || {};
    var choices = g.choices || {};
    show(t('ps_js_heading_grant', { project: project }));
    para(t('ps_js_list_hint'));
    var lanes = field(t('ps_js_lanes'), textInput('lanes', list(g.lanes)));
    var modes = field(t('ps_js_task_modes'), textInput('task_modes', list(g.task_modes)));
    var drivers = field(t('ps_js_drivers'), textInput('drivers', list(g.drivers)));
    var hosts = field(t('ps_js_hosts'), textInput('hosts', list(g.hosts)));
    var launch = checkboxes(t('ps_js_launch'), 'launch_harnesses', choices.launch_harnesses || [], g.launch_harnesses || []);
    var zones = checkboxes(t('ps_js_zones'), 'zones_open', choices.zones || [], g.zones_open || []);
    var max = el('input');
    max.type = 'number';
    max.min = '1';
    max.name = 'max_tasks';
    max.value = g.max_tasks || '';
    field(t('ps_js_max_tasks'), max);
    var expires = field(t('ps_js_expires_at'), textInput('expires_at', g.expires_at));
    if (g.cli) { folded(t('ps_js_cli'), pre([g.cli])); }
    button(t('ps_js_preview_btn'), function () {
      run('grant', project, { workspace: project, stage: 'preview', lanes: split(lanes.value), task_modes: split(modes.value),
        drivers: split(drivers.value), hosts: split(hosts.value), launch_harnesses: launch(), zones_open: zones(),
        max_tasks: max.value.trim(), expires_at: expires.value.trim() });
    });
    button(t('ps_js_cancel_btn'), close);
  }

  function executionForm(project, setup) {
    var ex = setup.execution || {};
    var slots = ex.slots || {};
    var current = ex.current || {};
    show(t('ps_js_heading_execution', { project: project }));
    if (ex.error) { para(t('ps_js_server_says', { message: ex.error }), 'ps-server'); }
    var sub = picker(t('ps_js_subagent'), 'subagent_executor', slots.subagent_executor || [], current.subagent_executor, false);
    var aud = picker(t('ps_js_auditor'), 'auditor', slots.auditor || [], current.auditor, false);
    var pie = picker(t('ps_js_pi_executor'), 'pi_executor', slots.pi_executor || [], current.pi_executor, true);
    button(t('ps_js_preview_btn'), function () {
      run('execution', project, { workspace: project, stage: 'preview', action: 'execution', subagent_executor: sub.value.trim(),
        auditor: aud.value.trim(), pi_executor: pie.value.trim() || null });
    });
    button(t('ps_js_cancel_btn'), close);
  }

  function modelForm(project, setup, role) {
    var m = setup.model || {};
    show(t('ps_js_heading_model', { project: project }));
    var select = el('select');
    select.name = 'role';
    var dflt = el('option', null, t('ps_js_role_default'));
    dflt.value = '';
    select.appendChild(dflt);
    (m.roles || []).forEach(function (r) {
      var o = el('option', null, r);
      o.value = r;
      if (r === role) { o.selected = true; }
      select.appendChild(o);
    });
    field(t('ps_js_role'), select);
    var model;
    if ((m.models || []).length) {
      model = picker(t('ps_js_model'), 'model', m.models, null, false);
    } else {
      para(t('ps_js_model_manual'));
      model = field(t('ps_js_model'), textInput('model', ''));
    }
    var ref = field(t('ps_js_owner_ref'), textInput('owner_ref', ''));
    button(t('ps_js_preview_btn'), function () {
      run('model', project, { workspace: project, stage: 'preview', action: 'model', model: model.value.trim(),
        role: select.value || null, owner_ref: ref.value.trim() || null });
    });
    button(t('ps_js_cancel_btn'), close);
  }

  document.querySelectorAll('button.ps-open').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var section = btn.closest('section.rp-ob-project');
      if (!section) { return; }
      var setup = {};
      try { setup = JSON.parse(section.getAttribute('data-ps-setup') || '{}'); } catch (e) { setup = {}; }
      var project = section.getAttribute('data-project');
      var row = btn.closest('li.rp-ob-item') || btn.parentNode;
      row.appendChild(panel);
      var action = btn.getAttribute('data-ps-action');
      if (action === 'grant') { grantForm(project, setup); }
      else if (action === 'execution') { executionForm(project, setup); }
      else if (action === 'model') { modelForm(project, setup, btn.getAttribute('data-ps-role')); }
      panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  });
}());
