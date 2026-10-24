// Execute the shipped slideshow against DOM/image/timer capabilities, not a copy
// of its navigation logic. No browser, network, or database is needed.
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const scriptPath = path.join(__dirname, '../static/accueil/js/slideshow.js');
const script = readFileSync(scriptPath, 'utf8');
const flush = async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); };
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; }

class Element {
  constructor(properties = {}) {
    Object.assign(this, {dataset: {}, hidden: false, textContent: '', attributes: {}, selectors: {}, lists: {}, listeners: new Map(), children: []}, properties);
    const classes = new Set(properties.classes || []);
    this.classList = {
      contains: value => classes.has(value),
      toggle: (value, force) => { const present = force === undefined ? !classes.has(value) : force; if (present) classes.add(value); else classes.delete(value); return present; },
    };
  }
  querySelector(selector) { return this.selectors[selector] || null; }
  querySelectorAll(selector) { return this.lists[selector] || []; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) {
    delete this.attributes[name];
    if (name.startsWith('data-')) delete this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())];
  }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }
  removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
  contains(element) { return element === this || this.children.some(child => child.contains(element)); }
  getBoundingClientRect() { return this.bounds || {top: 0, bottom: 800}; }
  async emit(type, properties = {}) {
    const event = {target: this, detail: 1, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...properties};
    for (const listener of [...(this.listeners.get(type) || [])]) listener(event);
    await flush();
    return event;
  }
}

class ImageElement extends Element {
  constructor({initial = false, decoding = null, broken = false} = {}) {
    super({dataset: initial ? {} : {src: '/media/homepage/extra.webp'}});
    this.complete = initial;
    this.naturalWidth = initial ? 1200 : 0;
    this._src = initial ? '/static/first.webp' : '';
    this.loading = initial ? 'eager' : 'lazy';
    this.decodeCount = 0;
    this.decode = () => { this.decodeCount += 1; return decoding ? decoding.promise : Promise.resolve(); };
    this.broken = broken;
  }
  get src() { return this._src; }
  set src(value) { this._src = value; this.complete = true; this.naturalWidth = this.broken ? 0 : 1200; }
}

function fixture({count = 3, autoplay = true, reduced = false, hover = true, decodings = {}, broken = [], interval = '6', rootPresent = true} = {}) {
  const images = Array.from({length: count}, (_, index) => new ImageElement({initial: index === 0, decoding: decodings[index], broken: broken.includes(index)}));
  const sources = images.map((_, index) => new Element({dataset: index ? {srcset: '/media/homepage/mobile.webp'} : {}}));
  const slides = images.map((image, index) => new Element({hidden: index > 0, classes: index ? [] : ['is-active'], attributes: {'aria-hidden': String(index !== 0)},
    selectors: {img: image}, lists: {'source[data-srcset]': index ? [sources[index]] : []}}));
  const pages = slides.map((_, index) => new Element({dataset: {slideshowPage: String(index)}, attributes: index ? {} : {'aria-current': 'true'}}));
  const previous = new Element();
  const next = new Element();
  const playIcon = new Element();
  const pauseIcon = new Element({hidden: true});
  const playback = new Element({selectors: {'[data-slideshow-play-icon]': playIcon, '[data-slideshow-pause-icon]': pauseIcon}});
  const controls = new Element({hidden: true, children: [previous, next, playback, ...pages]});
  const counter = new Element({textContent: `Photo 1 sur ${count}`});
  const announcement = new Element();
  const footer = new Element({hidden: true});
  const hero = new Element({dataset: {autoplay: String(autoplay), interval,
    positionLabel: 'Photo {current} sur {total}', playLabel: 'Lancer les photos', pauseLabel: 'Mettre en pause', errorLabel: 'Photo indisponible'},
    selectors: {'[data-slideshow-controls]': controls, '[data-slideshow-prev]': previous,
      '[data-slideshow-next]': next, '[data-slideshow-playback]': playback,
      '[data-slideshow-counter]': counter, '[data-slideshow-announcement]': announcement,
      '[data-slideshow-only-footer]': footer},
    lists: {'[data-slideshow-slide]': slides, '[data-slideshow-page]': pages}, children: [controls]});
  const document = new Element({hidden: false, activeElement: null, lists: {'[data-home-slideshow]': rootPresent ? [hero] : []}});
  const reduceQuery = new Element({matches: reduced});
  const hoverQuery = new Element({matches: hover});
  const observers = [];
  class Observer {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe(element) { this.target = element; }
    async setVisible(visible) { this.callback([{target: this.target, isIntersecting: visible}]); await flush(); }
  }
  const window = new Element({innerHeight: 900, IntersectionObserver: Observer,
    matchMedia: query => query.includes('reduced-motion') ? reduceQuery : hoverQuery});
  const timers = new Map();
  let nextTimer = 0;
  const context = vm.createContext({window, document, IntersectionObserver: Observer,
    setTimeout: (callback, delay) => { const id = ++nextTimer; timers.set(id, {callback, delay}); return id; },
    clearTimeout: id => timers.delete(id),
  });
  vm.runInContext(script, context, {filename: scriptPath});
  return {hero, slides, images, sources, pages, controls, previous, next, playback, playIcon, pauseIcon, counter, announcement, footer, document,
    reduceQuery, hoverQuery, observers, timers,
    active: () => slides.findIndex(slide => slide.classList.contains('is-active')),
    async tick() { const [id, task] = timers.entries().next().value || []; assert.ok(task, 'an autoplay timer should exist'); timers.delete(id); task.callback(); await flush(); },
  };
}

test('one photo or no slideshow keeps the original still image and adds no controls/timer', () => {
  for (const options of [{count: 1}, {rootPresent: false}]) {
    const page = fixture(options);
    assert.equal(page.active(), 0);
    assert.equal(page.controls.hidden, true);
    assert.equal(page.footer.hidden, true);
    assert.equal(page.hero.dataset.slideshowReady, undefined);
    assert.equal(page.timers.size, 0);
  }
});

test('initialization exposes controls but keeps other photographs unloaded', () => {
  const page = fixture();
  assert.equal(page.controls.hidden, false);
  assert.equal(page.footer.hidden, false);
  assert.equal(page.active(), 0);
  assert.equal(page.playback.getAttribute('aria-pressed'), 'true');
  assert.equal(page.playIcon.hidden, true);
  assert.equal(page.pauseIcon.hidden, false);
  assert.deepEqual(page.images.map(image => image.src), ['/static/first.webp', '', '']);
  assert.deepEqual([...page.timers.values()].map(task => task.delay), [6000]);
  assert.equal(page.announcement.textContent, '');
});

test('manual navigation keeps the first image visible until the next is decoded', async () => {
  const decoding = deferred();
  const page = fixture({decodings: {1: decoding}});
  await page.next.emit('click');
  assert.equal(page.images[1].src, '/media/homepage/extra.webp');
  assert.equal(page.sources[1].srcset, '/media/homepage/mobile.webp');
  assert.equal(page.images[1].decodeCount, 1);
  assert.equal(page.active(), 0);
  decoding.resolve();
  await flush();
  assert.equal(page.active(), 1);
  assert.equal(page.counter.textContent, 'Photo 2 sur 3');
  assert.equal(page.announcement.textContent, 'Photo 2 sur 3');
  assert.equal(page.pages[1].getAttribute('aria-current'), 'true');
  assert.equal(page.slides[0].getAttribute('aria-hidden'), 'true');
  assert.equal(page.slides[1].getAttribute('aria-hidden'), 'false');
});

test('rapid navigation displays only the most recent intent when decoding resolves out of order', async () => {
  const second = deferred();
  const third = deferred();
  const page = fixture({decodings: {1: second, 2: third}});
  await page.next.emit('click');
  await page.next.emit('click');
  third.resolve();
  await flush();
  assert.equal(page.active(), 2);
  second.resolve();
  await flush();
  assert.equal(page.active(), 2, 'late decoding cannot jump back to the obsolete request');
  assert.equal(page.counter.textContent, 'Photo 3 sur 3');
  await page.previous.emit('click');
  assert.equal(page.active(), 1, 'a previously decoded photo remains usable');
});

test('returning to the current image cancels a pending manual request', async () => {
  const second = deferred();
  const page = fixture({decodings: {1: second}});
  await page.next.emit('click');
  await page.previous.emit('click');
  second.resolve();
  await flush();
  assert.equal(page.active(), 0);
  assert.equal(page.counter.textContent, 'Photo 1 sur 3');
});

test('autoplay advances silently, respects the configured interval, and restarts a single timer', async () => {
  const page = fixture({interval: '8'});
  assert.deepEqual([...page.timers.values()].map(task => task.delay), [8000]);
  await page.tick();
  assert.equal(page.active(), 1);
  assert.equal(page.counter.textContent, 'Photo 2 sur 3');
  assert.equal(page.announcement.textContent, '', 'automatic changes must not interrupt screen readers');
  assert.equal(page.timers.size, 1);
});

test('focus, hidden tabs, and leaving the viewport each pause autoplay until cleared', async () => {
  const page = fixture();
  await page.hero.emit('focusin');
  assert.equal(page.timers.size, 0);
  await page.hero.emit('focusout', {relatedTarget: page.next});
  assert.equal(page.timers.size, 0, 'focus moving between controls stays paused');
  await page.hero.emit('focusout', {relatedTarget: null});
  assert.equal(page.timers.size, 1);
  page.document.hidden = true;
  await page.document.emit('visibilitychange');
  assert.equal(page.timers.size, 0);
  page.document.hidden = false;
  await page.document.emit('visibilitychange');
  assert.equal(page.timers.size, 1);
  await page.observers[0].setVisible(false);
  assert.equal(page.timers.size, 0);
  await page.observers[0].setVisible(true);
  assert.equal(page.timers.size, 1);
});

test('pausing while an automatic photograph decodes prevents its delayed appearance', async () => {
  const second = deferred();
  const page = fixture({decodings: {1: second}});
  await page.tick();
  await page.hero.emit('focusin');
  second.resolve();
  await flush();
  assert.equal(page.active(), 0);
  assert.equal(page.timers.size, 0);
  await page.hero.emit('focusout', {relatedTarget: null});
  assert.equal(page.timers.size, 1);
  await page.tick();
  assert.equal(page.active(), 1);
});

test('only fine mouse hover pauses autoplay, never a touch pointer', async () => {
  const page = fixture();
  await page.hero.emit('pointerenter', {pointerType: 'touch'});
  assert.equal(page.timers.size, 1);
  await page.hero.emit('pointerenter', {pointerType: 'mouse'});
  assert.equal(page.timers.size, 0);
  await page.hero.emit('pointerleave');
  assert.equal(page.timers.size, 1);
  const touchPage = fixture({hover: false});
  await touchPage.hero.emit('pointerenter', {pointerType: 'mouse'});
  assert.equal(touchPage.timers.size, 1);
});

test('reduced motion disables initial autoplay but allows explicit play and later preference changes pause it', async () => {
  const page = fixture({reduced: true});
  assert.equal(page.timers.size, 0);
  assert.equal(page.playback.getAttribute('aria-pressed'), 'false');
  assert.equal(page.playback.getAttribute('aria-label'), 'Lancer les photos');
  await page.playback.emit('click');
  assert.equal(page.timers.size, 1);
  await page.tick();
  assert.equal(page.active(), 1);
  await page.reduceQuery.emit('change');
  assert.equal(page.timers.size, 0);
  assert.equal(page.playback.getAttribute('aria-pressed'), 'false');
});

test('keyboard arrows and keyboard button activation switch instantly; pointer navigation restores fades', async () => {
  const page = fixture({autoplay: false});
  const event = await page.controls.emit('keydown', {key: 'ArrowRight'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(page.active(), 1);
  assert.equal(page.hero.classList.contains('is-instant'), true);
  await page.next.emit('click', {detail: 1});
  assert.equal(page.active(), 2);
  assert.equal(page.hero.classList.contains('is-instant'), false);
  await page.previous.emit('click', {detail: 0});
  assert.equal(page.active(), 1);
  assert.equal(page.hero.classList.contains('is-instant'), true);
});

test('a broken photograph leaves the current one visible and later navigation can skip it', async () => {
  const page = fixture({broken: [1], autoplay: false});
  await page.next.emit('click');
  assert.equal(page.active(), 0);
  assert.equal(page.announcement.textContent, 'Photo indisponible');
  await page.next.emit('click');
  assert.equal(page.active(), 2);
});


test('explicit Play resumes immediately on its focused, hovered button and other interactions restore pauses', async () => {
  const page = fixture();
  page.document.activeElement = page.playback;
  await page.hero.emit('focusin', {target: page.playback});
  await page.hero.emit('pointerenter', {pointerType: 'mouse'});
  assert.equal(page.timers.size, 0);
  await page.playback.emit('click');
  assert.equal(page.playback.getAttribute('aria-pressed'), 'false');
  await page.playback.emit('click');
  assert.equal(page.playback.getAttribute('aria-pressed'), 'true');
  assert.equal(page.timers.size, 1, 'explicit Play overrides its own focused/hovered state');
  await page.tick();
  assert.equal(page.active(), 1);
  await page.hero.emit('pointerover', {pointerType: 'mouse', target: page.next});
  assert.equal(page.timers.size, 0, 'moving to another control restores automatic pauses');
  await page.hero.emit('pointerleave');
  await page.hero.emit('focusout', {relatedTarget: null});
  assert.equal(page.timers.size, 1);
  await page.hero.emit('focusin', {target: page.next});
  assert.equal(page.timers.size, 0);
});

test('explicit reduced-motion playback still pauses when its button loses focus to another control', async () => {
  const page = fixture({reduced: true});
  await page.hero.emit('focusin', {target: page.playback});
  await page.playback.emit('click');
  assert.equal(page.timers.size, 1);
  await page.hero.emit('focusout', {relatedTarget: page.next});
  await page.hero.emit('focusin', {target: page.next});
  assert.equal(page.timers.size, 0);
});
