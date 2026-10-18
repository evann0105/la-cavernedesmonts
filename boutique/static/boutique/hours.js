(() => {
  const editor = document.querySelector('[data-hours-editor]');
  if (!editor) return;

  editor.querySelectorAll('[data-hours-day]').forEach(day => {
    const closed = day.querySelector('input[type="checkbox"][name$="-is_closed"]');
    if (!closed) return;
    const note = day.querySelector('[data-hours-closed-note]');
    const updateDay = () => {
      day.classList.toggle('is-closed', closed.checked);
      day.querySelectorAll('[data-hours-slots] input[type="time"]').forEach(input => {
        input.disabled = closed.checked;
      });
      if (note) note.hidden = !closed.checked;
    };
    closed.addEventListener('change', updateDay);
    updateDay();
  });
})();
