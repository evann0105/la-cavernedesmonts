(() => {
  'use strict';

  const drafts = [];
  const trackDraft = (form, fields, startsUnsaved, feedback) => {
    const initial = fields.map(field => field.value);
    const draft = { dirty: startsUnsaved, submitting: false };
    drafts.push(draft);
    const update = () => {
      draft.dirty = startsUnsaved || fields.some((field, index) => field.value !== initial[index]);
      if (feedback) feedback.textContent = draft.dirty ? 'Modifications non enregistrées.' : '';
    };
    fields.forEach(field => {
      field.addEventListener('input', update);
      field.addEventListener('change', update);
    });
    form.addEventListener('submit', () => { draft.submitting = true; });
    update();
  };

  document.querySelectorAll('[data-cancellation-reason-form]').forEach(form => {
    const select = form.querySelector('[name="reason_code"]');
    const reason = form.querySelector('[name="reason"]');
    const note = form.querySelector('[name="internal_note"]');
    const proposal = form.querySelector('[data-reason-proposal]');
    const proposalText = form.querySelector('[data-reason-proposal-text]');
    const apply = form.querySelector('[data-apply-reason]');
    const feedback = form.querySelector('[data-reason-feedback]');
    if (!select || !reason || !proposal || !proposalText || !apply) return;

    const selectedText = () => select.options[select.selectedIndex]?.dataset.reasonText || '';
    let previousSuggestion = selectedText();
    const applySuggestion = (text, announce = true) => {
      reason.value = text;
      reason.dispatchEvent(new Event('input', { bubbles: true }));
      proposal.hidden = true;
      if (feedback && announce) feedback.textContent = 'Le texte proposé est ajouté. Vous pouvez le personnaliser.';
    };
    select.addEventListener('change', () => {
      const nextText = selectedText();
      const canReplace = !reason.value.trim() || reason.value.trim() === previousSuggestion.trim();
      previousSuggestion = nextText;
      if (!nextText) {
        proposal.hidden = true;
        if (feedback) feedback.textContent = 'Vous pouvez saisir votre propre motif.';
      } else if (canReplace) {
        applySuggestion(nextText);
      } else {
        proposalText.textContent = nextText;
        proposal.hidden = false;
        if (feedback) feedback.textContent = 'Votre texte personnalisé est conservé.';
      }
    });
    apply.addEventListener('click', () => {
      const text = selectedText();
      if (!text) return;
      applySuggestion(text);
      reason.focus();
    });
    reason.addEventListener('input', () => {
      if (reason.value.trim() === selectedText().trim()) proposal.hidden = true;
    });
    // A failed server submission can still have a preset and an empty field.
    // Applying its suggestion here never replaces an authored message.
    if (!reason.value.trim() && previousSuggestion) applySuggestion(previousSuggestion, false);
    trackDraft(form, [select, reason, note].filter(Boolean), form.dataset.reasonUnsaved === 'true');
  });

  document.querySelectorAll('[data-cancellation-note-form]').forEach(form => {
    const note = form.querySelector('[name="internal_note"]');
    if (!note) return;
    trackDraft(form, [note], form.dataset.noteUnsaved === 'true', form.querySelector('[data-note-feedback]'));
  });

  window.addEventListener('beforeunload', event => {
    if (!drafts.some(draft => draft.dirty && !draft.submitting)) return;
    event.preventDefault();
    event.returnValue = '';
  });
})();
