(() => {
  const main = document.getElementById('contenu');
  if (!main?.querySelector('[data-cart-page]')) return;
  const pending = new Map();
  let timer;
  let busy = false;
  let syncFailed = false;
  let pendingCodeSubmit = null;
  const money = new Intl.NumberFormat(document.documentElement.lang || 'fr', {
    style: 'currency', currency: 'EUR',
  });
  const status = document.createElement('p');
  status.className = 'wrap small';
  status.setAttribute('role', 'status');
  main.append(status);

  function enhance() {
    main.querySelectorAll('[data-quantity-submit]').forEach(button => { button.hidden = true; });
  }
  enhance();

  // Integer cents avoid floating-point drift; the server still owns final prices.
  function previewPrices() {
    let totalCents = 0;
    let count = 0;
    for (const line of main.querySelectorAll('.cart-line')) {
      const input = line.querySelector('[name="quantity"]');
      const quantity = input.value && input.checkValidity()
        ? Number(input.value) : Number(input.defaultValue);
      const cents = Math.round(Number(line.dataset.unitPrice) * 100) * quantity;
      line.querySelector(':scope > strong').textContent = money.format(cents / 100);
      totalCents += cents;
      count += quantity;
    }
    const total = main.querySelector('.cart-summary .total strong');
    if (total) total.textContent = money.format(totalCents / 100);
    const badge = document.querySelector('.header .bag span');
    if (badge) badge.textContent = badge.textContent.replace(/\(\d+\)/, `(${count})`);
  }

  function discountPreview(recalculating) {
    const summary = main.querySelector('[data-cart-code-summary]');
    if (!summary) return;
    summary.setAttribute('aria-busy', String(recalculating));
    const discount = summary.querySelector('[data-cart-discount-amount]');
    const net = summary.querySelector('[data-cart-discounted-total]');
    if (discount) discount.textContent = recalculating
      ? 'Recalcul…' : `− ${money.format(Number(discount.dataset.cartDiscountAmount))}`;
    if (net) net.textContent = recalculating
      ? 'Recalcul…' : money.format(Number(net.dataset.cartDiscountedTotal));
  }

  function queue(form) {
    const input = form.elements.quantity;
    clearTimeout(timer);
    const key = form.elements.key.value;
    if (!input.value || !input.checkValidity()) {
      pending.delete(key);
      previewPrices();
      discountPreview(busy || pending.size > 0);
      status.textContent = 'Indiquez une quantité entière entre 0 et 10.';
      return;
    }
    pending.set(key, { url: form.action, body: new FormData(form) });
    previewPrices();
    discountPreview(true);
    status.textContent = 'Confirmation du panier…';
    timer = setTimeout(update, 120);
  }

  async function update() {
    if (busy || !pending.size) return;
    busy = true;
    main.setAttribute('aria-busy', 'true');
    let page;
    try {
      // Serialize session writes, keeping the latest quantity for each line.
      while (pending.size) {
        const [key, request] = pending.entries().next().value;
        pending.delete(key);
        const response = await fetch(request.url, {
          method: 'POST', body: request.body, credentials: 'same-origin',
        });
        if (!response.ok) throw new Error('Cart update failed');
        page = new DOMParser().parseFromString(await response.text(), 'text/html');
        if (!page.querySelector('[data-cart-page]')) throw new Error('Invalid cart response');
      }
      const focusId = document.activeElement?.id;
      // Quantities may change while the customer is typing a code. Preserve
      // that input when the authoritative cart HTML replaces the old summary.
      const typedCode = main.querySelector('[name="benefit_code"]')?.value;
      const nextMain = page.getElementById('contenu');
      main.replaceChildren(...nextMain.childNodes);
      const codeInput = main.querySelector('[name="benefit_code"]');
      if (codeInput && typedCode !== undefined) codeInput.value = typedCode;
      const bag = document.querySelector('.header .bag');
      const nextBag = page.querySelector('.header .bag');
      if (bag && nextBag) bag.replaceChildren(...nextBag.childNodes);
      status.textContent = page.querySelector('.messages') ? 'Vérifiez le message du panier.' : 'Panier mis à jour.';
      main.append(status);
      enhance();
      syncFailed = false;
      if (focusId) document.getElementById(focusId)?.focus({ preventScroll: true });
    } catch (error) {
      pending.clear();
      pendingCodeSubmit = null;
      syncFailed = true;
      main.querySelectorAll('.quantity-form input[name="quantity"]').forEach(input => {
        input.value = input.defaultValue;
      });
      previewPrices();
      discountPreview(false);
      status.textContent = 'La mise à jour n’a pas pu être confirmée. Réessayez avec « Mettre à jour ».';
      main.querySelectorAll('[data-quantity-submit]').forEach(button => { button.hidden = false; });
    } finally {
      busy = false;
      main.removeAttribute('aria-busy');
      // A code submission waits for all quantity writes. Reuse the NEW form
      // and its CSRF token, so both actions see the same server-side subtotal.
      if (!syncFailed && !pending.size && pendingCodeSubmit) {
        const intent = pendingCodeSubmit;
        pendingCodeSubmit = null;
        const button = main.querySelector(`[data-cart-code-form] [name="action"][value="${intent.action}"]`);
        if (button?.form) {
          const input = button.form.querySelector('[name="benefit_code"]');
          if (input) input.value = intent.code;
          button.form.requestSubmit(button);
        }
      }
    }
  }

  main.addEventListener('input', event => {
    if (event.target.matches('.quantity-form input[name="quantity"]')) queue(event.target.form);
  });
  main.addEventListener('change', event => {
    if (event.target.matches('.quantity-form input[name="quantity"]')) queue(event.target.form);
  });
  main.addEventListener('submit', event => {
    const form = event.target;
    if (form.matches('[data-cart-code-form]')) {
      const invalid = main.querySelector('.quantity-form input:invalid');
      if (invalid) { event.preventDefault(); invalid.reportValidity(); return; }
      if (syncFailed) {
        event.preventDefault();
        status.textContent = 'Confirmez le panier avec « Mettre à jour » avant d’appliquer ou de retirer un code.';
        return;
      }
      if (busy || pending.size) {
        event.preventDefault();
        const action = (event.submitter || form.querySelector('[name="action"]'))?.value;
        if (!['apply_benefit', 'remove_benefit'].includes(action)) return;
        pendingCodeSubmit = { action, code: form.querySelector('[name="benefit_code"]')?.value || '' };
        status.textContent = 'Confirmation du panier avant de mettre à jour votre code…';
        clearTimeout(timer);
        update();
      }
      return;
    }
    if (!form.matches('.quantity-form, [data-cart-remove]')) return;
    event.preventDefault();
    if (form.matches('[data-cart-remove]')) {
      const input = form.closest('.cart-line')?.querySelector('.quantity-form input[name="quantity"]');
      if (input) input.value = '0';
    }
    // Queue the action even while another request is in flight. A removal
    // replaces a pending quantity change for the same item and is never lost.
    queue(form);
    clearTimeout(timer);
    update();
  });
  main.addEventListener('click', event => {
    if (!event.target.closest('[data-cart-checkout]')) return;
    if (syncFailed) {
      event.preventDefault();
      status.textContent = 'Confirmez le panier avec « Mettre à jour » avant de poursuivre.';
      return;
    }
    const invalid = main.querySelector('.quantity-form input:invalid');
    if (invalid) { event.preventDefault(); invalid.reportValidity(); return; }
    if (busy || pending.size) {
      event.preventDefault();
      clearTimeout(timer);
      update();
    }
  });
})();
