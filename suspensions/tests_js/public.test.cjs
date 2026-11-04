const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/suspensions/public.js'), 'utf8');
const START = Date.parse('2026-11-03T10:00:00Z');
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };
const state = (changes = {}) => ({ paused: false, message: '', resume_label: '', next_change_at: null,
  checked_at: new Date(START).toISOString(), revision: 1, ...changes });

function browser(initial, responses = [initial]) {
  let elapsed = 0, id = 0, observer;
  const timers = new Map(), events = {}, windowEvents = {}, requests = [], responsesLeft = [...responses];
  const fieldset = { disabled: initial.paused }, stockButton = { disabled: true };
  const opened = { hidden: initial.paused }, paused = { hidden: !initial.paused };
  const message = { textContent: initial.message }, resume = { textContent: initial.resume_label };
  const announcement = { textContent: '' }, title = { textContent: '' };
  const input = { value: 'MON-CODE-EN-COURS' }, main = {};
  const banner = { hidden: !initial.paused, dataset: { ordersStatusUrl: '/etat-commandes/' }, querySelector: () => title };
  const navigator = { onLine: true };
  const document = { hidden: false,
    getElementById(name) { return name === 'order-pause-state' ? { textContent: JSON.stringify(initial) } : name === 'contenu' ? main : null; },
    querySelector(selector) { return selector === '[data-orders-banner]' ? banner : selector === '[data-orders-announcement]' ? announcement : null; },
    querySelectorAll(selector) { return ({ '[data-orders-fieldset]': [fieldset], '[data-orders-open]': [opened],
      '[data-orders-paused]': [paused], '[data-orders-message]': [message], '[data-orders-resume]': [resume] })[selector] || []; },
    addEventListener(name, callback) { (events[name] ||= []).push(callback); },
  };
  const window = { location: { origin: 'http://testserver' }, addEventListener(name, callback) { (windowEvents[name] ||= []).push(callback); } };
  const api = { fieldset, stockButton, opened, paused, banner, input, message, resume, announcement,
    timers, requests, navigator, document, responsesLeft, title,
    async advance(ms) {
      elapsed += ms;
      const due = [...timers].filter(([, entry]) => entry.when <= elapsed).sort((a,b) => a[1].when-b[1].when);
      for (const [key, entry] of due) if (timers.delete(key)) entry.fn();
      await flush();
    },
    async fire(name, data = {}) { for (const cb of [...(events[name] || []), ...(windowEvents[name] || [])]) cb(data); await flush(); },
    async replaceCart() { observer([{ addedNodes: [{ nodeType: 1, matches: () => true, querySelector: () => null }] }]); await flush(); },
  };
  vm.runInNewContext(source, { document, window, navigator, performance: { now: () => elapsed }, URL, Date,
    AbortController, MutationObserver: class { constructor(callback) { observer = callback; } observe() {} },
    setTimeout(fn, ms) { const key = ++id; timers.set(key, { fn, ms, when: elapsed + ms }); return key; },
    clearTimeout(key) { timers.delete(key); },
    fetch(url, options) {
      requests.push({ url, options });
      const next = responsesLeft.shift();
      assert.notEqual(next, undefined, 'Unexpected network polling');
      if (next instanceof Error) return Promise.reject(next);
      if (next === 'pending') return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted'))));
      return Promise.resolve({ ok: true, redirected: false, json: async () => next });
    },
  });
  return api;
}

test('open catalogue performs one read, no perpetual polling or form mutations', async () => {
  const b = browser(state()); await flush();
  assert.equal(b.requests.length, 1);
  assert.equal(b.requests[0].options.method, 'GET');
  assert.equal(b.requests[0].options.cache, 'no-store');
  assert.equal(b.timers.size, 0);
  assert.equal(b.input.value, 'MON-CODE-EN-COURS');
  assert.equal(b.stockButton.disabled, true);
});

test('a scheduled start and end update cached pages using server-relative monotonic time', async () => {
  const start = state({ next_change_at: new Date(START + 20000).toISOString() });
  const active = state({ paused: true, message: 'Vacances', resume_label: 'Reprise bientôt',
    checked_at: new Date(START + 20100).toISOString(), next_change_at: new Date(START + 40000).toISOString() });
  const ended = state({ checked_at: new Date(START + 40100).toISOString() });
  const b = browser(start, [start, active, ended]); await flush();
  await b.advance(20100);
  assert.equal(b.fieldset.disabled, true); assert.equal(b.opened.hidden, true); assert.equal(b.banner.hidden, false);
  assert.equal(b.message.textContent, 'Vacances');
  await b.advance(20000);
  assert.equal(b.fieldset.disabled, false); assert.equal(b.banner.hidden, true);
  assert.match(b.announcement.textContent, /de nouveau disponibles/);
  assert.equal(b.stockButton.disabled, true); assert.equal(b.timers.size, 0);
  assert.equal(b.input.value, 'MON-CODE-EN-COURS');
});

test('hidden and offline pages pause requests and refresh on resumption', async () => {
  const initial = state({ next_change_at: new Date(START + 20000).toISOString() });
  const active = state({ paused: true, message: 'Pause', resume_label: 'Reprise demain' });
  const b = browser(initial, [initial, active, active]); await flush();
  b.document.hidden = true; await b.fire('visibilitychange');
  await b.advance(30000); assert.equal(b.requests.length, 1);
  b.document.hidden = false; await b.fire('visibilitychange');
  assert.equal(b.fieldset.disabled, true); assert.equal(b.requests.length, 2);
  b.navigator.onLine = false; await b.fire('offline'); await b.fire('focus');
  assert.equal(b.requests.length, 2);
  b.navigator.onLine = true; await b.fire('online'); assert.equal(b.requests.length, 3);
});

test('long horizons only hop timers before the actual boundary', async () => {
  const initial = state({ next_change_at: new Date(START + 45 * 86400000).toISOString() });
  const b = browser(initial); await flush();
  assert.equal([...b.timers.values()][0].ms, 2147483000);
  await b.advance(2147483000);
  assert.equal(b.requests.length, 1);
  assert.ok([...b.timers.values()][0].ms <= 2147483000);
});

test('failed verification at a boundary blocks controls and stops retrying after bounded attempts', async () => {
  const initial = state({ next_change_at: new Date(START + 1000).toISOString() });
  const b = browser(initial, [initial, ...Array.from({length: 5}, () => new Error('offline'))]); await flush();
  await b.advance(1100);
  assert.equal(b.fieldset.disabled, true); assert.match(b.title.textContent, /Vérification/);
  for (const delay of [5000, 15000, 30000, 60000]) await b.advance(delay);
  assert.equal(b.requests.length, 6); assert.equal(b.timers.size, 0);
});

test('cart fragment replacement does not lose entered code or bypass pause', async () => {
  const paused = state({ paused: true, message: 'Pause', resume_label: 'Reprise lundi' });
  const b = browser(paused, [paused, paused]); await flush();
  b.fieldset.disabled = false; b.opened.hidden = false;
  await b.replaceCart();
  assert.equal(b.fieldset.disabled, true); assert.equal(b.opened.hidden, true);
  assert.equal(b.input.value, 'MON-CODE-EN-COURS');
});

test('a stale purchase click is blocked but quantity and code forms are unaffected', async () => {
  const b = browser(state({ paused: true, message: 'Pause', resume_label: 'Reprise lundi' })); await flush();
  let prevented = 0;
  await b.fire('submit', { target: { matches: () => true }, preventDefault() { prevented++; } });
  await b.fire('submit', { target: { matches: () => false }, preventDefault() { prevented++; } });
  await b.fire('click', { target: { closest: () => true }, preventDefault() { prevented++; } });
  assert.equal(prevented, 2);
});

test('fast hide/show during an aborted request resumes after abort settles', async () => {
  const b = browser(state(), ['pending', state({ paused: true, message: 'Pause', resume_label: 'Demain' })]);
  b.document.hidden = true;
  // Deliberately do not await between hide and show: abort rejection is pending.
  const hidden = b.fire('visibilitychange');
  b.document.hidden = false;
  const shown = b.fire('visibilitychange');
  await Promise.all([hidden, shown]);
  assert.equal(b.requests.length, 2); assert.equal(b.fieldset.disabled, true);
});
