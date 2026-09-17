(function(){
  const passToggle = document.querySelector('.lg-pass .toggle');
  const passInput = document.querySelector('.lg-pass input');
  if(passToggle && passInput){
    const icon = passToggle.querySelector('i');
    passToggle.addEventListener('click',()=>{
      const show = passInput.type==='password';
      passInput.type = show ? 'text' : 'password';
      passToggle.setAttribute('aria-pressed', show ? 'true' : 'false');
      passToggle.setAttribute('aria-label', show ? 'Masquer le mot de passe' : 'Afficher le mot de passe');
      if(icon){
        icon.classList.toggle('bi-eye', !show);
        icon.classList.toggle('bi-eye-slash', show);
      }
    });
  }
  const form = document.querySelector('.lg-form');
  const submitBtn = document.querySelector('.lg-submit');
  if(form && submitBtn){
    form.addEventListener('submit',()=>{
      submitBtn.classList.add('loading');
      const label = submitBtn.querySelector('.label');
      if(label){ label.textContent='Connexion…'; }
    });
  }
})();
