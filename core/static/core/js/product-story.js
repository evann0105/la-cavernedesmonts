(() => {
  const main = document.getElementById('main-product-image');
  const opener = document.querySelector('[data-open-photo]');
  const photos = [...document.querySelectorAll('[data-product-photo]')];
  const dialog = document.querySelector('.product-lightbox');
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
    });
    dialog.addEventListener('keydown', event => {
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); show(index + (event.key === 'ArrowLeft' ? -1 : 1)); }
    });
  }
  document.querySelector('.size-advice')?.addEventListener('click', () => {
    const help = document.getElementById('size-help');
    if (help) help.open = true;
  });
})();
