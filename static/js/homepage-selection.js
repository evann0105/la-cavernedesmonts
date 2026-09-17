(() => {
  const data = document.getElementById('homepage-product-choices');
  if (!data) return;
  const choices = JSON.parse(data.textContent);
  document.querySelectorAll('.homepage-choice').forEach(panel => {
    const select = panel.querySelector('select');
    const update = () => {
      const product = choices[panel.dataset.slot][select.value] || choices[panel.dataset.slot][''];
      const image = panel.querySelector('[data-choice-image]');
      image.hidden = !product.image;
      if (product.image) image.src = product.image;
      else image.removeAttribute('src');
      image.alt = product.name;
      panel.querySelector('[data-choice-name]').textContent = product.name;
    };
    select.addEventListener('change', update);
    update();
  });
})();
