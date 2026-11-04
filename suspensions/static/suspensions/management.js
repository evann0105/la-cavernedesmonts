(() => {
  'use strict';
  document.querySelectorAll('[data-pause-editor]').forEach(editor => {
    const message = editor.querySelector('[name="message"]');
    const end = editor.querySelector('[name="ends_at"]');
    const start = editor.querySelector('[data-pause-start]');
    const modes = [...editor.querySelectorAll('[name="mode"]')];
    const preview = document.querySelector('[data-pause-preview-message]');
    const datePreview = document.querySelector('[data-pause-preview-date]');
    const button = editor.querySelector('[data-pause-submit]');
    const fallbackMessage = preview?.dataset.fallback || '';
    const update = () => {
      const mode = modes.find(input => input.checked)?.value;
      if (start && modes.length) {
        start.hidden = mode === 'now';
        const input = start.querySelector?.('input');
        if (input) input.disabled = mode === 'now';
      }
      if (preview && message) preview.textContent = message.value.trim() || fallbackMessage;
      if (datePreview && end) {
        const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(end.value);
        // The entered value is a wall-clock time in Paris, regardless of the
        // device timezone. The server validates daylight-saving transitions.
        datePreview.textContent = match ? `Reprise des commandes le ${match[3]}/${match[2]}/${match[1]} à ${match[4]} h ${match[5]} · heure de Paris` : 'La date de reprise sera affichée ici.';
      }
      if (button && button.dataset.create === 'true') button.textContent = mode === 'now' ? 'Mettre les commandes en pause maintenant' : 'Programmer cette pause';
    };
    editor.addEventListener('input', update);
    editor.addEventListener('change', update);
    update();
  });
})();
