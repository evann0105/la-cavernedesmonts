(function(){
  const mega = document.getElementById('mega');
  if(!mega) return;

  const menuBtn = document.querySelector('.fb-menu');
  const tabs = Array.from(document.querySelectorAll('.fb-mega-tab'));

  function showPanel(name){
    document.querySelectorAll('.fb-mega__panel').forEach(p=>{
      p.hidden = p.getAttribute('data-panel') !== name;
    });
    tabs.forEach(t=>t.setAttribute('aria-selected', String(t.getAttribute('data-panel')===name)));
  }
  function openMega(){
    mega.hidden = false;
    // Default to first selected tab or 'femmes'
    const selected = tabs.find(t=>t.getAttribute('aria-selected')==='true');
    showPanel(selected ? selected.getAttribute('data-panel') : 'femmes');
    if(menuBtn) menuBtn.setAttribute('aria-expanded','true');
  }
  function closeMega(){
    if(mega.hidden) return;
    mega.hidden = true;
    if(menuBtn) menuBtn.setAttribute('aria-expanded','false');
  }

  if(menuBtn){
    menuBtn.addEventListener('click',()=>{
      if(mega.hidden){ openMega(); } else { closeMega(); }
    });
  }

  tabs.forEach(tab=>{
    tab.addEventListener('click',()=>{
      tabs.forEach(t=>t.setAttribute('aria-selected','false'));
      tab.setAttribute('aria-selected','true');
      showPanel(tab.getAttribute('data-panel'));
    });
  });

  // Close on outside click
  document.addEventListener('click',e=>{
    if(!mega.hidden && !e.target.closest('#mega') && !e.target.closest('.fb-menu')){
      closeMega();
    }
  });
  // ESC to close
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape') closeMega();
  });
})();