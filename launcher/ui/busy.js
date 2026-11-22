/* Full-screen "installing" overlay, so a long first install never looks frozen.
   It is created once and then only its text and bar are updated on every poll, so the animation keeps running
   smoothly. Shows: what is happening now, step n of N, elapsed time, and the latest log line as a live heartbeat. */
const Busy = (() => {
  let el = null;
  let started = 0;          // local clock synced to the server's elapsed time, so a reload doesn't reset it

  const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

  function create() {
    el = document.createElement('div');
    el.className = 'ml-busy';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.innerHTML = `
      <div class="ml-busy__art" aria-hidden="true">
        <span class="ml-busy__ring r1"></span><span class="ml-busy__ring r2"></span><span class="ml-busy__ring r3"></span>
        <span class="ml-busy__arc"></span><span class="ml-busy__core"></span>
      </div>
      <h1 class="ml-busy__title">Installing MOTIQ</h1>
      <p class="ml-busy__step" data-b="step"></p>
      <div class="ml-busy__bar"><i data-b="bar"></i></div>
      <p class="ml-busy__meta"><span data-b="count"></span> · <span data-b="time"></span></p>
      <p class="ml-busy__log" data-b="log"></p>
      <p class="ml-busy__hint" data-b="hint"></p>
      <button class="ml-link ml-busy__cancel" data-act="stop">Cancel</button>`;
    document.body.appendChild(el);
    requestAnimationFrame(() => el.classList.add('on'));
  }

  function show(job, cfg) {
    if (!el) create();
    started = Date.now() - (job.elapsed || 0) * 1000;
    const steps = job.steps || [];
    const done = steps.filter((s) => s.status === 'done').length;
    const cur = steps.find((s) => s.status === 'running');
    const q = (k) => el.querySelector(`[data-b="${k}"]`);
    q('step').textContent = cur ? (cur.detail || cur.label) : 'Getting ready…';
    q('bar').style.width = `${Math.max(4, Math.round(((done + (cur ? 0.5 : 0)) / Math.max(1, steps.length)) * 100))}%`;
    q('count').textContent = `Step ${Math.min(done + 1, steps.length)} of ${steps.length}`;
    q('time').textContent = `${fmt((Date.now() - started) / 1000)} elapsed`;
    const last = (job.lines || []).filter((l) => l.trim()).slice(-1)[0] || '';
    q('log').textContent = last.length > 110 ? `${last.slice(0, 110)}…` : last;
    q('hint').textContent = cfg && cfg.mode === 'app'
      ? 'The first start downloads about 500 MB. Keep this window open.'
      : 'The first install downloads about 3 GB and usually takes 10 to 30 minutes. Keep this window open; MOTIQ opens by itself when it is ready.';
  }

  function hide() {
    if (!el) return;
    const old = el;
    el = null;
    started = 0;
    old.classList.remove('on');
    setTimeout(() => old.remove(), 350);
  }

  /* called after every render: overlay while a setup/start job is running */
  function sync(route) {
    const job = S.st && S.st.job;
    if (job && job.state === 'running' && ['install', 'home'].includes(route.name)) show(job, S.cfg);
    else hide();
  }

  /* keep the elapsed clock ticking between polls */
  setInterval(() => {
    if (el && started) el.querySelector('[data-b="time"]').textContent = `${fmt((Date.now() - started) / 1000)} elapsed`;
  }, 1000);

  return { sync };
})();
