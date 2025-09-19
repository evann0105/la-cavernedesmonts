(function(){
  const passToggle = document.querySelector('.lg-pass .toggle');
  const passInput = document.querySelector('.lg-pass input');
  if(passToggle && passInput){
    passToggle.addEventListener('click',()=>{
      const show = passInput.type==='password';
      passInput.type = show ? 'text' : 'password';
      passToggle.textContent = show ? 'Masquer' : 'Afficher';
      passToggle.setAttribute('aria-pressed', show ? 'true' : 'false');
    });
  }
  const form = document.querySelector('.lg-form');
  const submitBtn = document.querySelector('.lg-submit');
  if(form && submitBtn){
    form.addEventListener('submit',()=>{
      submitBtn.classList.add('loading');
      submitBtn.querySelector('.label').textContent='Connexion…';
    });
  }
})();
