(() => {
  'use strict';
  document.querySelectorAll('[data-history-preview]').forEach(preview => {
    const frame = preview.querySelector('[data-history-frame]');
    const stage = preview.querySelector('[data-history-stage]');
    const viewport = preview.querySelector('[data-history-viewport]');
    const devices = preview.querySelector('[data-history-devices]');
    const status = preview.querySelector('[data-history-preview-status]');
    if (!frame || !stage || !viewport || !devices) return;
    let width = 1160;
    let documentHeight = 800;
    let observer;
    let scheduled;
    let activeDocument;
    const resize = () => {
      if (scheduled) return;
      scheduled = requestAnimationFrame(() => {
        scheduled = null;
        const style = getComputedStyle(stage);
        const available = stage.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
        const scale = Math.min(1, Math.max(1, available) / width);
        frame.style.width = `${width}px`;
        try {
          const doc = frame.contentDocument;
          if (doc && doc.body) {
            // Reset the viewport floor before measuring a shorter layout.
            frame.style.height = '400px';
            documentHeight = Math.max(400, doc.documentElement.scrollHeight, doc.body.scrollHeight);
          }
        } catch (_) { /* The isolated frame may have been replaced after logout. */ }
        frame.style.height = `${documentHeight}px`;
        frame.style.transform = `scale(${scale})`;
        viewport.style.width = `${width * scale}px`;
        viewport.style.height = `${documentHeight * scale}px`;
      });
    };
    devices.querySelectorAll('[data-history-device]').forEach(button => {
      button.addEventListener('click', () => {
        width = button.dataset.historyDevice === 'phone' ? 390 : 1160;
        devices.querySelectorAll('button').forEach(item => item.setAttribute('aria-pressed', item === button ? 'true' : 'false'));
        resize();
      });
    });
    const initialize = () => {
      try {
        const doc = frame.contentDocument;
        if (!doc || !doc.body || doc.URL === 'about:blank') return;
        if (doc === activeDocument) { resize(); return; }
        activeDocument = doc;
        // The response sandbox already blocks scripts and form submission.
        // Remove interactive affordances in the visual preview as well.
        doc.querySelectorAll('a[href], area[href]').forEach(link => {
          link.removeAttribute('href');
          link.setAttribute('aria-disabled', 'true');
          link.setAttribute('tabindex', '-1');
          link.removeAttribute('target');
        });
        doc.querySelectorAll('button, input, select, textarea').forEach(control => { control.disabled = true; });
        doc.addEventListener('submit', event => event.preventDefault(), true);
        doc.addEventListener('click', event => { if (event.target.closest?.('a, area, button, form')) event.preventDefault(); }, true);
        observer?.disconnect();
        if ('ResizeObserver' in window) {
          observer = new ResizeObserver(resize);
          observer.observe(doc.body);
        }
        doc.querySelectorAll('img').forEach(img => { img.addEventListener('load', resize); img.addEventListener('error', resize); });
        preview.dataset.previewReady = 'true';
        devices.hidden = false;
        if (status) status.textContent = 'Aperçu privé. Aucune modification n’a été appliquée.';
        resize();
      } catch (_) {
        if (status) status.textContent = 'L’aperçu n’est pas disponible. Les différences restent consultables dans l’historique.';
      }
    };
    frame.addEventListener('load', initialize);
    try { if (frame.contentDocument?.readyState === 'complete') initialize(); } catch (_) { /* Wait for the frame load event. */ }
    window.addEventListener('resize', resize);
  });
})();
