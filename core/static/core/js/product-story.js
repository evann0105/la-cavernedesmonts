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
  const sizePicker = document.querySelector('[data-product-sizes]');
  const form = document.querySelector('.product-purchase');
  if (colorPicker && form) {
    const colors = [...colorPicker.querySelectorAll('[data-product-color]')];
    const sizes = [...(sizePicker?.querySelectorAll('[data-product-size]') || [])];
    const variants = [...form.querySelectorAll('input[name="variant"]')];
    const purchase = form.querySelector('button[type="submit"]');
    const quantity = form.querySelector('input[name="quantity"]');
    const feedback = colorPicker.querySelector('[data-color-feedback]');
    const originalPhoto = opener?.href;
    const photoFor = button => {
      const url = button?.dataset.colorImage || originalPhoto;
      if (showColorPhoto && url) showColorPhoto(url);
    };
    const variantFor = (size, color) => {
      const matching = variants.filter(input =>
        input.dataset.variantSize === size && input.dataset.variantColor === color);
      return matching.find(input => !input.disabled && Number(input.dataset.variantAvailable) > 0) || matching[0];
    };

    colorPicker.hidden = false;
    if (sizes.length && variants.length) {
      let selectedSize = null;
      let selectedColor = null;
      const fallback = form.querySelector('[data-variant-fallback]');
      const canOrder = input => Boolean(input && !input.disabled && Number(input.dataset.variantAvailable) > 0);

      function selectSize(button) {
        const variant = button && variantFor(button.dataset.productSize, selectedColor?.dataset.productColor);
        selectedSize = canOrder(variant) ? button : null;
        variants.forEach(input => { input.checked = input === variant && Boolean(selectedSize); });
        sizes.forEach(item => item.setAttribute('aria-pressed', String(item === selectedSize)));
        if (purchase) purchase.disabled = !selectedSize;
        if (quantity) {
          const limit = selectedSize ? Math.min(10, Number(variant.dataset.variantAvailable)) : 10;
          quantity.max = String(limit);
          if (!Number.isFinite(Number(quantity.value)) || Number(quantity.value) < 1) quantity.value = '1';
          if (Number(quantity.value) > limit) quantity.value = String(limit);
        }
        if (!selectedColor) {
          feedback.textContent = colors.some(item => !item.disabled)
            ? 'Choisissez d’abord votre couleur pour voir les tailles disponibles.'
            : 'Ces coloris ne sont pas disponibles à la commande. Contactez la boutique.';
        } else {
          feedback.textContent = (selectedSize
            ? `${selectedColor.dataset.colorName} · ${selectedSize.dataset.sizeName}.`
            : `Choisissez votre taille pour le coloris ${selectedColor.dataset.colorName}.`) +
            (selectedColor.dataset.colorImage ? '' : ' Photo générale du produit : la photo de ce coloris n’est pas encore disponible.');
        }
      }

      function selectColor(button, preferredSize = selectedSize?.dataset.productSize) {
        selectedColor = button && !button.disabled ? button : null;
        colors.forEach(item => item.setAttribute('aria-pressed', String(item === selectedColor)));
        const compatibleSizes = [];
        sizes.forEach(item => {
          const variant = selectedColor && variantFor(item.dataset.productSize, selectedColor.dataset.productColor);
          item.hidden = !variant;
          item.disabled = !canOrder(variant);
          const state = item.querySelector('[data-size-state]');
          const unavailable = variant && !canOrder(variant);
          if (state) {
            state.hidden = !unavailable;
            state.textContent = unavailable
              ? (variant.dataset.variantStockKnown === 'false' ? 'À confirmer' : 'Épuisé') : '';
          }
          if (unavailable) item.title = `${item.dataset.sizeName} : ${state?.textContent || 'indisponible'} en ${selectedColor.dataset.colorName}`;
          else item.removeAttribute('title');
          if (canOrder(variant)) compatibleSizes.push(item);
        });
        const retained = compatibleSizes.find(item => item.dataset.productSize === preferredSize);
        selectSize(retained || (compatibleSizes.length === 1 ? compatibleSizes[0] : null));
        sizePicker.hidden = !selectedColor;
        photoFor(selectedColor);
      }

      colors.forEach(button => {
        const matching = variants.filter(input => input.dataset.variantColor === button.dataset.productColor);
        button.disabled = !matching.some(canOrder);
        const state = button.querySelector('[data-color-state]');
        if (state) {
          state.hidden = !button.disabled;
          state.textContent = button.disabled
            ? (matching.some(input => input.dataset.variantStockKnown === 'false') ? 'À confirmer' : (matching.length ? 'Épuisé' : 'Indisponible')) : '';
        }
        if (button.disabled) button.title = `${button.dataset.colorName} : ${state?.textContent || 'indisponible'}`;
        else button.removeAttribute('title');
        button.addEventListener('click', () => { if (!button.disabled) selectColor(button); });
      });
      sizes.forEach(button => button.addEventListener('click', () => {
        if (!button.hidden && !button.disabled) selectSize(button);
      }));
      form.addEventListener('submit', event => {
        const variant = selectedSize && variantFor(selectedSize.dataset.productSize, selectedColor?.dataset.productColor);
        if (canOrder(variant) && variant.checked) return;
        event.preventDefault();
        feedback.textContent = 'Choisissez une couleur et une taille disponibles avant d’ajouter le produit.';
        const target = selectedColor
          ? sizes.find(button => !button.hidden && !button.disabled)
          : colors.find(button => !button.disabled);
        target?.focus();
      });
      const restored = variants.find(input => input.checked && canOrder(input));
      const availableColors = colors.filter(button => !button.disabled);
      const initialColor = restored
        ? colors.find(button => button.dataset.productColor === restored.dataset.variantColor)
        : (availableColors.length === 1 ? availableColors[0] : null);
      selectColor(initialColor, restored?.dataset.variantSize);
      // The enhanced controls validate the pair; hidden radios cannot receive native validation focus.
      variants.forEach(input => { input.required = false; });
      if (fallback) fallback.hidden = true;
    } else {
      // Photos alone do not create a size/color combination that can be ordered.
      colors.forEach(button => button.addEventListener('click', () => {
        colors.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
        photoFor(button);
        feedback.textContent = 'Contactez la boutique pour vérifier les tailles et les stocks de ce coloris.';
      }));
      if (!variants.length && purchase) purchase.disabled = true;
      feedback.textContent = 'Contactez la boutique pour vérifier les tailles et les stocks de ces coloris.';
    }
  }
  document.querySelector('.size-advice')?.addEventListener('click', () => {
    const help = document.getElementById('size-help');
    if (help) help.open = true;
  });
})();
