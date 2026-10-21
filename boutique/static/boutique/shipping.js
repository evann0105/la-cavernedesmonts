(() => {
  const normalize = (text) => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('fr').trim();
  document.querySelectorAll('[data-shipping-zone-form]').forEach((form) => {
    const search = form.querySelector('[data-country-search]');
    const searchWrap = form.querySelector('[data-country-search-wrap]');
    const count = form.querySelector('[data-country-count]');
    const empty = form.querySelector('[data-country-empty]');
    const options = Array.from(form.querySelectorAll('[data-country-option]'));
    if (!search || !searchWrap || !count || !empty) return;

    const update = () => {
      const query = normalize(search.value);
      let visible = 0;
      let selected = 0;
      options.forEach((option) => {
        const checkbox = option.querySelector('input[type="checkbox"]');
        if (!checkbox) return;
        option.hidden = !normalize(`${option.textContent} ${checkbox.value}`).includes(query);
        if (!option.hidden) visible += 1;
        if (checkbox.checked) selected += 1;
      });
      count.textContent = `${selected} pays sélectionné${selected > 1 ? 's' : ''} · ${visible} pays affiché${visible > 1 ? 's' : ''}`;
      empty.hidden = visible > 0;
    };
    searchWrap.hidden = false;
    search.addEventListener('input', update);
    form.addEventListener('change', update);
    update();
  });
})();
