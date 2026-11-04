(() => {
  const banner = document.querySelector('[data-orders-banner]');
  const initial = document.getElementById('order-pause-state');
  if (!banner || !initial) return;
  let state;
  try { state = JSON.parse(initial.textContent); } catch (_) { return; }
  const valid = value => value && typeof value.paused === 'boolean'
    && typeof value.message === 'string' && typeof value.resume_label === 'string'
    && Number.isFinite(Date.parse(value.checked_at))
    && (value.next_change_at === null || Number.isFinite(Date.parse(value.next_change_at)));
  if (!valid(state)) return;
  const endpoint = new URL(banner.dataset.ordersStatusUrl, window.location.origin);
  if (endpoint.origin !== window.location.origin) return;
  const clock = () => performance.now();
  let serverTime = Date.parse(state.checked_at), receivedAt = clock();
  let timer = null, request = null, attempts = 0, uncertain = false, leaving = false, wakePending = false;
  const announcement = document.querySelector('[data-orders-announcement]');
  const now = () => serverTime + clock() - receivedAt;
  const dormant = () => document.hidden || navigator.onLine === false || leaving;
  const block = () => state.paused || uncertain;
  const clearTimer = () => { if (timer !== null) clearTimeout(timer); timer = null; };

  function apply(announce = false) {
    const blocked = block();
    const message = uncertain ? 'La disponibilité des commandes est en cours de vérification. Vous pouvez conserver votre panier.' : state.message;
    const resume = uncertain ? 'Si ce message persiste, actualisez la page pour vérifier la reprise.' : state.resume_label;
    banner.hidden = !blocked;
    document.body?.classList.toggle('has-order-pause', blocked);
    banner.querySelector('[data-orders-title]').textContent = uncertain
      ? 'Vérification des commandes en cours.' : 'Les commandes sont momentanément en pause.';
    document.querySelectorAll('[data-orders-message]').forEach(node => { node.textContent = message; });
    document.querySelectorAll('[data-orders-resume]').forEach(node => { node.textContent = resume; });
    document.querySelectorAll('[data-orders-fieldset]').forEach(node => { node.disabled = blocked; });
    document.querySelectorAll('[data-orders-open]').forEach(node => { node.hidden = blocked; });
    document.querySelectorAll('[data-orders-paused]').forEach(node => { node.hidden = !blocked; });
    if (announce && announcement) announcement.textContent = uncertain
      ? 'Vérification de la disponibilité des commandes.'
      : state.paused ? `Commandes en pause. ${state.resume_label}` : 'Les commandes sont de nouveau disponibles.';
  }

  function schedule() {
    clearTimer();
    if (dormant() || !state.next_change_at) return;
    const delay = Date.parse(state.next_change_at) - now();
    // A long pause uses timer hops, not recurring network requests.
    timer = setTimeout(() => {
      if (dormant()) return;
      if (Date.parse(state.next_change_at) - now() > 50) { schedule(); return; }
      uncertain = true;
      apply();
      check();
    }, Math.min(2147483000, Math.max(100, delay + 100)));
  }

  async function check() {
    if (dormant() || request) return;
    clearTimer();
    const controller = new AbortController();
    request = controller;
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(endpoint.href, { method: 'GET', credentials: 'same-origin',
        cache: 'no-store', headers: { Accept: 'application/json' }, signal: controller.signal });
      if (!response.ok || response.redirected) throw new Error('Unavailable');
      const next = await response.json();
      if (!valid(next)) throw new Error('Invalid state');
      if (dormant()) return;
      const changed = state.paused !== next.paused || state.message !== next.message
        || state.resume_label !== next.resume_label || uncertain;
      state = next;
      serverTime = Date.parse(next.checked_at);
      receivedAt = clock();
      uncertain = false;
      attempts = 0;
      apply(changed);
      schedule();
    } catch (_) {
      if (dormant()) return;
      if (state.next_change_at && Date.parse(state.next_change_at) <= now()) {
        uncertain = true;
        apply();
      }
      attempts += 1;
      // A failing endpoint never creates permanent background polling.
      if (attempts <= 4) timer = setTimeout(check, [5000, 15000, 30000, 60000][attempts - 1]);
    } finally {
      clearTimeout(timeout);
      if (request === controller) request = null;
      if (wakePending && !dormant()) { wakePending = false; check(); }
    }
  }

  function wake() {
    if (dormant()) return;
    attempts = 0;
    if (request) { wakePending = true; return; }
    check();
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimer(); request?.abort(); }
    else wake();
  });
  window.addEventListener('offline', () => { clearTimer(); request?.abort(); });
  window.addEventListener('online', wake);
  window.addEventListener('focus', wake);
  window.addEventListener('pagehide', () => { leaving = true; clearTimer(); request?.abort(); });
  window.addEventListener('pageshow', event => { leaving = false; if (event.persisted) wake(); });
  // Block a stale visible action immediately. The server independently checks
  // every new checkout; this never submits or changes a customer's form.
  document.addEventListener('submit', event => {
    if (!block() || !event.target.matches('.product-purchase, .checkout-payment-form')) return;
    event.preventDefault();
    apply(true);
  }, true);
  document.addEventListener('click', event => {
    if (block() && event.target.closest('[data-cart-checkout]')) {
      event.preventDefault();
      apply(true);
    }
  }, true);
  const main = document.getElementById('contenu');
  if (main) new MutationObserver(records => {
    // The cart replaces its HTML after quantity edits. Apply the known pause to
    // those new controls, then obtain fresh server state without losing input.
    const replaced = records.some(record => [...record.addedNodes].some(node => node.nodeType === 1
      && (node.matches('[data-orders-fieldset], [data-orders-open], [data-orders-paused]')
        || node.querySelector('[data-orders-fieldset], [data-orders-open], [data-orders-paused]'))));
    if (replaced) { apply(); wake(); }
  }).observe(main, { childList: true, subtree: true });
  apply();
  schedule();
  // Covers a document restored from cache and a pause edited since rendering.
  check();
})();
