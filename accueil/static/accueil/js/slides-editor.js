(() => {
  'use strict';
  const form = document.querySelector('[data-slides-editor]');
  if (!form) return;
  const list = form.querySelector('[data-slide-list]');
  const template = form.querySelector('[data-slide-template]');
  const total = form.querySelector('[name="slides-TOTAL_FORMS"]');
  if (!list || !template || !total) return;
  const MAX_SLIDES = 5;
  const MAX_FORMS = 10;
  const objectURLs = new Map();
  const announcement = form.querySelector('[data-slide-announcement]');
  const error = form.querySelector('[data-slide-error]');
  const picker = form.querySelector('#id_new_photos');
  const preview = form.querySelector('[data-slides-preview]');
  const frame = preview?.querySelector('[data-preview-frame]');
  let previewData, previewHTML;
  try {
    previewData = JSON.parse(document.getElementById('slideshow-preview-data')?.textContent || '{}');
    previewHTML = JSON.parse(document.getElementById('slideshow-preview-document')?.textContent || 'null');
  } catch { /* The ordinary editing form remains fully usable. */ }
  const featurePreview = previewData?.section === 'feature';
  const previewRootSelector = featurePreview ? '.feature-visual' : '.summit-hero';
  let previewIndex = 0;
  let previewMode = 'desktop';
  let frameReady = false;
  let frameHeight = 720;
  let previewPhotos = [];
  let resizeObserver;
  const cards = () => Array.from(list.querySelectorAll('[data-slide-card]'));
  const field = (card, name) => card.querySelector(`[name$="-${name}"]`);
  const removed = card => Boolean(field(card, 'DELETE')?.checked);
  const existing = card => card.dataset.existing === 'true';
  const hasFile = (card, name) => Boolean(field(card, name)?.files?.length);
  const hasContent = card => existing(card) || hasFile(card, 'image') || hasFile(card, 'mobile_image') || Boolean(field(card, 'alt')?.value.trim());
  const activeCards = () => cards().filter(card => !removed(card) && hasContent(card));
  const say = message => { if (announcement) announcement.textContent = message; };
  const showError = message => { if (error) { error.textContent = message; error.hidden = !message; } };

  function releaseCard(card) {
    const record = objectURLs.get(card);
    if (record) Object.values(record).forEach(item => URL.revokeObjectURL(item.url));
    objectURLs.delete(card);
  }
  function fileURL(card, name) {
    const file = field(card, name)?.files?.[0];
    const record = objectURLs.get(card) || {};
    const previous = record[name];
    if (previous && previous.file === file) return previous.url;
    if (previous) { URL.revokeObjectURL(previous.url); delete record[name]; }
    if (!file) { objectURLs.set(card, record); return ''; }
    const url = URL.createObjectURL(file);
    record[name] = {file, url};
    objectURLs.set(card, record);
    return url;
  }
  function photoFor(card) {
    const desktop = fileURL(card, 'image') || card.dataset.savedImage || '';
    const clear = card.querySelector('[data-slide-mobile-clear]')?.checked;
    const mobile = clear ? '' : fileURL(card, 'mobile_image') || card.dataset.savedMobile || '';
    return {desktop, mobile, alt: field(card, 'alt')?.value.trim() || card.dataset.savedAlt || 'Photo sans description'};
  }
  function updateCardPreview(card) {
    const photo = photoFor(card);
    [['desktop', photo.desktop], ['mobile', photo.mobile]].forEach(([name, url]) => {
      const box = card.querySelector(`[data-image-preview="${name}"]`);
      const img = box?.querySelector('img');
      if (box) box.hidden = !url;
      if (img) { if (url) img.src = url; else img.removeAttribute('src'); img.alt = photo.alt; }
    });
    const placeholder = card.querySelector('[data-mobile-placeholder]');
    if (placeholder) placeholder.hidden = Boolean(photo.mobile);
    const caption = card.querySelector('[data-image-caption="desktop"]');
    if (caption) caption.textContent = hasFile(card, 'image') ? 'Nouvelle photo · pas encore enregistrée' : existing(card) ? 'Photo actuellement enregistrée' : 'JPEG, PNG ou WebP. 10 Mo maximum par photo.';
  }
  function update() {
    const active = activeCards();
    let number = 0;
    cards().forEach(card => {
      const deleted = removed(card);
      const content = hasContent(card);
      const live = !deleted && content;
      if (live) number += 1;
      card.classList.toggle('is-deleted', deleted);
      card.hidden = deleted && !existing(card) || !content && !deleted && active.length >= MAX_SLIDES;
      const position = field(card, 'position');
      if (position) position.value = live ? String(number) : '';
      const title = card.querySelector('[data-slide-title]');
      if (title) title.textContent = deleted ? 'Photo retirée' : live ? `Photo supplémentaire ${number}` : 'Nouvelle photo';
      const state = card.querySelector('[data-slide-state]');
      if (state) state.textContent = deleted ? 'Elle sera retirée lorsque vous enregistrerez.' : existing(card) ? 'Photo enregistrée · modifications prises en compte à l’enregistrement' : content ? 'Nouvelle photo · pas encore enregistrée' : 'Choisissez une photo et décrivez-la avant d’enregistrer.';
      const move = card.querySelector('[data-slide-move]');
      if (move) move.hidden = !live;
      const up = card.querySelector('[data-slide-up]');
      const down = card.querySelector('[data-slide-down]');
      if (up) up.disabled = !live || active.indexOf(card) === 0;
      if (down) down.disabled = !live || active.indexOf(card) === active.length - 1;
      const undo = card.querySelector('[data-slide-undo]');
      if (undo) undo.hidden = !deleted || !existing(card);
      const positionBox = card.querySelector('[data-slide-position-field]');
      if (positionBox) positionBox.hidden = true;
      ['image', 'alt', 'position'].forEach(name => {
        const input = field(card, name);
        if (input) input.required = live && (name !== 'image' || !card.dataset.savedImage);
      });
      updateCardPreview(card);
    });
    const count = form.querySelector('[data-slide-count]');
    if (count) count.textContent = `${active.length} / ${MAX_SLIDES} photo${active.length > 1 ? 's' : ''} dans votre sélection`;
    const add = form.querySelector('[data-slide-add]');
    if (add) add.disabled = active.length >= MAX_SLIDES;
    if (picker) picker.disabled = active.length >= MAX_SLIDES;
    if (preview && !preview.hidden) refreshPreview();
  }
  function blankCard() {
    return cards().find(card => !existing(card) && (!hasContent(card) || removed(card)));
  }
  function resetNewCard(card) {
    releaseCard(card);
    card.querySelectorAll('input').forEach(input => {
      if (input.type === 'checkbox') input.checked = false;
      else input.value = '';
      input.removeAttribute('aria-invalid');
    });
    card.querySelectorAll('.errorlist').forEach(node => node.remove());
    card.querySelectorAll('.has-error').forEach(node => node.classList.remove('has-error'));
    card.hidden = false;
    return card;
  }
  function newCard() {
    let card = blankCard();
    if (card) {
      if (removed(card)) resetNewCard(card);
      return card;
    }
    const index = Number(total.value);
    if (!Number.isInteger(index) || index >= MAX_FORMS) {
      showError('Enregistrez vos modifications avant d’ajouter d’autres photos.');
      return null;
    }
    const wrapper = document.createElement('template');
    wrapper.innerHTML = template.innerHTML.replaceAll('__prefix__', String(index));
    card = wrapper.content.querySelector('[data-slide-card]');
    if (!card) return null;
    list.append(card);
    total.value = String(index + 1);
    return card;
  }
  function moveCard(card, direction, button) {
    const active = activeCards();
    const index = active.indexOf(card);
    const target = active[index + direction];
    if (!target) return;
    if (direction < 0) list.insertBefore(card, target);
    else list.insertBefore(target, card);
    update();
    // Keep focus on the same photo, even when the moved control reaches an edge.
    const preferred = direction < 0 ? card.querySelector('[data-slide-up]') : card.querySelector('[data-slide-down]');
    const alternative = direction < 0 ? card.querySelector('[data-slide-down]') : card.querySelector('[data-slide-up]');
    const focused = preferred?.disabled ? alternative : preferred;
    focused?.focus({preventScroll: true});
    focused?.scrollIntoView({block: 'nearest', behavior: 'instant'});
    say(`Photo déplacée en position ${activeCards().indexOf(card) + 1} après la photo principale.`);
  }
  list.addEventListener('click', event => {
    const card = event.target.closest('[data-slide-card]');
    if (!card) return;
    if (event.target.closest('[data-slide-up]')) moveCard(card, -1);
    else if (event.target.closest('[data-slide-down]')) moveCard(card, 1);
    else if (event.target.closest('[data-slide-undo]')) {
      if (activeCards().length >= MAX_SLIDES) { showError('Retirez une autre photo avant de conserver celle-ci.'); return; }
      field(card, 'DELETE').checked = false;
      update(); showError(''); field(card, 'DELETE').focus();
      say('La photo est de nouveau dans votre sélection.');
    }
  });
  list.addEventListener('change', event => {
    const card = event.target.closest('[data-slide-card]');
    if (!card) return;
    if (event.target === field(card, 'DELETE') && removed(card)) {
      releaseCard(card);
      if (!existing(card)) {
        resetNewCard(card);
        field(card, 'DELETE').checked = true;
      }
      const next = activeCards().find(item => item !== card);
      update();
      (existing(card) ? card.querySelector('[data-slide-undo]') : next?.querySelector('[name$="-alt"]') || form.querySelector('[data-slide-add]'))?.focus({preventScroll: true});
      say('Photo retirée de la sélection. Enregistrez pour appliquer ce changement.');
    } else {
      if (event.target.matches('[data-slide-mobile]') && event.target.files?.length) {
        const clear = card.querySelector('[data-slide-mobile-clear]');
        if (clear) clear.checked = false;
      }
      if (event.target.matches('[data-slide-mobile-clear]') && event.target.checked) {
        field(card, 'mobile_image').value = '';
        fileURL(card, 'mobile_image');
      }
      update();
    }
    showError('');
  });
  list.addEventListener('input', event => {
    if (event.target.matches('[name$="-alt"]')) update();
  });
  form.querySelector('[data-slide-add]')?.addEventListener('click', () => {
    if (activeCards().length >= MAX_SLIDES) return;
    const card = newCard();
    if (card) { update(); field(card, 'image').focus(); showError(''); }
  });
  let canAssignFiles = false;
  try { const transfer = new DataTransfer(); const input = document.createElement('input'); input.type = 'file'; input.files = transfer.files; canAssignFiles = true; } catch { /* Individual native file fields are the fallback. */ }
  if (canAssignFiles && picker) {
    form.querySelector('[data-slide-multi-upload]').hidden = false;
    picker.addEventListener('change', () => {
      const selected = Array.from(picker.files || []);
      const free = MAX_SLIDES - activeCards().length;
      if (selected.length > free) { showError(`Il reste ${free} place${free > 1 ? 's' : ''}. Sélectionnez au maximum ${free} photo${free > 1 ? 's' : ''}.`); picker.value = ''; return; }
      let first;
      for (const file of selected) {
        const card = newCard();
        if (!card) break;
        const transfer = new DataTransfer(); transfer.items.add(file);
        field(card, 'image').files = transfer.files;
        first ||= card;
        update();
      }
      picker.value = '';
      if (first) { showError(''); field(first, 'alt').focus(); say(`${selected.length} photo${selected.length > 1 ? 's' : ''} ajoutée${selected.length > 1 ? 's' : ''}. Renseignez leur description avant d’enregistrer.`); }
    });
  }
  form.addEventListener('change', event => {
    if (event.target.name === 'autoplay' && preview && !preview.hidden) refreshPreview();
  });
  form.addEventListener('submit', () => {
    // Empty placeholders do not consume one of the five allowed photos.
    cards().forEach(card => { if (!existing(card) && !hasContent(card)) field(card, 'DELETE').checked = true; });
  });

  function safeURL(value) {
    try { const url = new URL(value, window.location.href); return ['http:', 'https:', 'blob:'].includes(url.protocol) ? url.href : ''; } catch { return ''; }
  }
  function updatePublicControls(doc) {
    const hero = doc.querySelector(previewRootSelector);
    if (!hero) return;
    let multi = hero.querySelector('[data-preview-multi-footer]');
    if (!multi) {
      const source = doc.getElementById('slide-preview-controls-template');
      if (source) {
        const original = hero.querySelector(featurePreview ? '.feature-photo-note' : '.summit-hero-bottom');
        if (original) original.setAttribute('data-preview-single-footer', '');
        hero.append(source.content.cloneNode(true));
        multi = hero.querySelector('[data-preview-multi-footer]');
      }
    }
    const enabled = previewPhotos.length > 1;
    hero.classList.toggle('has-slideshow', enabled);
    if (featurePreview) hero.classList.toggle('feature-slideshow', enabled);
    const single = hero.querySelector('[data-preview-single-footer]');
    if (single) single.hidden = enabled;
    if (!multi) return;
    multi.hidden = !enabled;
    const counter = multi.querySelector('[data-preview-public-counter]');
    if (counter) counter.textContent = `Photo ${previewIndex + 1} sur ${previewPhotos.length}`;
    const playback = multi.querySelector('[data-preview-public-playback]');
    if (playback) playback.textContent = form.querySelector('[name="autoplay"]')?.checked ? 'Ⅱ' : '▶';
    const pages = multi.querySelector('[data-preview-public-pages]');
    if (pages) {
      pages.replaceChildren();
      previewPhotos.forEach((photo, index) => {
        const button = doc.createElement('button');
        button.type = 'button'; button.className = 'hero-slideshow-dot'; button.disabled = true;
        button.setAttribute('aria-label', `Photo ${index + 1}`);
        if (index === previewIndex) button.setAttribute('aria-current', 'true');
        button.append(doc.createElement('span')); pages.append(button);
      });
    }
  }
  function setPhoto() {
    if (!frameReady || !previewPhotos.length) return;
    const doc = frame.contentDocument;
    const img = featurePreview ? doc?.querySelector(previewRootSelector)?.querySelector('img') : doc?.querySelector('.summit-landscape');
    if (!img) return;
    const photo = previewPhotos[previewIndex];
    // Sources and srcset from the saved primary must not override a draft photo.
    img.parentElement.querySelectorAll('source').forEach(source => source.remove());
    img.removeAttribute('srcset'); img.removeAttribute('sizes');
    img.src = safeURL(previewMode === 'mobile' && photo.mobile ? photo.mobile : photo.desktop);
    img.alt = photo.alt;
    updatePublicControls(doc);
    img.addEventListener('load', fitPreview, {once: true});
    const caption = preview.querySelector('[data-preview-caption]');
    if (caption) caption.textContent = `${previewMode === 'mobile' ? 'Téléphone' : 'Ordinateur'} · Photo ${previewIndex + 1} sur ${previewPhotos.length} : ${photo.alt}. Aperçu non enregistré.`;
    preview.querySelector('[data-preview-counter]').textContent = `Photo ${previewIndex + 1} sur ${previewPhotos.length}`;
    preview.querySelector('[data-preview-prev]').disabled = previewPhotos.length < 2;
    preview.querySelector('[data-preview-next]').disabled = previewPhotos.length < 2;
    fitPreview();
  }
  function fitPreview() {
    if (!frame || !preview || preview.hidden) return;
    const width = previewMode === 'mobile' ? 390 : 1280;
    const baseHeight = previewMode === 'mobile' ? 844 : 720;
    const stage = preview.querySelector('[data-preview-stage]');
    const viewport = preview.querySelector('[data-preview-viewport]');
    viewport.style.width = `${width}px`;
    viewport.style.height = `${baseHeight}px`;
    // Read the real public layout after changing the viewport width.
    const hero = frame.contentDocument?.querySelector(featurePreview ? '#home-feature' : '.summit-hero');
    const needed = hero ? Math.ceil(hero.getBoundingClientRect().bottom + (previewMode === 'mobile' ? 8 : 16)) : baseHeight;
    frameHeight = Math.max(baseHeight, needed);
    viewport.style.height = `${frameHeight}px`;
    const scale = Math.min(1, stage.clientWidth / width);
    viewport.style.setProperty('--preview-scale', String(scale));
    stage.style.height = `${Math.ceil(frameHeight * scale)}px`;
  }
  function refreshPreview() {
    const primary = previewData?.primary;
    if (!primary?.url) return;
    const mobilePrimary = previewData.mobile_primary;
    const primaryMobile = mobilePrimary?.custom || !primary.custom ? mobilePrimary?.url || '' : '';
    previewPhotos = [{desktop: primary.url, mobile: primaryMobile, alt: previewData.hero?.image_alt || 'Photo principale'}];
    let incomplete = false;
    activeCards().forEach(card => { const photo = photoFor(card); if (photo.desktop) previewPhotos.push(photo); else incomplete = true; });
    previewIndex = Math.min(previewIndex, previewPhotos.length - 1);
    preview.querySelector('[data-preview-warning]').hidden = !incomplete;
    setPhoto();
  }
  if (frame && typeof previewHTML === 'string' && previewHTML && previewData?.primary?.url) {
    frame.addEventListener('load', () => {
      frameReady = true;
      const doc = frame.contentDocument;
      if (doc) { doc.body.inert = true; doc.querySelectorAll('a').forEach(link => link.removeAttribute('href')); doc.querySelectorAll('button').forEach(button => { button.disabled = true; }); }
      refreshPreview();
      doc?.fonts?.ready.then(fitPreview);
    });
    form.querySelectorAll('[data-preview-open]').forEach(button => {
      button.hidden = false;
      button.addEventListener('click', () => {
        preview.hidden = false;
        if (!frame.srcdoc) frame.srcdoc = previewHTML;
        refreshPreview();
        preview.querySelector('[data-preview-mode="desktop"]').focus({preventScroll: true});
        preview.scrollIntoView({block: 'start', behavior: 'instant'});
      });
    });
    preview.querySelector('[data-preview-close]')?.addEventListener('click', () => { preview.hidden = true; form.querySelector('[data-preview-open]')?.focus({preventScroll: true}); });
    preview.querySelectorAll('[data-preview-mode]').forEach(button => button.addEventListener('click', () => {
      previewMode = button.dataset.previewMode;
      preview.querySelectorAll('[data-preview-mode]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      setPhoto();
    }));
    preview.querySelector('[data-preview-prev]')?.addEventListener('click', () => { previewIndex = (previewIndex - 1 + previewPhotos.length) % previewPhotos.length; setPhoto(); });
    preview.querySelector('[data-preview-next]')?.addEventListener('click', () => { previewIndex = (previewIndex + 1) % previewPhotos.length; setPhoto(); });
    if (typeof ResizeObserver !== 'undefined') { resizeObserver = new ResizeObserver(fitPreview); resizeObserver.observe(preview.querySelector('[data-preview-stage]')); }
    else window.addEventListener('resize', fitPreview);
  }
  form.querySelectorAll('[data-slides-tools], [data-slide-add-actions]').forEach(node => { node.hidden = false; });
  // Restore the submitted order after a failed POST; prefixes and saved IDs stay unchanged.
  cards().sort((a, b) => (Number(field(a, 'position')?.value) || 999) - (Number(field(b, 'position')?.value) || 999)).forEach(card => list.append(card));
  update();
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    update();
    if (preview && !preview.hidden && frame && previewHTML) frame.srcdoc = previewHTML;
    if (resizeObserver && preview) resizeObserver.observe(preview.querySelector('[data-preview-stage]'));
  });
  window.addEventListener('pagehide', () => {
    if (frame) frame.removeAttribute('srcdoc');
    frameReady = false;
    cards().forEach(releaseCard);
    resizeObserver?.disconnect();
  });
})();
