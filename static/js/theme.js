// Global theme JS: light helpers for focus and reduced motion
(function(){
  // Respect reduced motion
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.documentElement.classList.add('reduced-motion');
  }

  // Add a keyboard navigation helper: show focus outlines when using Tab
  let usingKeyboard = false;
  window.addEventListener('keydown', (e)=>{
    if(e.key === 'Tab') { usingKeyboard = true; document.documentElement.classList.add('kbd'); }
  });
  window.addEventListener('mousedown', ()=>{
    if(usingKeyboard){ usingKeyboard = false; document.documentElement.classList.remove('kbd'); }
  });
})();
