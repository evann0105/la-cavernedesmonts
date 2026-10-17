(() => {
  const section = document.querySelector('[data-color-editor]');
  if (!section) return;
  const total = section.querySelector('[name="colors-TOTAL_FORMS"]');
  const max = Number(section.querySelector('[name="colors-MAX_NUM_FORMS"]').value);
  const add = section.querySelector('[data-add-color]');
  add.hidden = false;
  add.disabled = Number(total.value) >= max;
  add.addEventListener('click', () => {
    const index = Number(total.value);
    if (index >= max) return;
    const fragment = document.createElement('template');
    fragment.innerHTML = section.querySelector('[data-color-template]').innerHTML.replaceAll('__prefix__', String(index));
    const row = fragment.content.querySelector('[data-color-row]');
    section.querySelector('[data-color-rows]').append(fragment.content);
    total.value = index + 1;
    add.disabled = Number(total.value) >= max;
    section.querySelector('[data-color-status]').textContent = 'Nouvelle couleur ajoutée.';
    row.querySelector('input[type=text]').focus();
  });
})();
