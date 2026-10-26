(() => {
  'use strict';
  const picker = document.querySelector('[data-customer-picker]');
  if (!picker || !window.fetch) return;
  const form = picker.closest('form');
  const query = picker.querySelector('#client-query');
  const status = picker.querySelector('[data-picker-status]');
  const results = picker.querySelector('[data-picker-results]');
  const selected = picker.querySelector('[data-selected-client]');
  const selectedPanel = picker.querySelector('[data-picker-selected]');
  const searchButton = picker.querySelector('[data-picker-search]');
  const newButton = picker.querySelector('[data-picker-new]');
  const name = form?.elements.namedItem('customer_name');
  const email = form?.elements.namedItem('customer_email');
  const phone = form?.elements.namedItem('phone');
  const order = form?.elements.namedItem('related_order');
  if (!form || !name || !email || !phone || !order) return;
  const csrf = form.querySelector('[name=csrfmiddlewaretoken]').value;
  let searchRevision = 0;
  let selectionRevision = 0;
  let pendingSelection = false;

  // Keep regular buttons and Tab navigation; results are not a custom combobox.
  for (const button of picker.querySelectorAll('button')) button.type = 'button';
  async function request(url, values) {
    const response = await fetch(url, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'X-CSRFToken': csrf, 'Accept': 'application/json' },
      body: new URLSearchParams(values),
    });
    if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) {
      throw new Error('Lookup unavailable');
    }
    return response.json();
  }

  function textElement(tag, value, className) {
    const element = document.createElement(tag);
    element.textContent = value;
    if (className) element.className = className;
    return element;
  }

  function showResults(values) {
    results.replaceChildren();
    for (const customer of values) {
      const row = document.createElement('li');
      const details = document.createElement('div');
      details.append(textElement('strong', customer.name));
      details.append(textElement('p', `${customer.account_label} · ${customer.order_count} commande${customer.order_count === 1 ? '' : 's'}`, 'customer-muted'));
      details.append(textElement('p', [customer.email || 'Sans e-mail', customer.phone].filter(Boolean).join(' · '), 'customer-contact'));
      const button = textElement('button', 'Choisir cette fiche', 'button button-outline');
      button.type = 'button';
      button.dataset.pickKey = customer.key;
      button.setAttribute('aria-label', `Choisir la fiche de ${customer.name}`);
      row.append(details, button);
      results.append(row);
    }
  }

  async function search() {
    const current = ++searchRevision;
    const value = query.value.trim();
    if (value.length < 2 || (/^[0-9+(). /-]+$/.test(value) && value.replace(/\D/g, '').length < 4)) {
      results.replaceChildren();
      status.textContent = 'Saisissez au moins 2 caractères, ou 4 chiffres pour un téléphone.';
      return;
    }
    searchButton.disabled = true;
    status.textContent = 'Recherche en cours…';
    try {
      const data = await request(picker.dataset.searchUrl, {q: value});
      if (current !== searchRevision) return;
      showResults(data.results);
      status.textContent = data.results.length ? `${data.results.length} fiche${data.results.length === 1 ? '' : 's'} proposée${data.results.length === 1 ? '' : 's'}.${data.more ? ' Affinez la recherche pour voir les autres résultats.' : ''}` : 'Aucune fiche trouvée. Vérifiez la recherche ou renseignez un nouveau client ci-dessous.';
    } catch (_) {
      if (current === searchRevision) status.textContent = 'La recherche est momentanément indisponible. Réessayez ; les informations saisies sont conservées.';
    } finally {
      if (current === searchRevision) searchButton.disabled = false;
    }
  }

  function setCustomer(customer) {
    selected.value = customer?.key || '';
    name.value = customer?.name || '';
    email.value = customer?.email || '';
    phone.value = customer?.phone || '';
    email.disabled = !!customer?.email_locked;
    order.replaceChildren(new Option('Sans commande précise', ''));
    for (const item of customer?.orders || []) order.add(new Option(item.label, item.value));
    order.value = '';
    selectedPanel.hidden = !customer;
    picker.querySelector('[data-picked-name]').textContent = customer?.name || '';
    picker.querySelector('[data-picked-contact]').textContent = customer ? [customer.email || 'Sans adresse e-mail', customer.phone].filter(Boolean).join(' · ') : '';
    picker.querySelector('[data-picked-account]').textContent = customer?.account_label || '';
    newButton.textContent = customer ? 'Saisir un nouveau client' : 'Continuer avec un nouveau client';
    results.replaceChildren();
  }

  async function pick(key, button) {
    const current = ++selectionRevision;
    searchRevision++;
    searchButton.disabled = false;
    pendingSelection = true;
    button.disabled = true;
    status.textContent = 'Ouverture de la fiche…';
    try {
      const data = await request(picker.dataset.pickUrl, {client: key});
      if (current !== selectionRevision) return;
      setCustomer(data.customer);
      status.textContent = `La fiche de ${data.customer.name} est sélectionnée. Ses coordonnées et ses commandes sont disponibles ci-dessous.`;
      selectedPanel.setAttribute('tabindex', '-1');
      selectedPanel.focus({preventScroll: true});
    } catch (_) {
      if (current === selectionRevision) status.textContent = 'Cette fiche n’a pas pu être sélectionnée. Réessayez ; la sélection précédente est conservée.';
    } finally {
      if (current === selectionRevision) pendingSelection = false;
      button.disabled = false;
    }
  }

  searchButton.addEventListener('click', search);
  query.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); search(); }
  });
  query.addEventListener('input', () => {
    searchRevision++;
    searchButton.disabled = false;
    results.replaceChildren();
    status.textContent = '';
  });
  results.addEventListener('click', event => {
    const button = event.target.closest('[data-pick-key]');
    if (button) pick(button.dataset.pickKey, button);
  });
  newButton.addEventListener('click', () => {
    selectionRevision++;
    searchRevision++;
    searchButton.disabled = false;
    pendingSelection = false;
    setCustomer(null);
    status.textContent = 'Renseignez les coordonnées du nouveau client. La demande et le suivi sont conservés.';
    name.focus();
  });
  form.addEventListener('submit', event => {
    if (pendingSelection) {
      event.preventDefault();
      status.textContent = 'La fiche est en cours de sélection. Patientez avant de créer le dossier.';
    }
  });
})();
