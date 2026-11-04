const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/suspensions/dashboard.js'), 'utf8');
const instant = Date.parse('2027-07-15T08:00:00Z');
const iso = offset => new Date(instant + offset).toISOString();

function state(kind = 'open', checked = 0, next = null, overrides = {}) {
  return { schema: 1, state: kind, title: kind === 'paused' ? 'Commandes en pause' : 'Commandes ouvertes',
    badge: kind, schedule: 'Horaire ' + kind, message: '', checked_at: iso(checked),
    next_change_at: next === null ? null : iso(next), ...overrides };
}

function render(responses = [state()]) {
  let now = 0, id = 0;
  const timers = new Map(), calls = [], events = {}, documentEvents = {}, clicks = {};
  const fields = Object.fromEntries(['title', 'badge', 'schedule', 'message'].map(key => [key, { textContent: 'Connu ' + key, hidden: false }]));
  const button = { hidden: true, disabled: false, addEventListener: (name, fn) => { clicks[name] = fn; } };
  const feedback = { hidden: true, textContent: '' };
  const panel = { dataset: { state: 'scheduled', statusUrl: '/gestion/boutique/pause-commandes/statut-tableau/' },
    querySelector: selector => selector === '[data-pause-refresh]' ? button : selector === '[data-pause-feedback]' ? feedback : fields[selector.match(/data-pause-(\w+)/)[1]] };
  // The updater must never replace the panel or touch the other daily tasks.
  Object.defineProperty(panel, 'innerHTML', { set() { throw Error('panel replaced'); } });
  const document = { hidden: false, querySelector: selector => {
    assert.equal(selector, '[data-pause-dashboard]'); return panel;
  }, addEventListener: (name, fn) => { documentEvents[name] = fn; } };
  const window = { addEventListener: (name, fn) => { events[name] = fn; },
    setTimeout: (fn, delay) => { timers.set(++id, { fn, at: now + delay }); return id; },
    clearTimeout: key => timers.delete(key) };
  const fetch = async (url, options) => {
    calls.push({ url, options });
    if (!responses.length) throw Error('unexpected request');
    const next = responses.shift();
    if (next instanceof Error) throw next;
    if (typeof next === 'function') return next(options);
    return { ok: true, redirected: false, headers: { get: () => 'application/json; charset=utf-8' }, json: async () => next, ...next.response };
  };
  vm.runInNewContext(source, { document, window, fetch, performance: { now: () => now }, AbortController, Date });
  const flush = async () => { for (let step = 0; step < 12; step++) await Promise.resolve(); };
  const advance = async milliseconds => {
    const end = now + milliseconds;
    let count = 0;
    for (;;) {
      const due = [...timers].sort((a, b) => a[1].at - b[1].at).find(([, timer]) => timer.at <= end);
      if (!due) break;
      assert.ok(++count < 30, 'No permanent polling loop');
      now = due[1].at; timers.delete(due[0]); due[1].fn(); await flush();
    }
    now = end;
  };
  return { fields, button, feedback, panel, document, calls, timers, responses, flush, advance,
    click: () => clicks.click(), event: name => events[name]({ persisted: true }), visible: () => documentEvents.visibilitychange() };
}

test('refreshes only the indicator at start and end, then stops scheduling', async () => {
  const ui = render([state('scheduled', 0, 1000), state('paused', 1250, 3000), state('open', 3250)]);
  await ui.flush();
  assert.equal(ui.fields.title.textContent, 'Commandes ouvertes');
  await ui.advance(1250);
  assert.equal(ui.fields.title.textContent, 'Commandes en pause');
  await ui.advance(2000);
  assert.equal(ui.fields.title.textContent, 'Commandes ouvertes');
  assert.equal(ui.calls.length, 3);
  assert.equal(ui.timers.size, 0);
  await ui.advance(600000);
  assert.equal(ui.calls.length, 3);
  assert.equal(ui.calls[0].options.cache, 'no-store');
  assert.equal(ui.calls[0].options.credentials, 'same-origin');
});

test('hidden tabs wait for a return, and focus can catch a change made elsewhere', async () => {
  const ui = render([state('scheduled', 0, 1000), state('paused', 1500), state('open', 2000)]);
  await ui.flush(); ui.document.hidden = true;
  await ui.advance(1500);
  assert.equal(ui.calls.length, 1);
  ui.document.hidden = false; ui.visible(); await ui.flush();
  assert.equal(ui.panel.dataset.state, 'paused');
  ui.event('focus'); await ui.flush();
  assert.equal(ui.panel.dataset.state, 'open');
});

test('an expired login keeps the last known information with an explicit retry', async () => {
  const ui = render([{ response: { redirected: true, headers: { get: () => 'text/html' } } }]);
  await ui.flush();
  assert.equal(ui.fields.title.textContent, 'Connu title');
  assert.equal(ui.panel.dataset.stale, 'true');
  assert.match(ui.feedback.textContent, /Dernière information connue/);
  assert.equal(ui.button.hidden, false);
  assert.equal(ui.button.disabled, false);
  assert.equal(ui.timers.size, 0);
  ui.responses.push(state('paused')); ui.click(); await ui.flush();
  assert.equal(ui.panel.dataset.state, 'paused');
  assert.equal(ui.panel.dataset.stale, 'false');
  assert.equal(ui.feedback.textContent, 'Statut vérifié à l’instant.');
});

test('malformed or failed responses never erase the previous status or retry endlessly', async () => {
  const ui = render([new Error('offline'), { schema: 1, state: 'open' }, state('open')]);
  await ui.flush();
  assert.equal(ui.fields.title.textContent, 'Connu title');
  await ui.advance(100000); assert.equal(ui.calls.length, 1);
  ui.event('online'); await ui.flush();
  assert.equal(ui.fields.title.textContent, 'Connu title');
  assert.equal(ui.panel.dataset.stale, 'true');
  ui.click(); await ui.flush();
  assert.equal(ui.panel.dataset.stale, 'false');
});

test('public message text is never interpreted as markup', async () => {
  const message = '<img src=x onerror=alert(1)>';
  const ui = render([state('paused', 0, null, { message })]);
  await ui.flush();
  assert.equal(ui.fields.message.textContent, message);
  assert.equal(ui.fields.message.innerHTML, undefined);
  assert.equal(ui.fields.message.hidden, false);
});

test('a very distant boundary is split into timers without requesting repeatedly', async () => {
  const month = 32 * 86400000;
  const ui = render([state('scheduled', 0, month), state('paused', month + 250)]);
  await ui.flush();
  await ui.advance(26 * 86400000);
  assert.equal(ui.calls.length, 1);
  await ui.advance(6 * 86400000 + 250);
  assert.equal(ui.calls.length, 2);
  assert.equal(ui.panel.dataset.state, 'paused');
});

test('request timeout releases the button and simultaneous focus does not duplicate requests', async () => {
  const ui = render([options => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(Error('timeout')));
  }), state('open')]);
  ui.event('focus'); ui.visible();
  assert.equal(ui.calls.length, 1);
  await ui.advance(12000);
  assert.equal(ui.button.disabled, false);
  assert.equal(ui.panel.dataset.stale, 'true');
  ui.click(); await ui.flush();
  assert.equal(ui.calls.length, 2);
  assert.equal(ui.panel.dataset.state, 'open');
});
