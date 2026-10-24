(() => {
  const banner = document.querySelector('[data-site-announcement]');
  if (!banner) return;
  const end = Date.parse(banner.dataset.endsAt);
  if (!Number.isFinite(end)) return;
  let timer;
  function expire() {
    clearTimeout(timer);
    const remaining = end - Date.now();
    if (remaining <= 0) {
      banner.remove();
      document.body.classList.remove('has-site-announcement');
    } else timer = setTimeout(expire, Math.min(remaining, 60000));
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) expire(); });
  expire();
})();
