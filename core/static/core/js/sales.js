(() => {
  'use strict';

  const selection = document.querySelector('[data-sales-selection]');
  if (selection) {
    const choices = Array.from(selection.querySelectorAll('[data-sales-product-choice]'));
    const all = selection.querySelector('[data-sales-select-all]');
    const count = selection.querySelector('[data-sales-selected-count]');
    const plural = selection.querySelectorAll('[data-sales-selected-plural]');
    const submit = selection.querySelector('[data-sales-selection-submit]');
    const updateSelection = () => {
      const selected = choices.filter(choice => choice.checked).length;
      if (count) count.textContent = String(selected);
      plural.forEach(node => { node.textContent = selected > 1 ? 's' : ''; });
      if (all) {
        all.checked = choices.length > 0 && selected === choices.length;
        all.indeterminate = selected > 0 && selected < choices.length;
      }
      if (submit) submit.disabled = selected === 0;
    };
    choices.forEach(choice => choice.addEventListener('change', updateSelection));
    if (all) all.addEventListener('change', () => {
      choices.forEach(choice => { choice.checked = all.checked; });
      updateSelection();
    });
    updateSelection();
  }

  const priceForm = document.querySelector('[data-sales-price-form]');
  if (!priceForm) return;
  const kind = priceForm.querySelector('[name=kind]');
  const value = priceForm.querySelector('[name=value]');
  const startsAt = priceForm.querySelector('[name=starts_at]');
  const scheduledStart = priceForm.querySelector('[data-sales-scheduled-start]');
  const valueHelp = priceForm.querySelector('[data-sales-value-help]');
  const valueLabel = value && value.id ? priceForm.querySelector(`label[for="${value.id}"]`) : null;
  const format = new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'EUR' });
  const toNumber = input => {
    if (!input || input.value.trim() === '') return NaN;
    return Number(input.value.replace(',', '.'));
  };

  const updateStart = () => {
    const mode = priceForm.querySelector('[name=start_mode]:checked');
    const immediate = mode && mode.value === 'immediate';
    if (scheduledStart) scheduledStart.hidden = Boolean(immediate);
    if (startsAt) startsAt.required = !immediate;
  };
  const updatePrices = () => {
    if (!kind || !value) return;
    const amount = toNumber(value);
    const type = kind.value;
    if (valueHelp) valueHelp.textContent = type === 'percent'
      ? 'Exemple : 10 pour une réduction de 10 %.'
      : type === 'amount'
        ? 'Exemple : 10 pour retirer 10 € au prix de référence.'
        : 'Exemple : 60 pour proposer chaque déclinaison au prix final de 60 €.';
    if (valueLabel) valueLabel.textContent = type === 'percent'
      ? 'Pourcentage de réduction (%)'
      : type === 'amount' ? 'Montant à déduire (€)' : 'Prix final (€)';
    if (type === 'percent') value.max = '99.99';
    else value.removeAttribute('max');
    priceForm.querySelectorAll('[data-sales-price-row]').forEach(row => {
      const referenceInput = row.querySelector('input');
      const reference = toNumber(referenceInput);
      const regular = Number(row.dataset.regularPrice);
      const output = row.querySelector('[data-sales-price-preview]');
      const warning = row.querySelector('[data-sales-price-warning]');
      let finalPrice = NaN;
      let message = '';
      if (Number.isFinite(amount) && amount > 0 && Number.isFinite(reference) && reference > 0) {
        finalPrice = type === 'percent' ? reference * (1 - amount / 100)
          : type === 'amount' ? reference - amount : amount;
        finalPrice = Math.round((finalPrice + Number.EPSILON) * 100) / 100;
        if (finalPrice <= 0) message = 'Le prix doit rester supérieur à zéro.';
        else if (finalPrice >= reference || finalPrice >= regular) message = 'La réduction doit réellement baisser le prix.';
        else if (reference > regular) message = 'Vérifiez le prix de référence.';
      }
      if (output) output.textContent = Number.isFinite(finalPrice) && finalPrice > 0 ? format.format(finalPrice) : '—';
      if (warning) warning.textContent = message;
    });
  };
  priceForm.querySelectorAll('[name=start_mode]').forEach(input => input.addEventListener('change', updateStart));
  priceForm.addEventListener('input', updatePrices);
  if (kind) kind.addEventListener('change', updatePrices);
  updateStart();
  updatePrices();
})();
