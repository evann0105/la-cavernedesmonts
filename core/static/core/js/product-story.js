(() => {
  const main = document.getElementById('main-product-image');
  const opener = document.querySelector('[data-open-photo]');
  const photos = [...document.querySelectorAll('[data-product-photo]')];
  const dialog = document.querySelector('.product-lightbox');
  let showColorPhoto = null;
  let onGalleryPhotoChange = null;
  const photoUrl = url => {
    if (!url) return null;
    try {
      const resolved = new URL(url, document.baseURI || location.href);
      resolved.hash = '';
      return resolved.href;
    } catch (_) {
      return null;
    }
  };
  if (main && opener && dialog && photos.length) {
    const large = dialog.querySelector('img');
    const canvas = dialog.querySelector('.lightbox-canvas');
    const zoom = dialog.querySelector('[data-photo-zoom]');
    let index = 0;
    function displayPhoto(url, alt, photoIndex) {
      index = photoIndex;
      main.src = url;
      main.alt = alt;
      opener.href = url;
      large.src = url;
      large.alt = main.alt;
      photos.forEach((item, i) => i === index ? item.setAttribute('aria-current', 'true') : item.removeAttribute('aria-current'));
      dialog.querySelector('.photo-counter').textContent = index === null ? 'Photo du coloris' : `${index + 1} / ${photos.length}`;
      canvas.classList.remove('is-zoomed');
      zoom.setAttribute('aria-pressed', 'false');
      zoom.querySelector('[data-zoom-symbol]').textContent = '+';
      canvas.scrollTop = canvas.scrollLeft = 0;
    }
    function show(next, syncColor = false) {
      const photoIndex = (next + photos.length) % photos.length;
      const photo = photos[photoIndex];
      displayPhoto(photo.href, photo.querySelector('img').alt, photoIndex);
      if (syncColor) onGalleryPhotoChange?.(photo.href);
    }
    showColorPhoto = (url, alt) => {
      const resolved = photoUrl(url);
      if (!resolved) return;
      const target = photos.findIndex(photo => photoUrl(photo.href) === resolved);
      if (target >= 0) show(target);
      else displayPhoto(resolved, alt || main.alt, null);
    };
    show(0);
    photos.forEach((photo, i) => photo.addEventListener('click', event => { event.preventDefault(); show(i, true); }));
    opener.addEventListener('click', event => {
      if (typeof dialog.showModal !== 'function') return;
      event.preventDefault(); dialog.showModal();
    });
    dialog.querySelector('[data-photo-close]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => opener.focus());
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    const previous = dialog.querySelector('[data-photo-prev]');
    const next = dialog.querySelector('[data-photo-next]');
    previous.disabled = next.disabled = photos.length < 2;
    previous.addEventListener('click', () => show(index === null ? photos.length - 1 : index - 1, true));
    next.addEventListener('click', () => show(index === null ? 0 : index + 1, true));
    zoom.addEventListener('click', () => {
      const enlarged = canvas.classList.toggle('is-zoomed');
      zoom.setAttribute('aria-pressed', String(enlarged));
      zoom.querySelector('[data-zoom-symbol]').textContent = enlarged ? '−' : '+';
      if (enlarged) canvas.focus({ preventScroll: true });
    });
    dialog.addEventListener('keydown', event => {
      // Let the scrollable image respond to arrow keys while enlarged.
      if (canvas.classList.contains('is-zoomed')) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault();
        const target = event.key === 'ArrowLeft'
          ? (index === null ? photos.length - 1 : index - 1)
          : (index === null ? 0 : index + 1);
        show(target, true);
      }
    });
  }
  const colorPicker = document.querySelector('[data-product-colors]');
  const sizePicker = document.querySelector('[data-product-sizes]');
  const form = document.querySelector('.product-purchase');
  const priceDisplay = document.querySelector('[data-product-price]');
  const initialPriceLabel = priceDisplay?.textContent;
  const money = new Intl.NumberFormat(document.documentElement.lang || 'fr-FR', {
    style: 'currency', currency: 'EUR', minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
  const variantPrice = input => {
    const raw = input?.dataset.variantPrice;
    if (!raw) return null;
    const amount = Number(raw);
    return Number.isFinite(amount) && amount > 0 ? amount : null;
  };
  const priceLabel = inputs => {
    const prices = inputs.map(variantPrice).filter(amount => amount !== null);
    if (!prices.length) return '';
    const minimum = Math.min(...prices);
    return `${minimum === Math.max(...prices) ? '' : 'À partir de '}${money.format(minimum)}`;
  };
  const referencePrice = input => {
    const raw = input?.dataset.variantReference;
    const amount = raw ? Number(raw) : NaN;
    return Number.isFinite(amount) && amount > 0 ? amount : null;
  };
  let refreshSelectedPricing = null;
  function showPrice(inputs) {
    if (priceDisplay) priceDisplay.textContent = priceLabel(inputs) || initialPriceLabel;
    const discount = document.querySelector('[data-product-discount]');
    if (!discount) return;
    const current = variantPrice(inputs[0]);
    const reference = referencePrice(inputs[0]);
    const uniform = current !== null && reference !== null && reference > current && inputs.every(input =>
      variantPrice(input) === current && referencePrice(input) === reference);
    discount.hidden = !uniform;
    if (uniform) {
      discount.querySelector('[data-product-reference]').textContent = money.format(reference);
      discount.querySelector('[data-product-saving]').textContent = `${money.format(reference - current)} économisés`;
    }
  }
  if (colorPicker && form) {
    const colors = [...colorPicker.querySelectorAll('[data-product-color]')];
    const sizes = [...(sizePicker?.querySelectorAll('[data-product-size]') || [])];
    const variants = [...form.querySelectorAll('input[name="variant"]')];
    const purchase = form.querySelector('button[type="submit"]');
    const quantity = form.querySelector('input[name="quantity"]');
    const feedback = colorPicker.querySelector('[data-color-feedback]');
    const stockSummary = document.querySelector('[data-product-stock-summary]');
    const overallStockLabel = stockSummary?.textContent;
    const originalPhoto = opener?.href;
    const photoFor = button => {
      const url = button?.dataset.colorImage || originalPhoto;
      if (showColorPhoto && url) showColorPhoto(url, button ? `Coloris ${button.dataset.colorName}` : main?.alt);
    };
    const matchingVariants = (size, color) => variants.filter(input =>
      input.dataset.variantSize === size && input.dataset.variantColor === color);
    const variantFor = (size, color) => {
      const matching = matchingVariants(size, color);
      return matching.find(input => !input.disabled && Number(input.dataset.variantAvailable) > 0) || matching[0];
    };

    colorPicker.hidden = false;
    if (sizes.length && variants.length) {
      let selectedSize = null;
      let selectedColor = null;
      const fallback = form.querySelector('[data-variant-fallback]');
      const canOrder = input => Boolean(input && !input.disabled && Number(input.dataset.variantAvailable) > 0);
      const stockState = matching => matching.some(canOrder) ? 'available'
        : (!matching.length || matching.some(input => input.dataset.variantStockKnown === 'false') ? 'unknown' : 'out');
      const stockLabel = state => state === 'available' ? 'En stock' : (state === 'out' ? 'Rupture de stock' : 'Stock à confirmer');

      function selectSize(button) {
        const variant = button && variantFor(button.dataset.productSize, selectedColor?.dataset.productColor);
        selectedSize = canOrder(variant) ? button : null;
        variants.forEach(input => { input.checked = input === variant && Boolean(selectedSize); });
        sizes.forEach(item => item.setAttribute('aria-pressed', String(item === selectedSize)));
        if (purchase) purchase.disabled = !selectedSize;
        showPrice(selectedSize ? [variant] : (selectedColor
          ? variants.filter(input => input.dataset.variantColor === selectedColor.dataset.productColor) : variants));
        if (quantity) {
          const limit = selectedSize ? Math.min(10, Number(variant.dataset.variantAvailable)) : 10;
          quantity.max = String(limit);
          if (!Number.isFinite(Number(quantity.value)) || Number(quantity.value) < 1) quantity.value = '1';
          if (Number(quantity.value) > limit) quantity.value = String(limit);
        }
        if (stockSummary) stockSummary.textContent = selectedColor
          ? `${selectedColor.dataset.colorName} : ${stockLabel(selectedColor.dataset.stockState)}` : overallStockLabel;
        if (!selectedColor) {
          feedback.textContent = 'Choisissez d’abord votre couleur pour voir ses tailles et sa disponibilité.';
        } else if (selectedColor.dataset.stockState !== 'available') {
          feedback.textContent = `${selectedColor.dataset.colorName} : ${stockLabel(selectedColor.dataset.stockState)}.` +
            (selectedColor.dataset.stockState === 'unknown' ? ' Contactez la boutique pour vérifier la disponibilité.' : ' Vous pouvez consulter les tailles et les photos de ce coloris.');
        } else {
          feedback.textContent = (selectedSize
            ? `${selectedColor.dataset.colorName} · ${selectedSize.dataset.sizeName}.`
            : `Choisissez votre taille pour le coloris ${selectedColor.dataset.colorName}.`) +
            (selectedColor.dataset.colorImage ? '' : ' Photo générale du produit : la photo de ce coloris n’est pas encore disponible.');
        }
      }

      function selectColor(button, preferredSize = selectedSize?.dataset.productSize) {
        selectedColor = button || null;
        colors.forEach(item => item.setAttribute('aria-pressed', String(item === selectedColor)));
        const compatibleSizes = [];
        sizes.forEach(item => {
          const variant = selectedColor && variantFor(item.dataset.productSize, selectedColor.dataset.productColor);
          item.hidden = !variant;
          item.disabled = !canOrder(variant);
          const state = item.querySelector('[data-size-state]');
          const unavailable = variant && !canOrder(variant);
          item.dataset.stockState = variant ? stockState(matchingVariants(item.dataset.productSize, selectedColor.dataset.productColor)) : 'unknown';
          if (state) {
            state.hidden = !unavailable;
            state.textContent = unavailable ? stockLabel(item.dataset.stockState) : '';
          }
          const sizePrice = item.querySelector('[data-size-price]');
          const amount = variantPrice(variant);
          if (sizePrice) {
            sizePrice.hidden = amount === null;
            sizePrice.textContent = amount === null ? '' : money.format(amount);
          }
          const sizeReference = item.querySelector('[data-size-reference]');
          const reference = referencePrice(variant);
          if (sizeReference) {
            sizeReference.hidden = amount === null || reference === null || reference <= amount;
            sizeReference.textContent = sizeReference.hidden ? '' : money.format(reference);
          }
          if (unavailable) item.title = `${item.dataset.sizeName} : ${state?.textContent || 'indisponible'} en ${selectedColor.dataset.colorName}`;
          else item.removeAttribute('title');
          if (canOrder(variant)) compatibleSizes.push(item);
        });
        const retained = compatibleSizes.find(item => item.dataset.productSize === preferredSize);
        selectSize(retained || (compatibleSizes.length === 1 ? compatibleSizes[0] : null));
        sizePicker.hidden = !selectedColor || !sizes.some(button => !button.hidden);
        photoFor(selectedColor);
      }

      colors.forEach(button => {
        const matching = variants.filter(input => input.dataset.variantColor === button.dataset.productColor);
        button.dataset.stockState = stockState(matching);
        // A color remains selectable to inspect its photograph and unavailable sizes.
        button.disabled = false;
        const state = button.querySelector('[data-color-state]');
        if (state) {
          state.hidden = button.dataset.stockState === 'available';
          state.textContent = state.hidden ? '' : stockLabel(button.dataset.stockState);
        }
        const colorPrice = button.querySelector('[data-color-price]');
        if (colorPrice) {
          const label = priceLabel(matching);
          colorPrice.hidden = !label;
          colorPrice.textContent = label;
        }
        if (button.dataset.stockState !== 'available') button.title = `${button.dataset.colorName} : ${stockLabel(button.dataset.stockState)}`;
        else button.removeAttribute('title');
        button.addEventListener('click', () => selectColor(button));
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
          ? (sizes.find(button => !button.hidden && !button.disabled) || selectedColor)
          : colors.find(button => button.dataset.stockState === 'available') || colors[0];
        target?.focus();
      });
      onGalleryPhotoChange = url => {
        const resolved = photoUrl(url);
        const matching = colors.filter(button => button.dataset.colorImage && photoUrl(button.dataset.colorImage) === resolved);
        if (resolved && matching.length === 1) selectColor(matching[0]);
      };
      const restored = variants.find(input => input.checked && canOrder(input));
      const availableColors = colors.filter(button => button.dataset.stockState === 'available');
      const originalColor = colors.find(button => photoUrl(button.dataset.colorImage) === photoUrl(originalPhoto));
      const initialColor = restored
        ? colors.find(button => button.dataset.productColor === restored.dataset.variantColor)
        : (originalColor || (availableColors.length === 1 ? availableColors[0] : null));
      selectColor(initialColor, restored?.dataset.variantSize);
      refreshSelectedPricing = () => {
        colors.forEach(button => {
          const label = button.querySelector('[data-color-price]');
          if (label) label.textContent = priceLabel(variants.filter(input => input.dataset.variantColor === button.dataset.productColor));
        });
        selectColor(selectedColor);
      };
      // The enhanced controls validate the pair; hidden radios cannot receive native validation focus.
      variants.forEach(input => { input.required = false; });
      if (fallback) fallback.hidden = true;
    } else {
      // Photos alone do not create a size/color combination that can be ordered.
      const selectPhotoOnlyColor = button => {
        colors.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
        photoFor(button);
        if (stockSummary) stockSummary.textContent = `${button.dataset.colorName} : Stock à confirmer`;
        feedback.textContent = `${button.dataset.colorName} : Stock à confirmer. Contactez la boutique pour vérifier les tailles et les stocks de ce coloris.`;
      };
      colors.forEach(button => {
        button.disabled = false;
        button.dataset.stockState = 'unknown';
        const state = button.querySelector('[data-color-state]');
        if (state) { state.hidden = false; state.textContent = 'Stock à confirmer'; }
        button.addEventListener('click', () => selectPhotoOnlyColor(button));
      });
      onGalleryPhotoChange = url => {
        const resolved = photoUrl(url);
        const matching = colors.filter(button => button.dataset.colorImage && photoUrl(button.dataset.colorImage) === resolved);
        if (resolved && matching.length === 1) selectPhotoOnlyColor(matching[0]);
      };
      if (!variants.length && purchase) purchase.disabled = true;
      feedback.textContent = 'Stock à confirmer. Contactez la boutique pour vérifier les tailles et les stocks de ces coloris.';
      const originalColor = colors.find(button => photoUrl(button.dataset.colorImage) === photoUrl(originalPhoto));
      if (originalColor || colors.length === 1) selectPhotoOnlyColor(originalColor || colors[0]);
    }
  }
  if (priceDisplay?.dataset.priceEndpoint) {
    let priceTimer;
    let updating = false;
    function planPriceUpdate(serverTime, nextChange) {
      clearTimeout(priceTimer);
      if (!nextChange) return;
      const wait = Date.parse(nextChange) - Date.parse(serverTime);
      if (!Number.isFinite(wait)) return;
      priceTimer = setTimeout(refreshPrices, Math.min(2147483000, Math.max(1000, wait + 300)));
    }
    async function refreshPrices() {
      if (updating) return;
      updating = true;
      try {
        const response = await fetch(priceDisplay.dataset.priceEndpoint, {cache: 'no-store', credentials: 'same-origin'});
        if (!response.ok) throw new Error('Price unavailable');
        const data = await response.json();
        const rows = new Map(data.variants.map(row => [String(row.id), row]));
        document.querySelectorAll('input[name="variant"]').forEach(input => {
          const row = rows.get(input.value);
          if (!row) return;
          input.dataset.variantPrice = row.price;
          input.dataset.variantReference = row.reference || '';
        });
        refreshSelectedPricing?.();
        planPriceUpdate(data.server_time, data.next_change);
      } catch (_) {
        // The server rechecks the amount before payment; retry without losing the selection.
        priceTimer = setTimeout(refreshPrices, 30000);
      } finally {
        updating = false;
      }
    }
    planPriceUpdate(priceDisplay.dataset.priceServerTime, priceDisplay.dataset.priceNextChange);
    window.addEventListener('focus', refreshPrices);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') refreshPrices();
    });
  }
  document.querySelector('.size-advice')?.addEventListener('click', () => {
    const help = document.getElementById('size-help');
    if (help) help.open = true;
  });
})();
