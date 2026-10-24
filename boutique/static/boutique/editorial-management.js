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

  // The baseline stays unchanged when navigating back from a preview. Reverting
  // every edit removes the warning, and opening another tab never suppresses it.
  document.querySelectorAll('[data-editorial-dirty-form]').forEach(form => {
    const snapshot = () => JSON.stringify(Array.from(form.elements).filter(control => {
      return control.name && !['hidden', 'submit', 'button', 'reset'].includes(control.type);
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
