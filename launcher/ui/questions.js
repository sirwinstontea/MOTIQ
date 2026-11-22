/* The setup questions, one per screen. Each one reads and writes part of .motiq/config.json. */
const I = {
  arm: '<path d="M4 20h8M8 20v-4l4-6 5 3"/><circle cx="12" cy="10" r="2"/><path d="M17 13l3-3"/>',
  sim: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  cube: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
  bolt: '<path d="M13 3L5 14h6l-1 7 8-11h-6z"/>',
  phone: '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/>',
  both: '<rect x="2" y="5" width="13" height="10" rx="2"/><rect x="16" y="8" width="6" height="11" rx="1.5"/><path d="M6 19h5"/>',
  cam: '<path d="M4 8h3l2-2h6l2 2h3v10H4z"/><circle cx="12" cy="13" r="3"/>',
  depth: '<rect x="3" y="8" width="18" height="8" rx="3"/><circle cx="8" cy="12" r="1.6"/><circle cx="16" cy="12" r="1.6"/>',
  none: '<circle cx="12" cy="12" r="8"/><path d="M6.5 6.5l11 11"/>',
  auto: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><circle cx="12" cy="12" r="4"/>',
  table: '<path d="M3 10h18M6 10v9M18 10v9"/><path d="M9 10V6h6v4"/>',
  wrist: '<path d="M5 19l5-5M10 14l4-8 4 2-3 7z"/><circle cx="15.5" cy="10" r="1"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l8-8M16 7l2 2"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0014 0M12 18v3"/>',
  grip: '<path d="M8 3v7l-3 4v7M16 3v7l3 4v7M8 10h8"/>',
  vac: '<path d="M12 3v8M7 11h10l-2 6H9z"/><path d="M9 21h6"/>',
  mag: '<path d="M6 4v8a6 6 0 0012 0V4h-4v8a2 2 0 01-4 0V4z"/>',
  fixed: '<path d="M12 3v12M8 15h8l-4 6z"/>',
  gauge: '<path d="M4 16a8 8 0 1116 0"/><path d="M12 16l4-5"/>',
  file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
  shield: '<path d="M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6z"/><path d="M9 12l2 2 4-4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  warn: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17v.5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.5"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
  home: '<path d="M4 11l8-7 8 7v9H4z"/><path d="M10 20v-6h4v6"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  next: '<path d="M9 5l7 7-7 7"/>',
  open: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v6H4V6h6"/>',
  refresh: '<path d="M20 11a8 8 0 10-2.3 5.7M20 4v7h-7"/>',
};
const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${I[name] || I.info}</svg>`;

/* every setup runs on RoboDK; 'real' adds the UR arm whenever it is switched on and reachable */
const MODES = {
  robodk: { title: 'No, RoboDK only', icon: 'cube' },
  real: { title: 'Yes, a UR arm', sub: 'Used when it is connected.', icon: 'bolt' },
  app: { title: 'In-app demo', icon: 'sim' },
};
const AIS = {
  xai: { title: 'xAI', sub: 'Voice and vision.', icon: 'key' },
  none: { title: 'No key yet', sub: 'Runs offline.', icon: 'none' },
};
const GRIPPERS = {
  rigid: { title: 'Fixed tool', icon: 'fixed' },
  parallel: { title: 'Two-finger gripper', icon: 'grip' },
  vacuum: { title: 'Suction cup', icon: 'vac' },
  magnetic: { title: 'Magnet', icon: 'mag' },
  other: { title: 'Something else', icon: 'plus' },
};
const LANGS = [['', 'Automatic'], ['en', 'English'], ['it', 'Italiano'], ['es-ES', 'Español (España)'],
  ['es-MX', 'Español (México)'], ['fr', 'Français'], ['de', 'Deutsch'], ['pt-BR', 'Português (Brasil)'],
  ['nl', 'Nederlands'], ['ja', '日本語'], ['zh', '中文']];
const CLIENTS = {
  pc: { title: 'This computer', icon: 'sim' },
  phone: { title: 'Phone or tablet', sub: 'Expo Go, same Wi-Fi.', icon: 'phone' },
  both: { title: 'Both', icon: 'both' },
};

const isApp = (c) => c.mode === 'app';
const webcams = (d) => (d.cameras || []).filter((c) => !c.realsense);
const robotName = (c, d) => ((d.robots || []).find((r) => r.id === c.robot) || {}).name || c.robot;

/* cameras: "this computer's camera" (the built-in one) or "connect a camera" (any other one: USB webcam,
   Intel RealSense, ...). Webcam device numbers are the position among the non-RealSense cameras. */
const BUILTIN = /integrated|built-?in|hd (web)?cam|facetime|truevision|front|laptop|user.?facing/i;
function localCam(d) {
  const w = webcams(d);
  const i = Math.max(0, w.findIndex((x) => BUILTIN.test(x.name)));
  return w.length ? { device: String(i), name: w[i].name } : null;
}
function externalCams(d) {
  const local = localCam(d);
  const out = [];
  (d.cameras || []).filter((x) => x.realsense).forEach((x) => out.push({ value: 'realsense', title: x.name, icon: 'depth' }));
  webcams(d).forEach((w, i) => { if (!local || String(i) !== local.device) out.push({ value: `webcam:${i}`, title: w.name, icon: 'cam' }); });
  return out;
}
const cameraValue = (c) => (c.camera.type === 'webcam' ? `webcam:${c.camera.device || 0}` : c.camera.type);
const setCamera = (c, v) => {
  if (v.startsWith('webcam:')) { c.camera.type = 'webcam'; c.camera.device = v.split(':')[1]; } else c.camera.type = v;
};

const QUESTIONS = [
  {
    id: 'robot', nav: 'Robot arm', title: 'Which robot arm?', kind: 'choice', show: () => true,
    options: (c, d) => (d.robots || []).map((r) => ({
      value: r.id, title: r.name, icon: 'arm',
      sub: [r.dof && `${r.dof} axes`, r.nominal_reach_m && `${Math.round(r.nominal_reach_m * 1000)} mm`,
        r.nominal_payload_kg && `${r.nominal_payload_kg} kg`].filter(Boolean).join(' · '),
    })),
    get: (c) => c.robot,
    set: (c, v) => { c.robot = v; if (c.mode !== 'app' && !v.startsWith('ur_')) c.mode = 'robodk'; },
    summary: (c, d) => robotName(c, d),
  },
  {
    /* MOTIQ runs on RoboDK: offer to install it when it is missing (stays visible while/after installing) */
    id: 'robodk', nav: 'RoboDK', title: 'Install RoboDK?', kind: 'robodk',
    show: (c, d) => !isApp(c) && (!d.robodk || ((d.robodk_install || {}).state || 'idle') !== 'idle'),
    get: () => '', set: () => {},
    summary: (c, d) => (d.robodk ? 'Installed' : 'Not installed'),
  },
  {
    id: 'mode', nav: 'Real arm', title: 'Do you have the real arm?', kind: 'choice',
    show: (c) => String(c.robot).startsWith('ur_'),   // only UR arms have a real-arm driver
    options: () => ['robodk', 'real'].map((v) => ({ value: v, ...MODES[v] })),
    get: (c) => (c.mode === 'real' ? 'real' : 'robodk'), set: (c, v) => { c.mode = v; },
    summary: (c) => (c.mode === 'real' ? 'Yes, used when connected' : 'No, RoboDK only'),
  },
  {
    id: 'robot_ip', nav: 'Robot address', title: "Robot's IP address", kind: 'text',
    show: (c) => c.mode === 'real', placeholder: '192.168.1.20',
    get: (c) => c.robot_ip, set: (c, v) => { c.robot_ip = v.trim(); },
    valid: (c) => (/^\d{1,3}(\.\d{1,3}){3}$/.test(c.robot_ip || '') ? '' : 'Like 192.168.1.20'),
    summary: (c) => c.robot_ip || 'not set',
  },
  {
    id: 'ur_jog', nav: 'Arm driver', title: 'Where is ur_jog.py?', kind: 'file',
    show: (c) => c.mode === 'real', placeholder: 'ur_jog.py',
    get: (c) => c.ur_jog, set: (c, v) => { c.ur_jog = v.trim(); },
    valid: (c) => (/\.py$/i.test(c.ur_jog || '') ? '' : 'Choose ur_jog.py'),
    summary: (c) => (c.ur_jog ? c.ur_jog.split(/[\\/]/).pop() : 'not set'),
  },
  {
    id: 'real_ack', nav: 'Safety check', title: 'Safety check', kind: 'confirm',
    show: (c) => c.mode === 'real',
    items: ['The E-stop is within reach.', 'UR safety limits are set for this cell.', 'I approve every move before it runs.'],
    get: (c) => c.real_ack, set: (c, v) => { c.real_ack = v; },
    valid: (c) => (c.real_ack ? '' : 'Tick all three'),
    summary: (c) => (c.real_ack ? 'confirmed' : 'not confirmed'),
  },
  {
    id: 'gripper', nav: 'Tool', title: "What's on the arm?", kind: 'choice', show: (c) => !isApp(c),
    options: () => Object.entries(GRIPPERS).map(([v, g]) => ({ value: v, ...g })),
    get: (c) => c.gripper.kind, set: (c, v) => { c.gripper.kind = v; },
    summary: (c) => GRIPPERS[c.gripper.kind]?.title || c.gripper.kind,
  },
  {
    id: 'tool_size', nav: 'Tool size', title: 'Tool size', kind: 'toolsize', show: (c) => !isApp(c),
    get: (c) => c.gripper, set: () => {},
    valid: (c) => {
      const m = c.gripper.mass_kg, t = c.gripper.tcp_mm;
      if (m !== null && m !== '' && !(m >= 0 && m <= 50)) return 'Weight: 0 to 50 kg';
      if (t !== null && t !== '' && !(t >= 0 && t <= 1000)) return 'Length: 0 to 1000 mm';
      return '';
    },
    summary: (c) => [c.gripper.mass_kg ? `${c.gripper.mass_kg} kg` : 'weight unknown',
      c.gripper.tcp_mm ? `${c.gripper.tcp_mm} mm` : 'length unknown'].join(' · '),
  },
  {
    id: 'camera', nav: 'Camera', title: 'Which camera?', kind: 'choice', show: (c) => !isApp(c),
    options: (c, d) => {
      const local = localCam(d);
      return [
        { value: 'external', title: 'Connect a camera', sub: 'USB camera, Intel RealSense, …', icon: 'plus' },
        { value: 'local', title: "This computer's camera", sub: local ? local.name : 'None found.', icon: 'cam', disabled: !local },
      ];
    },
    get: (c) => c.camera.source || '',
    set: (c, v, d) => {
      c.camera.source = v;
      if (v === 'local') { const l = localCam(d || {}); if (l) setCamera(c, `webcam:${l.device}`); }
      else if (c.camera.type === 'auto' || c.camera.type === 'none') c.camera.type = '';
    },
    valid: (c) => (c.camera.source ? '' : 'Pick one'),
    summary: (c, d) => (c.camera.source === 'local' ? "This computer's camera"
      : ((externalCams(d).find((x) => x.value === cameraValue(c)) || {}).title || 'Connect a camera')),
  },
  {
    id: 'camera_pick', nav: 'Your camera', title: 'Plug in your camera', kind: 'choice',
    show: (c) => !isApp(c) && c.camera.source === 'external',
    options: (c, d) => externalCams(d),
    empty: 'No camera found yet. Plug it in, then refresh.',
    refresh: true,
    get: cameraValue, set: (c, v) => setCamera(c, v),
    valid: (c, d) => (externalCams(d).some((x) => x.value === cameraValue(c)) ? '' : 'Pick your camera'),
    summary: (c, d) => (externalCams(d).find((x) => x.value === cameraValue(c)) || {}).title || 'not connected',
  },
  {
    id: 'camera_mount', nav: 'Camera position', title: 'Where is the camera?', kind: 'choice',
    show: () => false,   // not asked: MOTIQ uses the default (fixed camera); still in config.json if ever needed
    options: () => [
      { value: 'cell', title: 'Above the table', icon: 'table' },
      { value: 'arm', title: 'On the arm', icon: 'wrist' },
    ],
    get: (c) => c.camera.mount, set: (c, v) => { c.camera.mount = v; },
    summary: (c) => (c.camera.mount === 'arm' ? 'On the arm' : 'Above the table'),
  },
  {
    id: 'ai', nav: 'AI key', title: 'Which AI key?', kind: 'choice', show: () => true,
    options: () => Object.entries(AIS).map(([v, a]) => ({ value: v, ...a })),
    get: (c) => c.ai.provider, set: (c, v) => { c.ai.provider = v; },
    summary: (c) => (AIS[c.ai.provider] || AIS.xai).title + (c.ai.key ? ' · added' : ''),
  },
  {
    id: 'key', nav: 'Paste key', title: 'Paste your key', kind: 'secret',
    show: (c) => c.ai.provider !== 'none',
    placeholder: () => 'xai-…',
    get: (c) => c.ai.key, set: (c, v) => { c.ai.key = v.trim(); },
    valid: (c) => (c.ai.key && c.ai.key.length > 10 ? '' : 'Paste the whole key'),
    later: (c) => { c.ai.provider = 'none'; c.ai.key = ''; },
    summary: (c) => (c.ai.key ? '•••• ' + c.ai.key.slice(-4) : 'not set'),
  },
  {
    id: 'language', nav: 'Language', title: 'Which language?', kind: 'select',
    show: (c) => c.ai.provider === 'xai', options: () => LANGS.map(([v, t]) => ({ value: v, title: t })),
    get: (c) => c.language, set: (c, v) => { c.language = v; },
    summary: (c) => (LANGS.find(([v]) => v === c.language) || ['', c.language])[1],
  },
  {
    id: 'client', nav: 'Device', title: 'Where will you use it?', kind: 'choice', show: () => true,
    options: () => Object.entries(CLIENTS).map(([v, x]) => ({ value: v, ...x })),
    get: (c) => c.client, set: (c, v) => { c.client = v; },
    summary: (c) => CLIENTS[c.client].title,
  },
  {
    id: 'speed', nav: 'Speed limit', title: 'Max speed', kind: 'range', min: 5, max: 50, step: 5,
    show: (c) => !isApp(c),
    get: (c) => c.speed_percent, set: (c, v) => { c.speed_percent = Number(v); },
    summary: (c) => `${c.speed_percent}% of top speed`,
  },
];
