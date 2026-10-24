(() => {
  'use strict';

  // Photo previews stay local until the form is submitted.
  const pageForm = document.querySelector('[data-editorial-page-form]');
  if (pageForm) {
    const input = pageForm.querySelector('[data-editorial-image-input]');
    const image = pageForm.querySelector('[data-editorial-image-preview]');
    const caption = pageForm.querySelector('[data-editorial-image-caption]');
    const reset = pageForm.querySelector('[data-editorial-image-reset]');
    const status = pageForm.querySelector('[data-editorial-image-status]');
    if (input && image && caption && status) {
      const savedSource = image.getAttribute('src');
      const savedCaption = caption.textContent;
      const originalSource = image.getAttribute('data-editorial-original-image') || savedSource;
      let localURL = null;
      const revoke = () => {
        if (localURL) URL.revokeObjectURL(localURL);
        localURL = null;
      };
      const showSelected = () => {
        revoke();
        const file = input.files && input.files[0];
        if (file && /^image\/(jpeg|png|webp)$/.test(file.type)) {
          localURL = URL.createObjectURL(file);
          image.src = localURL;
          caption.textContent = 'Aperçu de votre nouvelle photo · avant enregistrement';
          status.textContent = 'La nouvelle photo sera conservée dans votre brouillon, puis visible sur le site après publication.';
        } else {
          image.src = reset && reset.checked ? originalSource : savedSource;
          caption.textContent = reset && reset.checked ? 'Photo d’origine · avant enregistrement' : savedCaption;
          status.textContent = reset && reset.checked
            ? 'La photo d’origine sera rétablie dans votre brouillon, puis visible sur le site après publication.'
            : 'La photo choisie sera conservée dans votre brouillon. Elle apparaîtra sur le site après publication.';
        }
      };
      input.addEventListener('change', () => {
        if (input.files && input.files.length && reset) reset.checked = false;
        showSelected();
      });
      if (reset) reset.addEventListener('change', () => {
        if (reset.checked) input.value = '';
        showSelected();
      });
      window.addEventListener('pagehide', revoke);
      window.addEventListener('pageshow', showSelected);
      showSelected();
    }
  }

  const announcementForm = document.querySelector('[data-editorial-announcement-form]');
  const preview = document.querySelector('[data-editorial-announcement-preview]');
  if (announcementForm && preview) {
    const message = announcementForm.elements.namedItem('message');
    const kind = announcementForm.elements.namedItem('kind');
    const target = announcementForm.elements.namedItem('target');
    const linkLabel = announcementForm.elements.namedItem('link_label');
    const previewMessage = preview.querySelector('[data-editorial-preview-message]');
    const previewKind = preview.querySelector('[data-editorial-preview-kind]');
    const previewLink = preview.querySelector('[data-editorial-preview-link]');
    const previewLinkLabel = preview.querySelector('[data-editorial-preview-link-label]');
    const update = () => {
      if (message && previewMessage) previewMessage.textContent = message.value.trim() || 'Votre message apparaîtra ici.';
      if (kind && previewKind) {
        const selected = kind.options[kind.selectedIndex];
        previewKind.textContent = selected ? selected.textContent : 'Information';
      }
      if (previewLink && previewLinkLabel && linkLabel) {
        previewLinkLabel.textContent = linkLabel.value.trim();
        previewLink.hidden = !(target && target.value && linkLabel.value.trim());
      }
    };
    announcementForm.addEventListener('input', update);
    announcementForm.addEventListener('change', update);
    window.addEventListener('pageshow', update);
    update();
  }
  if (announcementForm) {
    const start = announcementForm.elements.namedItem('starts_at');
    const end = announcementForm.elements.namedItem('ends_at');
    const enabled = announcementForm.elements.namedItem('enabled');
    const warning = announcementForm.querySelector('[data-editorial-overlap-warning]');
    const list = announcementForm.querySelector('[data-editorial-overlap-list]');
    const status = announcementForm.querySelector('[data-editorial-overlap-status]');
    const endpoint = announcementForm.dataset.editorialOverlapUrl;
    if (start && end && enabled && warning && list && status && endpoint) {
      const formatter = new Intl.DateTimeFormat('fr-FR', {dateStyle: 'short', timeStyle: 'short', timeZone: 'Europe/Paris'});
      let generation = 0;
      let timer = null;
      let controller = null;
      const announce = message => { status.textContent = message; status.hidden = !message; };
      const scheduleCheck = () => {
        generation += 1;
        const requestGeneration = generation;
        clearTimeout(timer);
        if (controller) controller.abort();
        warning.hidden = true;
        if (!enabled.checked || !start.value || !end.value) { announce(''); return; }
        announce('Vérification des autres annonces…');
        timer = setTimeout(async () => {
          controller = new AbortController();
          const url = new URL(endpoint, window.location.href);
          url.searchParams.set('starts_at', start.value);
          url.searchParams.set('ends_at', end.value);
          const exclude = announcementForm.dataset.editorialOverlapExclude;
          if (exclude) url.searchParams.set('exclude_pk', exclude);
          try {
            const response = await fetch(url, {credentials: 'same-origin', signal: controller.signal, headers: {'Accept': 'application/json'}});
            const data = await response.json();
            if (requestGeneration !== generation) return;
            if (!response.ok || data.error) { announce(data.error || 'Impossible de vérifier les chevauchements. Réessayez dans un instant.'); return; }
            if (!Array.isArray(data.overlaps)) throw new Error('Invalid overlap response');
            const items = data.overlaps.map(other => {
              if (typeof other.name !== 'string' || typeof other.priority_label !== 'string') throw new Error('Invalid overlap');
              const from = new Date(other.starts_at);
              const until = new Date(other.ends_at);
              if (!Number.isFinite(from.getTime()) || !Number.isFinite(until.getTime())) throw new Error('Invalid overlap dates');
              const item = document.createElement('li');
              const title = document.createElement('strong');
              title.textContent = other.name;
              const period = document.createElement('span');
              period.textContent = `${other.priority_label} · du ${formatter.format(from)} au ${formatter.format(until)}`;
              item.append(title, period);
              return item;
            });
            list.replaceChildren(...items);
            warning.hidden = items.length === 0;
            announce(items.length ? `${items.length} autre${items.length > 1 ? 's' : ''} annonce${items.length > 1 ? 's' : ''} sur cette période.` : 'Aucun chevauchement avec une autre annonce activée.');
          } catch (error) {
            if (requestGeneration !== generation || error.name === 'AbortError') return;
            announce('Impossible de vérifier les chevauchements. Réessayez dans un instant.');
          }
        }, 250);
      };
      [start, end, enabled].forEach(control => { control.addEventListener('input', scheduleCheck); control.addEventListener('change', scheduleCheck); });
      window.addEventListener('pageshow', scheduleCheck);
    }
  }

  const reorderForm = document.querySelector('[data-editorial-faq-reorder]');
  if (reorderForm) {
    const list = reorderForm.querySelector('[data-editorial-faq-order-list]');
    const order = reorderForm.elements.namedItem('order');
    const reset = reorderForm.querySelector('[data-editorial-faq-reset]');
    const save = reorderForm.querySelector('[data-editorial-faq-save]');
    const status = reorderForm.querySelector('[data-editorial-faq-order-status]');
    if (list && order && reset && save && status) {
      const rows = () => Array.from(list.querySelectorAll('[data-editorial-faq-row]')).filter(row => row.parentElement === list);
      const originalRows = rows();
      const originalOrder = JSON.stringify(originalRows.map(row => Number(row.dataset.entryId)));
      const sync = (notify = true) => {
        const currentRows = rows();
        order.value = JSON.stringify(currentRows.map(row => Number(row.dataset.entryId)));
        currentRows.forEach((row, index) => {
          const position = row.querySelector('[data-editorial-faq-position]');
          position.textContent = String(index + 1);
          position.setAttribute('aria-label', `Position ${index + 1}`);
          row.querySelector('[data-editorial-faq-move="up"]').setAttribute('aria-disabled', String(index === 0));
          row.querySelector('[data-editorial-faq-move="down"]').setAttribute('aria-disabled', String(index === currentRows.length - 1));
        });
        reset.setAttribute('aria-disabled', String(order.value === originalOrder));
        if (notify) order.dispatchEvent(new Event('input', {bubbles: true}));
      };
      const announcePosition = row => {
        const currentRows = rows();
        const question = row.querySelector('[data-editorial-faq-question]').textContent.trim();
        status.textContent = `${question} — position ${currentRows.indexOf(row) + 1} sur ${currentRows.length}. Enregistrez pour appliquer cet ordre.`;
      };
      list.querySelectorAll('[data-editorial-faq-controls], [data-editorial-faq-handle]').forEach(control => { control.hidden = false; });
      reset.hidden = false;
      save.hidden = false;
      // Canonicalize the hidden JSON before the shared unsaved-change baseline.
      sync(false);
      list.addEventListener('click', event => {
        const button = event.target.closest('[data-editorial-faq-move]');
        if (!button || !list.contains(button) || button.getAttribute('aria-disabled') === 'true') return;
        const row = button.closest('[data-editorial-faq-row]');
        const currentRows = rows();
        const index = currentRows.indexOf(row);
        const direction = button.dataset.editorialFaqMove;
        if (direction === 'up' && index > 0) list.insertBefore(row, currentRows[index - 1]);
        else if (direction === 'down' && index >= 0 && index < currentRows.length - 1) list.insertBefore(currentRows[index + 1], row);
        else return;
        sync();
        announcePosition(row);
        // aria-disabled keeps the same arrow focusable at the first/last position.
        button.focus({preventScroll: true});
      });
      reset.addEventListener('click', () => {
        if (reset.getAttribute('aria-disabled') === 'true') return;
        list.append(...originalRows);
        sync();
        status.textContent = 'Les déplacements ont été annulés. L’ordre initial est rétabli.';
      });

      let dragged = null;
      let destination = null;
      let insertAfter = false;
      const clearDestination = () => {
        if (destination) destination.removeAttribute('data-drop-position');
        destination = null;
      };
      const finishDrag = () => {
        if (dragged) dragged.classList.remove('is-dragging');
        dragged = null;
        clearDestination();
      };
      list.addEventListener('dragstart', event => {
        const handle = event.target.closest('[data-editorial-faq-handle]');
        if (!handle || !list.contains(handle)) { event.preventDefault(); return; }
        dragged = handle.closest('[data-editorial-faq-row]');
        dragged.classList.add('is-dragging');
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = 'move';
          event.dataTransfer.setData('text/plain', dragged.dataset.entryId);
        }
      });
      list.addEventListener('dragover', event => {
        const target = event.target.closest('[data-editorial-faq-row]');
        if (!dragged || !target || target === dragged || target.parentElement !== list) { clearDestination(); return; }
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
        const bounds = target.getBoundingClientRect();
        clearDestination();
        destination = target;
        insertAfter = event.clientY >= bounds.top + bounds.height / 2;
        target.setAttribute('data-drop-position', insertAfter ? 'after' : 'before');
      });
      list.addEventListener('drop', event => {
        if (!dragged || !destination) return;
        event.preventDefault();
        const row = dragged;
        const before = order.value;
        const reference = insertAfter ? destination.nextElementSibling : destination;
        if (reference !== row) list.insertBefore(row, reference);
        finishDrag();
        sync();
        if (order.value !== before) announcePosition(row);
        row.querySelector('[data-editorial-faq-move="up"]').focus({preventScroll: true});
      });
      list.addEventListener('dragend', finishDrag);
      window.addEventListener('pageshow', () => sync());
    }
  }

  // The baseline stays unchanged when navigating back from a preview. Reverting
  // every edit removes the warning, and opening another tab never suppresses it.
  document.querySelectorAll('[data-editorial-dirty-form]').forEach(form => {
    const snapshot = () => JSON.stringify(Array.from(form.elements).filter(control => {
      return control.name && (!['hidden', 'submit', 'button', 'reset'].includes(control.type) || (control.type === 'hidden' && control.hasAttribute('data-editorial-dirty-value')));
    }).map(control => {
      if (control.type === 'file') return [control.name, Array.from(control.files || []).map(file => [file.name, file.size, file.type, file.lastModified])];
      if (['checkbox', 'radio'].includes(control.type)) return [control.name, control.value, control.checked];
      if (control.type === 'select-multiple') return [control.name, Array.from(control.selectedOptions).map(option => option.value)];
      return [control.name, control.value];
    }));
    const initial = snapshot();
    const unsavedOnLoad = form.hasAttribute('data-editorial-dirty-on-load');
    const status = form.querySelector('[data-editorial-dirty-status]');
    let submitting = false;
    let listening = false;
    const dirty = () => unsavedOnLoad || snapshot() !== initial;
    const warn = event => {
      if (submitting || !dirty()) return;
      event.preventDefault();
      event.returnValue = '';
    };
    const refresh = () => {
      const changed = dirty();
      const shouldListen = changed && !submitting;
      if (shouldListen !== listening) {
        window[shouldListen ? 'addEventListener' : 'removeEventListener']('beforeunload', warn);
        listening = shouldListen;
      }
      if (status && status.dataset.dirty !== String(changed)) {
        status.textContent = changed ? 'Modifications non enregistrées.' : 'Aucune modification non enregistrée.';
        status.dataset.dirty = String(changed);
      }
    };
    form.addEventListener('input', refresh);
    form.addEventListener('change', refresh);
    form.addEventListener('reset', () => queueMicrotask(refresh));
    form.addEventListener('submit', event => {
      const target = event.submitter && event.submitter.getAttribute('formtarget') || form.getAttribute('target') || '_self';
      queueMicrotask(() => {
        if (event.defaultPrevented || (target !== '_self' && target !== window.name) || !form.checkValidity()) return;
        submitting = true;
        refresh();
      });
    });
    window.addEventListener('pageshow', () => { submitting = false; refresh(); });
    refresh();
  });

  const responsivePreview = document.querySelector('[data-editorial-responsive-preview]');
  if (responsivePreview) {
    const frame = responsivePreview.querySelector('[data-editorial-preview-frame]');
    const controls = responsivePreview.querySelector('[data-editorial-device-controls]');
    const stage = responsivePreview.querySelector('[data-editorial-preview-stage]');
    const viewport = responsivePreview.querySelector('[data-editorial-preview-viewport]');
    const deviceButtons = responsivePreview.querySelectorAll('[data-editorial-preview-device]');
    const fitPreview = () => {
      if (!frame || !stage || !viewport) return;
      const style = window.getComputedStyle(stage);
      const available = stage.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 2;
      if (available <= 0) return;
      const width = responsivePreview.dataset.device === 'phone' ? 390 : 1280;
      const height = 720;
      const scale = Math.min(1, available / width);
      responsivePreview.dataset.previewReady = 'true';
      viewport.style.width = `${width * scale + 2}px`;
      viewport.style.height = `${height * scale + 2}px`;
      frame.style.width = `${width}px`;
      frame.style.height = `${height}px`;
      frame.style.transform = `scale(${scale})`;
    };
    if (controls) controls.hidden = false;
    deviceButtons.forEach(button => button.addEventListener('click', () => {
      responsivePreview.dataset.device = button.dataset.editorialPreviewDevice;
      deviceButtons.forEach(other => other.setAttribute('aria-pressed', String(other === button)));
      fitPreview();
    }));
    if (stage && typeof ResizeObserver !== 'undefined') new ResizeObserver(fitPreview).observe(stage);
    else window.addEventListener('resize', fitPreview);
    fitPreview();
    if (frame) {
      const disablePreviewActions = () => {
        try {
          const previewDocument = frame.contentDocument;
          if (!previewDocument || !previewDocument.body) return;
          previewDocument.querySelectorAll('a[href], area[href]').forEach(link => {
            link.removeAttribute('href');
            link.setAttribute('aria-disabled', 'true');
            link.setAttribute('tabindex', '-1');
          });
          previewDocument.querySelectorAll('button, input, select, textarea').forEach(control => { control.disabled = true; });
          previewDocument.querySelectorAll('form').forEach(previewForm => previewForm.addEventListener('submit', event => event.preventDefault()));
          if (!previewDocument.getElementById('editorial-preview-controls-style')) {
            const style = previewDocument.createElement('style');
            style.id = 'editorial-preview-controls-style';
            style.textContent = '.editorial-preview [aria-disabled="true"], .editorial-preview :disabled { cursor: default; } .editorial-preview button:disabled { opacity: 1; }';
            previewDocument.head.appendChild(style);
          }
        } catch (_) {
          // The sandbox remains in force if a sign-in redirect changes the frame.
        }
      };
      frame.addEventListener('load', disablePreviewActions);
      disablePreviewActions();
    }
  }
})();
