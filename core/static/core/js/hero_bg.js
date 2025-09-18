(function(){
  const slides = document.querySelectorAll('.hero-bg__slide');
  if(!slides.length) return;
  let idx = 0;
  const interval = 9000;
  function next(){
    const current = slides[idx];
    idx = (idx + 1) % slides.length;
    const upcoming = slides[idx];
    current.classList.remove('is-active');
    upcoming.classList.add('is-active');
  }
  setInterval(next, interval);
})();