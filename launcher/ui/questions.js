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

const MODES = {
  app: { title: 'Just the app', sub: 'The app simulates the arm by itself. Nothing else runs on this PC. Quickest way to look around.', icon: 'sim' },
  sim: { title: 'Simulation on this PC', sub: 'Robot controller, arm bridge and vision server run here on a simulated arm. Nothing physical moves.', icon: 'cube' },
  robodk: { title: 'RoboDK digital twin', sub: 'Same as above, but the arm moves in RoboDK so you see the whole cell in 3D.', icon: 'cube' },
  real: { title: 'Real robot arm', sub: 'Small step and joint moves go to your physical UR arm. Pick and place still run in simulation (no gripper driver yet).', icon: 'bolt' },
};
const AIS = {
  xai: { title: 'xAI key', sub: 'Voice, camera questions and video-to-skill. Get one at console.x.ai.', icon: 'key' },
  openrouter: { title: 'OpenRouter key', sub: 'Vision and planning only. Voice needs an xAI key.', icon: 'globe' },
  none: { title: 'No key yet', sub: 'Everything runs offline with canned answers. Voice stays off until you add a key in Settings.', icon: 'none' },
};
const GRIPPERS = {
  rigid: { title: 'Fixed tool', sub: "A rigid part that doesn't open or close, like the 3D-printed tool of the demo cell.", icon: 'fixed' },
  parallel: { title: 'Two-finger gripper', sub: 'Opens and closes (parallel jaws).', icon: 'grip' },
  vacuum: { title: 'Suction cup', sub: 'Vacuum gripper.', icon: 'vac' },
  magnetic: { title: 'Magnet', sub: 'Electromagnet or permanent magnet.', icon: 'mag' },
  other: { title: 'Something else', sub: 'Any other end effector.', icon: 'plus' },
};
const LANGS = [['', 'Detect automatically'], ['en', 'English'], ['it', 'Italiano'], ['es-ES', 'Español (España)'],
  ['es-MX', 'Español (México)'], ['fr', 'Français'], ['de', 'Deutsch'], ['pt-BR', 'Português (Brasil)'],
  ['nl', 'Nederlands'], ['ja', '日本語'], ['zh', '中文']];
const CLIENTS = {
  pc: { title: 'On this computer', sub: 'MOTIQ opens in its own window here.', icon: 'sim' },
  phone: { title: 'On a phone or tablet', sub: 'Scan a QR code with the free Expo Go app. Both devices need the same Wi-Fi.', icon: 'phone' },
  both: { title: 'Both', sub: 'A window here, and a QR code for your phone or tablet.', icon: 'both' },
};

const isApp = (c) => c.mode === 'app';
const webcams = (d) => (d.cameras || []).filter((c) => !c.realsense);
const robotName = (c, d) => ((d.robots || []).find((r) => r.id === c.robot) || {}).name || c.robot;

function cameraOptions(c, d) {
  const rs = (d.cameras || []).find((x) => x.realsense);
  const opts = [
    { value: 'auto', title: 'Pick automatically', sub: 'Uses a RealSense if one is plugged in, otherwise the first webcam.', icon: 'auto' },
    { value: 'realsense', title: 'Intel RealSense (D415 / D435)', sub: rs ? `Found: ${rs.name}` : 'Depth camera, best for measured object positions. Not detected right now.', icon: 'depth', badge: rs ? 'found' : '' },
  ];
  webcams(d).forEach((w, i) => opts.push({ value: `webcam:${i}`, title: w.name, sub: `Webcam ${i + 1}`, icon: 'cam', badge: 'found' }));
  if (!webcams(d).length) opts.push({ value: 'webcam:0', title: 'Webcam', sub: 'Any USB or built-in camera.', icon: 'cam' });
  opts.push({ value: 'none', title: 'No camera', sub: 'A synthetic test scene stands in, so everything still runs.', icon: 'none' });
  return opts;
}
const cameraValue = (c) => (c.camera.type === 'webcam' ? `webcam:${c.camera.device || 0}` : c.camera.type);

const QUESTIONS = [
  {
    id: 'robot', nav: 'Robot arm', title: 'Which robot arm do you have?',
    sub: 'MOTIQ knows these arms. Pick the one you will use, or the one you want to try in simulation.',
    kind: 'choice', show: () => true,
    options: (c, d) => (d.robots || []).map((r) => ({
      value: r.id, title: r.name, icon: 'arm', badge: r.ready ? 'ready' : 'needs setup',
      sub: [r.dof && `${r.dof} axes`, r.nominal_reach_m && `${Math.round(r.nominal_reach_m * 1000)} mm reach`,
        r.nominal_payload_kg && `${r.nominal_payload_kg} kg payload`].filter(Boolean).join(' · '),
    })),
    get: (c) => c.robot, set: (c, v) => { c.robot = v; },
    note: (c) => (c.robot !== 'ur_ur5e' ? { text: 'The UR5e is the arm MOTIQ was built and tested on. Other arms run in simulation; step moves on the real arm are UR only.' } : null),
    foot: 'Another arm? Add it later in the app under Robots, then Arm catalog.',
    summary: (c, d) => robotName(c, d),
  },
  {
    id: 'mode', nav: 'How it runs', title: 'How should MOTIQ run your arm?',
    sub: 'You can switch at any time in Settings.', kind: 'choice', show: () => true,
    options: (c, d) => Object.entries(MODES).map(([v, m]) => ({
      value: v, ...m,
      ...(v === 'robodk' && !d.robodk ? { sub: m.sub + ' RoboDK is not installed on this PC.', badge: 'not found' } : {}),
      ...(v === 'robodk' && d.robodk ? { badge: 'found' } : {}),
      ...(v === 'real' && !String(c.robot).startsWith('ur_') ? { disabled: true, sub: 'Only Universal Robots arms have a real-arm driver right now.' } : {}),
    })),
    get: (c) => c.mode, set: (c, v) => { c.mode = v; },
    valid: (c, d) => (c.mode === 'robodk' && !d.robodk ? 'Install RoboDK from robodk.com first, or pick another option.' : ''),
    summary: (c) => MODES[c.mode].title,
  },
  {
    id: 'robot_ip', nav: 'Robot address', title: "What is the robot's IP address?",
    sub: 'Find it on the teach pendant under Settings, then System, then Network. This PC must be on the same network.',
    kind: 'text', show: (c) => c.mode === 'real', placeholder: '192.168.1.20', field: 'IP address',
    get: (c) => c.robot_ip, set: (c, v) => { c.robot_ip = v.trim(); },
    valid: (c) => (/^\d{1,3}(\.\d{1,3}){3}$/.test(c.robot_ip || '') ? '' : 'Enter an address like 192.168.1.20.'),
    summary: (c) => c.robot_ip || 'not set',
  },
  {
    id: 'ur_jog', nav: 'Arm driver', title: 'Where is the UR driver file?',
    sub: "MOTIQ moves a real UR through a small driver, ur_jog.py, which isn't part of MOTIQ. Point to your copy.",
    kind: 'file', show: (c) => c.mode === 'real', field: 'ur_jog.py',
    get: (c) => c.ur_jog, set: (c, v) => { c.ur_jog = v.trim(); },
    valid: (c) => (/\.py$/i.test(c.ur_jog || '') ? '' : 'Choose the ur_jog.py file.'),
    summary: (c) => (c.ur_jog ? c.ur_jog.split(/[\\/]/).pop() : 'not set'),
  },
  {
    id: 'real_ack', nav: 'Safety check', title: 'Before the real arm moves',
    sub: 'MOTIQ adds software checks, but they do not replace the hardware safety of your cell.', kind: 'confirm',
    show: (c) => c.mode === 'real',
    items: ['The E-stop on the teach pendant is within reach whenever MOTIQ is running.',
      'The UR safety configuration (speed, force, joint and workspace limits) is set for this cell.',
      'Every move is approved first: the arm only moves after you say or tap yes.'],
    get: (c) => c.real_ack, set: (c, v) => { c.real_ack = v; },
    valid: (c) => (c.real_ack ? '' : 'Tick all three to continue.'),
    summary: (c) => (c.real_ack ? 'confirmed' : 'not confirmed'),
  },
  {
    id: 'gripper', nav: 'Tool', title: "What's on the end of the arm?", sub: 'The tool decides how the arm grasps. Grasps are simulated for every tool for now.',
    kind: 'choice', show: (c) => !isApp(c),
    options: () => Object.entries(GRIPPERS).map(([v, g]) => ({ value: v, ...g })),
    get: (c) => c.gripper.kind, set: (c, v) => { c.gripper.kind = v; },
    summary: (c) => GRIPPERS[c.gripper.kind]?.title || c.gripper.kind,
  },
  {
    id: 'tool_size', nav: 'Tool size', title: 'How heavy and how long is the tool?',
    sub: 'Used for the payload check and the tool tip position. Leave a field empty if you do not know; MOTIQ uses safe placeholders.',
    kind: 'toolsize', show: (c) => !isApp(c),
    get: (c) => c.gripper, set: () => {},
    valid: (c) => {
      const m = c.gripper.mass_kg, t = c.gripper.tcp_mm;
      if (m !== null && m !== '' && !(m >= 0 && m <= 50)) return 'Weight must be between 0 and 50 kg.';
      if (t !== null && t !== '' && !(t >= 0 && t <= 1000)) return 'Length must be between 0 and 1000 mm.';
      return '';
    },
    summary: (c) => [c.gripper.mass_kg ? `${c.gripper.mass_kg} kg` : 'weight unknown',
      c.gripper.tcp_mm ? `${c.gripper.tcp_mm} mm` : 'length unknown'].join(' · '),
  },
  {
    id: 'camera', nav: 'Camera', title: 'Which camera should MOTIQ use?',
    sub: 'The vision server finds objects with it and checks the result after every task.', kind: 'choice',
    show: (c) => !isApp(c), options: cameraOptions, get: cameraValue,
    set: (c, v) => {
      if (v.startsWith('webcam:')) { c.camera.type = 'webcam'; c.camera.device = v.split(':')[1]; } else c.camera.type = v;
    },
    summary: (c, d) => {
      const o = cameraOptions(c, d).find((x) => x.value === cameraValue(c));
      return o ? o.title : c.camera.type;
    },
  },
  {
    id: 'camera_mount', nav: 'Camera position', title: 'Where is the camera mounted?', kind: 'choice',
    sub: 'This decides how MOTIQ looks for objects that are out of view.',
    show: (c) => !isApp(c) && c.camera.type !== 'none',
    options: () => [
      { value: 'cell', title: 'Fixed, looking at the table', sub: 'On a stand or the ceiling. It sees the whole table at once.', icon: 'table' },
      { value: 'arm', title: 'On the arm', sub: 'At the wrist (eye-in-hand). The arm moves to look around.', icon: 'wrist' },
    ],
    get: (c) => c.camera.mount, set: (c, v) => { c.camera.mount = v; },
    summary: (c) => (c.camera.mount === 'arm' ? 'On the arm' : 'Fixed, looking at the table'),
  },
  {
    id: 'ai', nav: 'AI key', title: 'Which AI key do you have?',
    sub: 'MOTIQ uses Grok to listen, look and plan. The key stays on this computer.', kind: 'choice', show: () => true,
    options: () => Object.entries(AIS).map(([v, a]) => ({ value: v, ...a })),
    get: (c) => c.ai.provider, set: (c, v) => { c.ai.provider = v; },
    summary: (c) => AIS[c.ai.provider].title + (c.ai.key ? ' · added' : ''),
  },
  {
    id: 'key', nav: 'Paste key', title: 'Paste your key', kind: 'secret',
    sub: 'It is saved in MOTIQ’s settings files on this computer and sent only to the AI provider.',
    show: (c) => c.ai.provider !== 'none',
    field: (c) => (c.ai.provider === 'xai' ? 'xAI API key' : 'OpenRouter API key'),
    placeholder: (c) => (c.ai.provider === 'xai' ? 'xai-…' : 'sk-or-…'),
    get: (c) => c.ai.key, set: (c, v) => { c.ai.key = v.trim(); },
    valid: (c) => (c.ai.key && c.ai.key.length > 10 ? '' : 'Paste the whole key.'),
    later: (c) => { c.ai.provider = 'none'; c.ai.key = ''; },
    summary: (c) => (c.ai.key ? '•••• ' + c.ai.key.slice(-4) : 'not set'),
  },
  {
    id: 'language', nav: 'Language', title: 'Which language will you speak?', kind: 'select',
    sub: 'MOTIQ understands many languages. Choosing one helps when you mix languages or speak with an accent.',
    show: (c) => c.ai.provider === 'xai', options: () => LANGS.map(([v, t]) => ({ value: v, title: t })),
    get: (c) => c.language, set: (c, v) => { c.language = v; },
    summary: (c) => (LANGS.find(([v]) => v === c.language) || ['', c.language])[1],
  },
  {
    id: 'client', nav: 'Device', title: 'Where will you use MOTIQ?', kind: 'choice', show: () => true,
    sub: 'You can talk to it, watch the cameras and approve moves from here.',
    options: () => Object.entries(CLIENTS).map(([v, x]) => ({ value: v, ...x })),
    get: (c) => c.client, set: (c, v) => { c.client = v; },
    note: (c) => (c.client !== 'pc' ? { text: 'MOTIQ will listen on your local network so the phone can reach it. Use it on a network you trust.' } : null),
    summary: (c) => CLIENTS[c.client].title,
  },
  {
    id: 'speed', nav: 'Speed limit', title: 'How fast may the arm move?', kind: 'range', min: 5, max: 50, step: 5,
    sub: 'A share of the arm’s top speed. Start slow; you can raise it later.', show: (c) => !isApp(c),
    get: (c) => c.speed_percent, set: (c, v) => { c.speed_percent = Number(v); },
    summary: (c) => `${c.speed_percent}% of top speed`,
  },
];
