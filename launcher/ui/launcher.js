/* MOTIQ launcher UI: setup wizard (one question per screen), install progress, home panel and settings. */
const S = { cfg: null, det: {}, st: null, reached: 0, pending: false, acks: [false, false, false], showLog: false };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const val = (f, ...a) => (typeof f === 'function' ? f(...a) : f);

async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  return r.json();
}

async function refresh(full) {
  const st = await api('/api/state' + (full ? '?refresh=1' : ''));
  S.st = st;
  S.det = st.detected || {};
  if (!S.cfg) {
    S.cfg = st.config;
    S.acks = [st.config.real_ack, st.config.real_ack, st.config.real_ack];
  }
  return st;
}
const save = () => api('/api/config', { config: S.cfg });
const visible = () => QUESTIONS.filter((q) => q.show(S.cfg, S.det));
const invalid = (q) => (q.valid ? q.valid(S.cfg, S.det) : '');
const go = (h) => { if (location.hash === h) render(); else location.hash = h; };

/* ------------------------------------------------------------------ chrome */
function sidebar(route) {
  const logo = `<div class="gk-logo">MOT<b>IQ</b></div>`;
  let items = '';
  if (['welcome', 'q', 'summary'].includes(route.name)) {
    const qs = visible();
    const editAll = S.cfg.configured;
    items = qs.map((q, i) => {
      const cur = route.name === 'q' && route.id === q.id;
      const cls = [i < S.reached || editAll ? 'done' : '', i > S.reached && !editAll ? 'locked' : ''].join(' ');
      return `<a class="${cls}" ${cur ? 'aria-current="step"' : ''} data-go="#q/${q.id}"><span class="ml-num">${i + 1}</span>${esc(q.nav)}</a>`;
    }).join('');
    const sumLocked = S.reached < qs.length && !editAll;
    items += `<a class="${sumLocked ? 'locked' : ''}" ${route.name === 'summary' ? 'aria-current="step"' : ''} data-go="#summary"><span class="ml-num">${svg('check').replace('<svg', '<svg style="width:10px;height:10px;stroke:currentColor;fill:none;stroke-width:3"')}</span>Review</a>`;
  } else {
    const nav = [['home', 'Home', 'home'], ['settings', 'Settings', 'gear']];
    items = nav.map(([n, l, ic]) => `<a ${route.name === n || (n === 'settings' && route.name === 'edit') ? 'aria-current="page"' : ''} data-go="#${n}">${svg(ic)}${l}</a>`).join('');
  }
  $('side').innerHTML = logo + items + `<div class="ml-spacer"></div><div class="ml-foot">MOTIQ launcher ${esc(S.st?.version || '')}</div>`;
}

function chip() {
  const robot = robotName(S.cfg, S.det);
  const badge = { app: 'in-app sim', sim: 'sim', robodk: 'RoboDK', real: 'real arm' }[S.cfg.mode];
  const running = S.st?.running;
  const color = running ? 'var(--success)' : 'var(--ink-3)';
  return `<span class="gk-chip"><span class="oc-dot" style="color:${color}"></span>${esc(robot)} <em>${badge}</em></span>`;
}

function topbar(route, count) {
  $('top').innerHTML = (route.name === 'welcome' ? '' : chip()) + (count ? `<span class="oc-faint ml-count" style="font-size:12px">${count}</span>` : '');
}

function progress(frac) {
  const p = $('progress');
  p.hidden = frac === null;
  if (frac !== null) p.firstElementChild.style.width = `${Math.round(frac * 100)}%`;
}

function actions(html) { $('actions').innerHTML = html; }

/* ------------------------------------------------------------------ screens */
function renderWelcome() {
  $('screen').innerHTML = `<div class="ml-inner ml-welcome">
    <div class="oc-orb" aria-hidden="true"></div>
    <h1>hi, let's get your robot ready.</h1>
    <p>A few questions, one at a time. You can go back to any answer, and change all of them later in Settings.</p>
    <button class="oc-btn oc-btn--primary oc-btn--lg" data-act="begin">Start setup ${svg('next')}</button>
    <div class="ml-dunno"><button class="ml-link" data-act="demo">I don't know yet</button>
    <span>Try MOTIQ with a simulated arm. No robot, camera or key needed.</span></div></div>`;
  actions('');
  progress(null);
}

function choiceRow(o, selected) {
  const badge = o.badge ? `<span class="oc-pill ${['ready', 'found'].includes(o.badge) ? 'oc-pill--ok' : 'oc-pill--warn'}"><span class="oc-dot"></span>${esc(o.badge)}</span>` : '';
  return `<button class="oc-li ml-choice" role="radio" aria-checked="${selected}" data-pick="${esc(o.value)}" ${o.disabled ? 'disabled' : ''}>
    ${o.icon ? `<span class="oc-li__icon">${svg(o.icon)}</span>` : ''}
    <span class="oc-li__main"><span class="oc-li__t">${esc(o.title)}</span>${o.sub ? `<span class="oc-li__s" style="display:block">${esc(o.sub)}</span>` : ''}</span>
    ${badge}<span class="ml-check">${svg('check')}</span></button>`;
}

function questionBody(q) {
  const c = S.cfg;
  const v = q.get(c);
  if (q.kind === 'choice' || q.kind === 'select') {
    const opts = q.options(c, S.det);
    const grid = q.kind === 'select' ? ' style="display:grid;grid-template-columns:1fr 1fr;gap:2px"' : '';
    return `<div class="oc-list ml-choices" role="radiogroup"${grid}>${opts.map((o) => choiceRow(o, o.value === v)).join('')}</div>`;
  }
  if (q.kind === 'text' || q.kind === 'secret' || q.kind === 'file') {
    const type = q.kind === 'secret' ? 'password' : 'text';
    const extra = q.kind === 'file' ? `<button class="oc-btn oc-btn--secondary" data-act="browse">${svg('file')}Browse</button>`
      : q.kind === 'secret' ? `<button class="oc-icon-btn" data-act="reveal" aria-label="Show key">${svg('info')}</button>` : '';
    const later = q.later ? `<button class="ml-link" data-act="later">I'll add it later</button>` : '';
    return `<div class="ml-field"><label for="f">${esc(val(q.field, c))}</label>
      <div class="ml-inputrow"><input id="f" class="oc-input" type="${type}" autocomplete="off" spellcheck="false"
        placeholder="${esc(val(q.placeholder, c) || '')}" value="${esc(v || '')}">${extra}</div>
      ${q.kind === 'file' && S.det.ur_jog && !v ? `<span class="ml-hint">Found one next to MOTIQ: ${esc(S.det.ur_jog)}. <button class="ml-link" data-act="usefound">Use it</button></span>` : ''}
      </div>${later}`;
  }
  if (q.kind === 'toolsize') {
    const g = c.gripper;
    return `<div class="ml-twocol">
      <div class="ml-field"><label for="mass">Weight</label><div class="ml-unit"><input id="mass" class="oc-input" inputmode="decimal" placeholder="Don't know" value="${esc(g.mass_kg ?? '')}"><span>kg</span></div>
        <span class="ml-hint">Everything on the flange together.</span></div>
      <div class="ml-field"><label for="tcp">Length</label><div class="ml-unit"><input id="tcp" class="oc-input" inputmode="decimal" placeholder="Don't know" value="${esc(g.tcp_mm ?? '')}"><span>mm</span></div>
        <span class="ml-hint">From the flange to the point that touches things.</span></div></div>`;
  }
  if (q.kind === 'confirm') {
    return `<div class="oc-list">${q.items.map((t, i) => `<div class="ml-toggle-row"><span class="oc-li__icon">${svg('shield')}</span>
      <span class="oc-li__main">${esc(t)}</span><button class="oc-toggle" role="switch" aria-checked="${S.acks[i]}" data-ack="${i}" aria-label="Confirm"></button></div>`).join('')}</div>`;
  }
  if (q.kind === 'range') {
    const pct = ((v - q.min) / (q.max - q.min)) * 100;
    return `<div class="oc-card ml-range"><input id="f" class="oc-range" type="range" min="${q.min}" max="${q.max}" step="${q.step}" value="${v}" style="--v:${pct}%" aria-label="Speed limit"><b id="rv">${v}%</b></div>
      <div class="ml-note ok">${svg('info')}<span>The arm's own safety configuration always wins. This is an extra software limit on top of it.</span></div>`;
  }
  return '';
}

function renderQuestion(q, editing) {
  const qs = visible();
  const i = qs.indexOf(q);
  if (!editing) S.reached = Math.max(S.reached, i);
  const note = q.note ? q.note(S.cfg, S.det) : null;
  const err = invalid(q);
  $('screen').innerHTML = `<div class="ml-inner ml-q">
    <div class="gk-overline">${editing ? 'Settings' : `Question ${i + 1} of ${qs.length}`}</div>
    <h1 class="gk-h1">${esc(q.title)}</h1><p class="gk-sub">${esc(q.sub || '')}</p>
    ${questionBody(q)}
    ${note ? `<div class="ml-note">${svg('warn')}<span>${esc(note.text)}</span></div>` : ''}
    ${q.foot ? `<p class="oc-faint" style="font-size:12px;margin-top:14px">${esc(q.foot)}</p>` : ''}</div>`;
  const nextLabel = editing ? 'Save' : i === qs.length - 1 ? 'Review' : 'Next';
  actions(`<button class="oc-btn oc-btn--ghost" data-act="${editing ? 'cancel' : 'back'}">${svg('back')}${editing ? 'Cancel' : 'Back'}</button>
    <span class="ml-grow"></span><span class="oc-faint" id="err" style="font-size:12px">${esc(err)}</span>
    <button class="oc-btn oc-btn--primary" data-act="${editing ? 'save' : 'next'}" ${err ? 'disabled' : ''}>${nextLabel}${editing ? '' : svg('next')}</button>`);
  progress(editing ? null : (i + 1) / (qs.length + 1));
  topbar({ name: 'q' }, '');
  bindInputs(q);
  const f = document.querySelector('#screen input');
  if (f && f.type !== 'range') f.focus();
}

function bindInputs(q) {
  const c = S.cfg;
  const recheck = () => {
    const e = invalid(q);
    $('err').textContent = e;
    const b = document.querySelector('[data-act=next],[data-act=save]');
    if (b) b.disabled = !!e;
  };
  const f = $('f');
  if (f && q.kind === 'range') {
    f.oninput = () => { q.set(c, f.value); f.style.setProperty('--v', `${((f.value - q.min) / (q.max - q.min)) * 100}%`); $('rv').textContent = `${f.value}%`; };
  } else if (f) f.oninput = () => { q.set(c, f.value); recheck(); };
  const num = (el, key) => { if (el) el.oninput = () => { const t = el.value.trim().replace(',', '.'); c.gripper[key] = t === '' ? null : Number(t); recheck(); }; };
  num($('mass'), 'mass_kg');
  num($('tcp'), 'tcp_mm');
}

function renderSummary() {
  const qs = visible();
  S.reached = qs.length;
  const big = S.cfg.mode !== 'app';
  $('screen').innerHTML = `<div class="ml-inner ml-q"><div class="gk-overline">Review</div>
    <h1 class="gk-h1">Ready to set up MOTIQ</h1>
    <p class="gk-sub">Check your answers. MOTIQ installs what it needs, writes its settings and opens the app.</p>
    <div class="oc-list ml-settings">${qs.map((q) => `<div class="oc-li"><div class="oc-li__main"><div class="oc-li__t">${esc(q.nav)}</div>
      <div class="oc-li__s">${esc(q.summary(S.cfg, S.det))}</div></div><button class="oc-btn oc-btn--sm oc-btn--secondary" data-go="#q/${q.id}">Change</button></div>`).join('')}</div>
    <div class="ml-note ok">${svg('info')}<span>${big
      ? 'The first setup downloads Python 3.12, the vision and controller packages and the app (about 3 GB, 10 to 30 minutes). Later starts take seconds.'
      : 'The first setup downloads the app packages (about 500 MB, a few minutes). Later starts take seconds.'}</span></div></div>`;
  actions(`<button class="oc-btn oc-btn--ghost" data-act="back">${svg('back')}Back</button><span class="ml-grow"></span>
    <button class="oc-btn oc-btn--primary oc-btn--lg" data-act="install">${svg('play')}Install and start</button>`);
  progress(1);
  topbar({ name: 'summary' }, '');
}

const STEP_ICONS = { python: 'cube', controller: 'arm', vision: 'cam', node: 'sim', app: 'phone', configure: 'gear', start: 'play' };

function installBlock(job) {
  const steps = job.steps.map((s) => {
    const cls = { done: 'done', running: 'now', failed: 'failed' }[s.status] || '';
    return `<div class="gk-step ${cls}"><i>${svg(s.status === 'done' ? 'check' : STEP_ICONS[s.id])}</i>${esc(s.label)}</div>`;
  }).join('');
  const cur = job.steps.find((s) => s.status === 'running');
  const done = job.steps.filter((s) => s.status === 'done').length;
  const err = job.state === 'failed' ? `<div class="ml-error"><div class="oc-card__title">Setup stopped</div>
    <div style="font-size:13px;color:var(--ink-2)">${esc(job.error.split('\n')[0])}</div>${job.error.includes('\n') ? `<pre>${esc(job.error)}</pre>` : ''}</div>` : '';
  return `<div class="gk-steps">${steps}</div>
    <div class="oc-progress"><i style="width:${Math.round((done / job.steps.length) * 100)}%"></i></div>
    <p class="oc-muted" style="font-size:13px;margin:14px 0 18px">${esc(cur ? cur.detail || cur.label : job.state === 'done' ? 'Everything is running.' : '')}</p>
    ${err}
    <button class="ml-link" data-act="log">${S.showLog ? 'Hide details' : 'Show details'}</button>
    ${S.showLog ? `<pre class="ml-log" id="log">${esc(job.lines.join('\n'))}</pre>` : ''}`;
}

function renderInstall() {
  const job = S.st.job;
  if (!job) { go(S.cfg.configured ? '#home' : '#welcome'); return; }
  const title = job.state === 'done' ? 'MOTIQ is ready' : job.state === 'failed' ? "Something didn't work" : 'Setting up MOTIQ';
  $('screen').innerHTML = `<div class="ml-inner ml-install"><div class="gk-overline">${S.cfg.demo ? 'Trying MOTIQ' : 'Setup'}</div>
    <h1 class="gk-h1">${title}</h1><p class="gk-sub">${job.state === 'running' ? 'You can leave this window open. MOTIQ opens by itself when it is ready.' : ''}</p>
    ${installBlock(job)}</div>`;
  const log = $('log');
  if (log) log.scrollTop = log.scrollHeight;
  if (job.state === 'failed') {
    actions(`<button class="oc-btn oc-btn--secondary" data-go="#settings">Change answers</button><span class="ml-grow"></span>
      <button class="oc-btn oc-btn--primary" data-act="start">${svg('refresh')}Try again</button>`);
  } else if (job.state === 'done') {
    actions(`<span class="ml-grow"></span><button class="oc-btn oc-btn--primary" data-go="#home">Continue</button>`);
  } else {
    actions(`<button class="oc-btn oc-btn--ghost" data-act="stop">Cancel</button><span class="ml-grow"></span>`);
  }
  progress(null);
  topbar({ name: 'install' }, '');
}

function qrSvg(text) {
  if (typeof qrcode !== 'function') return '';
  const q = qrcode(0, 'M');
  q.addData(text);
  q.make();
  return q.createSvgTag({ cellSize: 4, margin: 0, scalable: true });
}

function renderHome() {
  const st = S.st;
  if (st.job && st.job.state !== 'done' && !(st.job.state === 'failed' && st.running)) { renderInstall(); return; }
  const svcs = st.services || [];
  const up = svcs.length && svcs.every((s) => s.state === 'up');
  const title = st.running ? (up ? 'MOTIQ is running' : 'Starting MOTIQ') : 'MOTIQ is stopped';
  const pill = (s) => ({ up: ['ok', 'running'], starting: ['warn', 'starting'], stopped: ['bad', 'stopped'] }[s.state]);
  const services = svcs.length ? svcs.map((s) => {
    const [k, l] = pill(s);
    return `<div class="oc-li"><span class="oc-li__icon">${svg({ expo: 'phone', vision: 'cam', teach: 'cam', controller: 'arm', bridge: 'bolt', chooser: 'gear' }[s.name])}</span>
      <div class="oc-li__main"><div class="oc-li__t">${esc(s.label)}</div><div class="oc-li__s">port ${s.port}</div></div>
      <span class="oc-pill oc-pill--${k}"><span class="oc-dot"></span>${l}</span></div>`;
  }).join('') : `<div class="oc-li"><div class="oc-li__main oc-faint" style="font-size:12px">Nothing is running. Press Start.</div></div>`;
  const kv = (k, v) => `<div class="gk-kv"><span>${esc(k)}</span><b title="${esc(v)}">${esc(v)}</b></div>`;
  const q = (id) => QUESTIONS.find((x) => x.id === id);
  const setup = [kv('Robot', robotName(S.cfg, S.det)), kv('Runs as', q('mode').summary(S.cfg)),
    ...(S.cfg.mode !== 'app' ? [kv('Camera', q('camera').summary(S.cfg, S.det)), kv('Speed limit', `${S.cfg.speed_percent}%`)] : []),
    kv('AI', q('ai').summary(S.cfg))].join('');
  const phone = st.running && st.phone_url ? `<div class="oc-card"><div class="ml-card-head"><h3 class="oc-card__title">Open on your phone</h3></div>
    <div class="ml-qr">${qrSvg(st.phone_url)}</div>
    <p class="oc-faint" style="font-size:12px;text-align:center;margin:0">Scan with Expo Go, same Wi-Fi.<br><span class="oc-mono">${esc(st.phone_url)}</span></p></div>` : '';
  const demo = S.cfg.demo ? `<div class="ml-banner"><span class="oc-li__icon">${svg('info')}</span><div class="oc-li__main">
    You're trying MOTIQ with the app's simulated arm. Set it up for your own robot when you're ready.</div>
    <button class="oc-btn oc-btn--sm oc-btn--secondary" data-act="finish">Set up my robot</button></div>` : '';
  const err = st.job && st.job.state === 'failed' ? `<div class="ml-error"><div class="oc-card__title">Last start failed</div>
    <div style="font-size:13px;color:var(--ink-2)">${esc(st.job.error.split('\n')[0])}</div></div>` : '';
  $('screen').innerHTML = `<div class="ml-inner" style="max-width:820px"><div class="gk-overline">Home</div>
    <h1 class="gk-h1">${title}</h1><p class="gk-sub">${esc(robotName(S.cfg, S.det))} · ${esc(q('mode').summary(S.cfg))}</p>
    ${demo}${err}
    <div class="ml-home-grid"><div class="oc-card"><div class="ml-card-head"><h3 class="oc-card__title">Services</h3></div>
      <div class="ml-svc">${services}</div></div>
      <div class="oc-stack">${phone}<div class="oc-card"><div class="ml-card-head"><h3 class="oc-card__title">Your setup</h3>
      <button class="oc-btn oc-btn--sm oc-btn--ghost" data-go="#settings">Change</button></div>${setup}</div></div></div></div>`;
  actions(st.running
    ? `<button class="oc-btn oc-btn--danger" data-act="stop">${svg('stop')}Stop</button><span class="ml-grow"></span>
       <button class="oc-btn oc-btn--secondary" data-go="#settings">${svg('gear')}Settings</button>
       <button class="oc-btn oc-btn--primary" data-act="open">${svg('open')}Open MOTIQ</button>`
    : `<button class="oc-btn oc-btn--ghost" data-act="quit">Close</button><span class="ml-grow"></span>
       <button class="oc-btn oc-btn--secondary" data-go="#settings">${svg('gear')}Settings</button>
       <button class="oc-btn oc-btn--primary" data-act="start">${svg('play')}Start</button>`);
  progress(null);
  topbar({ name: 'home' }, '');
}

const GROUPS = [['Robot', ['robot', 'mode', 'robot_ip', 'ur_jog', 'real_ack', 'gripper', 'tool_size', 'speed']],
  ['Camera', ['camera', 'camera_mount']], ['Voice and AI', ['ai', 'key', 'language']], ['Device', ['client']]];

function renderSettings() {
  const vis = visible();
  const missing = vis.filter((q) => invalid(q));
  const groups = GROUPS.map(([title, ids]) => {
    const rows = ids.map((id) => vis.find((q) => q.id === id)).filter(Boolean).map((q) => {
      const bad = invalid(q);
      return `<div class="oc-li"><div class="oc-li__main"><div class="oc-li__t">${esc(q.nav)}</div>
        <div class="oc-li__s">${esc(q.summary(S.cfg, S.det))}</div></div>
        ${bad ? `<span class="oc-pill oc-pill--warn"><span class="oc-dot"></span>needs an answer</span>` : ''}
        <button class="oc-btn oc-btn--sm oc-btn--secondary" data-go="#edit/${q.id}">Change</button></div>`;
    }).join('');
    return rows ? `<h2 class="gk-overline">${title}</h2><div class="oc-list ml-settings">${rows}</div>` : '';
  }).join('');
  const p = S.cfg.ports;
  const banner = S.pending ? `<div class="ml-banner"><span class="oc-li__icon">${svg('refresh')}</span><div class="oc-li__main">
    ${missing.length ? 'Answer the questions marked above, then restart.' : 'Changes saved. Restart MOTIQ to use them.'}</div></div>` : '';
  $('screen').innerHTML = `<div class="ml-inner ml-q"><div class="gk-overline">Settings</div><h1 class="gk-h1">Settings</h1>
    <p class="gk-sub">Change any answer. MOTIQ rewrites its configuration and restarts with it.</p>${banner}${groups}
    <h2 class="gk-overline">Advanced</h2><div class="oc-card">
      <div class="gk-kv"><span>MOTIQ folder</span><b title="${esc(S.st.root)}">${esc(S.st.root)}</b></div>
      <div class="gk-kv"><span>Ports</span><b>app ${p.expo} · bridge ${p.bridge} · vision ${p.vision} · controller ${p.controller}</b></div>
      <div class="gk-kv" style="border-bottom:0"><span>Settings file</span><b>.motiq/config.json</b></div></div>
    <p style="margin-top:16px"><button class="ml-link" data-act="redo">Go through the setup questions again</button></p></div>`;
  actions(`<button class="oc-btn oc-btn--ghost" data-go="#home">${svg('back')}Home</button><span class="ml-grow"></span>
    <button class="oc-btn oc-btn--primary" data-act="restart" ${missing.length ? 'disabled' : ''}>${svg('refresh')}${S.st.running ? 'Save and restart' : 'Save and start'}</button>`);
  progress(null);
  topbar({ name: 'settings' }, '');
}

/* ------------------------------------------------------------------ router */
function route() {
  const [name, id] = location.hash.replace(/^#\/?/, '').split('/');
  return { name: name || (S.cfg.configured ? 'home' : 'welcome'), id };
}

function render() {
  if (!S.cfg) return;
  const r = route();
  sidebar(r);
  if (r.name === 'q' || r.name === 'edit') {
    const q = visible().find((x) => x.id === r.id) || visible()[0];
    renderQuestion(q, r.name === 'edit');
  } else if (r.name === 'summary') renderSummary();
  else if (r.name === 'install') renderInstall();
  else if (r.name === 'settings') renderSettings();
  else if (r.name === 'home') renderHome();
  else renderWelcome();
}

function step(dir) {
  const qs = visible();
  const r = route();
  const i = qs.findIndex((q) => q.id === r.id);
  if (dir < 0) go(i <= 0 ? '#welcome' : `#q/${qs[i - 1].id}`);
  else go(i >= qs.length - 1 ? '#summary' : `#q/${qs[i + 1].id}`);
}

async function act(name) {
  const r = route();
  const q = QUESTIONS.find((x) => x.id === r.id);
  if (name === 'begin') { S.cfg.demo = false; go(`#q/${visible()[0].id}`); }
  else if (name === 'demo') { await api('/api/demo'); S.cfg = null; await refresh(); go('#install'); }
  else if (name === 'next') { await save(); step(1); }
  else if (name === 'back') { if (r.name === 'summary') go(`#q/${visible().slice(-1)[0].id}`); else step(-1); }
  else if (name === 'save') { await save(); S.pending = true; go('#settings'); }
  else if (name === 'cancel') { S.cfg = null; await refresh(); go('#settings'); }
  else if (name === 'install' || name === 'start' || name === 'restart') {
    S.cfg.configured = true;
    await save();
    S.pending = false;
    await api('/api/start');
    await refresh();
    go(name === 'restart' ? '#home' : '#install');
  } else if (name === 'stop') { await api('/api/stop'); await refresh(); render(); }
  else if (name === 'open') api('/api/open');
  else if (name === 'quit') { await api('/api/quit'); window.close(); }
  else if (name === 'finish' || name === 'redo') { S.cfg.demo = false; if (S.cfg.mode === 'app' && name === 'finish') S.cfg.mode = 'sim'; S.reached = 0; go(`#q/${visible()[0].id}`); }
  else if (name === 'log') { S.showLog = !S.showLog; render(); }
  else if (name === 'browse') {
    const res = await api('/api/browse', { title: 'Choose ur_jog.py' });
    if (res.path) { q.set(S.cfg, res.path); render(); }
  } else if (name === 'usefound') { q.set(S.cfg, S.det.ur_jog); render(); }
  else if (name === 'reveal') { const f = $('f'); f.type = f.type === 'password' ? 'text' : 'password'; }
  else if (name === 'later') { q.later(S.cfg); await save(); r.name === 'edit' ? go('#settings') : step(1); }
}

document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-go],[data-act],[data-pick],[data-ack]');
  if (!t || t.disabled) return;
  if (t.dataset.go) go(t.dataset.go);
  else if (t.dataset.act) act(t.dataset.act);
  else if (t.dataset.pick !== undefined) {
    const q = QUESTIONS.find((x) => x.id === route().id);
    q.set(S.cfg, t.dataset.pick);
    const scroll = $('screen').scrollTop;
    render();
    $('screen').scrollTop = scroll;
  } else if (t.dataset.ack !== undefined) {
    S.acks[+t.dataset.ack] = !S.acks[+t.dataset.ack];
    S.cfg.real_ack = S.acks.every(Boolean);
    t.setAttribute('aria-checked', S.acks[+t.dataset.ack]);
    const b = document.querySelector('[data-act=next],[data-act=save]');
    if (b) b.disabled = !S.cfg.real_ack;
    $('err').textContent = S.cfg.real_ack ? '' : 'Tick all three to continue.';
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || e.target.tagName === 'BUTTON') return;
  const b = document.querySelector('[data-act=next],[data-act=save]');
  if (b && !b.disabled) b.click();
});

window.addEventListener('hashchange', render);

async function poll() {
  try {
    await refresh();
    const r = route().name;
    if (r === 'install' || r === 'home') {
      if (r === 'install' && S.st.job && S.st.job.state === 'done') setTimeout(() => go('#home'), 1200);
      render();
    }
  } catch (e) { /* launcher restarting */ }
  setTimeout(poll, route().name === 'install' ? 1000 : 2500);
}

(async () => {
  await refresh(true);
  if (!location.hash && S.st.job && S.st.job.state === 'running') location.hash = S.cfg.configured ? '#home' : '#install';
  render();
  setTimeout(poll, 1500);
})();
