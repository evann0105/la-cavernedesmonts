(() => {
  const section = document.querySelector('[data-color-editor]');
  if (!section) return;
  const total = section.querySelector('[name="colors-TOTAL_FORMS"]');
  const maximum = Number(section.querySelector('[name="colors-MAX_NUM_FORMS"]').value);
  const add = section.querySelector('[data-add-color]');
  const rows = section.querySelector('[data-color-rows]');
  const normalize = value => value.trim().replace(/\s+/g, ' ');
  const previews = new WeakMap();

  function updateCard(row) {
    const name = normalize(row.querySelector('[name$="-name"]').value);
    row.querySelector('[data-color-name-label]').textContent = name;
    const deleted = row.querySelector('[name$="-DELETE"]').checked;
    row.classList.toggle('is-removed', deleted);
    row.querySelector('[data-color-removal-note]').hidden = !deleted;
    const warning = row.querySelector('[data-existing-color-warning]');
    if (warning) warning.hidden = normalize(row.dataset.originalColor).toLocaleLowerCase('fr') === name.toLocaleLowerCase('fr');
  }

  function updatePreview(row) {
    const input = row.querySelector('input[type="file"][name$="-image"]');
    const image = row.querySelector('[data-color-preview]');
    const placeholder = row.querySelector('[data-color-placeholder]');
    const previous = previews.get(row);
    if (previous) URL.revokeObjectURL(previous);
    previews.delete(row);
    const file = input.files?.[0];
    const source = file ? URL.createObjectURL(file) : image.dataset.originalImage;
    if (file) previews.set(row, source);
    if (source) {
      image.src = source;
      image.hidden = false;
      placeholder.hidden = true;
    } else {
      image.removeAttribute('src');
      image.hidden = true;
      placeholder.hidden = false;
    }
  }

  add.hidden = false;
  add.disabled = Number(total.value) >= maximum;
  rows.querySelectorAll('[data-color-row]').forEach(updateCard);
  add.addEventListener('click', () => {
    const index = Number(total.value);
    if (index >= maximum) return;
    const fragment = document.createElement('template');
    fragment.innerHTML = section.querySelector('[data-color-template]').innerHTML.replaceAll('__prefix__', String(index));
    const row = fragment.content.querySelector('[data-color-row]');
    rows.append(fragment.content);
    total.value = index + 1;
    add.disabled = Number(total.value) >= maximum;
    updateCard(row);
    section.dispatchEvent(new CustomEvent('catalog:color-added', {bubbles: true, detail: {row}}));
    section.querySelector('[data-color-status]').textContent = 'Nouveau coloris ajouté. Choisissez sa photo, puis ses tailles et quantités.';
    row.querySelector('[name$="-name"]').focus();
  });
  rows.addEventListener('input', event => {
    const row = event.target.closest('[data-color-row]');
    if (row && (event.target.name.endsWith('-name') || event.target.name.endsWith('-DELETE'))) updateCard(row);
  });
  rows.addEventListener('change', event => {
    const row = event.target.closest('[data-color-row]');
    if (!row) return;
    updateCard(row);
    if (event.target.matches('input[type="file"][name$="-image"]')) updatePreview(row);
  });
})();
