const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const tick = () => new Promise(resolve => setImmediate(resolve));
const script = fs.readFileSync(path.join(__dirname, '../static/core/js/cart.js'), 'utf8');

// Replacement HTML gets fresh inputs and forms: a queued action must not use
// detached elements. Selectors distinguish quantity, removal and code forms.
function cartFixture() {
  const handlers = {}, requests = [], submissions = [], requestSubmitCalls = [], pages = new Map();
  let scheduled, current, status;
  const matches = (selector, accepted) => selector.split(',').some(part => accepted.includes(part.trim()));
  const document = {
    documentElement: { lang: 'fr' }, activeElement: null,
    getElementById(id) {
      if (id === 'contenu') return main;
      return [current.quantity, current.code].find(input => input.id === id) || null;
    },
    querySelector: () => null,
    createElement() { status = { textContent: '', setAttribute() {} }; return status; },
  };
  function state(quantityValue = '1') {
    const quantity = {
      id: 'qty-1', value: String(quantityValue), defaultValue: String(quantityValue),
      checkValidity() { return /^(?:[0-9]|10)$/.test(this.value); },
      matches: selector => matches(selector, ['.quantity-form input[name="quantity"]']),
      focus() { document.activeElement = this; },
      reportValidity() { this.validityReported = true; },
    };
    const code = { id: 'customer-code-input-cart', value: '',
      focus() { document.activeElement = this; } };
    const quantitySubmit = { hidden: false }, lineTotal = { textContent: '80,00 €' }, total = { textContent: '80,00 €' };
    const discount = { textContent: '− 8,00 €', dataset: { cartDiscountAmount: '8.00' } };
    const net = { textContent: '72,00 €', dataset: { cartDiscountedTotal: '72.00' } };
    const summary = { setAttribute() {}, querySelector(selector) {
      return selector === '[data-cart-discount-amount]' ? discount : selector === '[data-cart-discounted-total]' ? net : null;
    } };
    const line = { dataset: { unitPrice: '80.00' }, querySelector(selector) {
      if (['[name="quantity"]', '.quantity-form input[name="quantity"]'].includes(selector)) return quantity;
      return selector === ':scope > strong' ? lineTotal : null;
    } };
    const quantityForm = { action: '/panier/modifier/', elements: { quantity, key: { value: 'variant:1' } },
      matches: selector => matches(selector, ['.quantity-form']) };
    quantity.form = quantityForm;
    const removeForm = { action: quantityForm.action,
      elements: { quantity: { value: '0', checkValidity: () => true }, key: { value: 'variant:1' } },
      matches: selector => matches(selector, ['[data-cart-remove]']), closest: selector => selector === '.cart-line' ? line : null };
    const forms = {}, buttons = {};
    const next = { quantity, code, quantitySubmit, quantityForm, removeForm, line, total, discount, net, summary, forms, buttons };
    for (const action of ['apply_benefit', 'remove_benefit']) {
      const form = { action: '/paiement/',
        matches: selector => matches(selector, ['[data-cart-code-form]']),
        querySelector(selector) {
          if (selector === '[name="benefit_code"]') return action === 'apply_benefit' ? code : null;
          return selector === '[name="action"]' ? buttons[action] : null;
        },
        requestSubmit(button) {
          assert.equal(current, next, 'queued submission must use the replacement form');
          assert.equal(button.form, this);
          requestSubmitCalls.push({ action: button.value, code: current.code.value });
          submit(this, button);
        },
      };
      forms[action] = form;
      buttons[action] = { name: 'action', value: action, form };
    }
    return next;
  }
  function select(selector, value = current) {
    if (selector === '[data-cart-page]') return {};
    if (selector === '.cart-summary .total strong') return value.total;
    if (selector === '.quantity-form input:invalid') return value.quantity.checkValidity() ? null : value.quantity;
    if (selector === '[name="benefit_code"]') return value.code;
    if (selector === '[data-cart-code-summary]') return value.summary;
    if (selector.includes('[data-cart-code-form]') && selector.includes('[name="action"]')) {
      return value.buttons[/\[value="([^"]+)"\]/.exec(selector)?.[1]] || null;
    }
    return null;
  }
  const main = {
    querySelector: selector => select(selector),
    querySelectorAll(selector) {
      if (selector === '.cart-line') return [current.line];
      if (selector === '[data-quantity-submit]') return [current.quantitySubmit];
      return selector === '.quantity-form input[name="quantity"]' ? [current.quantity] : [];
    },
    replaceChildren(...nodes) { current = nodes[0]; }, append() {}, setAttribute() {}, removeAttribute() {},
    addEventListener(name, callback) { handlers[name] = callback; },
  };
  function submit(form, button) {
    let prevented = false;
    handlers.submit({ target: form, submitter: button, preventDefault() { prevented = true; } });
    if (!prevented && form.matches('[data-cart-code-form]')) submissions.push({ action: button.value, code: current.code.value });
    return prevented;
  }
  class FormData { constructor(form) { this.quantity = form.elements.quantity.value; } }
  class DOMParser { parseFromString(key) { return pages.get(key); } }
  current = state();
  vm.runInNewContext(script, { document, Intl, Map, Number, Math, Error, FormData, DOMParser,
    setTimeout(callback) { scheduled = callback; return 1; }, clearTimeout() { scheduled = undefined; },
    fetch(url, options) { return new Promise((resolve, reject) => requests.push({ url, quantity: options.body.quantity, resolve, reject })); },
  });
  return {
    requests, submissions, requestSubmitCalls,
    get current() { return current; }, get status() { return status; },
    setQuantity(value) { current.quantity.value = String(value); handlers.input({ target: current.quantity }); },
    startTimer() { const callback = scheduled; scheduled = undefined; callback?.(); },
    remove() { return submit(current.removeForm); },
    submitCode(action = 'apply_benefit') { return submit(current.forms[action], current.buttons[action]); },
    clickCheckout() {
      let prevented = false;
      handlers.click({ target: { closest: selector => selector === '[data-cart-checkout]' ? {} : null }, preventDefault() { prevented = true; } });
      return prevented;
    },
    async respond(index, { ok = true, quantity = requests[index].quantity } = {}) {
      const next = state(quantity), key = `response-${index}`;
      pages.set(key, { getElementById: id => id === 'contenu' ? { childNodes: [next] } : null, querySelector: selector => select(selector, next) });
      requests[index].resolve({ ok, text: async () => key });
      await tick();
    },
    focusCode() { document.activeElement = current.code; },
  };
}

test('a removal is queued while a quantity request is in flight', async () => {
  const cart = cartFixture();
  cart.setQuantity('2'); cart.startTimer();
  assert.equal(cart.requests.length, 1);
  assert.equal(cart.remove(), true);
  await cart.respond(0);
  assert.equal(cart.requests.length, 2);
  assert.equal(cart.requests[1].quantity, '0');
  await cart.respond(1);
});

test('applying a code waits for an in-flight quantity update and uses the new form', async () => {
  const cart = cartFixture();
  cart.setQuantity('2'); cart.startTimer();
  cart.current.code.value = 'CDM-MON-CODE';
  assert.equal(cart.submitCode(), true);
  assert.deepEqual(cart.submissions, []);
  assert.deepEqual(cart.requestSubmitCalls, []);
  await cart.respond(0);
  assert.deepEqual(cart.submissions, [{ action: 'apply_benefit', code: 'CDM-MON-CODE' }]);
  assert.equal(cart.requestSubmitCalls.length, 1);
});

test('applying a code flushes a debounced quantity update before submitting', async () => {
  const cart = cartFixture();
  cart.setQuantity('3'); cart.current.code.value = 'CDM-ATTENDRE';
  assert.equal(cart.submitCode(), true);
  assert.equal(cart.requests.length, 1);
  assert.equal(cart.requests[0].quantity, '3');
  assert.deepEqual(cart.submissions, []);
  await cart.respond(0);
  assert.deepEqual(cart.submissions, [{ action: 'apply_benefit', code: 'CDM-ATTENDRE' }]);
});

test('a queued code waits for every newer quantity before submitting once', async () => {
  const cart = cartFixture();
  cart.setQuantity('2'); cart.startTimer(); cart.current.code.value = 'CDM-DERNIER'; cart.submitCode();
  cart.setQuantity('4');
  await cart.respond(0);
  assert.equal(cart.requests.length, 2);
  assert.equal(cart.requests[1].quantity, '4');
  assert.deepEqual(cart.submissions, []);
  await cart.respond(1);
  assert.deepEqual(cart.submissions, [{ action: 'apply_benefit', code: 'CDM-DERNIER' }]);
  assert.equal(cart.requestSubmitCalls.length, 1);
});

test('removing a code keeps its action when a quantity update is pending', async () => {
  const cart = cartFixture();
  cart.setQuantity('2'); cart.startTimer();
  assert.equal(cart.submitCode('remove_benefit'), true);
  await cart.respond(0);
  assert.deepEqual(cart.submissions, [{ action: 'remove_benefit', code: '' }]);
});

test('a failed quantity update never submits a queued code and blocks checkout', async () => {
  const cart = cartFixture();
  cart.setQuantity('2'); cart.startTimer(); cart.current.code.value = 'CDM-A-GARDER'; cart.submitCode();
  await cart.respond(0, { ok: false });
  assert.deepEqual(cart.requestSubmitCalls, []);
  assert.deepEqual(cart.submissions, []);
  assert.equal(cart.current.code.value, 'CDM-A-GARDER');
  assert.equal(cart.current.quantitySubmit.hidden, false);
  assert.equal(cart.submitCode(), true);
  assert.equal(cart.clickCheckout(), true);
  assert.deepEqual(cart.submissions, []);
});

test('code typed during an update survives replacement by the server cart', async () => {
  const cart = cartFixture();
  cart.setQuantity('2'); cart.startTimer();
  const previous = cart.current.code;
  cart.current.code.value = 'CDM-SAISI-PENDANT'; cart.focusCode();
  await cart.respond(0);
  assert.notEqual(cart.current.code, previous);
  assert.equal(cart.current.code.value, 'CDM-SAISI-PENDANT');
  assert.deepEqual(cart.submissions, []);
});

test('discount and net total show recalculation while quantities are unconfirmed', async () => {
  const cart = cartFixture(); cart.setQuantity('2');
  assert.match(cart.current.discount.textContent, /Recalcul/);
  assert.match(cart.current.net.textContent, /Recalcul/);
  cart.startTimer(); await cart.respond(0);
  assert.equal(cart.current.discount.textContent, '− 8,00 €');
  assert.equal(cart.current.net.textContent, '72,00 €');
});

test('invalid quantity prevents a code submission without making a request', () => {
  const cart = cartFixture(); cart.setQuantity('1.5'); cart.current.code.value = 'CDM-INCHANGE';
  assert.equal(cart.submitCode(), true);
  assert.equal(cart.current.quantity.validityReported, true);
  assert.deepEqual(cart.requests, []);
  assert.deepEqual(cart.submissions, []);
});

test('a code submits normally when quantities are already confirmed', () => {
  const cart = cartFixture(); cart.current.code.value = 'CDM-DIRECT';
  assert.equal(cart.submitCode(), false);
  assert.deepEqual(cart.requests, []);
  assert.deepEqual(cart.submissions, [{ action: 'apply_benefit', code: 'CDM-DIRECT' }]);
});
