// Browser-capability fixture for the shipped admin script. The fixture supplies
// DOM, FileList/DataTransfer and iframe APIs; all editing decisions run in the
// actual slides-editor.js, without posting a form or contacting a server.
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const scriptPath = path.join(__dirname, '../static/accueil/js/slides-editor.js');
const script = readFileSync(scriptPath, 'utf8');
const flush = async () => { for (let step = 0; step < 8; step += 1) await Promise.resolve(); };
const dataKey = name => name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());

class Element {
  constructor(tag = 'div', attributes = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = {};
    this.dataset = {};
    this.children = [];
    this.parentElement = null;
    this.listeners = new Map();
    this.hidden = false;
    this.checked = false;
    this.disabled = false;
    this.required = false;
    this.files = [];
    this._value = '';
    this.textContent = '';
    this.clientWidth = 640;
    this.style = {setProperty(name, value) { this[name] = String(value); }};
    const classes = new Set();
    this.classList = {
      contains: name => classes.has(name),
      add: name => classes.add(name),
      remove: name => classes.delete(name),
      toggle(name, force) { const enabled = force === undefined ? !classes.has(name) : force; if (enabled) classes.add(name); else classes.delete(name); return enabled; },
    };
    Object.entries(attributes).forEach(([name, value]) => this.setAttribute(name, value));
  }
  get value() { return this._value; }
  set value(value) { this._value = String(value); if (this.type === 'file' && value === '') this.files = []; }
  get className() { return this.getAttribute('class') || ''; }
  set className(value) { this.setAttribute('class', value); }
  get name() { return this.getAttribute('name') || ''; }
  set name(value) { this.setAttribute('name', value); }
  get id() { return this.getAttribute('id') || ''; }
  get type() { return this.getAttribute('type') || ''; }
  set type(value) { this.setAttribute('type', value); }
  get src() { return this.getAttribute('src') || ''; }
  set src(value) { this.setAttribute('src', value); }
  get srcdoc() { return this.getAttribute('srcdoc') || ''; }
  set srcdoc(value) { this.setAttribute('srcdoc', value); }
  get alt() { return this.getAttribute('alt') || ''; }
  set alt(value) { this.setAttribute('alt', value); }
  set innerHTML(_) { throw new Error('Dynamic HTML is not permitted outside the fixed empty-card template'); }
  get innerHTML() { return ''; }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name.startsWith('data-')) this.dataset[dataKey(name)] = String(value);
    if (name === 'class') String(value).split(/\s+/).filter(Boolean).forEach(item => this.classList.add(item));
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; if (name.startsWith('data-')) delete this.dataset[dataKey(name)]; }
  append(...nodes) { nodes.forEach(node => {
    if (node.tagName === 'FRAGMENT') { this.append(...node.children.slice()); return; }
    node.remove(); this.children.push(node); node.parentElement = this;
  }); }
  replaceChildren(...nodes) { this.children.slice().forEach(node => node.remove()); this.append(...nodes); }
  cloneNode(deep = false) {
    const clone = new Element(this.tagName.toLowerCase(), {...this.attributes});
    ['hidden', 'checked', 'disabled', 'required', 'textContent', '_value'].forEach(name => { clone[name] = this[name]; });
    if (deep) this.children.forEach(child => clone.append(child.cloneNode(true)));
    return clone;
  }
  insertBefore(node, before) {
    if (node === before) return;
    node.remove();
    const index = this.children.indexOf(before);
    if (index < 0) throw new Error('insertBefore target is not a child');
    this.children.splice(index, 0, node); node.parentElement = this;
  }
  remove() { if (this.parentElement) { this.parentElement.children = this.parentElement.children.filter(node => node !== this); this.parentElement = null; } }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  matches(selector) {
    return selector.split(',').some(part => {
      const value = part.trim();
      if (value.startsWith('.')) return this.classList.contains(value.slice(1));
      if (value.startsWith('#')) return this.id === value.slice(1);
      const attribute = value.match(/^\[([^\]$=]+)(\$?=)?(?:"([^"]*)")?\]$/);
      if (attribute) {
        const actual = this.getAttribute(attribute[1]);
        return attribute[2] === '$=' ? actual !== null && actual.endsWith(attribute[3]) : attribute[2] === '=' ? actual === attribute[3] : actual !== null;
      }
      return this.tagName.toLowerCase() === value;
    });
  }
  querySelectorAll(selector) {
    const result = [];
    const visit = node => node.children.forEach(child => { if (child.matches(selector)) result.push(child); visit(child); });
    visit(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  getBoundingClientRect() { return {bottom: 720}; }
  focus() { this.focused = true; }
  scrollIntoView(options) { this.scrolled = options; }
  addEventListener(type, callback, options = {}) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push({callback, once: Boolean(options?.once)});
  }
  removeEventListener(type, callback) { this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item.callback !== callback)); }
  async emit(type, properties = {}) {
    const event = {type, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...properties};
    let node = this;
    do {
      event.currentTarget = node;
      for (const item of [...(node.listeners.get(type) || [])]) {
        item.callback(event);
        if (item.once) node.removeEventListener(type, item.callback);
      }
      node = node.parentElement;
    } while (node && type !== 'load');
    await flush(); return event;
  }
}

const input = (prefix, name, type, value = '') => {
  const element = new Element('input', {name: `slides-${prefix}-${name}`, id: `id_slides-${prefix}-${name}`, type});
  element.value = value; return element;
};
function cardFixture(prefix, {id = '', alt = '', position = '', desktop = '', mobile = ''} = {}) {
  const card = new Element('article', {'data-slide-card': '', 'data-existing': String(Boolean(id)), 'data-saved-image': desktop, 'data-saved-mobile': mobile, 'data-saved-alt': alt});
  const values = {id: input(prefix, 'id', 'hidden', id), alt: input(prefix, 'alt', 'text', alt), position: input(prefix, 'position', 'number', position),
    image: input(prefix, 'image', 'file'), mobile_image: input(prefix, 'mobile_image', 'file'), DELETE: input(prefix, 'DELETE', 'checkbox')};
  values.image.setAttribute('data-slide-image', ''); values.mobile_image.setAttribute('data-slide-mobile', '');
  Object.values(values).forEach(element => card.append(element));
  for (const key of ['title', 'state', 'move', 'position-field', 'mobile-placeholder']) card.append(new Element('div', {[`data-slide-${key}`]: ''}));
  // The placeholder intentionally has its actual template selector.
  card.querySelector('[data-slide-mobile-placeholder]').setAttribute('data-mobile-placeholder', '');
  for (const action of ['up', 'down', 'undo']) card.append(new Element('button', {[`data-slide-${action}`]: '', type: 'button'}));
  for (const mode of ['desktop', 'mobile']) {
    const box = new Element('div', {'data-image-preview': mode}); box.append(new Element('img')); card.append(box);
  }
  card.append(new Element('p', {'data-image-caption': 'desktop'}));
  const clear = input(prefix, 'mobile_image-clear', 'checkbox'); clear.setAttribute('data-slide-mobile-clear', ''); card.append(clear);
  card.fields = values; card.clear = clear; return card;
}

class TemplateElement extends Element {
  constructor() { super('template'); this.content = new Element('fragment'); this._html = ''; }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = html;
    // Only the fixture's fixed prototype is parsed. The real script chooses the
    // form prefix; constructing the inert DOM does not duplicate its decisions.
    const prefix = html.match(/name="slides-([^"-]+)-image"/)?.[1];
    if (prefix !== undefined) { this.content.children = []; this.content.append(cardFixture(prefix)); }
  }
}

function fixture({saved = [], extraBlank = true, totalForms, dataTransfer = true, withPreview = true, primaryData = null, observeResize = false} = {}) {
  const form = new Element('form', {'data-slides-editor': ''});
  const list = new Element('div', {'data-slide-list': ''});
  const template = new TemplateElement(); template.setAttribute('data-slide-template', '');
  template.innerHTML = '<article data-slide-card><input name="slides-__prefix__-image"></article>';
  saved.forEach((value, index) => list.append(cardFixture(index, value)));
  if (extraBlank) list.append(cardFixture(saved.length));
  const total = new Element('input', {name: 'slides-TOTAL_FORMS', type: 'hidden'}); total.value = totalForms ?? list.children.length;
  const picker = new Element('input', {id: 'id_new_photos', type: 'file', multiple: ''});
  const multiBox = new Element('div', {'data-slide-multi-upload': ''}); multiBox.hidden = true; multiBox.append(picker);
  const add = new Element('button', {'data-slide-add': '', type: 'button'});
  const autoplay = new Element('input', {name: 'autoplay', type: 'checkbox'}); autoplay.checked = true;
  form.append(total, list, template, multiBox, add, autoplay);
  for (const key of ['announcement', 'error', 'count', 'add-actions']) form.append(new Element('div', {[`data-slide-${key}`]: ''}));
  form.append(new Element('div', {'data-slides-tools': ''}));
  const open = new Element('button', {'data-preview-open': '', type: 'button'}); form.append(open);
  const preview = new Element('section', {'data-slides-preview': ''}); preview.hidden = true;
  const frame = new Element('iframe', {'data-preview-frame': ''}); frame.srcdoc = '';
  const frameDocument = new Element('document'); frameDocument.body = new Element('body'); frameDocument.append(frameDocument.body);
  const picture = new Element('picture'); const source = new Element('source', {srcset: '/static/original-mobile.webp'});
  const image = new Element('img', {class: 'summit-landscape', src: '/static/original.webp', srcset: '/static/original.webp 1200w', sizes: '100vw'});
  picture.append(source, image);
  const publicHero = new Element('section', {class: 'summit-hero'});
  const publicSingleFooter = new Element('div', {class: 'summit-hero-bottom'});
  publicHero.append(picture, publicSingleFooter); frameDocument.body.append(publicHero);
  const controlsTemplate = new Element('template', {id: 'slide-preview-controls-template'});
  controlsTemplate.content = new Element('fragment');
  const multiFooter = new Element('div', {'data-preview-multi-footer': '', class: 'summit-hero-bottom hero-slideshow-footer'});
  multiFooter.append(new Element('span', {'data-preview-public-counter': ''}), new Element('span', {'data-preview-public-playback': ''}), new Element('div', {'data-preview-public-pages': ''}));
  controlsTemplate.content.append(multiFooter); frameDocument.body.append(controlsTemplate);
  frameDocument.getElementById = id => frameDocument.querySelector(`#${id}`);
  frameDocument.createElement = tag => new Element(tag);
  frameDocument.fonts = {ready: Promise.resolve()};
  const oldLink = new Element('a', {href: 'https://example.test/'}); const oldButton = new Element('button'); frameDocument.body.append(oldLink, oldButton);
  frame.contentDocument = frameDocument;
  preview.append(frame);
  for (const name of ['close', 'prev', 'next']) preview.append(new Element('button', {[`data-preview-${name}`]: '', type: 'button'}));
  for (const mode of ['desktop', 'mobile']) preview.append(new Element('button', {'data-preview-mode': mode, type: 'button'}));
  for (const name of ['counter', 'caption', 'warning', 'stage', 'viewport']) preview.append(new Element('div', {[`data-preview-${name}`]: ''}));
  form.append(preview);
  const document = new Element('document'); document.append(form);
  const primary = primaryData || {primary: {url: '/static/first.webp'}, mobile_primary: {url: '/static/first-mobile.webp'}, hero: {image_alt: 'Photo principale'}};
  const previewHTML = '<!doctype html><html><head><link rel="stylesheet" href="/static/css/summit.css"></head><body>Saved public banner</body></html>';
  const dataNode = new Element('script', {id: 'slideshow-preview-data'}); dataNode.textContent = JSON.stringify(primary);
  const htmlNode = new Element('script', {id: 'slideshow-preview-document'}); htmlNode.textContent = JSON.stringify(withPreview ? previewHTML : null);
  document.append(dataNode, htmlNode);
  document.getElementById = id => document.querySelector(`#${id}`);
  document.createElement = tag => tag === 'template' ? new TemplateElement() : new Element(tag);
  const window = new Element('window'); window.location = {href: 'https://boutique.example/gestion/accueil/photos/'};
  const created = []; const revoked = []; const network = [];
  class FixtureURL extends URL {
    static createObjectURL(file) { const url = `blob:https://boutique.example/${created.length + 1}`; created.push({file, url}); return url; }
    static revokeObjectURL(url) { revoked.push(url); }
  }
  class Transfer {
    constructor() { if (!dataTransfer) throw new Error('File assignment unsupported'); this._files = []; this.items = {add: file => this._files.push(file)}; }
    get files() { return this._files.slice(); }
  }
  let submitted = 0;
  form.submit = form.requestSubmit = () => { submitted += 1; };
  const fetch = (...args) => { network.push(args); throw new Error('Unexpected request'); };
  window.fetch = fetch;
  const resizeObservers = [];
  class Observer {
    constructor(callback) { this.callback = callback; this.observations = []; this.disconnected = false; resizeObservers.push(this); }
    observe(target) { this.observations.push(target); this.disconnected = false; }
    disconnect() { this.disconnected = true; }
  }
  const context = vm.createContext({document, window, URL: FixtureURL, DataTransfer: Transfer, fetch, ...(observeResize ? {ResizeObserver: Observer} : {})});
  vm.runInContext(script, context, {filename: scriptPath});
  return {form, list, total, picker, multiBox, add, autoplay, preview, open, frame, frameDocument, publicHero, publicSingleFooter, image, oldLink, oldButton, primary, previewHTML, document, window, created, revoked, network, resizeObservers,
    cards: () => list.querySelectorAll('[data-slide-card]'),
    get submitted() { return submitted; },
    async pick(files) { picker.files = files; return picker.emit('change'); },
    async openPreview() { await open.emit('click'); await frame.emit('load'); },
  };
}
const file = name => ({name, type: 'image/webp', size: 1200});
const live = page => page.cards().filter(card => !card.fields.DELETE.checked && (card.dataset.existing === 'true' || card.fields.image.files.length || card.fields.alt.value));


test('saved cards with no new files initialize normally and retain their original images and identities', () => {
  const page = fixture({saved: [{id: '41', desktop: '/media/saved.webp', alt: 'Forêt', position: '1'}]});
  const saved = page.cards()[0];
  assert.equal(saved.fields.id.value, '41');
  assert.equal(saved.querySelector('[data-image-preview="desktop"]').querySelector('img').src, '/media/saved.webp');
  assert.equal(saved.fields.image.required, false);
  assert.equal(page.created.length, 0);
  assert.equal(page.submitted, 0);
  assert.deepEqual(page.network, []);
});

test('multi-selection distributes each File to a stable individual form and never submits or uploads it', async () => {
  const page = fixture();
  const first = file('foret.webp'); const second = file('montagne.webp');
  assert.equal(page.picker.name, '', 'the multi-picker itself must not enter the submitted formset');
  await page.pick([first, second]);
  const rows = live(page);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].fields.image.files[0], first);
  assert.equal(rows[1].fields.image.files[0], second);
  assert.equal(rows[0].fields.image.name, 'slides-0-image');
  assert.equal(rows[1].fields.image.name, 'slides-1-image');
  assert.deepEqual(rows.map(card => card.fields.position.value), ['1', '2']);
  rows[0].fields.alt.value = 'Un brouillon de description';
  await rows[0].fields.alt.emit('input');
  assert.equal(rows[0].fields.alt.value, 'Un brouillon de description');
  assert.equal(page.total.value, '2');
  assert.equal(page.picker.files.length, 0);
  assert.equal(page.submitted, 0);
  assert.deepEqual(page.network, []);
});

test('moving cards restores submitted order then changes positions without changing any saved ID or field prefix', async () => {
  const page = fixture({saved: [
    {id: '41', desktop: '/saved/a.webp', alt: 'A', position: '3'},
    {id: '42', desktop: '/saved/b.webp', alt: 'B', position: '1'},
    {id: '43', desktop: '/saved/c.webp', alt: 'C', position: '2'},
  ], extraBlank: false});
  assert.deepEqual(live(page).map(card => card.fields.id.value), ['42', '43', '41']);
  const target = live(page)[0];
  const originalNames = Object.values(target.fields).map(field => field.name);
  await target.querySelector('[data-slide-down]').emit('click');
  assert.deepEqual(live(page).map(card => card.fields.id.value), ['43', '42', '41']);
  assert.deepEqual(live(page).map(card => card.fields.position.value), ['1', '2', '3']);
  assert.deepEqual(Object.values(target.fields).map(field => field.name), originalNames);
  assert.equal(target.fields.id.value, '42');
  assert.equal(target.fields.image.id, 'id_slides-1-image');
  await target.querySelector('[data-slide-up]').emit('click');
  assert.deepEqual(live(page).map(card => card.fields.id.value), ['42', '43', '41']);
});

test('removed saved rows keep their deletion flag and identity while live positions skip them, and undo restores them', async () => {
  const page = fixture({saved: [
    {id: '41', desktop: '/saved/a.webp', position: '1'},
    {id: '42', desktop: '/saved/b.webp', position: '2'},
    {id: '43', desktop: '/saved/c.webp', position: '3'},
  ], extraBlank: false});
  const removed = page.cards()[1];
  removed.fields.DELETE.checked = true;
  await removed.fields.DELETE.emit('change');
  assert.equal(removed.fields.id.value, '42');
  assert.equal(removed.fields.DELETE.name, 'slides-1-DELETE');
  assert.deepEqual(live(page).map(card => card.fields.position.value), ['1', '2']);
  assert.equal(removed.fields.position.value, '');
  await live(page)[1].querySelector('[data-slide-up]').emit('click');
  assert.deepEqual(live(page).map(card => card.fields.id.value), ['43', '41']);
  assert.equal(removed.fields.DELETE.checked, true);
  await removed.querySelector('[data-slide-undo]').emit('click');
  assert.equal(removed.fields.DELETE.checked, false);
  assert.equal(live(page).length, 3);
  assert.deepEqual(live(page).map(card => card.fields.position.value), ['1', '2', '3']);
});

test('repeated add/remove of an unsaved photo recycles its form rather than growing TOTAL_FORMS past the server cap', async () => {
  const page = fixture();
  const firstRow = page.cards()[0];
  for (let attempt = 0; attempt < 14; attempt += 1) {
    await page.pick([file(`draft-${attempt}.webp`)]);
    const row = live(page)[0];
    assert.equal(row, firstRow);
    assert.equal(row.fields.image.name, 'slides-0-image');
    row.fields.alt.value = `Draft ${attempt}`;
    row.fields.DELETE.checked = true;
    await row.fields.DELETE.emit('change');
    assert.equal(row.fields.image.files.length, 0);
    assert.equal(row.fields.alt.value, '');
    assert.equal(row.hidden, true);
    assert.equal(page.total.value, '1');
  }
  assert.equal(page.cards().length, 1);
  assert.equal(new Set(page.revoked).size, 14);
  assert.equal(page.submitted, 0);
});

test('the five-photo selection limit rejects an oversized batch before changing any row', async () => {
  const page = fixture({saved: [1, 2, 3, 4].map(number => ({id: String(number), desktop: `/saved/${number}.webp`, position: String(number)}))});
  const previousTotal = page.total.value;
  await page.pick([file('one.webp'), file('two.webp')]);
  assert.equal(live(page).length, 4);
  assert.equal(page.total.value, previousTotal);
  assert.equal(page.form.querySelector('[data-slide-error]').hidden, false);
  await page.pick([file('last.webp')]);
  assert.equal(live(page).length, 5);
  assert.equal(page.add.disabled, true);
  assert.equal(page.picker.disabled, true);
});

test('when no reusable row remains, the ten-form server cap blocks adding another form', async () => {
  const page = fixture({saved: [{id: '41', desktop: '/saved/one.webp', position: '1'}], extraBlank: false, totalForms: 10});
  await page.add.emit('click');
  assert.equal(page.total.value, '10');
  assert.equal(page.cards().length, 1);
  assert.equal(page.form.querySelector('[data-slide-error]').hidden, false);
  assert.match(page.form.querySelector('[data-slide-error]').textContent, /Enregistrez/);
});

test('draft desktop/mobile files are used in the iframe and clearing the mobile crop removes its File and preview URL', async () => {
  const page = fixture();
  await page.pick([file('desktop.webp')]);
  const row = live(page)[0];
  row.fields.alt.value = 'Photo en forêt';
  row.fields.mobile_image.files = [file('mobile.webp')];
  await row.fields.mobile_image.emit('change');
  const desktopURL = row.querySelector('[data-image-preview="desktop"]').querySelector('img').src;
  const mobileURL = row.querySelector('[data-image-preview="mobile"]').querySelector('img').src;
  await page.openPreview();
  assert.equal(page.frame.srcdoc, page.previewHTML, 'the real public layout document remains the iframe source');
  assert.equal(page.frameDocument.body.inert, true);
  assert.equal(page.oldLink.getAttribute('href'), null);
  assert.equal(page.oldButton.disabled, true);
  await page.preview.querySelector('[data-preview-next]').emit('click');
  assert.equal(page.image.src, desktopURL);
  await page.preview.querySelector('[data-preview-mode="mobile"]').emit('click');
  assert.equal(page.image.src, mobileURL);
  assert.equal(page.image.getAttribute('srcset'), null);
  assert.equal(page.frameDocument.querySelectorAll('source').length, 0, 'saved responsive sources cannot override a draft');
  row.clear.checked = true;
  await row.clear.emit('change');
  assert.equal(row.fields.mobile_image.files.length, 0);
  assert.equal(page.image.src, desktopURL);
  assert.ok(page.revoked.includes(mobileURL));
  assert.equal(page.preview.querySelector('[data-preview-viewport]').style.width, '390px');
  assert.match(page.preview.querySelector('[data-preview-caption]').textContent, /Téléphone/);
  assert.equal(page.submitted, 0);
  assert.deepEqual(page.network, []);
});

test('a custom primary without a custom mobile crop previews that same custom desktop image on mobile', async () => {
  const page = fixture({primaryData: {primary: {url: '/media/homepage/custom.webp', custom: true}, mobile_primary: {url: '/static/old-mobile.webp', custom: false}, hero: {image_alt: 'Personnalisée'}}});
  await page.openPreview();
  await page.preview.querySelector('[data-preview-mode="mobile"]').emit('click');
  assert.equal(page.image.src, 'https://boutique.example/media/homepage/custom.webp');
});

test('untrusted descriptions and filenames remain text/attributes and never become interpolated preview HTML', async () => {
  const page = fixture();
  const unsafe = '"><img src=x onerror=alert(1)><script>alert(2)</script>';
  await page.pick([file(unsafe)]);
  const row = live(page)[0];
  row.fields.alt.value = unsafe;
  await row.fields.alt.emit('input');
  await page.openPreview();
  await page.preview.querySelector('[data-preview-next]').emit('click');
  assert.equal(page.image.alt, unsafe);
  assert.ok(page.preview.querySelector('[data-preview-caption]').textContent.includes(unsafe));
  assert.equal(page.frame.srcdoc, page.previewHTML);
  assert.equal(page.frame.srcdoc.includes(unsafe), false);
  assert.equal(page.frameDocument.querySelectorAll('img').length, 1);
  assert.equal(page.frameDocument.querySelectorAll('script').length, 0);
});

test('object URLs are reused for an unchanged File and revoked when it is replaced or the page is left', async () => {
  const page = fixture();
  await page.pick([file('first.webp')]);
  const row = live(page)[0];
  const firstURL = page.created[0].url;
  row.fields.alt.value = 'Texte modifié';
  await row.fields.alt.emit('input');
  assert.equal(page.created.length, 1, 'text editing must not create another blob URL for the same File');
  row.fields.image.files = [file('replacement.webp')];
  await row.fields.image.emit('change');
  assert.ok(page.revoked.includes(firstURL));
  const currentURL = page.created.at(-1).url;
  await page.window.emit('pagehide');
  assert.ok(page.revoked.includes(currentURL));
  assert.equal(page.frame.srcdoc, '');
});

test('a back-forward-cache return rebuilds blob previews and reattaches resize observation without losing drafts', async () => {
  const page = fixture({observeResize: true});
  const draft = file('draft.webp');
  await page.pick([draft]);
  const row = live(page)[0];
  row.fields.alt.value = 'Brouillon conservé';
  await page.openPreview();
  const before = page.created.at(-1).url;
  await page.window.emit('pagehide');
  assert.ok(page.revoked.includes(before));
  assert.equal(page.resizeObservers[0].disconnected, true);
  await page.window.emit('pageshow', {persisted: true});
  await page.frame.emit('load');
  assert.equal(row.fields.image.files[0], draft);
  assert.equal(row.fields.alt.value, 'Brouillon conservé');
  assert.notEqual(page.created.at(-1).url, before);
  assert.equal(page.frame.srcdoc, page.previewHTML);
  assert.equal(page.resizeObservers[0].disconnected, false);
  assert.equal(page.submitted, 0);
});

test('without assignable FileLists, individual native file fields still stage a photo normally', async () => {
  const page = fixture({dataTransfer: false});
  assert.equal(page.multiBox.hidden, true);
  await page.add.emit('click');
  const row = page.cards()[0];
  const selected = file('native.webp');
  row.fields.image.files = [selected];
  await row.fields.image.emit('change');
  assert.equal(live(page).length, 1);
  assert.equal(row.fields.image.files[0], selected);
  assert.equal(row.fields.position.value, '1');
  assert.equal(page.submitted, 0);
});


test('the preview switches the real public footer between one photo and multiple inert controls, then back after deletion', async () => {
  const page = fixture();
  await page.openPreview();
  const multi = page.publicHero.querySelector('[data-preview-multi-footer]');
  assert.ok(multi, 'the script clones the provided public footer template');
  assert.equal(page.publicHero.classList.contains('has-slideshow'), false);
  assert.equal(page.publicSingleFooter.hidden, false);
  assert.equal(multi.hidden, true);
  await page.pick([file('first.webp'), file('second.webp')]);
  assert.equal(page.publicHero.classList.contains('has-slideshow'), true);
  assert.equal(page.publicSingleFooter.hidden, true);
  assert.equal(multi.hidden, false);
  assert.equal(multi.querySelector('[data-preview-public-counter]').textContent, 'Photo 1 sur 3');
  const pages = multi.querySelector('[data-preview-public-pages]');
  assert.equal(pages.children.length, 3);
  assert.ok(pages.children.every(button => button.disabled));
  assert.equal(pages.children[0].getAttribute('aria-current'), 'true');
  await page.preview.querySelector('[data-preview-next]').emit('click');
  assert.equal(multi.querySelector('[data-preview-public-counter]').textContent, 'Photo 2 sur 3');
  assert.equal(pages.children[1].getAttribute('aria-current'), 'true');
  assert.equal(multi.querySelector('[data-preview-public-playback]').textContent, 'Ⅱ');
  page.autoplay.checked = false;
  await page.autoplay.emit('change');
  assert.equal(multi.querySelector('[data-preview-public-playback]').textContent, '▶');
  for (const row of live(page)) {
    row.fields.DELETE.checked = true;
    await row.fields.DELETE.emit('change');
  }
  assert.equal(page.publicHero.classList.contains('has-slideshow'), false);
  assert.equal(page.publicSingleFooter.hidden, false);
  assert.equal(multi.hidden, true);
  assert.equal(pages.children.length, 1, 'old page markers are replaced instead of accumulating');
  assert.equal(multi.querySelector('[data-preview-public-counter]').textContent, 'Photo 1 sur 1');
  assert.equal(page.submitted, 0);
});
