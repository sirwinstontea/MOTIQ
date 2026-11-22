/* Setup step after installing: teach MOTIQ the user's own objects.
   #objects  list of taught objects, Train button, done arrow
   #teach    name it, film it (live camera), then see whether it needs training and drop badly boxed pictures
   The work is done by computer-vision's `python -m vision.teach_objects` through the launcher's /api/objects/*. */
const OBJ_STATUS = {
  ready: 'Found without training', needs_training: 'Needs training', trained: 'Learned',
  filming: 'Not finished', filmed: 'Not finished', failed: 'Filming failed',
};
const objState = () => S.st.objects || { list: [], job: { stage: 'idle' }, trained_model: false };
const jobBusy = (j) => ['starting', 'filming', 'labeling', 'training', 'exporting'].includes(j.stage);
const liveUrl = () => `http://127.0.0.1:${S.cfg.ports.vision}/frame/webcam.jpg?raw=true`;
let liveTimer = null;

function startLive() {
  clearInterval(liveTimer);
  liveTimer = setInterval(() => {
    const img = document.getElementById('live');
    if (!img) { clearInterval(liveTimer); return; }
    const next = new Image();
    next.onload = () => { img.src = next.src; };
    next.src = `${liveUrl()}&t=${Date.now()}`;
  }, 180);
}

function renderObjects() {
  const o = objState();
  const j = o.job;
  const training = j.cmd === 'train' && jobBusy(j);
  const need = o.list.some((x) => x.status === 'needs_training');
  const rows = o.list.map((x) => `<div class="oc-li ml-obj"><div class="oc-li__main"><div class="oc-li__t">${esc(x.name || x.slug)}</div>
      <div class="oc-li__s">${esc(OBJ_STATUS[x.status] || x.status || '')}</div></div>
      <button class="ml-link" data-act="objremove" data-slug="${esc(x.slug)}" ${training ? 'disabled' : ''}>Remove</button></div>`).join('');
  const trainBlock = training
    ? `<div class="ml-train"><p class="ml-faint" id="ttext">${j.stage === 'exporting' ? 'Saving the model…' : `Training on ${j.device === 'gpu' ? 'your graphics card' : 'this PC'}… ${Math.round((j.progress || 0) * 100)}%`}</p>
       <div class="oc-progress"><i id="tbar" style="width:${Math.round((j.progress || 0) * 100)}%"></i></div></div>`
    : need ? `<button class="oc-btn oc-btn--primary ml-yes" data-act="objtrain">Train</button>
       <p class="ml-faint">Can take 20 to 60 minutes without a graphics card.</p>` : '';
  const err = j.cmd === 'train' && j.stage === 'error' ? `<p class="ml-faint">Training stopped: ${esc(j.error)}</p>` : '';
  $('screen').innerHTML = `<div class="ml-inner ml-q">
    <h1 class="gk-h1">Teach your objects</h1>
    ${rows ? `<div class="oc-list ml-review">${rows}</div>` : ''}
    <button class="oc-btn oc-btn--secondary ml-add" data-go="#teach" ${training ? 'disabled' : ''}>${svg('plus')}Add an object</button>
    ${trainBlock}${err}</div>`;
  actions(`<span class="ml-grow"></span>${arrow('next', 'objdone', training)}`);
  progress(null);
  topbar({ name: 'objects' });
}

function renderTeach() {
  const o = objState();
  const j = o.job;
  const running = S.st.running;
  let body;
  if (j.cmd === 'capture' && jobBusy(j)) {
    const filming = j.stage === 'filming' || j.stage === 'starting';
    body = `<h1 class="gk-h1">${filming ? 'Turn it slowly' : 'Checking…'}</h1>
      ${filming ? `<img id="live" class="ml-live" alt="Camera" src="${liveUrl()}">` : ''}
      <div class="oc-progress ml-tprog"><i id="tbar" style="width:${Math.round((j.progress || 0) * 100)}%"></i></div>
      <p class="ml-faint" id="ttext">${filming ? 'Keep it in view. Show every side.' : 'Looking for it in every picture.'}</p>`;
    actions(`<button class="oc-btn oc-btn--ghost" data-act="objstop">Cancel</button><span class="ml-grow"></span>`);
  } else if (j.cmd === 'capture' && (j.stage === 'done' || j.stage === 'labeled') && (S.teachSlug || j.slug)) {
    const x = o.list.find((y) => y.slug === (S.teachSlug || j.slug)) || {};
    const ok = x.status === 'ready';
    const dropped = new Set(x.dropped || []);
    const grid = !ok ? `<div class="ml-grid">${(x.previews || []).filter((_, i) => i % 2 === 0).map((f) => `<button class="ml-thumb${dropped.has(f) ? ' off' : ''}"
        data-act="objdrop" data-slug="${esc(x.slug)}" data-frame="${esc(f)}"><img src="/objects/${esc(x.slug)}/${esc(f)}" alt=""></button>`).join('')}</div>
        <p class="ml-faint">Tap a picture where the box is wrong to leave it out.</p>` : '';
    body = `<h1 class="gk-h1">${esc(x.name || '')}</h1>
      <div class="ml-note ${ok ? 'ok' : ''}">${svg(ok ? 'check' : 'info')}<span>${ok
        ? 'MOTIQ already recognises it. No training needed.'
        : `Found in ${Math.round((x.found_ratio || 0) * 100)}% of the pictures. MOTIQ will learn it when you train.`}</span></div>${grid}`;
    actions(`<button class="oc-btn oc-btn--ghost" data-act="objagain">Film again</button><span class="ml-grow"></span>${arrow('next', 'objlist')}`);
  } else {
    const err = j.cmd === 'capture' && j.stage === 'error' ? `<p class="ml-faint">${esc(j.error)}</p>` : '';
    body = `<h1 class="gk-h1">What is it called?</h1>
      <div class="ml-field"><input id="f" class="oc-input" placeholder="e.g. red cap" autocomplete="off" value="${esc(S.teachName || '')}"></div>
      ${running ? `<img id="live" class="ml-live" alt="Camera" src="${liveUrl()}">` : '<p class="ml-faint">Start MOTIQ first, so the camera is on.</p>'}
      ${err}`;
    actions(`${arrow('back', 'objlist')}<span class="ml-grow"></span>
      <button class="oc-btn oc-btn--primary ml-yes" data-act="objcapture" ${running ? '' : 'disabled'}>Start filming</button>`);
  }
  $('screen').innerHTML = `<div class="ml-inner ml-q ml-teach">${body}</div>`;
  const f = $('f');
  if (f) { f.oninput = () => { S.teachName = f.value; }; f.focus(); }
  if (document.getElementById('live')) startLive();
  progress(null);
  topbar({ name: 'teach' });
}

/* while filming or training, move the bar in place instead of re-rendering (keeps the live picture smooth) */
function objectsTick() {
  const j = objState().job;
  const key = `${j.cmd}/${j.stage}`;
  if (key !== S.objKey) { S.objKey = key; render(); return; }
  const bar = document.getElementById('tbar');
  if (bar) bar.style.width = `${Math.round((j.progress || 0) * 100)}%`;
  const t = document.getElementById('ttext');
  if (t && j.cmd === 'train' && j.stage === 'training') t.textContent = `Training on ${j.device === 'gpu' ? 'your graphics card' : 'this PC'}… ${Math.round((j.progress || 0) * 100)}%`;
}

async function objectsAct(name, el) {
  if (name === 'objcapture') {
    const n = (S.teachName || '').trim();
    if (!n) { $('f').focus(); return; }
    const r = await api('/api/objects/capture', { name: n });
    if (r.ok === false) return;
    S.teachSlug = n.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'object';
    await refresh(); render();
  } else if (name === 'objstop') { await api('/api/objects/stop', {}); await refresh(); go('#objects'); }
  else if (name === 'objagain') { S.teachName = (objState().list.find((y) => y.slug === S.teachSlug) || {}).name || ''; S.teachSlug = ''; render(); }
  else if (name === 'objlist') { S.teachSlug = ''; S.teachName = ''; go('#objects'); }
  else if (name === 'objdrop') {
    const off = !el.classList.contains('off');
    el.classList.toggle('off', off);
    await api('/api/objects/drop', { slug: el.dataset.slug, frame: el.dataset.frame, dropped: off });
  } else if (name === 'objremove') { await api('/api/objects/remove', { slug: el.dataset.slug }); setTimeout(async () => { await refresh(); render(); }, 600); }
  else if (name === 'objtrain') { await api('/api/objects/train', {}); await refresh(); render(); }
  else if (name === 'objdone') {
    /* the vision server reads the taught objects / trained model when it starts: restart it with them */
    S.cfg.objects_done = true;
    await save();
    if (objState().list.length) { await api('/api/start'); await refresh(); }
    go('#home');
  }
}
