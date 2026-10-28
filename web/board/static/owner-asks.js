// AIPOS-F248: home "needs you" one click. Every button of an ".hm-ask" carries the declared write path and its request body
// (data-ask-path / data-ask-payload, rendered by the server: the inbox ruling / spot check route of write exception 3, or the
// envelope / project-grant signing route of write exception 1). Click -> (sentence input when the option needs one) -> POST
// stage=preview -> one confirm line in place -> POST stage=confirm with the session-bound preview token -> the ask turns into
// "recorded · advisor told" in place. Texts come from the page (board i18n); every check is on the server (Owner session, Origin,
// one-time preview token bound to the session and the parameter digest incl. the item hash, board audit log). No credentials here.
(function () {
  'use strict';
  var body = document.body;
  if (!body || body.getAttribute('data-asks-owner') !== 'true') { return; }
  var conf = {};
  try { conf = JSON.parse(body.getAttribute('data-asks-texts') || '{}'); } catch (e) { conf = {}; }

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
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function bar(ask) {
    var line = ask.querySelector(':scope > .hm-what > .hm-bar-line');
    line.textContent = '';
    line.hidden = false;
    return line;
  }

  function busy(ask, on) {
    Array.prototype.forEach.call(ask.querySelectorAll(':scope > .hm-acts button'), function (b) { b.disabled = on; });
  }

  function failed(ask, res) {
    var d = res.data || {};
    var line = bar(ask);
    line.setAttribute('data-state', 'failed');
    line.appendChild(el('span', 'hm-bar-text', res.login ? t('hm_js_login')
      : t('hm_js_fail', { message: d.message || d.error || ('HTTP ' + res.status) })));
    busy(ask, false);
  }

  function done(ask, button) {
    ask.classList.add('done');
    ask.setAttribute('data-done', 'true');
    var acts = ask.querySelector(':scope > .hm-acts');
    acts.textContent = button.getAttribute('data-ask-done') || '';
    var line = ask.querySelector(':scope > .hm-what > .hm-bar-line');
    line.hidden = true;
  }

  function confirmStep(ask, button, path, payload, token) {
    var line = bar(ask);
    line.setAttribute('data-state', 'working');
    line.appendChild(el('span', 'hm-bar-text', t('hm_js_working')));
    post(path, Object.assign({}, payload, { stage: 'confirm', preview_token: token })).then(function (res) {
      if (res.status !== 200 || !res.data.ok) { failed(ask, res); return; }
      done(ask, button);
    });
  }

  function preview(ask, button, words) {
    var path = button.getAttribute('data-ask-path');
    var payload = {};
    try { payload = JSON.parse(button.getAttribute('data-ask-payload') || '{}'); } catch (e) { payload = {}; }
    if (button.getAttribute('data-ask-words') !== 'none') { payload.owner_words = words || ''; }
    busy(ask, true);
    var line = bar(ask);
    line.setAttribute('data-state', 'working');
    line.appendChild(el('span', 'hm-bar-text', t('hm_js_working')));
    post(path, Object.assign({}, payload, { stage: 'preview' })).then(function (res) {
      if (res.status !== 200 || !res.data.ok) { failed(ask, res); return; }
      var confirmLine = fill(button.getAttribute('data-ask-confirm') || '',
        { words: words ? t('hm_js_words_part', { words: words }) : '' });
      var row = bar(ask);
      row.setAttribute('data-state', 'confirm');
      row.appendChild(el('span', 'hm-bar-text', confirmLine));
      var ok = el('button', 'hm-btn hm-btn-small', t('hm_js_confirm'));
      ok.type = 'button';
      ok.addEventListener('click', function () { confirmStep(ask, button, path, payload, res.data.preview_token); });
      var cancel = el('button', 'hm-alt', t('hm_js_cancel'));
      cancel.type = 'button';
      cancel.addEventListener('click', function () { row.hidden = true; busy(ask, false); });
      row.appendChild(ok);
      row.appendChild(cancel);
    });
  }

  function askWords(ask, button) {
    var required = button.getAttribute('data-ask-words') === 'required';
    var row = bar(ask);
    row.setAttribute('data-state', 'words');
    var input = el('input', 'hm-words');
    input.type = 'text';
    input.maxLength = 200;
    input.placeholder = t(required ? 'hm_js_words_required' : 'hm_js_words_optional');
    var next = el('button', 'hm-btn hm-btn-small', t('hm_js_next'));
    next.type = 'button';
    next.addEventListener('click', function () {
      var words = String(input.value || '').trim();
      if (required && !words) { input.focus(); return; }
      preview(ask, button, words);
    });
    var cancel = el('button', 'hm-alt', t('hm_js_cancel'));
    cancel.type = 'button';
    cancel.addEventListener('click', function () { row.hidden = true; });
    row.appendChild(input);
    row.appendChild(next);
    row.appendChild(cancel);
    input.focus();
  }

  Array.prototype.forEach.call(document.querySelectorAll('.hm-ask[data-item]'), function (ask) {
    Array.prototype.forEach.call(ask.querySelectorAll(':scope > .hm-acts button[data-ask-path]'), function (button) {
      button.addEventListener('click', function () {
        if (button.getAttribute('data-ask-words') === 'required') { askWords(ask, button); return; }
        preview(ask, button, '');
      });
    });
  });
}());
