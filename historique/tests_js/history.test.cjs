const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/historique/history.js'), 'utf8');

class Element {
  constructor() { this.listeners = {}; this.style = {}; this.dataset = {}; this.attributes = {}; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  emit(type, event = {}) { event.preventDefault ||= () => { event.prevented = true; }; (this.listeners[type] || []).forEach(fn => fn(event)); return event; }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
}
function setup({ loaded = true, access = true, stageWidth = 700 } = {}) {
  const window = new Element(), frame = new Element(), preview = new Element();
  const stage = { clientWidth: stageWidth }, viewport = { style: {} }, status = {};
  const desktop = new Element(), phone = new Element();
  desktop.dataset.historyDevice = 'desktop'; phone.dataset.historyDevice = 'phone';
  const devices = { hidden: true, querySelectorAll: () => [desktop, phone] };
  const link = new Element(), control = {}, img = new Element(), doc = new Element();
  link.attributes = { href: '/panier/ajouter/', target: '_blank' };
  doc.readyState = loaded ? 'complete' : 'loading'; doc.URL = '/gestion/historique/versions/1/apercu/contenu/';
  doc.body = {};
  const height = () => frame.style.width === '390px' ? 1600 : 800;
  Object.defineProperty(doc.body, 'scrollHeight', { get: height });
  doc.documentElement = {};
  Object.defineProperty(doc.documentElement, 'scrollHeight', { get: () => Math.max(height(), parseFloat(frame.style.height) || 0) });
  doc.querySelectorAll = selector => selector.startsWith('a[') ? [link] : selector === 'img' ? [img] : [control];
  Object.defineProperty(frame, 'contentDocument', { get: () => { if (!access) throw new Error('Cross-origin'); return doc; } });
  const elements = { '[data-history-frame]': frame, '[data-history-stage]': stage, '[data-history-viewport]': viewport, '[data-history-devices]': devices, '[data-history-preview-status]': status };
  preview.querySelector = selector => elements[selector];
  const frames = [];
  vm.runInNewContext(source, { window, document: { querySelectorAll: () => [preview] },
    getComputedStyle: () => ({ paddingLeft: '10px', paddingRight: '10px' }),
    requestAnimationFrame: fn => { frames.push(fn); return frames.length; } });
  const flush = () => { while (frames.length) frames.shift()(); };
  return { window, preview, frame, stage, viewport, devices, desktop, phone, status, link, control, doc, img, flush };
}

test('a cached already-loaded preview initializes without waiting for a missed load event', () => {
  const ui = setup(); ui.flush();
  assert.equal(ui.devices.hidden, false);
  assert.equal(ui.preview.dataset.previewReady, 'true');
  assert.match(ui.status.textContent, /Aucune modification/);
  ui.frame.emit('load'); ui.flush();
  assert.equal(ui.doc.listeners.submit.length, 1, 'A repeated load must not bind duplicate listeners');
});

test('an unloaded preview keeps fallback visible and initializes on load', () => {
  const ui = setup({ loaded: false });
  assert.equal(ui.devices.hidden, true);
  ui.frame.emit('load'); ui.flush();
  assert.equal(ui.devices.hidden, false);
});

test('preview links lose focus/navigation and controls cannot submit changes', () => {
  const ui = setup(); ui.flush();
  assert.equal(ui.link.attributes.href, undefined);
  assert.equal(ui.link.attributes.target, undefined);
  assert.equal(ui.link.attributes.tabindex, '-1');
  assert.equal(ui.link.attributes['aria-disabled'], 'true');
  assert.equal(ui.control.disabled, true);
  assert.equal(ui.doc.emit('submit').prevented, true);
  assert.equal(ui.doc.emit('click', { target: { closest: () => ({}) } }).prevented, true);
});

test('phone and desktop both fit the available width and switching back shrinks height', () => {
  const ui = setup(); ui.flush();
  assert.ok(Math.abs(parseFloat(ui.viewport.style.width) - 680) < .001);
  ui.phone.emit('click'); ui.flush();
  assert.equal(ui.frame.style.width, '390px');
  assert.equal(ui.frame.style.height, '1600px');
  assert.equal(ui.viewport.style.width, '390px');
  assert.equal(ui.phone.attributes['aria-pressed'], 'true');
  ui.desktop.emit('click'); ui.flush();
  assert.equal(ui.frame.style.height, '800px');
  assert.equal(ui.desktop.attributes['aria-pressed'], 'true');
  assert.equal(ui.phone.attributes['aria-pressed'], 'false');
});

test('small containers never receive a negative scale and large screens do not enlarge above native width', () => {
  const ui = setup({ stageWidth: 0 }); ui.flush();
  assert.equal(parseFloat(ui.viewport.style.width), 1);
  ui.stage.clientWidth = 2000; ui.window.emit('resize'); ui.flush();
  assert.equal(ui.viewport.style.width, '1160px');
  assert.equal(ui.frame.style.transform, 'scale(1)');
});

test('access loss is explained without breaking surrounding history controls', () => {
  const ui = setup({ access: false });
  assert.doesNotThrow(() => ui.frame.emit('load'));
  assert.equal(ui.devices.hidden, true);
  assert.match(ui.status.textContent, /Les différences restent consultables/);
});
