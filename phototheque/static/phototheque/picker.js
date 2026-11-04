/* Choosing a library photo changes only its own file field, never the form. */
(() => {
  'use strict';
  const readIds = value => {
    try {
      const ids = JSON.parse(value || '[]');
      if (!Array.isArray(ids) || ids.length > 12) return [];
      return [...new Set(ids.map(String).filter(id => /^[1-9]\d{0,18}$/.test(id)))];
    } catch (_) { return []; }
  };
  const safeURL = (value, base) => {
    if (typeof value !== 'string' || !value) return '';
    try {
      const url = new URL(value, base);
      return ['http:', 'https:'].includes(url.protocol) && url.origin === new URL(base).origin ? url.href : '';
    } catch (_) { return ''; }
  };

  function mount(doc, win) {
    const modal = doc.querySelector('[data-photo-library-picker]');
    if (!modal || typeof modal.showModal !== 'function') return null;
    const endpoint = safeURL(modal.dataset.libraryUrl, win.location.href);
    if (!endpoint) return null;
    const find = selector => modal.querySelector(selector);
    const search = find('[data-library-search]');
    const query = search.querySelector('[name="q"]');
    const folder = search.querySelector('[name="folder"]');
    const results = find('[data-library-results]');
    const status = find('[data-library-status]');
    const previous = find('[data-library-previous]');
    const next = find('[data-library-next]');
    const pageLabel = find('[data-library-page]');
    const count = find('[data-library-count]');
    const confirm = find('[data-library-confirm]');
    const preview = find('[data-library-preview]');
    const previewImage = find('[data-library-preview-image]');
    const previewTitle = find('[data-library-preview-title]');
    const previewSize = find('[data-library-preview-size]');
    const toggle = find('[data-library-toggle]');
    const initialized = new WeakSet();
    const metadata = new WeakMap();
    let field = null, opener = null, selection = new Map(), current = null;
    let page = 1, pages = 1, controller = null, generation = 0, fetching = false;

    const element = (tag, className, text) => {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const button = (className, text) => {
      const node = element('button', className, text); node.type = 'button'; return node;
    };
    const fileInput = owner => owner.querySelector('input[type="file"]');
    const hiddenInput = owner => owner.querySelector('[data-library-value]');
    const multiple = owner => owner.dataset.libraryMultiple === 'true';
    const maximum = owner => Math.max(1, Math.min(12, Number(owner.dataset.libraryLimit) || 1));
    function notifyField(owner) {
      const input = hiddenInput(owner);
      input.dispatchEvent(new win.Event('input', {bubbles: true}));
      input.dispatchEvent(new win.Event('change', {bubbles: true}));
      owner.dispatchEvent(new win.CustomEvent('phototheque:selection', {bubbles: true,
        detail: {photos: [...(metadata.get(owner) || new Map()).values()]}}));
    }
    function renderField(owner) {
      const input = fileInput(owner), ids = readIds(hiddenInput(owner).value);
      const list = owner.querySelector('[data-library-selection]');
      const records = metadata.get(owner) || new Map();
      input.dataset.libraryPreview = safeURL(records.get(ids[0])?.url, win.location.href);
      input.dataset.libraryPhotos = JSON.stringify(ids.map(id => records.get(id)).filter(Boolean));
      list.replaceChildren();
      for (const id of ids) {
        const photo = records.get(id) || {id, title: `Photo n° ${id}`};
        const row = element('li'); row.dataset.libraryId = id;
        const thumb = safeURL(photo.thumb || photo.url, win.location.href);
        if (thumb) { const img = element('img'); img.src = thumb; img.alt = ''; img.width = 48; img.height = 48; row.append(img); }
        row.append(element('span', '', photo.title));
        const remove = button('photo-library-remove', 'Retirer');
        remove.dataset.libraryRemove = id;
        remove.setAttribute('aria-label', `Retirer ${photo.title} de la sélection`);
        row.append(remove); list.append(row);
      }
      list.hidden = !ids.length;
      input.required = input.dataset.libraryRequired === 'true' && ids.length === 0;
      const tooMany = ids.length + (input.files?.length || 0) > maximum(owner);
      input.setCustomValidity(tooMany ? `Choisissez au maximum ${maximum(owner)} photos, fichiers et bibliothèque compris.` : '');
      const note = owner.querySelector('[data-library-field-status]');
      note.textContent = tooMany ? input.validationMessage : ids.length ?
        `${ids.length} photo${ids.length > 1 ? 's' : ''} de la bibliothèque sélectionnée${ids.length > 1 ? 's' : ''}. Enregistrez la page pour appliquer ce choix.` : '';
    }
    function initialize(owner) {
      if (initialized.has(owner)) return;
      initialized.add(owner);
      const input = fileInput(owner);
      if (!input) return;
      input.dataset.libraryRequired = input.required ? 'true' : 'false';
      const records = new Map();
      owner.querySelectorAll('[data-library-id]').forEach(row => records.set(row.dataset.libraryId, {
        id: row.dataset.libraryId, title: row.querySelector('span')?.textContent || 'Photo sélectionnée',
        thumb: row.querySelector('img')?.getAttribute('src') || '', url: row.dataset.libraryUrl || ''
      }));
      metadata.set(owner, records);
      const trigger = owner.querySelector('[data-library-open]');
      trigger.hidden = false; trigger.disabled = input.disabled;
      renderField(owner);
    }
    doc.addEventListener('phototheque:assign', event => {
      const owner = event.target.closest('[data-photo-library-field]');
      if (!owner || !Array.isArray(event.detail?.photos)) return;
      initialize(owner);
      const photos = event.detail.photos.slice(0, maximum(owner)).filter(photo => /^[1-9]\d{0,18}$/.test(String(photo.id)));
      hiddenInput(owner).value = JSON.stringify(photos.map(photo => String(photo.id)));
      metadata.set(owner, new Map(photos.map(photo => [String(photo.id), photo])));
      renderField(owner); notifyField(owner);
    });
    function refreshSelection() {
      count.textContent = selection.size ? `${selection.size} photo${selection.size > 1 ? 's' : ''} sélectionnée${selection.size > 1 ? 's' : ''}${multiple(field) ? ` · ${maximum(field)} maximum` : ''}.` : 'Aucune photo sélectionnée.';
      confirm.disabled = selection.size === 0;
      results.querySelectorAll('[data-photo-id]').forEach(node => node.setAttribute('aria-pressed', selection.has(node.dataset.photoId) ? 'true' : 'false'));
      if (current) toggle.textContent = selection.has(current.id) ? 'Retirer de la sélection' : 'Choisir cette photo';
    }
    function showPreview(photo) {
      current = photo;
      preview.hidden = false;
      previewImage.hidden = false; toggle.hidden = false;
      previewImage.src = photo.url; previewImage.alt = photo.title;
      previewTitle.textContent = photo.title;
      previewSize.textContent = photo.width && photo.height ? `${photo.width} × ${photo.height} pixels` : '';
      refreshSelection();
    }
    function chooseCurrent() {
      if (!current || !field) return;
      if (selection.has(current.id)) selection.delete(current.id);
      else {
        if (!multiple(field)) selection.clear();
        const uploads = multiple(field) ? fileInput(field).files?.length || 0 : 0;
        if (selection.size + uploads >= maximum(field)) {
          status.textContent = `Vous avez atteint la limite de ${maximum(field)} photos. Retirez un choix ou un fichier avant d’en ajouter un autre.`;
          return;
        }
        selection.set(current.id, current);
      }
      refreshSelection();
    }
    function stopRequest() {
      generation += 1;
      controller?.abort(); controller = null; fetching = false;
    }
    function close() {
      stopRequest();
      if (modal.open) modal.close();
      const target = opener;
      field = null; opener = null;
      if (target?.isConnected) target.focus({preventScroll: true});
    }
    async function load(targetPage = 1) {
      if (!field || !modal.open) return;
      stopRequest();
      const ownGeneration = generation;
      const request = new win.AbortController(); controller = request;
      const timeout = win.setTimeout(() => request.abort(), 10000);
      fetching = true; results.setAttribute('aria-busy', 'true');
      previous.disabled = true; next.disabled = true;
      status.textContent = 'Chargement des photos…';
      const url = new URL(endpoint);
      url.searchParams.set('q', query.value.trim().slice(0, 160));
      url.searchParams.set('folder', folder.value);
      url.searchParams.set('page', String(targetPage));
      try {
        const response = await win.fetch(url.href, {method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}, signal: request.signal});
        if (response.redirected || response.status === 401 || response.status === 403 || !response.headers.get('content-type')?.includes('application/json')) {
          throw new Error('Votre session a expiré ou votre accès a changé. Fermez cette fenêtre et reconnectez-vous ; votre saisie est conservée.');
        }
        if (!response.ok) throw new Error('Les photos ne sont pas disponibles pour le moment. Réessayez avec « Rechercher ».');
        const data = await response.json();
        if (!field || !modal.open || ownGeneration !== generation) return;
        if (!Array.isArray(data.results) || data.results.length > 24 || !data.pagination || !Array.isArray(data.folders)) throw new Error('La bibliothèque n’a pas pu être chargée. Réessayez avec « Rechercher ».');
        const retainedFolder = folder.value;
        folder.replaceChildren(new win.Option('Tous les dossiers', ''), new win.Option('Sans dossier', 'unfiled'));
        for (const item of data.folders) {
          if (/^[1-9]\d{0,18}$/.test(String(item.id))) folder.append(new win.Option(String(item.name).slice(0, 80), String(item.id)));
        }
        folder.value = retainedFolder;
        results.replaceChildren();
        let rendered = 0;
        for (const item of data.results) {
          const id = String(item.id), url = safeURL(item.url, win.location.href), thumb = safeURL(item.thumb || item.url, win.location.href);
          if (!/^[1-9]\d{0,18}$/.test(id) || !url || !thumb) continue;
          const photo = {id, title: String(item.title || 'Photo sans nom').slice(0, 160), url, thumb,
            width: Number.isSafeInteger(item.width) ? item.width : 0, height: Number.isSafeInteger(item.height) ? item.height : 0};
          if (selection.has(id)) selection.set(id, photo);
          const row = element('li'), card = button('photo-library-card');
          card.dataset.photoId = id;
          card.setAttribute('aria-label', `Aperçu : ${photo.title}`);
          const img = element('img'); img.src = thumb; img.alt = ''; img.loading = 'lazy'; img.width = 180; img.height = 140;
          card.append(img, element('span', '', photo.title));
          card.addEventListener('click', () => { showPreview(photo); chooseCurrent(); });
          row.append(card); results.append(row); rendered += 1;
        }
        page = Math.max(1, Number(data.pagination.page) || 1);
        pages = Math.max(1, Number(data.pagination.pages) || 1);
        pageLabel.textContent = `Page ${page} sur ${pages}`;
        previous.disabled = !data.pagination.has_previous; next.disabled = !data.pagination.has_next;
        status.textContent = rendered ? `${Number(data.pagination.total) || rendered} photo${Number(data.pagination.total) > 1 ? 's' : ''} trouvée${Number(data.pagination.total) > 1 ? 's' : ''}. Cliquez sur une photo pour la voir et la choisir.` : 'Aucune photo ne correspond. Essayez un autre nom ou dossier, ou ajoutez un fichier dans le formulaire.';
        refreshSelection();
      } catch (error) {
        if (ownGeneration === generation && modal.open) status.textContent = request.signal.aborted ? 'Le chargement prend trop de temps. Réessayez avec « Rechercher ».' : error.message || 'La bibliothèque est momentanément indisponible.';
      } finally {
        win.clearTimeout(timeout);
        if (ownGeneration === generation) { controller = null; fetching = false; results.removeAttribute('aria-busy'); }
      }
    }
    function open(owner, trigger) {
      initialize(owner);
      if (fileInput(owner).disabled) return;
      field = owner; opener = trigger;
      const records = metadata.get(owner) || new Map();
      selection = new Map(readIds(hiddenInput(owner).value).map(id => [id, records.get(id) || {id, title: `Photo n° ${id}`} ]));
      current = null; preview.hidden = false; previewImage.hidden = true; toggle.hidden = true;
      previewTitle.textContent = 'Votre photo en détail';
      previewSize.textContent = 'Choisissez une photo pour voir son aperçu et ses dimensions.';
      previewImage.removeAttribute('src'); results.replaceChildren();
      find('#photo-library-title').textContent = multiple(owner) ? 'Choisir des photos' : 'Choisir une photo';
      modal.showModal(); refreshSelection();
      query.focus({preventScroll: true}); load(1);
    }
    doc.querySelectorAll('[data-photo-library-field]').forEach(initialize);
    doc.addEventListener('click', event => {
      const trigger = event.target.closest('[data-library-open]');
      if (trigger) { event.preventDefault(); open(trigger.closest('[data-photo-library-field]'), trigger); return; }
      const remove = event.target.closest('[data-library-remove]');
      if (!remove) return;
      const owner = remove.closest('[data-photo-library-field]');
      const ids = readIds(hiddenInput(owner).value).filter(id => id !== remove.dataset.libraryRemove);
      hiddenInput(owner).value = JSON.stringify(ids);
      metadata.get(owner)?.delete(remove.dataset.libraryRemove);
      renderField(owner); notifyField(owner);
      owner.querySelector('[data-library-open]').focus({preventScroll: true});
    });
    doc.addEventListener('change', event => {
      const owner = event.target.closest('[data-photo-library-field]');
      if (!owner) return;
      const input = fileInput(owner);
      if (event.target === input && input.files?.length && !multiple(owner)) {
        hiddenInput(owner).value = '[]'; metadata.set(owner, new Map());
      }
      if (event.target.matches('input[type="checkbox"]') && event.target.checked) {
        input.value = ''; hiddenInput(owner).value = '[]'; metadata.set(owner, new Map());
      }
      if (event.target === input || event.target.matches('input[type="checkbox"]')) { renderField(owner); notifyField(owner); }
    });
    search.addEventListener('submit', event => { event.preventDefault(); event.stopPropagation(); load(1); });
    folder.addEventListener('change', () => load(1));
    previous.addEventListener('click', () => { if (!fetching && page > 1) load(page - 1); });
    next.addEventListener('click', () => { if (!fetching && page < pages) load(page + 1); });
    toggle.addEventListener('click', chooseCurrent);
    confirm.addEventListener('click', () => {
      if (!field || !selection.size) return;
      const owner = field, input = fileInput(owner);
      if (!multiple(owner)) input.value = '';
      const clear = owner.querySelector('input[type="checkbox"]');
      if (clear) clear.checked = false;
      hiddenInput(owner).value = JSON.stringify([...selection.keys()]);
      metadata.set(owner, new Map(selection));
      renderField(owner); notifyField(owner); close();
    });
    modal.querySelectorAll('[data-library-close]').forEach(node => node.addEventListener('click', close));
    modal.addEventListener('cancel', event => { event.preventDefault(); close(); });
    modal.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const controls = [...modal.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]')].filter(node => !node.closest('[hidden]'));
      if (!controls.length) return;
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && doc.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && doc.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    const observer = new win.MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
      if (node.nodeType !== 1) return;
      if (node.matches('[data-photo-library-field]')) initialize(node);
      node.querySelectorAll('[data-photo-library-field]').forEach(initialize);
    })));
    observer.observe(doc.body, {childList: true, subtree: true});
    return {open, close, load, chooseCurrent, showPreview, renderField, get selection() { return selection; }};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {readIds, safeURL, mount};
  if (typeof document !== 'undefined') mount(document, window);
})();
