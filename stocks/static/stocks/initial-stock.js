(() => {
  const section = document.querySelector('[data-initial-stock]');
  if (!section) return;
  const form = section.closest('form') || document;
  const total = section.querySelector('[name="stock-TOTAL_FORMS"]');
  const maximum = Number(section.querySelector('[name="stock-MAX_NUM_FORMS"]').value);
  const add = section.querySelector('[data-add-stock]');
  const fallback = section.querySelector('[data-stock-fallback]');
  const fallbackRows = section.querySelector('[data-stock-rows]');
  const template = section.querySelector('[data-stock-template]');
  const status = section.querySelector('[data-stock-status]');
  const summary = section.querySelector('[data-stock-summary]');
  const summaryList = section.querySelector('[data-stock-summary-list]');
  const summaryEmpty = section.querySelector('[data-stock-summary-empty]');
  const detachedNote = section.querySelector('[data-stock-detached-note]');
  const sizeSuggestions = section.querySelector('#initial-stock-sizes');
  const colorSuggestions = section.querySelector('#initial-stock-colors');
  const declaredSizes = document.querySelector('[name="sizes"]');
  const colorEditor = document.querySelector('[data-color-editor]');
  const stockRows = new Set(fallbackRows.querySelectorAll('[data-stock-row]'));
  const colorCards = new Map();
  const linkedColors = new WeakMap();
  const normalize = value => value.trim().replace(/\s+/g, ' ');
  const keyFor = value => normalize(value).toLocaleLowerCase('fr');
  const field = (row, name) => row.querySelector(`[name$="-${name}"]`);
  const hasValues = row => ['size', 'color', 'quantity', 'reference', 'price'].some(name => field(row, name).value.trim()) || Boolean(row.querySelector('.errorlist'));
  const isPristine = row => !linkedColors.has(row) && !field(row, 'DELETE').checked && !hasValues(row);
  const canAdd = () => Number(total.value) < maximum || [...stockRows].some(isPristine);

  function registerColor(row) {
    if (colorCards.has(row)) return;
    colorCards.set(row, {
      row,
      name: field(row, 'name'),
      deleted: row.querySelector(`[name="${field(row, 'name').name.replace(/-name$/, '-DELETE')}"]`) || {checked: false},
      block: row.querySelector('[data-color-stock]'),
      rows: row.querySelector('[data-color-stock-rows]'),
      add: row.querySelector('[data-add-color-size]'),
      title: row.querySelector('[data-color-stock-title]'),
      empty: row.querySelector('[data-color-stock-empty]'),
      nameHint: row.querySelector('[data-color-stock-name-hint]'),
      price: field(row, 'color_price'),
      applyPrice: row.querySelector('[data-apply-color-price]'),
      priceStatus: row.querySelector('[data-color-price-status]'),
      sizes: field(row, 'sizes'),
      applySizes: row.querySelector('[data-apply-color-sizes]'),
      sizesError: row.querySelector('[data-color-sizes-error]'),
      sizesStatus: row.querySelector('[data-color-sizes-status]'),
      sizesPending: row.querySelector('[data-color-sizes-pending]'),
      sizesInitiallyInvalid: field(row, 'sizes')?.getAttribute('aria-invalid') === 'true',
    });
    const card = colorCards.get(row);
    if (card.sizes && card.sizesError) {
      const describedBy = new Set((card.sizes.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
      describedBy.add(card.sizesError.id);
      card.sizes.setAttribute('aria-describedby', [...describedBy].join(' '));
    }
  }

  function prepareStock(row) {
    field(row, 'size').setAttribute('list', sizeSuggestions.id);
    field(row, 'color').setAttribute('list', colorSuggestions.id);
    row.querySelector('[data-copy-stock-color]').hidden = false;
  }

  function placeRows(matchUnlinked) {
    const matches = new Map();
    colorCards.forEach(card => {
      const name = normalize(card.name.value);
      if (!name || card.deleted.checked) return;
      const key = keyFor(name);
      if (!matches.has(key)) matches.set(key, []);
      matches.get(key).push(card);
    });
    stockRows.forEach(row => {
      let owner = linkedColors.get(row);
      if (owner?.deleted.checked) {
        linkedColors.delete(row);
        owner = null;
        detachedNote.hidden = false;
        fallback.open = true;
      }
      if (!owner && matchUnlinked && !field(row, 'DELETE').checked && hasValues(row)) {
        const matching = matches.get(keyFor(field(row, 'color').value));
        if (matching?.length === 1) {
          owner = matching[0];
          linkedColors.set(row, owner);
        }
      }
      const colorField = row.querySelector('[data-stock-color-field]');
      const ownerName = owner ? normalize(owner.name.value) : '';
      if (ownerName) field(row, 'color').value = ownerName;
      colorField.hidden = Boolean(ownerName) && !colorField.querySelector('.errorlist');
      row.classList.toggle('is-color-linked', Boolean(ownerName));
      row.querySelector('[data-stock-legend]').textContent = ownerName ? 'Taille' : 'Déclinaison';
      row.querySelector('[data-copy-stock-color]').hidden = Boolean(owner);
      const destination = owner ? owner.rows : fallbackRows;
      if (row.parentElement !== destination) destination.append(row);
    });
  }

  function fillSuggestions(list, values) {
    const unique = new Map();
    values.forEach(value => {
      const label = normalize(value);
      if (label && !unique.has(keyFor(label))) unique.set(keyFor(label), label);
    });
    list.replaceChildren(...[...unique.values()].map(value => {
      const option = document.createElement('option');
      option.value = value;
      return option;
    }));
  }

  function listedSizes(card) {
    const value = card.sizes?.value || '';
    const sizes = new Map();
    value.split(/[,;\n]/).forEach(part => {
      const size = normalize(part);
      if (size && !sizes.has(keyFor(size))) sizes.set(keyFor(size), size);
    });
    const labels = [...sizes.values()];
    let error = '';
    if (value.length > 1000) error = 'La liste des tailles ne doit pas dépasser 1 000 caractères.';
    else if (labels.some(size => size.length > 50)) error = 'Chaque taille doit contenir au maximum 50 caractères. Séparez les tailles par des virgules.';
    return {labels, error};
  }

  function knownSizes(card) {
    const known = new Set();
    const colorKey = keyFor(card.name.value);
    card.row.querySelectorAll('[data-existing-stock-item][data-stock-size]').forEach(item => {
      const originalColor = item.dataset.stockColor || card.row.dataset.originalColor || '';
      if (colorKey === keyFor(originalColor)) known.add(keyFor(item.dataset.stockSize));
    });
    stockRows.forEach(row => {
      if (!field(row, 'DELETE').checked && hasValues(row) && keyFor(field(row, 'color').value) === colorKey) {
        known.add(keyFor(field(row, 'size').value || 'Taille unique'));
      }
    });
    return known;
  }

  function missingSizes(card) {
    const known = knownSizes(card);
    return listedSizes(card).labels.filter(size => !known.has(keyFor(size)));
  }

  function showSizesError(card, message) {
    card.sizesError.textContent = message;
    card.sizesError.hidden = false;
    card.sizes.setAttribute('aria-invalid', 'true');
  }

  function clearSizesError(card) {
    card.sizesError.textContent = '';
    card.sizesError.hidden = true;
    if (!card.sizesInitiallyInvalid) card.sizes.removeAttribute('aria-invalid');
  }

  function refreshSuggestions() {
    fillSuggestions(sizeSuggestions, [
      'XS', 'S', 'M', 'L', 'XL', 'XXL', 'Taille unique',
      ...(declaredSizes ? declaredSizes.value.split(/[,;\n]/) : []),
      ...[...stockRows].map(row => field(row, 'size').value),
    ]);
    fillSuggestions(colorSuggestions, [
      ...[...colorCards.values()].filter(card => !card.deleted.checked).map(card => card.name.value),
      ...[...stockRows].map(row => field(row, 'color').value),
    ]);
  }

  function refresh(matchUnlinked = false) {
    placeRows(matchUnlinked);
    const combinations = new Map();
    const groups = new Map();
    const addAllowed = canAdd();
    add.disabled = !addAllowed;
    stockRows.forEach(row => {
      const ignored = field(row, 'DELETE').checked;
      row.classList.toggle('is-ignored', ignored);
      row.querySelector('[data-copy-stock-color]').disabled = ignored || !addAllowed;
      row.querySelector('[data-stock-duplicate]').hidden = true;
      if (ignored || !hasValues(row)) return;
      const size = normalize(field(row, 'size').value) || 'Taille unique';
      const color = normalize(field(row, 'color').value) || 'Coloris unique';
      const rawQuantity = field(row, 'quantity').value;
      const combinationKey = JSON.stringify([keyFor(size), keyFor(color)]);
      if (!combinations.has(combinationKey)) combinations.set(combinationKey, []);
      combinations.get(combinationKey).push(row);
      const groupKey = keyFor(color);
      if (!groups.has(groupKey)) groups.set(groupKey, {color, entries: []});
      groups.get(groupKey).entries.push({size, rawQuantity, combinationKey, rawPrice: field(row, 'price').value || linkedColors.get(row)?.price?.value || ''});
    });
    combinations.forEach(matching => {
      if (matching.length > 1) matching.forEach(row => {
        row.querySelector('[data-stock-duplicate]').hidden = false;
      });
    });
    colorCards.forEach(card => {
      const name = normalize(card.name.value);
      card.block.hidden = card.deleted.checked;
      card.title.textContent = name ? `· ${name}` : '';
      card.add.disabled = card.deleted.checked || !name || !addAllowed;
      if (card.applySizes) card.applySizes.disabled = card.deleted.checked || !name;
      if (card.applyPrice) card.applyPrice.disabled = card.deleted.checked || !name;
      card.nameHint.hidden = Boolean(name);
      card.empty.hidden = [...stockRows].some(row => linkedColors.get(row) === card && !field(row, 'DELETE').checked);
      if (card.sizesPending) {
        const listed = listedSizes(card);
        const pending = missingSizes(card).length;
        card.sizesPending.hidden = !pending || Boolean(listed.error) || !name || card.deleted.checked;
        card.sizesPending.textContent = `${pending} ${pending === 1 ? 'taille à préparer' : 'tailles à préparer'}. Cliquez sur « Préparer les tailles », puis renseignez les quantités.`;
      }
    });
    const elements = [...groups.values()].map(group => {
      const wrapper = document.createElement('div');
      const term = document.createElement('dt');
      term.textContent = group.color;
      const description = document.createElement('dd');
      const list = document.createElement('ul');
      group.entries.forEach(({size, rawQuantity, combinationKey, rawPrice}) => {
        const item = document.createElement('li');
        const quantity = Number(rawQuantity);
        let stockLabel = 'quantité à renseigner';
        if (rawQuantity !== '' && Number.isInteger(quantity) && quantity >= 0) {
          stockLabel = quantity === 0 ? 'rupture de stock' : `${quantity.toLocaleString('fr-FR')} ${quantity === 1 ? 'unité' : 'unités'}`;
          item.classList.toggle('is-out-of-stock', quantity === 0);
        }
        const duplicate = combinations.get(combinationKey).length > 1;
        item.classList.toggle('is-duplicate', duplicate);
        const priceValue = Number((rawPrice || '').replace(',', '.'));
        const priceLabel = rawPrice === '' ? 'prix général' : Number.isFinite(priceValue) && priceValue > 0 ? priceValue.toLocaleString('fr-FR', {style: 'currency', currency: 'EUR'}) : 'prix à vérifier';
        item.textContent = `${size} · ${stockLabel} · ${priceLabel}${duplicate ? ' · doublon' : ''}`;
        list.append(item);
      });
      description.append(list);
      wrapper.append(term, description);
      return wrapper;
    });
    summaryList.replaceChildren(...elements);
    summaryEmpty.hidden = elements.length > 0;
    refreshSuggestions();
  }

  function createStockRow() {
    const pristine = [...stockRows].find(isPristine);
    if (pristine) return pristine;
    const index = Number(total.value);
    if (index >= maximum) return null;
    const fragment = document.createElement('template');
    fragment.innerHTML = template.innerHTML.replaceAll('__prefix__', String(index));
    const row = fragment.content.querySelector('[data-stock-row]');
    row.querySelector('[data-stock-number]').textContent = String(index + 1);
    prepareStock(row);
    fallbackRows.append(fragment.content);
    stockRows.add(row);
    total.value = index + 1;
    return row;
  }

  function addStock(card, color) {
    const name = card ? normalize(card.name.value) : normalize(color || '');
    if (card && (!name || card.deleted.checked)) return;
    const row = createStockRow();
    if (!row) return;
    if (card) {
      linkedColors.set(row, card);
      field(row, 'price').value = card.price?.value || '';
    }
    if (name) field(row, 'color').value = name;
    if (!card) fallback.open = true;
    refresh(false);
    status.textContent = name
      ? `Nouvelle taille pour le coloris ${name}. Indiquez sa taille et sa quantité.`
      : 'Nouvelle déclinaison ajoutée. Indiquez sa taille, sa couleur et sa quantité.';
    field(row, 'size').focus();
  }

  function applyListedSizes(card) {
    if (!card?.sizes || card.deleted.checked) return;
    clearSizesError(card);
    card.sizesStatus.textContent = '';
    const name = normalize(card.name.value);
    if (!name) {
      showSizesError(card, 'Indiquez le nom de la couleur avant de préparer ses tailles.');
      card.name.focus();
      return;
    }
    const listed = listedSizes(card);
    if (listed.error) {
      showSizesError(card, listed.error);
      card.sizes.focus();
      return;
    }
    if (!listed.labels.length) {
      showSizesError(card, 'Indiquez au moins une taille, par exemple M, L, XL.');
      card.sizes.focus();
      return;
    }
    const missing = missingSizes(card);
    const capacity = maximum - Number(total.value) + [...stockRows].filter(isPristine).length;
    if (missing.length > capacity) {
      showSizesError(card, `Réduisez cette liste pour rester sous la limite de ${maximum} nouvelles déclinaisons par enregistrement.`);
      card.sizes.focus();
      return;
    }
    const created = [];
    missing.forEach(size => {
      const row = createStockRow();
      linkedColors.set(row, card);
      field(row, 'color').value = name;
      field(row, 'size').value = size;
      field(row, 'price').value = card.price?.value || '';
      created.push(row);
    });
    card.sizes.value = listed.labels.join(', ');
    refresh(false);
    card.sizesStatus.textContent = created.length
      ? `${created.length} ${created.length === 1 ? 'taille préparée' : 'tailles préparées'} pour ${name}. Renseignez chaque quantité ci-dessous.`
      : 'Les tailles indiquées sont déjà enregistrées ou préparées. Leurs quantités et références sont conservées.';
    if (created.length) field(created[0], 'quantity').focus();
  }

  function applyColorPrice(card) {
    if (!card?.price || card.deleted.checked || !normalize(card.name.value)) return;
    const inputs = [
      ...[...stockRows].filter(row => linkedColors.get(row) === card && !field(row, 'DELETE').checked).map(row => field(row, 'price')),
      ...card.row.querySelectorAll('[data-existing-price-row] [name$="-price"]'),
    ];
    inputs.forEach(input => { input.value = card.price.value; });
    card.priceStatus.textContent = inputs.length
      ? `Prix appliqué à ${inputs.length} ${inputs.length === 1 ? 'taille' : 'tailles'}. Vous pouvez encore ajuster chaque prix avant d’enregistrer.`
      : 'Préparez les tailles : ce prix sera repris pour chaque nouvelle taille.';
    refresh(false);
  }

  if (colorEditor) {
    colorEditor.querySelectorAll('[data-color-row]').forEach(registerColor);
    section.classList.add('is-grouped');
    section.querySelector('[data-stock-section-title]').hidden = true;
    section.querySelector('[data-stock-manual-intro]').hidden = true;
    colorEditor.append(section);
    colorEditor.addEventListener('catalog:color-added', event => {
      registerColor(event.detail.row);
      refresh(true);
    });
  }
  add.hidden = false;
  summary.hidden = false;
  stockRows.forEach(prepareStock);
  refresh(true);
  fallback.open = [...fallbackRows.querySelectorAll('[data-stock-row]')].some(row => hasValues(row) || field(row, 'DELETE').checked);
  add.addEventListener('click', () => addStock());
  form.addEventListener('click', event => {
    const applyPrice = event.target.closest('[data-apply-color-price]');
    if (applyPrice) {
      applyColorPrice(colorCards.get(applyPrice.closest('[data-color-row]')));
      return;
    }
    const apply = event.target.closest('[data-apply-color-sizes]');
    if (apply) {
      applyListedSizes(colorCards.get(apply.closest('[data-color-row]')));
      return;
    }
    const addSize = event.target.closest('[data-add-color-size]');
    if (addSize) {
      addStock(colorCards.get(addSize.closest('[data-color-row]')));
      return;
    }
    const copy = event.target.closest('[data-copy-stock-color]');
    if (copy) addStock(null, field(copy.closest('[data-stock-row]'), 'color').value || 'Coloris unique');
  });
  form.addEventListener('input', event => {
    if (event.target.closest('[data-stock-row]')) refresh(false);
    else if ((event.target.name?.startsWith('colors-') || event.target.name?.startsWith('original-'))) {
      const card = colorCards.get(event.target.closest('[data-color-row]'));
      if (card?.sizes && event.target === card.sizes) {
        clearSizesError(card);
        card.sizesStatus.textContent = '';
      }
      refresh(true);
    }
  });
  form.addEventListener('change', event => {
    if (event.target.closest('[data-stock-row]') || (event.target.name?.startsWith('colors-') || event.target.name?.startsWith('original-'))) refresh(true);
  });
  form.addEventListener('submit', event => {
    refresh(true);
    for (const card of colorCards.values()) {
      if (!card.sizes || card.deleted.checked) continue;
      const listed = listedSizes(card);
      if (listed.error) {
        event.preventDefault();
        showSizesError(card, listed.error);
        card.sizes.focus();
        return;
      }
      if (missingSizes(card).length) {
        event.preventDefault();
        showSizesError(card, 'Cliquez sur « Préparer les tailles », puis renseignez leurs quantités avant d’enregistrer le produit.');
        (card.applySizes.disabled ? card.name : card.applySizes).focus();
        return;
      }
    }
  });
  declaredSizes?.addEventListener('input', refreshSuggestions);
})();
