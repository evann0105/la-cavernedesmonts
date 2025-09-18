(function(){
  // Mega dropdown via category chips
  const mega = document.getElementById('mega');
  const cats = Array.from(document.querySelectorAll('.fb-cat'));
  if(!cats.length || !mega) return;

  function openPanel(name, trigger){
    mega.hidden = false;
    document.querySelectorAll('.fb-mega__panel').forEach(p=>{
      p.hidden = p.getAttribute('data-panel') !== name;
    });
    cats.forEach(c=>c.setAttribute('aria-expanded', String(c===trigger)));
  }
  function closeMega(){
    if(mega.hidden) return;
    mega.hidden = true;
    cats.forEach(c=>c.setAttribute('aria-expanded','false'));
  }

  cats.forEach(btn=>{
    btn.addEventListener('click',()=>{
      const name = btn.getAttribute('data-panel');
      const expanded = btn.getAttribute('aria-expanded') === 'true';
      if(expanded){
        closeMega();
      } else {
        openPanel(name, btn);
      }
    });
  });

  // Close on outside click
  document.addEventListener('click',e=>{
    if(!mega.hidden && !e.target.closest('#mega') && !e.target.closest('.fb-cats')){
      closeMega();
    }
  });
  // ESC to close
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape') closeMega();
  });
})();