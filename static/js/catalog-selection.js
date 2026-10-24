/* One selection per signed-in account and browser tab; IDs are rechecked by the server. */
(() => {
  const form = document.querySelector('[data-catalog-selection]');
  if (!form) return;
  const boxes = Array.from(form.querySelectorAll('[data-catalog-product-checkbox]'));
  const all = form.querySelector('[data-catalog-select-all]');
  const clear = form.querySelector('[data-catalog-clear]');
  const submit = form.querySelector('[data-catalog-bulk-submit]');
  const count = form.querySelector('[data-catalog-selection-count]');
  const results = form.querySelector('[data-catalog-select-results]');
  const feedback = form.querySelector('[data-catalog-selection-feedback]');
  const note = form.querySelector('[data-catalog-selection-note]');
  const key = form.dataset.selectionKey;
  const limit = Number(form.dataset.selectionLimit);
  let selected = new Set();
  let persistent = true;
  let busy = false;
  const validId = value => typeof value === 'string' && /^[1-9][0-9]{0,18}$/.test(value);
  const say = message => { feedback.textContent = message; feedback.hidden = !message; };
  const read = () => {
    try {
      const ids = JSON.parse(sessionStorage.getItem(key) || '[]');
      selected = new Set(Array.isArray(ids) && ids.length <= limit ? ids.filter(validId) : []);
    } catch (_) { selected = new Set(); }
  };
  const persist = () => {
    try { sessionStorage.setItem(key, JSON.stringify([...selected])); }
    catch (_) { persistent = false; }
    note.textContent = persistent
      ? 'La sélection est conservée dans cet onglet, même si vous changez de page ou de filtre. Ajouter des résultats conserve les articles déjà cochés. Les doublons archivés sont exclus.'
      : 'Ce navigateur ne peut pas conserver votre sélection entre les pages. Gardez cette page ouverte pour modifier les articles cochés.';
  };
  const update = () => {
    let visible = 0;
    boxes.forEach(box => {
      box.checked = selected.has(box.value);
      box.disabled = busy;
      if (box.checked) visible += 1;
      box.closest('[data-catalog-product-row]')?.classList.toggle('is-selected', box.checked);
    });
    all.checked = boxes.length > 0 && visible === boxes.length;
    all.indeterminate = visible > 0 && visible < boxes.length;
    all.disabled = busy || boxes.length === 0;
    clear.disabled = busy;
    clear.hidden = selected.size === 0;
    submit.disabled = busy || selected.size === 0;
    const outside = selected.size - visible;
    count.textContent = selected.size === 0 ? 'Cochez les articles à modifier ensemble.'
      : `${selected.size} article${selected.size > 1 ? 's' : ''} sélectionné${selected.size > 1 ? 's' : ''}${outside ? `, dont ${outside} hors de cette page` : ''}.`;
  };
  const add = ids => {
    const next = new Set([...selected, ...ids]);
    if (next.size > limit) {
      say(`Vous pouvez modifier jusqu’à ${limit} articles ensemble. Décochez des articles ou précisez les filtres. Votre sélection a été conservée.`);
      update();
      return false;
    }
    selected = next;
    persist(); update();
    return true;
  };
  read();
  try {
    const applied = JSON.parse(document.getElementById('catalog-selection-applied').textContent);
    if (Array.isArray(applied)) applied.forEach(id => selected.delete(String(id)));
  } catch (_) { /* No successful operation to remove from the selection. */ }
  persist(); update();
  form.querySelector('[data-catalog-select-all-control]').hidden = false;
  results.hidden = false;
  boxes.forEach(box => box.addEventListener('change', () => {
    say('');
    if (box.checked) add([box.value]);
    else { selected.delete(box.value); persist(); update(); }
  }));
  all.addEventListener('change', () => {
    say('');
    if (all.checked) add(boxes.map(box => box.value));
    else { boxes.forEach(box => selected.delete(box.value)); persist(); update(); }
  });
  clear.addEventListener('click', () => {
    selected.clear(); persist(); update(); say('La sélection a été vidée.');
    if (!all.disabled) all.focus();
  });
  results.addEventListener('click', async () => {
    if (busy) return;
    busy = true; results.disabled = true; update(); say('Préparation de la sélection…');
    try {
      const response = await fetch(form.dataset.selectionResultsUrl, {credentials: 'same-origin', headers: {'Accept': 'application/json'}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Impossible de sélectionner les résultats. Réessayez.');
      if (!Array.isArray(data.ids) || data.ids.some(id => !validId(id))) throw new Error('La liste reçue est invalide. Actualisez le catalogue.');
      if (add(data.ids)) say(`${data.ids.length} résultat${data.ids.length > 1 ? 's' : ''} pris en compte. Vérifiez la liste des articles à l’étape suivante.`);
    } catch (error) {
      say(error instanceof SyntaxError ? 'Reconnectez-vous ou actualisez le catalogue, puis réessayez. Votre sélection est conservée.' : error.message || 'La sélection n’a pas pu être chargée. Réessayez.');
    } finally { busy = false; results.disabled = false; update(); }
  });
  const remember = () => {
    try { sessionStorage.setItem(`${key}:return`, JSON.stringify({url: form.dataset.catalogReturn, y: window.scrollY})); }
    catch (_) { /* A product anchor remains available without browser storage. */ }
  };
  document.querySelectorAll('[data-catalog-remember]').forEach(link => link.addEventListener('click', remember));
  form.addEventListener('submit', event => {
    if (busy || !selected.size) { event.preventDefault(); return; }
    form.querySelectorAll('[data-catalog-selected-id]').forEach(input => input.remove());
    boxes.forEach(box => box.removeAttribute('name'));
    selected.forEach(id => {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = 'selected_products'; input.value = id;
      input.dataset.catalogSelectedId = ''; form.append(input);
    });
    remember();
  });
  const restorePosition = () => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(`${key}:return`) || 'null');
      if (saved?.url === form.dataset.catalogReturn && Number.isFinite(saved.y) && saved.y >= 0) {
        window.scrollTo({top: saved.y, behavior: 'instant'});
        sessionStorage.removeItem(`${key}:return`);
      }
    } catch (_) { /* Normal navigation and product anchors are the fallback. */ }
  };
  if (document.readyState === 'complete') restorePosition();
  else window.addEventListener('load', restorePosition, {once: true});
  window.addEventListener('pageshow', event => {
    if (event.persisted) { read(); persist(); update(); restorePosition(); }
  });
})();
