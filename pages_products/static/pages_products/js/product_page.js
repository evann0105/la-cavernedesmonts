// Minimal gallery behavior for dedicated page
(function(){
  const main = document.querySelector('.pp__main img');
  const thumbs = document.querySelectorAll('.pp__thumbs img');
  thumbs.forEach(img => {
    img.addEventListener('mouseenter', () => main && (main.src = img.src));
    img.addEventListener('focus', () => main && (main.src = img.src));
    img.addEventListener('click', () => main && (main.src = img.src));
  });
})();

// Quantity stepper and swatches
(function(){
  const input = document.querySelector('.pp__qty-input');
  document.querySelectorAll('.pp__qty-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      if(!input) return;
      const act = btn.dataset.act;
      const current = parseInt(input.value || '1', 10) || 1;
      if(act === '+' ) input.value = current + 1;
      if(act === '-' ) input.value = Math.max(1, current - 1);
    });
  });
  const swatches = document.querySelectorAll('.pp__swatch');
  swatches.forEach(s => s.addEventListener('click', () => {
    swatches.forEach(x => x.classList.remove('is-active'));
    s.classList.add('is-active');
  }));
})();
