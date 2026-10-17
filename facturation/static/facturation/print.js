document.querySelectorAll('[data-print-document]').forEach((button) => {
  button.addEventListener('click', () => window.print());
});
