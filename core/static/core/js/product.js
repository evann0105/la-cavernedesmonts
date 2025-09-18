// Product detail interactions: thumbs, lightbox, and quantity stepper
(function(){
  const mainImg = document.querySelector('.product-detail .gallery .main img');
  const thumbs = Array.from(document.querySelectorAll('.product-detail .thumbs img'));
  const lightbox = document.querySelector('.lightbox');
  const lightboxImg = lightbox ? lightbox.querySelector('img') : null;
  let currentIndex = 0;

  // Swap image on thumb click, hover, and keyboard focus
  thumbs.forEach((t, i)=>{
    if(!mainImg) return;
    const activate = ()=> showAt(i);
    t.addEventListener('click', activate);
    t.addEventListener('mouseenter', activate);
    t.addEventListener('focus', activate);
    if(i===0) t.setAttribute('aria-current','true');
  });

  // Lightbox open/close
  const zoomBtn = document.querySelector('.product-detail .gallery .zoom');
  function open(){ if(lightbox){ lightbox.classList.add('open'); } }
  function close(){ if(lightbox){ lightbox.classList.remove('open'); } }
  zoomBtn && zoomBtn.addEventListener('click', open);
  lightbox && lightbox.addEventListener('click', (e)=>{ if(e.target===lightbox) close(); });
  lightbox && lightbox.querySelector('.close')?.addEventListener('click', close);

  function showAt(idx){
    if(!thumbs.length || !mainImg || !lightboxImg) return;
    const safe = ((idx % thumbs.length) + thumbs.length) % thumbs.length;
    currentIndex = safe;
    const src = thumbs[safe].src;
    mainImg.src = src;
    lightboxImg.src = src;
    thumbs.forEach(x=>x.removeAttribute('aria-current'));
    thumbs[safe].setAttribute('aria-current','true');
  }

  lightbox && lightbox.querySelector('.prev')?.addEventListener('click', ()=> showAt(currentIndex-1));
  lightbox && lightbox.querySelector('.next')?.addEventListener('click', ()=> showAt(currentIndex+1));

  // Keyboard navigation
  document.addEventListener('keydown', (e)=>{
    if(!lightbox || !lightbox.classList.contains('open')) return;
    if(e.key === 'ArrowLeft') showAt(currentIndex-1);
    if(e.key === 'ArrowRight') showAt(currentIndex+1);
    if(e.key === 'Escape') close();
  });

  // Quantity stepper
  const qty = document.querySelector('.quantity input');
  document.querySelector('.quantity .minus')?.addEventListener('click', ()=>{
    if(!qty) return; const v = Math.max(1, (parseInt(qty.value||'1',10)-1)); qty.value = String(v);
  });
  document.querySelector('.quantity .plus')?.addEventListener('click', ()=>{
    if(!qty) return; const v = (parseInt(qty.value||'1',10)+1); qty.value = String(v);
  });

  // Back FAB
  document.querySelector('.back-fab')?.addEventListener('click', ()=>{
    if(history.length > 1) history.back();
    else window.location.href = '/';
  });
})();
