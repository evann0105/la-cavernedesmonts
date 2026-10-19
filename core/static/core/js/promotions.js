(() => {
  'use strict';
  const form = document.querySelector('[data-promotion-form]');
  if (!form) return;
  const selected = name => form.querySelector(`[name="${name}"]:checked`)?.value;
  const price = value => Number(String(value).replace(',', '.'));
  const euro = new Intl.NumberFormat('fr-FR', {style: 'currency', currency: 'EUR'});
  const rows = [...form.querySelectorAll('[data-promotion-target]')];
  const sync = () => {
    const immediate = selected('start_mode') === 'immediate';
    const startBox = form.querySelector('[data-scheduled-start]');
    if (startBox) startBox.hidden = immediate;
    const start = form.elements.starts_at;
    if (start) {
      start.disabled = immediate;
      start.required = !immediate;
    }
    const scope = selected('scope');
    const color = form.elements.color?.value;
    form.querySelector('[data-color-scope]').hidden = scope !== 'color';
    form.querySelector('[data-variant-scope]').hidden = scope !== 'variants';
    const keys = new Set([...form.querySelectorAll('[name="targets"]:checked')].map(input => input.value));
    const kind = form.elements.kind.value;
    const rawValue = form.elements.value.value;
    const value = price(rawValue);
    const label = form.querySelector('label[for="id_value"]');
    if (label) label.textContent = kind === 'percent' ? 'Pourcentage à déduire (%)' : kind === 'amount' ? 'Montant à déduire (€)' : 'Prix final pendant la période (€)';
    for (const row of rows) {
      const active = scope === 'all' || (scope === 'color' && row.dataset.targetColor === color) || (scope === 'variants' && keys.has(row.dataset.promotionTarget));
      row.hidden = !active;
      const input = row.querySelector('[data-reference-price]');
      input.required = active;
      const reference = price(input.value);
      const regular = price(row.dataset.regularPrice);
      const next = kind === 'percent' ? reference * (1 - value / 100) : kind === 'amount' ? reference - value : value;
      const cents = Math.round((next + Number.EPSILON) * 100) / 100;
      const valid = rawValue !== '' && input.value !== '' && Number.isFinite(cents) && value > 0 && cents > 0 && cents < reference && cents < regular;
      row.querySelector('[data-promotion-preview]').textContent = valid ? euro.format(cents) : '—';
    }
  };
  form.addEventListener('change', sync);
  form.addEventListener('input', sync);
  sync();
})();
