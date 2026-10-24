const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/boutique/editorial-management.js'), 'utf8');

class Events {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }
  removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
  emit(type, details = {}) {
    const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...details };
    for (const listener of this.listeners.get(type) || []) listener(event);
    return event;
  }
}

function form(elements = [{ name: 'heading', type: 'text', value: 'Texte enregistré' }], attributes = {}) {
  const result = new Events();
  result.elements = elements;
  result.valid = true;
  result.status = { textContent: '', dataset: {} };
  result.hasAttribute = name => Object.hasOwn(attributes, name);
  result.getAttribute = name => attributes[name] || null;
  result.querySelector = selector => selector === '[data-editorial-dirty-status]' ? result.status : null;
  result.checkValidity = () => result.valid;
  return result;
}

function run(forms) {
  const window = new Events();
  window.name = '';
  const document = {
    querySelector: () => null,
    querySelectorAll: selector => selector === '[data-editorial-dirty-form]' ? forms : [],
  };
  vm.runInNewContext(source, { window, document, queueMicrotask });
  return { window, warned: () => window.emit('beforeunload').defaultPrevented };
}

const flush = () => new Promise(resolve => queueMicrotask(resolve));

test('unchanged forms do not warn; changing then restoring the text removes the warning', () => {
  const editor = form();
  const ui = run([editor]);
  assert.equal(ui.warned(), false);
  editor.elements[0].value = 'Texte corrigé';
  editor.emit('input');
  assert.equal(ui.warned(), true);
  assert.equal(editor.status.dataset.dirty, 'true');
  editor.elements[0].value = 'Texte enregistré';
  editor.emit('input');
  assert.equal(ui.warned(), false);
  assert.equal(editor.status.dataset.dirty, 'false');
});

test('photo selection and removal, checkbox changes, and form reset are tracked', async () => {
  const upload = { name: 'image', type: 'file', files: [] };
  const clear = { name: 'image-clear', type: 'checkbox', value: 'on', checked: false };
  const editor = form([upload, clear]);
  const ui = run([editor]);
  upload.files = [{ name: 'photo.png', size: 200, type: 'image/png', lastModified: 123 }];
  editor.emit('change');
  assert.equal(ui.warned(), true);
  upload.files = [];
  editor.emit('change');
  assert.equal(ui.warned(), false);
  clear.checked = true;
  editor.emit('change');
  assert.equal(ui.warned(), true);
  editor.emit('reset');
  clear.checked = false;
  await flush();
  assert.equal(ui.warned(), false);
});

test('an invalid submission or one cancelled by another handler keeps the warning', async () => {
  const editor = form();
  const ui = run([editor]);
  editor.elements[0].value = 'Brouillon';
  editor.emit('input');
  editor.valid = false;
  editor.emit('submit');
  await flush();
  assert.equal(ui.warned(), true);
  editor.valid = true;
  editor.addEventListener('submit', event => event.preventDefault());
  editor.emit('submit');
  await flush();
  assert.equal(ui.warned(), true);
});

test('successful same-tab save suppresses warnings, but browser back restores protection', async () => {
  const editor = form();
  const ui = run([editor]);
  editor.elements[0].value = 'Brouillon';
  editor.emit('input');
  assert.equal(ui.warned(), true);
  editor.emit('submit');
  await flush();
  assert.equal(ui.warned(), false);
  ui.window.emit('pageshow');
  assert.equal(ui.warned(), true);
});

test('submitting into another tab does not disable the current page warning', async () => {
  const editor = form();
  const ui = run([editor]);
  editor.elements[0].value = 'Brouillon';
  editor.emit('input');
  editor.emit('submit', { submitter: { getAttribute: name => name === 'formtarget' ? '_blank' : null } });
  await flush();
  assert.equal(ui.warned(), true);
});

test('server-rejected edits stay protected even when no additional field changes occur', async () => {
  const editor = form(undefined, { 'data-editorial-dirty-on-load': '' });
  const ui = run([editor]);
  assert.equal(ui.warned(), true);
  editor.emit('submit');
  await flush();
  assert.equal(ui.warned(), false);
});

test('hidden revision tokens and submit controls do not create false warnings', () => {
  const editor = form([
    { name: 'revision', type: 'hidden', value: '1' },
    { name: 'action', type: 'submit', value: 'preview' },
  ]);
  const ui = run([editor]);
  editor.elements[0].value = '2';
  editor.elements[1].value = 'publish';
  editor.emit('change');
  assert.equal(ui.warned(), false);
});

test('saving one form never removes the protection of a second unsaved form', async () => {
  const first = form();
  const second = form();
  const ui = run([first, second]);
  first.elements[0].value = 'Premier brouillon';
  second.elements[0].value = 'Second brouillon';
  first.emit('input');
  second.emit('input');
  first.emit('submit');
  await flush();
  assert.equal(ui.warned(), true);
  second.elements[0].value = 'Texte enregistré';
  second.emit('input');
  assert.equal(ui.warned(), false);
});

function overlapForm() {
  const element = () => ({ hidden: false, textContent: '', children: [],
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; } });
  const start = Object.assign(new Events(), { value: '2027-06-10T12:00' });
  const end = Object.assign(new Events(), { value: '2027-06-10T14:00' });
  const enabled = Object.assign(new Events(), { checked: true });
  const fields = { starts_at: start, ends_at: end, enabled };
  const warning = element();
  const list = element();
  const status = element();
  const editor = {
    dataset: { editorialOverlapUrl: '/gestion/pages-annonces/annonces/chevauchements/', editorialOverlapExclude: '12' },
    elements: { namedItem: name => fields[name] || null },
    querySelector: selector => ({ '[data-editorial-overlap-warning]': warning,
      '[data-editorial-overlap-list]': list, '[data-editorial-overlap-status]': status })[selector] || null,
  };
  const window = new Events();
  window.location = { href: 'https://example.test/gestion/pages-annonces/annonces/nouvelle/' };
  const requests = [];
  const timers = new Map();
  let nextTimer = 0;
  const document = {
    querySelector: selector => selector === '[data-editorial-announcement-form]' ? editor : null,
    querySelectorAll: () => [],
    createElement: () => element(),
  };
  vm.runInNewContext(source, {
    document, window, queueMicrotask, URL, AbortController,
    setTimeout(callback) { timers.set(++nextTimer, callback); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) { return new Promise(resolve => requests.push({ url, options, resolve })); },
  });
  return { start, end, enabled, warning, list, status, window, requests,
    runTimers() { for (const callback of timers.values()) callback(); timers.clear(); },
  };
}

const overlap = name => ({ name, starts_at: '2027-06-10T10:00:00+00:00',
  ends_at: '2027-06-10T11:00:00+00:00', priority_label: 'Normale' });

async function reply(request, overlaps) {
  request.resolve({ ok: true, json: async () => ({ overlaps, error: '' }) });
  await new Promise(resolve => setImmediate(resolve));
}

test('out-of-order overlap responses never replace a newer calendar result', async () => {
  const ui = overlapForm();
  ui.window.emit('pageshow');
  ui.runTimers();
  assert.equal(ui.requests.length, 1);
  ui.start.value = '2027-06-11T12:00';
  ui.start.emit('input');
  assert.equal(ui.requests[0].options.signal.aborted, true);
  ui.runTimers();
  await reply(ui.requests[1], [overlap('Nouvelle période')]);
  assert.equal(ui.list.children[0].children[0].textContent, 'Nouvelle période');
  await reply(ui.requests[0], [overlap('Ancienne période')]);
  assert.equal(ui.list.children[0].children[0].textContent, 'Nouvelle période');
});

test('turning an announcement off hides overlap notices and ignores an in-flight response', async () => {
  const ui = overlapForm();
  ui.window.emit('pageshow');
  ui.runTimers();
  ui.enabled.checked = false;
  ui.enabled.emit('change');
  assert.equal(ui.requests[0].options.signal.aborted, true);
  assert.equal(ui.warning.hidden, true);
  assert.equal(ui.status.textContent, '');
  await reply(ui.requests[0], [overlap('Annonce désormais sans effet')]);
  assert.equal(ui.warning.hidden, true);
  assert.equal(ui.status.textContent, '');
});

test('overlap names remain plain text and dates are displayed in Paris time', async () => {
  const ui = overlapForm();
  ui.window.emit('pageshow');
  ui.runTimers();
  const request = ui.requests[0];
  assert.equal(request.url.searchParams.get('exclude_pk'), '12');
  assert.equal(request.url.searchParams.get('starts_at'), '2027-06-10T12:00');
  assert.equal(request.options.credentials, 'same-origin');
  await reply(request, [overlap('<img src=x onerror=alert(1)>')]);
  assert.equal(ui.warning.hidden, false);
  const [name, period] = ui.list.children[0].children;
  assert.equal(name.textContent, '<img src=x onerror=alert(1)>');
  assert.deepEqual(name.children, []);
  assert.match(period.textContent, /12:00/);
  assert.match(period.textContent, /13:00/);
});
