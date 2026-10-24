(() => {
  // All panels remain open without JavaScript. Native validation can always
  // reveal a field before the browser moves focus to an invalid control.
  document.querySelectorAll('[data-home-editor]').forEach(form => {
    const panels = Array.from(form.querySelectorAll('[data-home-panel]'));
    if (panels.length > 1) {
      panels.forEach((panel, index) => {
        panel.open = index === 0 || panel.hasAttribute('data-panel-errors');
      });
    }
    form.addEventListener('invalid', event => {
      let panel = event.target.closest('details');
      while (panel && form.contains(panel)) {
        panel.open = true;
        panel = panel.parentElement?.closest('details');
      }
    }, true);
  });

  const source = document.getElementById('homepage-featured-choices');
  if (!source) return;
  let choices;
  try { choices = JSON.parse(source.textContent); } catch { return; }
  document.querySelectorAll('[data-featured-position]').forEach(card => {
    const select = card.querySelector('select');
    const image = card.querySelector('[data-featured-image]');
    const name = card.querySelector('[data-featured-name]');
    if (!select || !image || !name) return;
    select.addEventListener('change', () => {
      const product = choices[select.value];
      image.hidden = !product;
      image.alt = product?.name || '';
      if (product?.image) image.src = product.image;
      else image.removeAttribute('src');
      name.textContent = product?.name || 'Aucun produit choisi à cette position.';
    });
  });
})();
