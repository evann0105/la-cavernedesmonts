const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/suspensions/management.js'), 'utf8');

function render({ mode = 'scheduled', message = 'Une pause pour les vacances.', end = '2027-06-04T09:30', create = true } = {}) {
  const text = { value: message }, date = { value: end }, start = { hidden: false }, preview = { dataset: { fallback: 'Pause temporaire.' } }, datePreview = {}, button = { dataset: { create: String(create) }, textContent: 'Enregistrer les modifications' };
  const modes = ['now', 'scheduled'].map(value => ({ value, checked: mode === value }));
  const listeners = {};
  const controls = { '[name="message"]': text, '[name="ends_at"]': date, '[data-pause-start]': start, '[data-pause-submit]': button };
  const editor = { querySelector: selector => controls[selector], querySelectorAll: () => modes, addEventListener: (event, fn) => { listeners[event] = fn; } };
  const document = { querySelectorAll: () => [editor], querySelector: selector => selector === '[data-pause-preview-message]' ? preview : datePreview };
  vm.runInNewContext(source, { document });
  return { text, date, start, preview, datePreview, button, modes, change: () => listeners.change(), input: () => listeners.input() };
}

test('the public preview uses plain text and never converts customer-entered markup to HTML', () => {
  const ui = render({ message: '<img src=x onerror=alert(1)>' });
  assert.equal(ui.preview.textContent, '<img src=x onerror=alert(1)>');
  assert.equal(ui.preview.innerHTML, undefined);
  ui.text.value = '  '; ui.input();
  assert.equal(ui.preview.textContent, 'Pause temporaire.');
});

test('Paris wall-clock values remain unchanged on a device in any timezone', () => {
  const ui = render({ end: '2027-06-04T09:30' });
  assert.equal(ui.datePreview.textContent, 'Reprise des commandes le 04/06/2027 à 09 h 30 · heure de Paris');
  ui.date.value = ''; ui.change();
  assert.equal(ui.datePreview.textContent, 'La date de reprise sera affichée ici.');
});

test('switching immediate/scheduled preserves the chosen date and clearly changes the action', () => {
  const ui = render();
  assert.equal(ui.start.hidden, false);
  assert.equal(ui.button.textContent, 'Programmer cette pause');
  ui.modes[0].checked = true; ui.modes[1].checked = false; ui.change();
  assert.equal(ui.start.hidden, true);
  assert.equal(ui.button.textContent, 'Mettre les commandes en pause maintenant');
  assert.equal(ui.date.value, '2027-06-04T09:30');
});

test('existing pause editor keeps its save label', () => {
  const ui = render({ create: false });
  assert.equal(ui.button.textContent, 'Enregistrer les modifications');
});
