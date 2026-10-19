(() => {
  const section = document.querySelector('[data-color-editor]');
  if (!section) return;
  const total = section.querySelector('[name="colors-TOTAL_FORMS"]');
  const maximum = Number(section.querySelector('[name="colors-MAX_NUM_FORMS"]').value);
  const add = section.querySelector('[data-add-color]');
  const rows = section.querySelector('[data-color-rows]');
  const normalize = value => value.trim().replace(/\s+/g, ' ');
  const previews = new WeakMap();
  const mainInput = document.querySelector('input[type="file"][name="main_image"]');
  const mainClear = document.querySelector('[name="main_image-clear"]');
  const removeOriginal = document.querySelector('[name="remove_original"]');
  const originalCard = rows.querySelector('[data-original-color-card]');

  function updateCard(row) {
    const nameField = row.querySelector('[name$="-name"]');
    const name = normalize(nameField.value);
    row.querySelector('[data-color-name-label]').textContent = name || (row.hasAttribute('data-original-color-card') ? 'Coloris d’origine' : '');
    const deleteField = row.querySelector(`[name="${nameField.name.replace(/-name$/, '-DELETE')}"]`);
    const deleted = deleteField?.checked || false;
    row.classList.toggle('is-removed', deleted);
    const removalNote = row.querySelector('[data-color-removal-note]');
    if (removalNote) removalNote.hidden = !deleted;
    const warning = row.querySelector('[data-existing-color-warning]');
    if (warning) warning.hidden = normalize(row.dataset.originalColor).toLocaleLowerCase('fr') === name.toLocaleLowerCase('fr');
  }

  function updatePreview(row) {
    const isOriginal = row.hasAttribute('data-original-color-card');
    const input = isOriginal ? mainInput : row.querySelector('input[type="file"][name$="-image"]');
    const image = row.querySelector('[data-color-preview]');
    const placeholder = row.querySelector('[data-color-placeholder]');
    const previous = previews.get(row);
    if (previous) URL.revokeObjectURL(previous);
    previews.delete(row);
    const file = input?.files?.[0];
    let existing = image.dataset.originalImage || '';
    if (isOriginal) {
      const staticImage = row.dataset.staticImage || '';
      const originalImage = row.dataset.mainImageUrl || '';
      if (mainClear?.checked) existing = removeOriginal?.checked ? '' : staticImage;
      else if (removeOriginal?.checked && originalImage === staticImage) existing = '';
    }
    const source = file ? URL.createObjectURL(file) : existing;
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
  if (originalCard) {
    const updateOriginalPreview = () => updatePreview(originalCard);
    mainInput?.addEventListener('change', updateOriginalPreview);
    mainClear?.addEventListener('change', updateOriginalPreview);
    removeOriginal?.addEventListener('change', updateOriginalPreview);
    updateOriginalPreview();
  }
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
