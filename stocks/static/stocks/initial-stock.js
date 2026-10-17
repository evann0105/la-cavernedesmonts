(() => {
  const section = document.querySelector('[data-initial-stock]');
  if (!section) return;
  const total = section.querySelector('[name="stock-TOTAL_FORMS"]');
  const maximum = Number(section.querySelector('[name="stock-MAX_NUM_FORMS"]').value);
  const add = section.querySelector('[data-add-stock]');
  const rows = section.querySelector('[data-stock-rows]');
  const template = section.querySelector('[data-stock-template]');
  const status = section.querySelector('[data-stock-status]');
  add.hidden = false;
  add.disabled = Number(total.value) >= maximum;
  add.addEventListener('click', () => {
    const index = Number(total.value);
    if (index >= maximum) return;
    const container = document.createElement('template');
    container.innerHTML = template.innerHTML.replaceAll('__prefix__', String(index));
    const row = container.content.querySelector('[data-stock-row]');
    row.querySelector('[data-stock-number]').textContent = String(index + 1);
    rows.append(container.content);
    total.value = index + 1;
    add.disabled = Number(total.value) >= maximum;
    status.textContent = 'Nouvelle ligne ajoutée.';
    row.querySelector('input').focus();
  });
})();
