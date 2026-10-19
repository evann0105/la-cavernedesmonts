// Exercises the shipped browser script, without a browser or third-party DOM.
// The fixture supplies DOM capabilities; it never reproduces selection logic.
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const scriptPath = process.env.PRODUCT_STORY_SCRIPT || path.join(__dirname, '../static/core/js/product-story.js');
const script = readFileSync(scriptPath, 'utf8');

class Element {
  constructor(properties = {}) {
    Object.assign(this, {dataset: {}, hidden: false, disabled: false, checked: false,
      required: false, textContent: '', value: '', attributes: {}, selectors: {}, lists: {}, listeners: new Map()}, properties);
    const classes = new Set();
    this.classList = {
      remove: value => classes.delete(value),
      contains: value => classes.has(value),
      toggle: value => { if (classes.has(value)) { classes.delete(value); return false; } classes.add(value); return true; },
    };
  }
  querySelector(selector) { return this.selectors[selector] || null; }
  querySelectorAll(selector) { return this.lists[selector] || []; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }
  async emit(type, properties = {}) {
    const event = {target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...properties};
    for (const listener of this.listeners.get(type) || []) await listener(event);
    return event;
  }
  click() { return this.disabled ? Promise.resolve(null) : this.emit('click'); }
  focus() { this.focused = true; }
}

function fixture({selected = '11', unknownRedM = false, refreshRows = null} = {}) {
  const redImage = 'https://boutique.example/media/rouge.webp';
  const greenImage = 'https://boutique.example/media/vert.webp';
  const main = new Element({src: redImage, alt: 'Polaire rouge'});
  const opener = new Element({href: redImage});
  const canvas = new Element();
  const zoom = new Element({selectors: {'[data-zoom-symbol]': new Element({textContent: '+'})}});
  const dialog = new Element({selectors: {
    img: new Element(), '.lightbox-canvas': canvas, '[data-photo-zoom]': zoom,
    '.photo-counter': new Element(), '[data-photo-close]': new Element(),
    '[data-photo-prev]': new Element(), '[data-photo-next]': new Element(),
  }});
  const photos = [redImage, greenImage].map((href, i) => new Element({href,
    selectors: {img: new Element({alt: i ? 'Polaire verte' : 'Polaire rouge'})}}));
  const colors = ['rouge', 'vert'].map((color, i) => new Element({dataset: {
    productColor: color, colorName: i ? 'Vert' : 'Rouge', colorImage: i ? greenImage : redImage,
  }, selectors: {'[data-color-state]': new Element(), '[data-color-price]': new Element()}}));
  const sizes = ['m', 'l'].map(size => new Element({dataset: {productSize: size, sizeName: size.toUpperCase()},
    selectors: {'[data-size-state]': new Element(), '[data-size-price]': new Element(), '[data-size-reference]': new Element()}}));
  const variants = [
    ['11', 'rouge', 'm', '72.00', '80.00', '0', !unknownRedM],
    ['12', 'rouge', 'l', '90.00', '', '2', true],
    ['21', 'vert', 'l', '100.00', '', '3', true],
  ].map(([value, color, size, price, reference, available, known]) => new Element({
    value, checked: value === selected, disabled: Number(available) === 0, required: true,
    dataset: {variantColor: color, variantSize: size, variantPrice: price,
      variantReference: reference, variantAvailable: available, variantStockKnown: String(known)},
  }));
  const feedback = new Element();
  const colorPicker = new Element({hidden: true, selectors: {'[data-color-feedback]': feedback}, lists: {'[data-product-color]': colors}});
  const sizePicker = new Element({hidden: true, lists: {'[data-product-size]': sizes}});
  const purchase = new Element();
  const quantity = new Element({value: '1'});
  const fallback = new Element();
  const form = new Element({dataset: {selectedVariant: selected},
    selectors: {'button[type="submit"]': purchase, 'input[name="quantity"]': quantity, '[data-variant-fallback]': fallback},
    lists: {'input[name="variant"]': variants}});
  const price = new Element({textContent: '72,00 €', dataset: refreshRows ? {
    priceEndpoint: '/produits/polaire/prix/', priceServerTime: '2026-10-20T12:00:00Z', priceNextChange: '2026-10-20T12:01:00Z',
  } : {}});
  const discount = new Element({hidden: true, selectors: {
    '[data-product-reference]': new Element(), '[data-product-saving]': new Element(),
  }});
  const stock = new Element({textContent: 'Rouge · M : Rupture de stock'});
  const document = new Element({baseURI: 'https://boutique.example/produits/polaire/',
    documentElement: {lang: 'fr-FR'}, visibilityState: 'visible', selectors: {
      '[data-open-photo]': opener, '.product-lightbox': dialog,
      '[data-product-colors]': colorPicker, '[data-product-sizes]': sizePicker,
      '.product-purchase': form, '[data-product-price]': price,
      '[data-product-discount]': discount, '[data-product-stock-summary]': stock,
    }, lists: {'[data-product-photo]': photos, 'input[name="variant"]': variants}});
  document.getElementById = id => id === 'main-product-image' ? main : null;
  const window = new Element();
  const timers = new Map();
  let nextTimer = 0;
  let fetchCount = 0;
  const context = vm.createContext({document, window, URL, Intl,
    location: {href: document.baseURI + '?variante=' + selected},
    setTimeout: (callback, delay) => { const id = ++nextTimer; timers.set(id, {callback, delay}); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: async () => { fetchCount += 1; return {ok: true, json: async () => ({
      server_time: '2026-10-20T12:01:01Z', next_change: null, variants: refreshRows || [],
    })}; },
  });
  vm.runInContext(script, context, {filename: scriptPath});
  return {colors, sizes, variants, form, purchase, quantity, price, discount, feedback, stock, main, fallback,
    redImage, greenImage, timers, get fetchCount() { return fetchCount; }};
}

function checkedIds(page) { return page.variants.filter(input => input.checked).map(input => input.value); }
function assertRedMUnavailable(page, expectedPrice = '72,00') {
  assert.equal(page.colors[0].getAttribute('aria-pressed'), 'true');
  assert.equal(page.colors[1].getAttribute('aria-pressed'), 'false');
  assert.equal(page.sizes[0].getAttribute('aria-pressed'), 'true');
  assert.equal(page.sizes[1].getAttribute('aria-pressed'), 'false');
  assert.equal(page.sizes[0].disabled, true);
  assert.equal(page.sizes[1].disabled, false);
  assert.equal(page.purchase.disabled, true);
  assert.deepEqual(checkedIds(page), []);
  assert.match(page.price.textContent, new RegExp(expectedPrice));
  assert.equal(page.main.src, page.redImage);
}

test('an explicit sold-out M stays selected visually, with its price and blocked purchase', async () => {
  const page = fixture();
  assertRedMUnavailable(page);
  assert.match(page.stock.textContent, /Rouge · M : Rupture de stock/);
  assert.equal(page.fallback.hidden, true);
  const submitted = await page.form.emit('submit');
  assert.equal(submitted.defaultPrevented, true);
  assert.deepEqual(checkedIds(page), []);
});

test('clicking its already selected color does not silently substitute the only available L', async () => {
  const page = fixture();
  await page.colors[0].click();
  assertRedMUnavailable(page);
  assert.match(page.stock.textContent, /M : Rupture de stock/);
});

test('the customer can intentionally choose available L and submit its exact variant', async () => {
  const page = fixture();
  await page.sizes[1].click();
  assert.equal(page.purchase.disabled, false);
  assert.equal(page.sizes[0].getAttribute('aria-pressed'), 'false');
  assert.equal(page.sizes[1].getAttribute('aria-pressed'), 'true');
  assert.deepEqual(checkedIds(page), ['12']);
  assert.match(page.price.textContent, /90,00/);
  assert.equal(page.quantity.max, '2');
  assert.equal((await page.form.emit('submit')).defaultPrevented, false);
  await page.sizes[0].click();
  assert.deepEqual(checkedIds(page), ['12'], 'disabled size buttons cannot change the purchasable pair');
});

test('a green L link preselects its radio, price, photograph and availability', async () => {
  const page = fixture({selected: '21'});
  assert.equal(page.colors[0].getAttribute('aria-pressed'), 'false');
  assert.equal(page.colors[1].getAttribute('aria-pressed'), 'true');
  assert.equal(page.sizes[0].hidden, true);
  assert.equal(page.sizes[1].getAttribute('aria-pressed'), 'true');
  assert.deepEqual(checkedIds(page), ['21']);
  assert.equal(page.purchase.disabled, false);
  assert.match(page.price.textContent, /100,00/);
  assert.equal(page.main.src, page.greenImage);
  assert.match(page.feedback.textContent, /Vert · L/);
  assert.equal(page.quantity.max, '3');
  assert.equal((await page.form.emit('submit')).defaultPrevented, false);
});

test('an uncounted requested M remains blocked and is described as unknown, not sold out', () => {
  const page = fixture({unknownRedM: true});
  assertRedMUnavailable(page);
  assert.match(page.stock.textContent, /Rouge · M : Stock à confirmer/);
  assert.doesNotMatch(page.stock.textContent, /Rupture/);
});

test('the scheduled price refresh retains an unavailable explicit M without choosing L', async () => {
  const page = fixture({refreshRows: [
    {id: 11, price: '80.00', reference: null}, {id: 12, price: '95.00', reference: null},
    {id: 21, price: '110.00', reference: null},
  ]});
  assert.equal(page.discount.hidden, false);
  assert.equal(page.timers.size, 1);
  const timer = [...page.timers.values()][0];
  assert.ok(timer.delay >= 60000 && timer.delay < 61000);
  await timer.callback();
  assert.equal(page.fetchCount, 1);
  assertRedMUnavailable(page, '80,00');
  assert.equal(page.discount.hidden, true);
  assert.match(page.stock.textContent, /M : Rupture de stock/);
});

test('a scheduled refresh changes the selected green price while retaining size, radio and photograph', async () => {
  const page = fixture({selected: '21', refreshRows: [
    {id: 11, price: '80.00', reference: null}, {id: 12, price: '95.00', reference: null},
    {id: 21, price: '110.00', reference: null},
  ]});
  await [...page.timers.values()][0].callback();
  assert.deepEqual(checkedIds(page), ['21']);
  assert.equal(page.purchase.disabled, false);
  assert.equal(page.main.src, page.greenImage);
  assert.equal(page.colors[1].getAttribute('aria-pressed'), 'true');
  assert.equal(page.sizes[1].getAttribute('aria-pressed'), 'true');
  assert.match(page.price.textContent, /110,00/);
  assert.equal(page.quantity.max, '3');
  assert.equal((await page.form.emit('submit')).defaultPrevented, false);
});
