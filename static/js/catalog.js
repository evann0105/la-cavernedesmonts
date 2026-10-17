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
  const previewsByInput = new WeakMap();
  form.addEventListener('change', event => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== 'file') return;
    let state = previewsByInput.get(input);
    if (!state) {
      const previews = document.createElement('div');
      previews.className = 'catalog-upload-preview';
      input.insertAdjacentElement('afterend', previews);
      state = {previews, urls: []};
      previewsByInput.set(input, state);
    }
    state.urls.forEach(url => URL.revokeObjectURL(url));
    state.urls = [];
    state.previews.replaceChildren();
    Array.from(input.files).slice(0, 12).forEach(file => {
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) return;
      const image = document.createElement('img');
      const url = URL.createObjectURL(file);
      state.urls.push(url);
      image.src = url;
      image.alt = 'Photo sélectionnée : ' + file.name;
      image.width = 100;
      image.height = 100;
      state.previews.append(image);
    });
  });
})();
