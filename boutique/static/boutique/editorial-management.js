(() => {
  'use strict';

  // Local previews only: no request is made until the form is submitted.
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
          status.textContent = 'La nouvelle photo sera publiée lorsque vous enregistrerez cette page.';
        } else {
          image.src = savedSource;
          caption.textContent = savedCaption;
          status.textContent = reset && reset.checked
            ? 'La photo d’origine sera rétablie à l’enregistrement.'
            : 'La photo choisie n’est publiée qu’après l’enregistrement.';
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
})();
