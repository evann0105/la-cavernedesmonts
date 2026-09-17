(() => {
  const form = document.querySelector('.catalog-form');
  if (!form) return;
  let changed = false;
  form.addEventListener('input', () => { changed = true; });
  form.addEventListener('change', () => { changed = true; });
  form.addEventListener('submit', () => { changed = false; });
  window.addEventListener('beforeunload', event => {
    if (changed) { event.preventDefault(); event.returnValue = ''; }
  });
  form.querySelectorAll('input[type=file]').forEach(input => {
    let urls = [];
    const previews = document.createElement('div');
    previews.className = 'catalog-upload-preview';
    input.insertAdjacentElement('afterend', previews);
    input.addEventListener('change', () => {
      urls.forEach(url => URL.revokeObjectURL(url));
      urls = [];
      previews.replaceChildren();
      Array.from(input.files).slice(0, 12).forEach(file => {
        if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) return;
        const image = document.createElement('img');
        const url = URL.createObjectURL(file);
        urls.push(url);
        image.src = url;
        image.alt = 'Photo sélectionnée : ' + file.name;
        image.width = 100;
        image.height = 100;
        previews.append(image);
      });
    });
  });
})();
