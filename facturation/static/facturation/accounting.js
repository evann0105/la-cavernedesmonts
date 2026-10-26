(() => {
  const form = document.querySelector('[data-accounting-period]');
  if (!form) return;
  const selector = form.querySelector('[name="periode"]');
  const update = () => {
    for (const group of form.querySelectorAll('[data-period-kinds]')) {
      const visible = group.dataset.periodKinds.split(' ').includes(selector.value);
      group.hidden = !visible;
      for (const field of group.querySelectorAll('input, select')) field.disabled = !visible;
    }
  };
  selector.addEventListener('change', update);
  update();
})();
