(() => {
  'use strict';

  document.querySelectorAll('[data-home-slideshow]').forEach(hero => {
    const slides = Array.from(hero.querySelectorAll('[data-slideshow-slide]'));
    const controls = hero.querySelector('[data-slideshow-controls]');
    const previous = hero.querySelector('[data-slideshow-prev]');
    const next = hero.querySelector('[data-slideshow-next]');
    const playback = hero.querySelector('[data-slideshow-playback]');
    const pages = Array.from(hero.querySelectorAll('[data-slideshow-page]'));
    const counter = hero.querySelector('[data-slideshow-counter]');
    const announcement = hero.querySelector('[data-slideshow-announcement]');
    if (slides.length < 2 || !controls || !previous || !next || !playback || hero.dataset.slideshowReady) return;
    hero.dataset.slideshowReady = 'true';

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const mouseHover = window.matchMedia('(hover: hover) and (pointer: fine)');
    const interval = [5, 6, 8, 10].includes(Number(hero.dataset.interval)) ? Number(hero.dataset.interval) * 1000 : 6000;
    const loaded = new Map();
    const failed = new Set();
    let current = 0;
    let requestId = 0;
    let pending = null;
    let timer = null;
    let hovered = false;
    let focused = hero.contains(document.activeElement);
    const initialBounds = hero.getBoundingClientRect();
    let inView = initialBounds.bottom > 0 && initialBounds.top < window.innerHeight;
    let explicitFocusPlayback = false;
    let explicitHoverPlayback = false;
    let playing = hero.dataset.autoplay === 'true' && !reducedMotion.matches;

    function positionLabel(index) {
      return hero.dataset.positionLabel.replace('{current}', String(index + 1)).replace('{total}', String(slides.length));
    }

    function clearTimer() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    }

    function canPlay() {
      return playing && (!hovered || explicitHoverPlayback) && (!focused || explicitFocusPlayback)
        && !document.hidden && inView && failed.size < slides.length - 1;
    }

    function paintPlayback() {
      playback.setAttribute('aria-pressed', String(playing));
      playback.setAttribute('aria-label', playing ? hero.dataset.pauseLabel : hero.dataset.playLabel);
      const playIcon = playback.querySelector('[data-slideshow-play-icon]');
      const pauseIcon = playback.querySelector('[data-slideshow-pause-icon]');
      if (playIcon) playIcon.hidden = playing;
      if (pauseIcon) pauseIcon.hidden = !playing;
    }

    function adjacentIndex(base, direction) {
      let target = base;
      for (let step = 0; step < slides.length; step += 1) {
        target = (target + direction + slides.length) % slides.length;
        if (!failed.has(target)) return target;
      }
      return current;
    }

    function schedule() {
      clearTimer();
      if (!canPlay() || pending) return;
      timer = setTimeout(() => {
        timer = null;
        requestPhoto(adjacentIndex(current, 1), {automatic: true});
      }, interval);
    }

    function playbackChanged() {
      if (!canPlay() && pending && pending.automatic) {
        requestId += 1;
        pending = null;
      }
      paintPlayback();
      schedule();
    }

    function ensureReady(index) {
      if (loaded.has(index)) return loaded.get(index);
      const image = slides[index].querySelector('img');
      if (!image) return Promise.reject(new Error('Missing photograph'));
      const ready = new Promise((resolve, reject) => {
        function cleanup() {
          image.removeEventListener('load', onLoad);
          image.removeEventListener('error', onError);
        }
        function onLoad() {
          cleanup();
          if (!image.naturalWidth) { reject(new Error('Unavailable photograph')); return; }
          // Decoding finishes before the new photo replaces the visible one.
          const decoding = typeof image.decode === 'function' ? image.decode() : Promise.resolve();
          Promise.resolve(decoding).catch(() => {
            if (!image.naturalWidth) throw new Error('Unavailable photograph');
          }).then(resolve, reject);
        }
        function onError() { cleanup(); reject(new Error('Unavailable photograph')); }
        image.addEventListener('load', onLoad);
        image.addEventListener('error', onError);
        // Extra images are activated only when needed, even with JS enabled.
        // Eager loading here avoids waiting forever on an invisible lazy image.
        image.loading = 'eager';
        slides[index].querySelectorAll('source[data-srcset]').forEach(source => {
          source.srcset = source.dataset.srcset;
          source.removeAttribute('data-srcset');
        });
        if (image.dataset.src) {
          image.src = image.dataset.src;
          image.removeAttribute('data-src');
        }
        if (image.complete) {
          if (image.naturalWidth) onLoad();
          else onError();
        }
      });
      loaded.set(index, ready);
      return ready;
    }

    async function requestPhoto(index, {automatic = false, instant = false} = {}) {
      if (index < 0 || index >= slides.length) return;
      clearTimer();
      if (!automatic) { explicitFocusPlayback = false; explicitHoverPlayback = false; }
      const id = ++requestId;
      pending = {index, automatic};
      if (index === current) { pending = null; schedule(); return; }
      try {
        await ensureReady(index);
        if (id !== requestId) return;
        pending = null;
        if (automatic && !canPlay()) { schedule(); return; }
        // Removing an instant state is committed before a later pointer fade.
        hero.classList.toggle('is-instant', instant);
        void hero.offsetWidth;
        slides.forEach((slide, position) => {
          const active = position === index;
          slide.classList.toggle('is-active', active);
          slide.setAttribute('aria-hidden', String(!active));
        });
        pages.forEach((page, position) => {
          if (position === index) page.setAttribute('aria-current', 'true');
          else page.removeAttribute('aria-current');
        });
        current = index;
        if (counter) counter.textContent = positionLabel(index);
        if (!automatic && announcement) announcement.textContent = positionLabel(index);
      } catch (_) {
        failed.add(index);
        if (id !== requestId) return;
        pending = null;
        if (!automatic && announcement) announcement.textContent = hero.dataset.errorLabel;
      }
      schedule();
    }

    function move(direction, instant) {
      // Repeated manual requests advance the latest intent, not a queued slide.
      const base = pending && !pending.automatic ? pending.index : current;
      requestPhoto(adjacentIndex(base, direction), {instant});
    }

    previous.addEventListener('click', event => move(-1, event.detail === 0));
    next.addEventListener('click', event => move(1, event.detail === 0));
    pages.forEach((page, index) => page.addEventListener('click', event => requestPhoto(index, {instant: event.detail === 0})));
    playback.addEventListener('click', () => {
      playing = !playing;
      // An explicit Play must work while its own button is focused or hovered.
      // Moving to another control restores the usual pause behavior.
      explicitFocusPlayback = playing;
      explicitHoverPlayback = playing;
      playbackChanged();
    });
    controls.addEventListener('keydown', event => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault();
        move(event.key === 'ArrowLeft' ? -1 : 1, true);
      } else if (event.key === 'Home' || event.key === 'End') {
        event.preventDefault();
        requestPhoto(event.key === 'Home' ? 0 : slides.length - 1, {instant: true});
      }
    });
    hero.addEventListener('pointerenter', event => {
      if (mouseHover.matches && event.pointerType === 'mouse') {
        explicitFocusPlayback = false; explicitHoverPlayback = false;
        hovered = true; playbackChanged();
      }
    });
    hero.addEventListener('pointerover', event => {
      if (mouseHover.matches && event.pointerType === 'mouse' && !playback.contains(event.target)) {
        explicitFocusPlayback = false; explicitHoverPlayback = false; playbackChanged();
      }
    });
    hero.addEventListener('pointerleave', () => { hovered = false; explicitHoverPlayback = false; playbackChanged(); });
    hero.addEventListener('focusin', event => {
      focused = true;
      if (!playback.contains(event.target)) { explicitFocusPlayback = false; explicitHoverPlayback = false; }
      playbackChanged();
    });
    hero.addEventListener('focusout', event => {
      focused = hero.contains(event.relatedTarget);
      if (!playback.contains(event.relatedTarget)) { explicitFocusPlayback = false; explicitHoverPlayback = false; }
      playbackChanged();
    });
    document.addEventListener('visibilitychange', playbackChanged);
    const onReducedMotionChange = () => {
      if (reducedMotion.matches) { playing = false; explicitFocusPlayback = false; explicitHoverPlayback = false; }
      playbackChanged();
    };
    if (typeof reducedMotion.addEventListener === 'function') reducedMotion.addEventListener('change', onReducedMotionChange);
    else if (typeof reducedMotion.addListener === 'function') reducedMotion.addListener(onReducedMotionChange);
    if ('IntersectionObserver' in window) {
      const observer = new IntersectionObserver(entries => {
        entries.forEach(entry => { if (entry.target === hero) inView = entry.isIntersecting; });
        playbackChanged();
      }, {threshold: 0});
      observer.observe(hero);
    } else {
      const checkVisibility = () => {
        const bounds = hero.getBoundingClientRect();
        const visible = bounds.bottom > 0 && bounds.top < window.innerHeight;
        if (visible !== inView) { inView = visible; playbackChanged(); }
      };
      window.addEventListener('scroll', checkVisibility, {passive: true});
      window.addEventListener('resize', checkVisibility);
    }

    slides.forEach(slide => { slide.hidden = false; });
    controls.hidden = false;
    const footer = hero.querySelector('[data-slideshow-only-footer]');
    if (footer) footer.hidden = false;
    paintPlayback();
    schedule();
  });
})();
