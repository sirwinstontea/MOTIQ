/* Wizard animations in the style of Animate UI (animate-ui.com), which is React-only, so this uses the same engine
   (Motion, vendor/motion.min.js) directly: springs and short ease-out tweens, exits quicker than entrances,
   transforms and opacity only, a check that draws like a pen and a selection that glides between options.
   Everything is skipped when the system asks for reduced motion or Motion failed to load. */
const Anim = (() => {
  const M = window.Motion;
  const off = !M || matchMedia('(prefers-reduced-motion: reduce)').matches;
  const OUT = [0.22, 1, 0.36, 1];                       // strong ease-out
  const SPRING = { type: 'spring', stiffness: 380, damping: 32 };
  const SOFT = { type: 'spring', stiffness: 260, damping: 26 };
  const a = (el, kf, opt) => (off || !el ? null : M.animate(el, kf, opt));
  let last = { key: '', idx: 0 };
  let ind = { key: '', box: null };
  let steps = {};

  /* the check mark draws like a pen stroke */
  function drawCheck(row) {
    const path = row && row.querySelector('.ml-check path');
    if (!path || off) return;
    const len = path.getTotalLength ? path.getTotalLength() : 24;
    path.style.strokeDasharray = len;
    a(path, { strokeDashoffset: [len, 0] }, { duration: 0.32, ease: OUT, delay: 0.06 });
    a(row.querySelector('.ml-check'), { scale: [0.6, 1] }, SPRING);
  }

  /* one highlight behind the selected option, which glides to the newly picked one */
  function indicator(key, entering) {
    const list = document.querySelector('.ml-choices');
    if (!list) { ind = { key: '', box: null }; return; }
    const row = list.querySelector('.ml-choice[aria-checked=true]');
    let el = list.querySelector('.ml-ind');
    if (!el) { el = document.createElement('span'); el.className = 'ml-ind'; list.prepend(el); }
    list.classList.add('has-ind');
    if (!row) { el.style.opacity = 0; ind = { key, box: null }; return; }
    const box = { x: row.offsetLeft, y: row.offsetTop, w: row.offsetWidth, h: row.offsetHeight };
    Object.assign(el.style, { left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px`, opacity: 1 });
    const moved = ind.key === key && ind.box && (ind.box.x !== box.x || ind.box.y !== box.y);
    if (moved) {
      a(el, { x: [ind.box.x - box.x, 0], y: [ind.box.y - box.y, 0] }, SPRING);
      drawCheck(row);
    } else if (!entering && ind.key === key && !ind.box) drawCheck(row);
    ind = { key, box };
  }

  /* a new screen slides in from the side you are going towards; its parts follow one after another */
  function enter(dir) {
    const inner = document.querySelector('#screen .ml-inner');
    if (!inner) return;
    const x = dir === 0 ? 0 : dir > 0 ? 28 : -28;
    a(inner, { opacity: [0, 1], x: [x, 0] }, { duration: 0.38, ease: OUT });
    const parts = inner.querySelectorAll('.gk-h1, .ml-choice, .ml-review-row, .ml-field, .ml-toggle-row, .ml-range, .gk-step, .ml-yes, .ml-twocol');
    if (parts.length) a(parts, { opacity: [0, 1], y: [10, 0] }, { delay: M.stagger(0.035, { startDelay: 0.05 }), duration: 0.34, ease: OUT });
    a(document.querySelectorAll('#actions > *'), { opacity: [0, 1], scale: [0.9, 1] }, { delay: M ? M.stagger(0.04, { startDelay: 0.12 }) : 0, ...SOFT });
  }

  /* the welcome title appears word by word out of a light blur ("pop") */
  function welcome() {
    const h = document.querySelector('.ml-welcome h1');
    if (!h || off) return;
    h.innerHTML = h.textContent.split(' ').map((w) => `<span class="ml-word">${w}</span>`).join(' ');
    a(h.querySelectorAll('.ml-word'), { opacity: [0, 1], y: [14, 0], filter: ['blur(6px)', 'blur(0px)'] },
      { delay: M.stagger(0.08, { startDelay: 0.1 }), duration: 0.5, ease: OUT });
    a(document.querySelector('.ml-welcome .oc-orb'), { opacity: [0, 1], scale: [0.5, 1] }, SOFT);
    a(document.querySelector('.ml-welcome .ml-link'), { opacity: [0, 1] }, { delay: 0.45, duration: 0.3 });
  }

  /* install steps: a finished step's icon pops, the running one is already haloed by CSS */
  function install() {
    document.querySelectorAll('.gk-step').forEach((el, i) => {
      const st = el.className;
      if (steps[i] && steps[i] !== st && /\bdone\b/.test(st)) {
        a(el.querySelector('i'), { scale: [0.5, 1], filter: ['blur(4px)', 'blur(0px)'] }, SPRING);
      }
      steps[i] = st;
    });
  }

  function after(route, idx) {
    const key = `${route.name}/${route.id || ''}`;
    const entering = key !== last.key;
    if (entering) {
      enter(route.name === 'welcome' ? 0 : Math.sign(idx - last.idx) || 1);
      if (route.name === 'welcome') welcome();
      if (route.name === 'install') steps = {};
    }
    indicator(key, entering);
    if (route.name === 'install') install();
    last = { key, idx };
  }

  /* the speed number nudges when it changes */
  const bump = (el) => a(el, { y: [-6, 0], opacity: [0.5, 1] }, { duration: 0.2, ease: OUT });

  /* buttons give a little when pressed and spring back */
  if (!off) {
    document.addEventListener('pointerdown', (e) => {
      const b = e.target.closest('.ml-arrow, .oc-btn, .ml-choice');
      if (b && !b.disabled) a(b, { scale: 0.95 }, { duration: 0.1 });
    });
    const release = () => document.querySelectorAll('.ml-arrow, .oc-btn, .ml-choice').forEach((b) => {
      if (b.style.transform && b.style.transform !== 'none') a(b, { scale: 1 }, SPRING);
    });
    document.addEventListener('pointerup', release);
    document.addEventListener('pointercancel', release);
  }

  return { after, bump };
})();
