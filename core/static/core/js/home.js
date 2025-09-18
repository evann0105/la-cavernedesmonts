(function(){
  // Elevate header on scroll for a subtle depth effect
  const header = document.querySelector('.site-header');
  if(header){
    const onScroll = ()=>{
      if(window.scrollY > 8){ header.style.boxShadow = '0 6px 24px rgba(0,0,0,.06)'; header.style.transition='box-shadow .2s'; }
      else { header.style.boxShadow = 'none'; }
    };
    window.addEventListener('scroll', onScroll, {passive:true});
    onScroll();
  }

  const root = document.querySelector('[data-carousel]');
  if(!root) return;
  const slides = Array.from(root.querySelectorAll('.slide'));
  const nextBtn = root.querySelector('[data-next]');
  const prevBtn = root.querySelector('[data-prev]');
  const dotsWrap = root.querySelector('[data-indicators]');
  let i = slides.findIndex(s => s.classList.contains('active'));
  if(i < 0) i = 0;
  let animating = false;

  // Build indicators
  const dots = slides.map((_, idx) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('aria-label', `Aller à la diapositive ${idx+1}`);
  b.addEventListener('click', () => show(idx, true));
    dotsWrap?.appendChild(b);
    return b;
  });

  function updateDots(){
    dots.forEach((d, idx) => d.setAttribute('aria-current', idx === i ? 'true' : 'false'));
  }

  function show(n, user=false){
    if(animating) return; // prevent spam during transition
    const current = slides[i];
    const nextIndex = (n + slides.length) % slides.length;
    if(nextIndex === i) return;
    const next = slides[nextIndex];
    animating = true;
    current.classList.remove('active');
    next.classList.add('active');
    i = nextIndex;
    updateDots();
    // unlock after CSS transition (~700-800ms)
    window.setTimeout(()=>{ animating = false; }, 800);
    if(user) restart();
  }

  nextBtn?.addEventListener('click', ()=> show(i+1, true));
  prevBtn?.addEventListener('click', ()=> show(i-1, true));

  // Keyboard navigation
  root.addEventListener('keydown', (e)=>{
    if(e.key === 'ArrowRight') { show(i+1, true); }
    if(e.key === 'ArrowLeft') { show(i-1, true); }
  });
  root.tabIndex = 0;

  // Auto-play with pause on hover
  let timer; 
  const start = () => timer = setInterval(()=> show(i+1), 7000);
  const stop = () => { if(timer) clearInterval(timer); };
  const restart = () => { stop(); start(); };
  start();
  root.addEventListener('mouseenter', stop);
  root.addEventListener('mouseleave', start);

  // Basic swipe for touch devices
  let sx = 0; let dx = 0;
  root.addEventListener('touchstart', (e)=>{ sx = e.touches[0].clientX; dx = 0; }, {passive:true});
  root.addEventListener('touchmove', (e)=>{ dx = e.touches[0].clientX - sx; }, {passive:true});
  root.addEventListener('touchend', ()=>{
    if(Math.abs(dx) > 40) { dx < 0 ? show(i+1, true) : show(i-1, true); }
    dx = 0;
  });

  updateDots();
})();

(function(){
  const root = document.querySelector('[data-product-section]');
  if(!root) return;
  const tabs = Array.from(root.querySelectorAll('.pc-tab'));
  const panels = Array.from(root.querySelectorAll('.ps-panel'));
  function showPanel(name){
    panels.forEach(p=> p.hidden = p.getAttribute('data-panel') !== name);
    tabs.forEach(t=> t.setAttribute('aria-selected', String(t.getAttribute('data-tab')===name)));
  }
  tabs.forEach(t=> t.addEventListener('click', ()=> showPanel(t.getAttribute('data-tab'))));
})();
