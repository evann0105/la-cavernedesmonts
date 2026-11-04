(() => {
  'use strict';
  const root = document.querySelector('[data-cancellation-live]');
  if (!root) return;
  const message = root.querySelector('[data-live-status]');
  const refresh = root.querySelector('[data-live-refresh]');
  let endpoint;
  try {
    endpoint = new URL(root.dataset.statusUrl, window.location.href);
    if (endpoint.origin !== window.location.origin) return;
  } catch (_) { return; }
  const INTERVAL = 5000;
  const MAX_DURATION = 15 * 60 * 1000;
  const REQUEST_TIMEOUT = 10000;
  const pending = new Map();
  let version = root.dataset.liveVersion || '';
  let serverActive = root.dataset.liveActive === 'true';
  let stopped = serverActive ? '' : 'complete';
  let deadline = Date.now() + MAX_DURATION;
  let timer = null;
  let controller = null;
  let failures = 0;
  let submitting = false;

  const say = (text) => { if (message && message.textContent !== text) message.textContent = text; };
  const buttonLabel = () => {
    if (!refresh) return;
    refresh.hidden = false;
    refresh.textContent = stopped === 'limit' ? 'Reprendre le suivi' : 'Actualiser le suivi';
  };
  const cancelTimer = () => { window.clearTimeout(timer); timer = null; };
  const isPaused = () => document.hidden || navigator.onLine === false;

  function changedInputs(region) {
    return Array.from(region.querySelectorAll('input:not([type="hidden"]), textarea, select, [contenteditable="true"]')).some((field) => {
      if (field.matches('[contenteditable="true"]')) return field.dataset.liveEdited === 'true';
      if (field.tagName === 'SELECT') return Array.from(field.options).some((option) => option.selected !== option.defaultSelected);
      if (field.type === 'checkbox' || field.type === 'radio') return field.checked !== field.defaultChecked;
      return field.value !== field.defaultValue;
    });
  }

  function flushRegions() {
    // Each region is independent. A focused action button or a changed field
    // postpones only its own replacement; private notes live outside regions.
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    let replaced = false;
    for (const [name, fragment] of pending) {
      const region = root.querySelector(`[data-live-region="${name}"]`);
      if (!region) { pending.delete(name); continue; }
      if (region.contains(document.activeElement) || changedInputs(region)) continue;
      region.innerHTML = fragment.html;
      region.dataset.liveVersion = fragment.version;
      pending.delete(name);
      replaced = true;
    }
    if (replaced && (window.scrollX !== scrollX || window.scrollY !== scrollY)) {
      window.scrollTo({left: scrollX, top: scrollY, behavior: 'instant'});
    }
    if (pending.size) say('Une mise à jour est prête. Elle s’affichera lorsque vous aurez terminé votre action.');
    else if (stopped === 'complete') say('Remboursement, stock et messages traités. Le suivi est à jour.');
    else if (replaced) say('Le suivi vient d’être actualisé.');
  }

  function schedule(delay = INTERVAL) {
    cancelTimer();
    buttonLabel();
    if (submitting || stopped || !serverActive || isPaused() || controller) return;
    if (Date.now() >= deadline) {
      stopped = 'limit';
      say('Le suivi automatique est en pause après 15 minutes. Reprenez-le pour continuer.');
      buttonLabel();
      return;
    }
    timer = window.setTimeout(() => update(false), delay);
  }

  async function update(manual) {
    if (submitting || controller) return;
    cancelTimer();
    if (manual) {
      stopped = '';
      deadline = Date.now() + MAX_DURATION;
      failures = 0;
    }
    if (isPaused()) {
      say(navigator.onLine === false ? 'Connexion interrompue. Le suivi reprendra au retour du réseau.' : 'Le suivi reprendra lorsque cette page sera visible.');
      return;
    }
    if (!manual && (stopped || !serverActive)) return;
    if (!manual && Date.now() >= deadline) { schedule(); return; }
    const requestController = new AbortController();
    controller = requestController;
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; requestController.abort(); }, REQUEST_TIMEOUT);
    const url = new URL(endpoint);
    url.searchParams.set('version', version);
    if (manual) say('Actualisation du suivi…');
    try {
      const response = await fetch(url, {
        method: 'GET', credentials: 'same-origin', cache: 'no-store',
        headers: {'Accept': 'application/json'}, signal: requestController.signal,
      });
      if (requestController.signal.aborted) return;
      if (response.redirected || [401, 403].includes(response.status)) {
        stopped = 'auth';
        say('Votre accès doit être renouvelé. Reconnectez-vous dans un autre onglet, puis actualisez le suivi.');
        return;
      }
      if (response.status === 404) {
        stopped = 'missing';
        say('Ce dossier n’est plus accessible. Le suivi automatique est arrêté.');
        return;
      }
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Unavailable follow-up');
      const data = await response.json();
      if (requestController.signal.aborted || submitting) return;
      if (typeof data.version !== 'string' || typeof data.active !== 'boolean' || typeof data.changed !== 'boolean') throw new Error('Invalid follow-up');
      if (data.changed) {
        for (const name of ['progress', 'stock', 'notifications']) {
          const fragment = data.fragments?.[name];
          if (!fragment || typeof fragment.html !== 'string' || typeof fragment.version !== 'string') throw new Error('Invalid region');
        }
        for (const name of ['progress', 'stock', 'notifications']) {
          const fragment = data.fragments[name];
          const region = root.querySelector(`[data-live-region="${name}"]`);
          if (region && region.dataset.liveVersion !== fragment.version) pending.set(name, fragment);
          else pending.delete(name);
        }
      }
      version = data.version;
      serverActive = data.active;
      root.dataset.liveVersion = version;
      root.dataset.liveActive = String(serverActive);
      const total = root.querySelector('[data-live-total-label]');
      if (total && typeof data.total_label === 'string') total.textContent = data.total_label;
      stopped = serverActive ? '' : 'complete';
      failures = 0;
      flushRegions();
      if (serverActive && !pending.size) say(data.changed ? 'Le suivi vient d’être actualisé.' : 'Suivi automatique actif. Aucun changement pour le moment.');
    } catch (error) {
      if (requestController.signal.aborted && !timedOut) return;
      failures += 1;
      say('Le suivi est momentanément indisponible. Une nouvelle vérification sera faite automatiquement.');
    } finally {
      window.clearTimeout(timeout);
      if (controller === requestController) controller = null;
      schedule(failures ? Math.min(60000, INTERVAL * (2 ** Math.min(failures, 4))) : INTERVAL);
    }
  }

  function pause() {
    cancelTimer();
    if (controller) controller.abort();
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) pause();
    else schedule();
  });
  window.addEventListener('offline', () => {
    pause();
    say('Connexion interrompue. Le suivi reprendra au retour du réseau.');
  });
  window.addEventListener('online', () => { if (!stopped) say('Connexion rétablie. Le suivi va reprendre.'); schedule(); });
  window.addEventListener('pagehide', () => { submitting = true; pause(); });
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) { submitting = false; schedule(); }
  });
  // Navigation may be cancelled by the unsaved-note guard. Only pagehide
  // freezes the page; an intercepted submit must not disable later updates.
  root.addEventListener('submit', () => { pause(); schedule(); });
  root.addEventListener('input', (event) => {
    if (event.target.matches('[contenteditable="true"]')) event.target.dataset.liveEdited = 'true';
  });
  root.addEventListener('focusout', () => window.setTimeout(flushRegions, 0));
  refresh?.addEventListener('click', () => update(true));
  buttonLabel();
  say(serverActive ? 'Suivi automatique actif. Cette page se met à jour sans interrompre votre travail.' : 'Remboursement, stock et messages traités. Le suivi est à jour.');
  schedule();
})();
