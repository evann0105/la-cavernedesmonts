const toggle = document.querySelector('.menu-toggle');
const menu = document.getElementById('mobile-nav');
if (toggle && menu) {
  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded', String(open));
    menu.hidden = !open;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !menu.hidden) {
      menu.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      toggle.focus();
    }
  });
}
document.querySelectorAll('[data-gallery]').forEach(link => {
  link.addEventListener('click', event => {
    const main = document.getElementById('main-product-image');
    if (main) { event.preventDefault(); main.src = link.href; }
  });
});

// Progressive enhancement: content remains readable without JS or observation support.
(() => {
  const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  if (!('IntersectionObserver' in window)) return;

  const elements = document.querySelectorAll(
    '.chapter, .section-heading, .family-heading, .moment, .product-card, .family-card, .contact-band h2'
  );
  const seen = new WeakSet();
  let observer;

  function finish(element) {
    element.classList.remove('reveal-enter');
  }

  function start() {
    observer?.disconnect();
    elements.forEach(finish);
    if (preference.matches) return;

    observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        if (seen.has(entry.target) || preference.matches) continue;
        seen.add(entry.target);
        // Keyboard navigation must never animate the focused control out of place.
        if (!entry.target.contains(document.activeElement)) {
          entry.target.classList.add('reveal-enter');
        }
      }
    }, { threshold: 0.08 });

    elements.forEach(element => {
      // Do not animate the initial viewport, anchor destinations or restored scroll position.
      if (element.getBoundingClientRect().top < window.innerHeight) seen.add(element);
      if (!seen.has(element)) observer.observe(element);
    });
  }

  elements.forEach(element => {
    element.addEventListener('animationend', () => finish(element));
    element.addEventListener('focusin', () => finish(element));
  });
  preference.addEventListener('change', start);
  start();
})();
