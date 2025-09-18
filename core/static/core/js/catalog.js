// Quick View modal for product cards
(function(){
  const modal = document.querySelector('.qv-modal');
  if(!modal) return;
  const content = modal.querySelector('.qv-content');

  function open(html){
    content.innerHTML = '';
    content.appendChild(html);
    modal.classList.add('open');
  }
  function close(){ modal.classList.remove('open'); content.innerHTML = ''; }
  modal.addEventListener('click', (e)=>{ if(e.target === modal) close(); });
  document.addEventListener('keydown', (e)=>{ if(e.key === 'Escape' && modal.classList.contains('open')) close(); });

  document.querySelectorAll('.product-card .quickview').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      const id = btn.getAttribute('data-pid');
      const tpl = document.querySelector(`#qv-${id}`);
      if(!tpl) return;
      const clone = tpl.content.cloneNode(true);
      // thumbs swap
      const main = clone.querySelector('.qv-main img');
      clone.querySelectorAll('.qv-thumbs img').forEach((t, i)=>{
        if(!main) return;
        if(i===0) t.setAttribute('aria-current','true');
        t.addEventListener('click', ()=>{
          clone.querySelectorAll('.qv-thumbs img').forEach(x=>x.removeAttribute('aria-current'));
          t.setAttribute('aria-current','true');
          main.src = t.src;
        });
      });
      open(clone);
    });
  });
})();
