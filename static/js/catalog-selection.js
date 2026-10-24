/* Selection stays on the current catalogue page; the server validates every ID. */
(() => {
  document.querySelectorAll('[data-catalog-selection]').forEach(form => {
    const boxes = Array.from(form.querySelectorAll('[data-catalog-product-checkbox]'));
    const all = form.querySelector('[data-catalog-select-all]');
    const allControl = form.querySelector('[data-catalog-select-all-control]');
    const clear = form.querySelector('[data-catalog-clear]');
    const submit = form.querySelector('[data-catalog-bulk-submit]');
    const count = form.querySelector('[data-catalog-selection-count]');
    if (!all || !clear || !submit || !count) return;
    const update = () => {
      const selected = boxes.filter(box => box.checked).length;
      all.checked = boxes.length > 0 && selected === boxes.length;
      all.indeterminate = selected > 0 && selected < boxes.length;
      all.disabled = boxes.length === 0;
      clear.hidden = selected === 0;
      submit.disabled = selected === 0;
      count.textContent = selected === 0 ? 'Cochez les articles à modifier ensemble.' : `${selected} article${selected > 1 ? 's' : ''} sélectionné${selected > 1 ? 's' : ''} sur cette page.`;
      boxes.forEach(box => box.closest('[data-catalog-product-row]')?.classList.toggle('is-selected', box.checked));
    };
    allControl.hidden = false;
    all.addEventListener('change', () => { boxes.forEach(box => { box.checked = all.checked; }); update(); });
    clear.addEventListener('click', () => { boxes.forEach(box => { box.checked = false; }); update(); all.focus(); });
    boxes.forEach(box => box.addEventListener('change', update));
    form.addEventListener('submit', event => { if (!boxes.some(box => box.checked)) { event.preventDefault(); all.focus(); } });
    window.addEventListener('pageshow', update);
    update();
  });
})();
