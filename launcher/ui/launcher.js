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
/* after saving, forget a typed key in the page: only "a key is saved" and its last 4 characters come back */
const save = async () => {
  const r = await api('/api/config', { config: S.cfg });
  if (r && r.config) S.cfg.ai = r.config.ai;
  return r;
};
const visible = () => QUESTIONS.filter((q) => q.show(S.cfg, S.det));
const invalid = (q) => (q.valid ? q.valid(S.cfg, S.det) : '');
const go = (h) => { if (location.hash === h) render(); else location.hash = h; };

/* ------------------------------------------------------------------ chrome */
const WIZARD = ['welcome', 'q', 'summary', 'install', 'objects', 'teach'];

function sidebar(route) {
  /* the setup wizard is full width with no side menu; Home and Settings keep it */
  const wizard = WIZARD.includes(route.name);
  document.querySelector('.ml-shell').classList.toggle('ml-wizard', wizard);
  if (wizard) { $('side').innerHTML = ''; return; }
  const nav = [['home', 'Home', 'home'], ['settings', 'Settings', 'gear']];
  const items = nav.map(([n, l, ic]) => `<a ${route.name === n || (n === 'settings' && route.name === 'edit') ? 'aria-current="page"' : ''} data-go="#${n}">${svg(ic)}${l}</a>`).join('');
  $('side').innerHTML = `<div class="gk-logo">MOT<b>IQ</b></div>` + items;
}

/* round back / next arrows used on every wizard screen */
const arrow = (dir, act, disabled) => `<button class="ml-arrow${dir === 'next' ? ' ml-arrow--next' : ''}" data-act="${act}"
  aria-label="${dir === 'next' ? 'Next' : 'Back'}" ${disabled ? 'disabled' : ''}>${svg(dir)}</button>`;

const runsOn = () => (S.cfg.mode === 'app' ? 'In-app demo' : S.st?.plan?.real_arm ? 'RoboDK + real arm' : 'RoboDK');

function chip() {
  const robot = robotName(S.cfg, S.det);
  const badge = runsOn();
  const running = S.st?.running;
  const color = running ? 'var(--success)' : 'var(--ink-3)';
  return `<span class="gk-chip"><span class="oc-dot" style="color:${color}"></span>${esc(robot)} <em>${badge}</em></span>`;
}

function topbar(route) {
  $('top').innerHTML = WIZARD.includes(route.name) ? '' : chip();
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
    <h1>Set up MOTIQ</h1>
    <button class="ml-link" data-act="demo">Just try it</button></div>`;
  actions(`<span class="ml-grow"></span>${arrow('next', 'begin')}`);
  progress(null);
}

function choiceRow(o, selected) {
  return `<button class="oc-li ml-choice" role="radio" aria-checked="${selected}" data-pick="${esc(o.value)}" ${o.disabled ? 'disabled' : ''}>
    ${o.icon ? `<span class="oc-li__icon">${svg(o.icon)}</span>` : ''}
    <span class="oc-li__main"><span class="oc-li__t">${esc(o.title)}</span>${o.sub ? `<span class="oc-li__s" style="display:block">${esc(o.sub)}</span>` : ''}</span>
    <span class="ml-check">${svg('check')}</span></button>`;
}

function questionBody(q) {
  const c = S.cfg;
  const v = q.get(c);
  if (q.kind === 'choice' || q.kind === 'select') {
    const opts = q.options(c, S.det);
    const grid = q.kind === 'select' ? ' style="display:grid;grid-template-columns:1fr 1fr;gap:2px"' : '';
    const list = opts.length ? `<div class="oc-list ml-choices" role="radiogroup"${grid}>${opts.map((o) => choiceRow(o, o.value === v)).join('')}</div>`
      : `<p class="ml-faint">${esc(q.empty || 'Nothing found.')}</p>`;
    return list + (q.refresh ? `<button class="ml-link" data-act="rescan">${S.scanning ? 'Looking…' : 'Refresh'}</button>` : '');
  }
  if (q.kind === 'text' || q.kind === 'secret' || q.kind === 'file') {
    const type = q.kind === 'secret' ? 'password' : 'text';
    const extra = q.kind === 'file' ? `<button class="oc-btn oc-btn--secondary" data-act="browse">Browse</button>` : '';
    const later = q.later ? `<button class="ml-link" data-act="later">Skip</button>` : '';
    return `<div class="ml-field">
      <div class="ml-inputrow"><input id="f" class="oc-input" type="${type}" autocomplete="off" spellcheck="false"
        placeholder="${esc(val(q.placeholder, c) || '')}" value="${esc(v || '')}">${extra}</div>
      ${q.kind === 'file' && S.det.ur_jog && !v ? `<button class="ml-link" data-act="usefound">Use ${esc(S.det.ur_jog.split(/[\\/]/).pop())} found next to MOTIQ</button>` : ''}
      </div>${later}`;
  }
  if (q.kind === 'toolsize') {
    const g = c.gripper;
    return `<div class="ml-twocol">
      <div class="ml-field"><label for="mass">Weight</label><div class="ml-unit"><input id="mass" class="oc-input" inputmode="decimal" placeholder="optional" value="${esc(g.mass_kg ?? '')}"><span>kg</span></div></div>
      <div class="ml-field"><label for="tcp">Length</label><div class="ml-unit"><input id="tcp" class="oc-input" inputmode="decimal" placeholder="optional" value="${esc(g.tcp_mm ?? '')}"><span>mm</span></div></div></div>`;
  }
  if (q.kind === 'robodk') {
    const ins = S.det.robodk_install || { state: 'idle' };
    if (S.det.robodk) return `<div class="ml-note ok">${svg('check')}<span>RoboDK is installed.</span></div>`;
    const busy = ins.state === 'downloading' || ins.state === 'installing';
    return `<button class="oc-btn oc-btn--primary oc-btn--lg ml-yes" data-act="robodk" ${busy ? 'disabled' : ''}>${busy ? 'Installing…' : ins.state === 'failed' ? 'Try again' : 'Yes, install it'}</button>
      ${ins.message ? `<p class="ml-faint">${esc(ins.message)}</p>` : ''}`;
  }
  if (q.kind === 'confirm') {
    return `<div class="oc-list">${q.items.map((t, i) => `<div class="ml-toggle-row"><span class="oc-li__icon">${svg('shield')}</span>
      <span class="oc-li__main">${esc(t)}</span><button class="oc-toggle" role="switch" aria-checked="${S.acks[i]}" data-ack="${i}" aria-label="Confirm"></button></div>`).join('')}</div>`;
  }
  if (q.kind === 'range') {
    const pct = ((v - q.min) / (q.max - q.min)) * 100;
    return `<div class="oc-card ml-range"><input id="f" class="oc-range" type="range" min="${q.min}" max="${q.max}" step="${q.step}" value="${v}" style="--v:${pct}%" aria-label="Speed limit"><b id="rv">${v}%</b></div>`;
  }
  return '';
}

function renderQuestion(q, editing) {
  const qs = visible();
  const i = qs.indexOf(q);
  if (!editing) S.reached = Math.max(S.reached, i);
  const err = invalid(q);
  $('screen').innerHTML = `<div class="ml-inner ml-q">
    <h1 class="gk-h1">${esc(q.title)}</h1>
    ${questionBody(q)}</div>`;
  const msg = `<span class="ml-grow ml-err" id="err">${esc(err)}</span>`;
  actions(editing
    ? `<button class="oc-btn oc-btn--ghost" data-act="cancel">Cancel</button>${msg}
       <button class="oc-btn oc-btn--primary" data-act="save" ${err ? 'disabled' : ''}>Save</button>`
    : `${arrow('back', 'back')}${msg}${arrow('next', 'next', err)}`);
  progress(editing ? null : (i + 1) / (qs.length + 1));
  topbar({ name: 'q' });
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
    f.oninput = () => { q.set(c, f.value); f.style.setProperty('--v', `${((f.value - q.min) / (q.max - q.min)) * 100}%`); $('rv').textContent = `${f.value}%`; Anim.bump($('rv')); };
  } else if (f) f.oninput = () => { q.set(c, f.value); recheck(); };
  const num = (el, key) => { if (el) el.oninput = () => { const t = el.value.trim().replace(',', '.'); c.gripper[key] = t === '' ? null : Number(t); recheck(); }; };
  num($('mass'), 'mass_kg');
  num($('tcp'), 'tcp_mm');
}

function renderSummary() {
  const qs = visible();
  S.reached = qs.length;
  const size = (S.cfg.mode !== 'app' ? 'About 3 GB download.' : 'About 500 MB download.')
    + (S.cfg.mode !== 'app' && !S.det.robodk ? ' Needs RoboDK (robodk.com).' : '');
  $('screen').innerHTML = `<div class="ml-inner ml-q">
    <h1 class="gk-h1">Ready</h1>
    <div class="oc-list ml-review">${qs.map((q) => `<button class="oc-li ml-review-row" data-go="#q/${q.id}">
      <span class="oc-li__s">${esc(q.nav)}</span><span class="oc-li__t">${esc(q.summary(S.cfg, S.det))}</span></button>`).join('')}</div>
    <p class="ml-faint">${size}</p></div>`;
  actions(`${arrow('back', 'back')}<span class="ml-grow"></span>
    <button class="oc-btn oc-btn--primary oc-btn--lg" data-act="install">Install</button>`);
  progress(1);
  topbar({ name: 'summary' });
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
    ${job.state === 'failed' ? `<button class="ml-link" data-act="log">${S.showLog ? 'Hide details' : 'Details'}</button>` : ''}
    ${S.showLog && job.state === 'failed' ? `<pre class="ml-log" id="log">${esc(job.lines.join('\n'))}</pre>` : ''}`;
}

function renderInstall() {
  const job = S.st.job;
  if (!job) { go(S.cfg.configured ? '#home' : '#welcome'); return; }
  const title = job.state === 'done' ? 'Ready' : job.state === 'failed' ? 'Setup stopped' : 'Installing';
  $('screen').innerHTML = `<div class="ml-inner ml-install">
    <h1 class="gk-h1">${title}</h1>
    ${installBlock(job)}</div>`;
  const log = $('log');
  if (log) log.scrollTop = log.scrollHeight;
  if (job.state === 'failed') {
    actions(`${arrow('back', 'answers')}<span class="ml-grow"></span>
      <button class="oc-btn oc-btn--primary" data-act="start">Try again</button>`);
  } else if (job.state === 'done') {
    /* the MOTIQ app opens by itself when setup finishes; this reopens it if that window was closed */
    actions(`<button class="oc-btn oc-btn--ghost" data-act="home">Settings</button><span class="ml-grow"></span>
      <button class="oc-btn oc-btn--primary oc-btn--lg" data-act="open">Open MOTIQ</button>`);
  } else {
    actions(`<button class="oc-btn oc-btn--ghost" data-act="stop">Cancel</button><span class="ml-grow"></span>`);
  }
  progress(null);
  topbar({ name: 'install' });
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
  const setup = [kv('Robot', robotName(S.cfg, S.det)), kv('Runs on', runsOn()),
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
  $('screen').innerHTML = `<div class="ml-inner" style="max-width:820px">
    <h1 class="gk-h1">${title}</h1><p class="gk-sub">${esc(robotName(S.cfg, S.det))} · ${esc(runsOn())}</p>
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
  ['Camera', ['camera', 'camera_pick']], ['Voice and AI', ['ai', 'key', 'language']], ['Device', ['client']]];

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
    ${S.cfg.mode !== 'app' ? `<h2 class="gk-overline">Objects</h2><div class="oc-list ml-settings"><div class="oc-li"><div class="oc-li__main">
      <div class="oc-li__t">Your objects</div><div class="oc-li__s">${(S.st.objects?.list || []).length} taught${S.st.objects?.trained_model ? ' · model trained' : ''}</div></div>
      <button class="oc-btn oc-btn--sm oc-btn--secondary" data-go="#objects">Change</button></div></div>` : ''}
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
  else if (r.name === 'objects') renderObjects();
  else if (r.name === 'teach') renderTeach();
  else renderWelcome();
  Anim.after(r, order(r));
}

/* position of a screen in the wizard, so animations know whether you went forward or back */
function order(r) {
  if (r.name === 'welcome') return 0;
  if (r.name === 'q') return 1 + Math.max(0, visible().findIndex((q) => q.id === r.id));
  return { summary: 100, install: 101, objects: 101.5, teach: 101.7, home: 102, settings: 103, edit: 104 }[r.name] || 0;
}

function step(dir) {
  const qs = visible();
  const r = route();
  const i = qs.findIndex((q) => q.id === r.id);
  if (dir < 0) go(i <= 0 ? '#welcome' : `#q/${qs[i - 1].id}`);
  else go(i >= qs.length - 1 ? '#summary' : `#q/${qs[i + 1].id}`);
}

async function act(name, el) {
  if (name.startsWith('obj')) return objectsAct(name, el);
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
  else if (name === 'finish' || name === 'redo') { S.cfg.demo = false; if (S.cfg.mode === 'app' && name === 'finish') S.cfg.mode = 'robodk'; S.reached = 0; go(`#q/${visible()[0].id}`); }
  else if (name === 'log') { S.showLog = !S.showLog; render(); }
  else if (name === 'answers') go('#summary');
  else if (name === 'rescan') { S.scanning = true; render(); await refresh(true); S.scanning = false; render(); }
  else if (name === 'robodk') { await api('/api/robodk', {}); await refresh(); render(); }
  else if (name === 'home') go('#home');
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
  else if (t.dataset.act) act(t.dataset.act, t);
  else if (t.dataset.pick !== undefined) {
    const q = QUESTIONS.find((x) => x.id === route().id);
    q.set(S.cfg, t.dataset.pick, S.det);
    const scroll = $('screen').scrollTop;
    render();
    $('screen').scrollTop = scroll;
  } else if (t.dataset.ack !== undefined) {
    S.acks[+t.dataset.ack] = !S.acks[+t.dataset.ack];
    S.cfg.real_ack = S.acks.every(Boolean);
    t.setAttribute('aria-checked', S.acks[+t.dataset.ack]);
    const b = document.querySelector('[data-act=next],[data-act=save]');
    if (b) b.disabled = !S.cfg.real_ack;
    $('err').textContent = S.cfg.real_ack ? '' : 'Tick all three';
  }
});

document.addEventListener('keydown', (e) => {
  /* Enter or the right arrow key go forward, the left arrow key goes back (not while typing in a field) */
  const typing = e.target.tagName === 'INPUT' && e.target.type !== 'range';
  let sel = null;
  if (e.key === 'Enter' && e.target.tagName !== 'BUTTON') sel = '#actions .ml-arrow--next, [data-act=save]';
  else if (e.key === 'ArrowRight' && !typing && e.target.type !== 'range') sel = '#actions .ml-arrow--next';
  else if (e.key === 'ArrowLeft' && !typing && e.target.type !== 'range') sel = '#actions .ml-arrow:not(.ml-arrow--next)';
  const b = sel && document.querySelector(sel);
  if (b && !b.disabled) { e.preventDefault(); b.click(); }
});

window.addEventListener('hashchange', render);

async function poll() {
  try {
    await refresh();
    const r = route().name;
    if (r === 'install' || r === 'home' || ((r === 'q' || r === 'edit') && route().id === 'robodk')) {
      if (r === 'install' && S.st.job && S.st.job.state === 'done') setTimeout(() => go(needsObjects() ? '#objects' : '#home'), 1200);
      render();
    } else if (r === 'objects' || r === 'teach') objectsTick();
  } catch (e) { /* launcher restarting */ }
  setTimeout(poll, ['install', 'objects', 'teach'].includes(route().name) ? 1000 : 2500);
}

/* after the first install: offer "Teach your objects" once (not for the in-app demo, which has no vision server) */
const needsObjects = () => !S.cfg.objects_done && S.cfg.mode !== 'app';

(async () => {
  await refresh(true);
  if (!location.hash && S.st.job && S.st.job.state === 'running') location.hash = S.cfg.configured ? '#home' : '#install';
  render();
  setTimeout(poll, 1500);
})();
