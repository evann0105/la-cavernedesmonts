(() => {
  'use strict';
  const panel = document.querySelector('[data-pause-dashboard]');
  if (!panel) return;
  const refreshButton = panel.querySelector('[data-pause-refresh]');
  const feedback = panel.querySelector('[data-pause-feedback]');
  const fields = Object.fromEntries(['title', 'badge', 'schedule', 'message'].map(key => [key, panel.querySelector(`[data-pause-${key}]`)]));
  const MAX_DELAY = 2147483000;
  let timer = null, inFlight = false, retryOnShow = false;
  const clearBoundary = () => { if (timer !== null) window.clearTimeout(timer); timer = null; };
  function report(text, stale) {
    feedback.textContent = text;
    feedback.hidden = !text;
    panel.dataset.stale = String(stale);
  }
  function schedule(data) {
    clearBoundary();
    if (!data.next_change_at) return;
    // Use the server's interval, not a possibly incorrect device clock.
    const remaining = Date.parse(data.next_change_at) - Date.parse(data.checked_at) + 250;
    const deadline = performance.now() + Math.max(250, remaining);
    function waitForBoundary() {
      const delay = deadline - performance.now();
      if (delay <= 0) {
        if (document.hidden) retryOnShow = true;
        else refresh();
        return;
      }
      timer = window.setTimeout(waitForBoundary, Math.min(delay, MAX_DELAY));
    }
    waitForBoundary();
  }
  function valid(data) {
    return data && data.schema === 1 && ['open', 'scheduled', 'paused'].includes(data.state)
      && ['title', 'badge', 'schedule', 'message'].every(key => typeof data[key] === 'string')
      && Number.isFinite(Date.parse(data.checked_at))
      && (data.next_change_at === null || Number.isFinite(Date.parse(data.next_change_at)));
  }
  async function refresh(manual = false) {
    if (inFlight) return;
    if (document.hidden) { retryOnShow = true; return; }
    inFlight = true;
    retryOnShow = false;
    clearBoundary();
    refreshButton.disabled = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(panel.dataset.statusUrl, {
        method: 'GET', credentials: 'same-origin', cache: 'no-store',
        headers: { Accept: 'application/json' }, signal: controller.signal,
      });
      // A login redirect must never be inserted into the dashboard.
      if (!response.ok || response.redirected || !response.headers.get('content-type')?.includes('application/json')) throw new Error('unavailable');
      const data = await response.json();
      if (!valid(data)) throw new Error('invalid');
      const changed = panel.dataset.state !== data.state || fields.schedule.textContent !== data.schedule;
      Object.keys(fields).forEach(key => { fields[key].textContent = data[key]; });
      fields.message.hidden = !data.message;
      panel.dataset.state = data.state;
      panel.dataset.checkedAt = data.checked_at;
      panel.dataset.nextChangeAt = data.next_change_at || '';
      report(manual ? 'Statut vérifié à l’instant.' : changed ? 'Le statut des commandes a été actualisé.' : '', false);
      schedule(data);
    } catch (error) {
      // Keep the last known information, clearly marked as unverified.
      report('Dernière information connue : vérification impossible pour le moment. Actualisez le statut ; si votre session a expiré, reconnectez-vous.', true);
    } finally {
      window.clearTimeout(timeout);
      refreshButton.disabled = false;
      inFlight = false;
    }
  }
  refreshButton.hidden = false;
  refreshButton.addEventListener('click', () => refresh(true));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.addEventListener('focus', () => refresh());
  window.addEventListener('pageshow', event => { if (event.persisted || retryOnShow) refresh(); });
  window.addEventListener('online', () => refresh());
  window.addEventListener('pagehide', clearBoundary);
  // Revalidate once on entry, then only on a boundary or a user return.
  refresh();
})();
