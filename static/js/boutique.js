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
