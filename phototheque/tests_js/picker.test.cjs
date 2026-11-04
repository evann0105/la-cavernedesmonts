const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, readIds, safeURL } = require('../static/phototheque/picker.js');
const tick = async () => { for (let i = 0; i < 3; i++) await new Promise(resolve => setImmediate(resolve)); };
const dataKey = key => key.replace(/^data-/, '').replace(/-([a-z])/g, (_, x) => x.toUpperCase());

class Event {
  constructor(type, values = {}) { this.type = type; Object.assign(this, values); this.defaultPrevented = false; }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.stopped = true; }
}
class Node {
  constructor(tag, doc) {
    this.tagName = tag.toUpperCase(); this.doc = doc; this.nodeType = 1; this.children = []; this.dataset = {};
    this.attributes = {}; this.listeners = {}; this.value = ''; this.hidden = false; this.disabled = false;
    this.required = false; this.files = []; this.textContent = ''; this.isConnected = true;
  }
  set innerHTML(_) { throw new Error('Untrusted HTML insertion'); }
  append(...nodes) { for (const node of nodes) { node.parentElement = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name.startsWith('data-')) this.dataset[dataKey(name)] = String(value); }
  getAttribute(name) { return name.startsWith('data-') ? this.dataset[dataKey(name)] ?? null : this.attributes[name] ?? this[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  matches(selector) {
    if (selector.includes(',')) return selector.split(',').some(part => this.matches(part.trim()));
    if (selector.includes(':not(:disabled)')) { if (this.disabled) return false; selector = selector.replace(':not(:disabled)', ''); }
    if (selector.startsWith('#')) return this.id === selector.slice(1);
    const tag = selector.match(/^[a-z]+/i)?.[0];
    if (tag && this.tagName !== tag.toUpperCase()) return false;
    for (const [, name, value] of selector.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)) {
      const actual = name === 'hidden' ? this.hidden ? '' : null : this.getAttribute(name);
      if (actual === null || actual === undefined || (value !== undefined && String(actual) !== value)) return false;
    }
    return true;
  }
  querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  dispatchEvent(event) {
    event.target ||= this;
    for (const callback of this.listeners[event.type] || []) callback(event);
    if (event.bubbles && !event.stopped) this.parentElement?.dispatchEvent(event);
    return !event.defaultPrevented;
  }
  focus() { this.doc.activeElement = this; }
  setCustomValidity(value) { this.validationMessage = value; }
}
function browser({multiple = false, limit = 12, selected = [], responses = []} = {}) {
  const doc = new Node('document'); doc.doc = doc;
  doc.createElement = tag => new Node(tag, doc);
  const add = (parent, tag, marker, attrs = {}) => {
    const node = doc.createElement(tag);
    if (marker) node.setAttribute('data-' + marker, '');
    Object.assign(node, attrs); parent.append(node); return node;
  };
  const body = add(doc, 'body'); doc.body = body;
  const form = add(body, 'form');
  const other = add(form, 'input', null, {name: 'description', value: 'Mon texte non enregistré'});
  let submits = 0; form.addEventListener('submit', () => submits++);
  function makeField(name = 'image', initial = selected) {
    const field = add(form, 'div', 'photo-library-field');
    field.dataset.libraryMultiple = String(multiple); field.dataset.libraryLimit = String(multiple ? limit : 1);
    const native = add(field, 'div', 'library-native');
    const input = add(native, 'input', null, {name, type: 'file', required: true});
    const hidden = add(field, 'input', 'library-value', {name: name + '-library', type: 'hidden', value: JSON.stringify(initial.map(x => x.id))});
    const trigger = add(field, 'button', 'library-open', {hidden: true});
    const list = add(field, 'ul', 'library-selection');
    const status = add(field, 'p', 'library-field-status');
    initial.forEach(photo => { const li = add(list, 'li', 'library-id'); li.dataset.libraryId = String(photo.id); li.dataset.libraryUrl = photo.url; add(li, 'span', null, {textContent: photo.title}); });
    return {field, input, hidden, trigger, list, status};
  }
  const widget = makeField();
  const modal = add(body, 'dialog', 'photo-library-picker'); modal.dataset.libraryUrl = '/gestion/photos/photos.json';
  modal.showModal = () => { modal.open = true; }; modal.close = () => { modal.open = false; };
  const heading = add(modal, 'h2', null, {id: 'photo-library-title'});
  const close = add(modal, 'button', 'library-close');
  const search = add(modal, 'form', 'library-search');
  const query = add(search, 'input', null, {name: 'q'});
  const folder = add(search, 'select', null, {name: 'folder'});
  add(search, 'button', null, {type: 'submit'});
  const status = add(modal, 'p', 'library-status');
  const results = add(modal, 'ul', 'library-results');
  const previous = add(modal, 'button', 'library-previous');
  const next = add(modal, 'button', 'library-next');
  const page = add(modal, 'span', 'library-page');
  const preview = add(modal, 'aside', 'library-preview');
  const previewImage = add(preview, 'img', 'library-preview-image');
  const previewTitle = add(preview, 'h3', 'library-preview-title');
  add(preview, 'p', 'library-preview-size');
  const toggle = add(preview, 'button', 'library-toggle');
  const count = add(modal, 'p', 'library-count');
  const confirm = add(modal, 'button', 'library-confirm');
  const pending = [...responses], requests = [];
  let observer;
  const timers = new Map();
  const win = {location: {href: 'http://testserver/gestion/produits/nouveau/'}, Event, CustomEvent: Event, AbortController,
    Option: function(text, value) { const option = doc.createElement('option'); option.textContent = text; option.value = value; return option; },
    MutationObserver: class { constructor(cb) { observer = cb; } observe() {} },
    setTimeout(fn, ms) { const key = Symbol(); timers.set(key, {fn, ms}); return key; }, clearTimeout(key) { timers.delete(key); },
    fetch(url, options) {
      requests.push({url, options});
      const value = pending.shift() || pageData();
      if (value instanceof Promise) return value;
      return Promise.resolve(value.response || {ok: true, status: 200, headers: {get: () => 'application/json'}, json: async () => value});
    }};
  const api = mount(doc, win);
  const click = node => node.dispatchEvent(new Event('click', {bubbles: true}));
  return {doc, win, api, modal, widget, other, requests, results, query, folder, search, status, previewTitle, previewImage,
    close, previous, next, page, toggle, count, confirm, heading, pending, timers, click,
    get submits() { return submits; },
    async open() { click(widget.trigger); await tick(); },
    clone(name) { const result = makeField(name, []); observer([{addedNodes: [result.field]}]); return result; }};
}
const photo = (id = 1, title = 'Montagne') => ({id, title, url: `/media/photo-${id}.webp`, thumb: `/media/thumb-${id}.webp`, width: 600, height: 400});
function pageData(photos = [photo()], page = 1, pages = 1) {
  return {results: photos, folders: [{id: 2, name: 'Accueil'}], pagination: {page, pages, total: photos.length, has_previous: page > 1, has_next: page < pages}};
}

test('only bounded identifiers and same-origin HTTP images are accepted', () => {
  assert.deepEqual(readIds('["1","1","2"]'), ['1','2']);
  assert.deepEqual(readIds('{"id":1}'), []);
  assert.deepEqual(readIds('["../image","javascript:evil"]'), []);
  assert.equal(safeURL('javascript:alert(1)', 'https://shop.test/'), '');
  assert.equal(safeURL('https://other.test/image.png', 'https://shop.test/'), '');
  assert.equal(safeURL('/media/x.webp', 'https://shop.test/'), 'https://shop.test/media/x.webp');
});
test('clicking a thumbnail selects immediately, including the mobile one-column flow', async () => {
  const b = browser(); await b.open();
  assert.equal(b.confirm.disabled, true);
  b.click(b.results.querySelector('[data-photo-id]'));
  assert.equal(b.confirm.disabled, false);
  assert.equal(b.results.querySelector('[data-photo-id]').getAttribute('aria-pressed'), 'true');
  b.click(b.confirm);
  assert.equal(b.widget.hidden.value, '["1"]');
  assert.equal(b.widget.input.required, false);
  assert.equal(b.widget.input.dataset.libraryPreview, 'http://testserver/media/photo-1.webp');
  assert.equal(b.other.value, 'Mon texte non enregistré');
  assert.equal(b.submits, 0);
  assert.equal(b.doc.activeElement, b.widget.trigger);
  assert.equal(b.modal.open, false);
  assert.equal(b.requests[0].options.method, 'GET');
});
test('Escape abandons pending changes and restores focus without altering the form', async () => {
  const b = browser(); await b.open(); b.click(b.results.querySelector('[data-photo-id]'));
  b.modal.dispatchEvent(new Event('cancel'));
  assert.equal(b.widget.hidden.value, '[]');
  assert.equal(b.other.value, 'Mon texte non enregistré');
  assert.equal(b.doc.activeElement, b.widget.trigger);
  assert.equal(b.submits, 0);
});
test('multiple selection survives folder changes and pagination', async () => {
  const b = browser({multiple: true, responses: [pageData([photo(1)], 1, 2), pageData([photo(2)], 2, 2)]});
  await b.open(); b.click(b.results.querySelector('[data-photo-id]'));
  b.click(b.next); await tick(); b.click(b.results.querySelector('[data-photo-id]'));
  b.click(b.confirm);
  assert.equal(b.widget.hidden.value, '["1","2"]');
  assert.match(b.requests[1].url, /page=2/);
});
test('existing uploads count toward the multi-photo limit', async () => {
  const b = browser({multiple: true, limit: 2, responses: [pageData([photo(1), photo(2)])]});
  b.widget.input.files = [{name: 'nouvelle.png'}];
  await b.open(); const cards = b.results.querySelectorAll('[data-photo-id]');
  b.click(cards[0]); b.click(cards[1]);
  assert.equal(b.api.selection.size, 1);
  assert.match(b.status.textContent, /limite de 2/);
  b.click(b.confirm);
  assert.equal(b.widget.input.files.length, 1);
});
test('failed-form selections can be removed and restore native required validation', () => {
  const b = browser({selected: [photo()]});
  assert.equal(b.widget.input.required, false);
  b.click(b.widget.list.querySelector('[data-library-remove]'));
  assert.equal(b.widget.hidden.value, '[]');
  assert.equal(b.widget.input.required, true);
  assert.equal(b.widget.input.dataset.libraryPreview, '');
});
test('dynamic formset fields get independent picker controls and names', async () => {
  const b = browser(); const clone = b.clone('colors-5-image');
  assert.equal(clone.trigger.hidden, false);
  b.click(clone.trigger); await tick(); b.click(b.results.querySelector('[data-photo-id]')); b.click(b.confirm);
  assert.equal(clone.hidden.name, 'colors-5-image-library');
  assert.equal(clone.hidden.value, '["1"]');
  assert.equal(b.widget.hidden.value, '[]');
  assert.equal(b.doc.activeElement, clone.trigger);
});
test('API titles are text only and remote image URLs cannot create cards', async () => {
  const title = '<img src=x onerror=alert(1)>';
  const b = browser({responses: [pageData([photo(1, title), {...photo(2), url:'https://evil.test/photo'}])]});
  await b.open(); assert.equal(b.results.children.length, 1);
  b.click(b.results.querySelector('[data-photo-id]'));
  assert.equal(b.previewTitle.textContent, title);
  assert.equal(b.previewTitle.children.length, 0);
});
test('auth expiry is explained without submitting or navigating away', async () => {
  const b = browser({responses: [{response: {status: 403, headers: {get: () => 'text/html'}}}]});
  await b.open();
  assert.match(b.status.textContent, /session a expiré/);
  assert.equal(b.win.location.href, 'http://testserver/gestion/produits/nouveau/');
  assert.equal(b.other.value, 'Mon texte non enregistré');
  assert.equal(b.submits, 0);
});
test('Tab and Shift-Tab stay within the dialog', async () => {
  const b = browser(); await b.open(); b.click(b.results.querySelector('[data-photo-id]'));
  b.confirm.focus();
  const tab = new Event('keydown', {key:'Tab'}); b.modal.dispatchEvent(tab);
  assert.equal(tab.defaultPrevented, true); assert.equal(b.doc.activeElement, b.close);
  const reverse = new Event('keydown', {key:'Tab', shiftKey:true}); b.modal.dispatchEvent(reverse);
  assert.equal(reverse.defaultPrevented, true); assert.equal(b.doc.activeElement, b.confirm);
});
