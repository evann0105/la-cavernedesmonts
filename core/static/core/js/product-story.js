(() => {
  const main = document.getElementById('main-product-image');
  const opener = document.querySelector('[data-open-photo]');
  const photos = [...document.querySelectorAll('[data-product-photo]')];
  const dialog = document.querySelector('.product-lightbox');
  let showColorPhoto = null;
  if (main && opener && dialog && photos.length) {
    const large = dialog.querySelector('img');
    const canvas = dialog.querySelector('.lightbox-canvas');
    const zoom = dialog.querySelector('[data-photo-zoom]');
    let index = 0;
    function show(next) {
      index = (next + photos.length) % photos.length;
      const photo = photos[index];
      main.src = photo.href;
      main.alt = photo.querySelector('img').alt;
      opener.href = photo.href;
      large.src = photo.href;
      large.alt = main.alt;
      photos.forEach((item, i) => i === index ? item.setAttribute('aria-current', 'true') : item.removeAttribute('aria-current'));
      dialog.querySelector('.photo-counter').textContent = `${index + 1} / ${photos.length}`;
      canvas.classList.remove('is-zoomed');
      zoom.setAttribute('aria-pressed', 'false');
      zoom.querySelector('[data-zoom-symbol]').textContent = '+';
      canvas.scrollTop = canvas.scrollLeft = 0;
    }
    showColorPhoto = url => {
      const target = photos.findIndex(photo => photo.href === new URL(url, location.href).href);
      if (target >= 0) show(target);
    };
    photos.forEach((photo, i) => photo.addEventListener('click', event => { event.preventDefault(); show(i); }));
    opener.addEventListener('click', event => {
      if (typeof dialog.showModal !== 'function') return;
      event.preventDefault(); show(index); dialog.showModal();
    });
    dialog.querySelector('[data-photo-close]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => opener.focus());
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    const previous = dialog.querySelector('[data-photo-prev]');
    const next = dialog.querySelector('[data-photo-next]');
    previous.disabled = next.disabled = photos.length < 2;
    previous.addEventListener('click', () => show(index - 1));
    next.addEventListener('click', () => show(index + 1));
    zoom.addEventListener('click', () => {
      const enlarged = canvas.classList.toggle('is-zoomed');
      zoom.setAttribute('aria-pressed', String(enlarged));
      zoom.querySelector('[data-zoom-symbol]').textContent = enlarged ? '−' : '+';
      if (enlarged) canvas.focus({ preventScroll: true });
    });
    dialog.addEventListener('keydown', event => {
      // Let the scrollable image respond to arrow keys while enlarged.
      if (canvas.classList.contains('is-zoomed')) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); show(index + (event.key === 'ArrowLeft' ? -1 : 1)); }
    });
  }
  const colorPicker = document.querySelector('[data-product-colors]');
  if (colorPicker) {
    colorPicker.hidden = false;
    const buttons = [...colorPicker.querySelectorAll('[data-product-color]')];
    const form = colorPicker.closest('form');
    const variants = [...form.querySelectorAll('input[name="variant"]')];
    const fallbackSizes = [...form.querySelectorAll('input[name="size"]')];
    const purchase = form.querySelector('button[type="submit"]');
    const feedback = colorPicker.querySelector('[data-color-feedback]');
    const originalPhoto = opener?.href;
    const unavailable = new Map(variants.map(input => [input, input.disabled]));
    function chooseColor(button, reset) {
      const key = button.dataset.productColor;
      buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      if (showColorPhoto && (button.dataset.colorImage || originalPhoto)) {
        showColorPhoto(button.dataset.colorImage || originalPhoto);
      }
      const matching = variants.filter(input => input.dataset.variantColor === key);
      variants.forEach(input => {
        const matches = input.dataset.variantColor === key;
        input.closest('[data-variant-row]').hidden = !matches;
        input.disabled = !matches || unavailable.get(input);
        if (reset) input.checked = false;
      });
      const available = matching.filter(input => !unavailable.get(input));
      if (reset && available.length === 1) available[0].checked = true;
      // A color photograph alone must never invent a purchasable stock variant.
      fallbackSizes.forEach(input => { input.disabled = true; input.checked = false; });
      if (purchase) purchase.disabled = available.length === 0;
      feedback.textContent = available.length === 0
        ? 'Ce coloris n’est pas disponible à la commande. Contactez la boutique.'
        : (button.dataset.colorImage ? `Coloris : ${button.textContent.trim()}.` : 'Photo générale du produit : la photo de ce coloris n’est pas encore disponible.');
    }
    buttons.forEach(button => button.addEventListener('click', () => chooseColor(button, true)));
    variants.forEach(input => input.addEventListener('change', () => {
      const button = buttons.find(item => item.dataset.productColor === input.dataset.variantColor);
      if (button) chooseColor(button, false);
    }));
    if (!variants.some(input => !input.disabled) && purchase) {
      purchase.disabled = true;
      feedback.textContent = 'Contactez la boutique pour vérifier les tailles et les stocks de ces coloris.';
    }
  }
  document.querySelector('.size-advice')?.addEventListener('click', () => {
    const help = document.getElementById('size-help');
    if (help) help.open = true;
  });
})();
