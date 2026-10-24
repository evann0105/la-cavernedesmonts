(() => {
  document.querySelectorAll('.catalog-bulk-choice').forEach(group => {
    const toggle = group.querySelector('input[type="checkbox"][name^="change_"]');
    if (!toggle) return;
    const fields = Array.from(group.querySelectorAll('input,select,textarea')).filter(field => field !== toggle);
    const update = () => {
      fields.forEach(field => { field.disabled = !toggle.checked; });
      group.classList.toggle('is-inactive', !toggle.checked);
    };
    toggle.addEventListener('change', update);
    window.addEventListener('pageshow', update);
    update();
  });
})();
