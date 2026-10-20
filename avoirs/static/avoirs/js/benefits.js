(() => {
  document.querySelectorAll('[data-copy-benefit]').forEach((button) => {
    button.addEventListener('click', async () => {
      const output = button.closest('article').querySelector('[data-copy-feedback]');
      try {
        await navigator.clipboard.writeText(button.dataset.copyBenefit);
        output.textContent = 'Code copié. Collez-le à l’étape de commande.';
      } catch (_) {
        output.textContent = 'Sélectionnez le code affiché pour le copier.';
      }
    });
  });
})();
