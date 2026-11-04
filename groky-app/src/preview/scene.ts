// Conceptual 2D preview of a skill: turns its steps into an animation timeline.
//
// Pure TypeScript with no React Native imports; rendered by components/SkillPreview.
//
// This is NOT a simulation. No collisions, no real reachability, no real robot geometry:
// it shows what the skill intends so a person can review it before anything moves.

export type PreviewStep = { tool: string; args: Record<string, unknown> };
export type PreviewSkill = { device: 'arm' | 'dog'; steps: PreviewStep[] };
export type Pt = { x: number; y: number };

// scene size in scene units; renderers scale it to fit
export const W = 400;
export const H = 240;

// ---------- arm scene (side view, y down) ----------

export const ARM = {
  surface: 178, // cube centre when resting on the table or the box floor
  tableY: 190,
  base: { x: 210, y: 190 },
  shoulder: { x: 210, y: 150 },
  l1: 95,
  l2: 85,
  jaw: 22, // wrist to cube centre when gripping
  cube: 24,
  box: { x0: 280, x1: 360, top: 160 },
  hand: { x: 72, y: 70 }, // where a person takes the object at "handover"
  hoverY: 96, // wrist height when hovering over a spot
};

const SPOT: Record<string, Pt> = {
  table: { x: 120, y: ARM.surface },
  box: { x: (ARM.box.x0 + ARM.box.x1) / 2, y: ARM.surface },
};
const HOME: Pt = { x: 210, y: 70 };

export function fk(q1: number, q2: number) {
  const { shoulder: s, l1, l2 } = ARM;
  const elbow = { x: s.x + l1 * Math.cos(q1), y: s.y - l1 * Math.sin(q1) };
  const wrist = { x: elbow.x + l2 * Math.cos(q1 + q2), y: elbow.y - l2 * Math.sin(q1 + q2) };
  return { shoulder: s, elbow, wrist };
}

// two-link IK for the wrist, always elbow-up
function ik(w: Pt) {
  const { shoulder: s, l1, l2 } = ARM;
  const dx = w.x - s.x;
  const dy = s.y - w.y;
  const d = clamp(Math.hypot(dx, dy), Math.abs(l1 - l2) + 1, l1 + l2 - 0.5);
  const a2 = Math.acos(clamp((d * d - l1 * l1 - l2 * l2) / (2 * l1 * l2), -1, 1));
  const beta = Math.atan2(l2 * Math.sin(a2), l1 + l2 * Math.cos(a2));
  const side = dx >= 0 ? 1 : -1;
  return { q1: Math.atan2(dy, dx) + side * beta, q2: -side * a2 };
}

const inBox = (x: number) => x > ARM.box.x0 + 6 && x < ARM.box.x1 - 6;

// ---------- dog scene (top-down) ----------

export const DOG_PX_PER_M = 100;

const POSES: Record<string, { len: number; wid: number; wave: number }> = {
  stand: { len: 1, wid: 1, wave: 0 },
  sit: { len: 0.72, wid: 1, wave: 0 },
  lie: { len: 1.12, wid: 1.25, wave: 0 },
  wave: { len: 0.9, wid: 1, wave: 1 },
};

// ---------- timeline ----------

export type StepInfo = { index: number; label: string; note: string; warn: boolean; start: number; end: number };

export type ArmFrame = {
  device: 'arm';
  shoulder: Pt;
  elbow: Pt;
  wrist: Pt;
  grip: number; // 0 open, 1 closed
  cube: Pt;
  held: boolean;
  scan: number; // 0..1, camera check overlay
};

export type DogFrame = {
  device: 'dog';
  pos: Pt; // scene units
  heading: number; // degrees, counter-clockwise, 0 = facing right
  len: number;
  wid: number;
  wave: number; // paw angle in radians
  pose: string;
  scan: number;
  trail: Pt[];
  scale: number; // dog size multiplier from the zoom-to-fit view
};

export type Frame = (ArmFrame | DogFrame) & { t: number; step: number };

export type Timeline = {
  device: 'arm' | 'dog';
  duration: number;
  steps: StepInfo[];
  grid: { xs: number[]; ys: number[] }; // dog floor grid lines (scene units), empty for the arm
  sample: (t: number) => Frame;
};

type Ease = 'inout' | 'in' | 'linear';
type Key<V> = { t: number; step: number; ease: Ease; v: V };

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const lerp = (a: number, b: number, u: number) => a + (b - a) * u;
const num = (v: unknown, fallback = 0) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
};
const str = (v: unknown) => (v == null ? '' : String(v));

function ease(e: Ease, u: number) {
  if (e === 'linear') return u;
  if (e === 'in') return u * u;
  return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
}

// segment [a, b] containing t, with eased progress
function locate<V>(keys: Key<V>[], t: number): [Key<V>, Key<V>, number] {
  if (t <= keys[0].t) return [keys[0], keys[0], 0];
  for (let i = 1; i < keys.length; i++) {
    if (t <= keys[i].t) {
      const a = keys[i - 1];
      const b = keys[i];
      const span = b.t - a.t;
      return [a, b, span > 0 ? ease(b.ease, (t - a.t) / span) : 1];
    }
  }
  const last = keys[keys.length - 1];
  return [last, last, 1];
}

function label(step: PreviewStep) {
  if (step.tool === 'wait') return `wait ${str(step.args.seconds)}s`;
  const name = step.tool.slice(step.tool.indexOf('_') + 1).replace(/_/g, ' ');
  const vals = Object.values(step.args).filter((v) => v !== '' && v != null);
  return vals.length ? `${name} · ${vals.join(' · ')}` : name;
}

// generic builder shared by both scenes
function builder<V extends object>(initial: V) {
  const keys: Key<V>[] = [{ t: 0, step: -1, ease: 'linear', v: { ...initial } }];
  const st = { t: 0, step: -1, v: { ...initial } };
  const push = (dt: number, patch: Partial<V>, e: Ease = 'inout', fix?: (v: V) => void) => {
    st.v = { ...st.v, ...patch };
    fix?.(st.v);
    st.t += dt;
    keys.push({ t: st.t, step: st.step, ease: e, v: { ...st.v } });
  };
  return { keys, st, push };
}

const LEAD = 0.5;
const TAIL = 0.8;
const MIN_STEP = 0.4;

function runSteps(
  skill: PreviewSkill,
  st: { t: number; step: number },
  hold: (dt: number) => void,
  doStep: (step: PreviewStep) => { note: string; warn?: boolean },
): StepInfo[] {
  const infos: StepInfo[] = [];
  hold(LEAD);
  skill.steps.forEach((step, index) => {
    st.step = index;
    const start = st.t;
    let res: { note: string; warn?: boolean };
    if (step.tool === 'wait') {
      const secs = Math.max(0, num(step.args.seconds));
      hold(Math.min(secs, 2.5));
      res = { note: secs > 2.5 ? `Waits ${secs}s (shortened in the preview)` : `Waits ${secs}s` };
    } else if (!step.tool.startsWith(`${skill.device}_`)) {
      hold(MIN_STEP);
      res = { note: `${step.tool} isn't a ${skill.device} action, so it isn't shown`, warn: true };
    } else {
      res = doStep(step);
    }
    if (st.t - start < MIN_STEP) hold(MIN_STEP - (st.t - start));
    infos.push({ index, label: label(step), note: res.note, warn: !!res.warn, start, end: st.t });
  });
  st.step = skill.steps.length;
  hold(TAIL);
  return infos;
}

// ---------- arm ----------

type ArmV = { q1: number; q2: number; grip: number; cx: number; cy: number; held: number; scan: number };

function buildArm(skill: PreviewSkill): Timeline {
  const { keys, st, push: rawPush } = builder<ArmV>({ ...ik(HOME), grip: 0, ...cubeAt(SPOT.table), held: 0, scan: 0 });
  let taken = false; // handed to the person, can't be picked again

  const tip = (v: ArmV) => {
    const w = fk(v.q1, v.q2).wrist;
    return { x: w.x, y: w.y + ARM.jaw };
  };
  // while held, the cube's free position tracks the gripper so a release starts from the right place
  const follow = (v: ArmV) => {
    if (v.held) Object.assign(v, cubeAt(tip(v)));
  };
  const push = (dt: number, patch: Partial<ArmV>, e: Ease = 'inout') => rawPush(dt, patch, e, follow);
  const hold = (dt: number) => push(dt, {}, 'linear');

  const moveTo = (w: Pt) => {
    const q = ik(w);
    const dq = Math.max(Math.abs(q.q1 - st.v.q1), Math.abs(q.q2 - st.v.q2));
    if (dq > 1e-3) push(clamp(dq / 1.8, 0.35, 2), q);
  };
  const hover = (p: Pt) => ({ x: p.x, y: ARM.hoverY });
  const grasp = (p: Pt) => ({ x: p.x, y: p.y - ARM.jaw });
  const handWrist = grasp(ARM.hand);

  const close = (): { note: string; warn?: boolean } => {
    if (st.v.held) return { note: 'Already holding the cube' };
    const p = tip(st.v);
    const near = !taken && Math.hypot(p.x - st.v.cx, p.y - st.v.cy) < 10;
    push(0.35, { grip: 1, held: near ? 1 : 0 });
    return near ? { note: 'Grabs the cube' } : { note: 'Gripper closes on nothing', warn: true };
  };

  const open = (): { note: string; warn?: boolean } => {
    if (!st.v.held) {
      push(0.35, { grip: 0 });
      return { note: 'Gripper opens' };
    }
    push(0.35, { grip: 0, held: 0 });
    if (Math.hypot(st.v.cx - ARM.hand.x, st.v.cy - ARM.hand.y) < 12) {
      taken = true;
      return { note: 'Lets go: the person takes the cube' };
    }
    const drop = ARM.surface - st.v.cy;
    if (drop > 0.5) push(clamp(Math.sqrt(drop / 900), 0.15, 0.5), { cy: ARM.surface }, 'in');
    return { note: inBox(st.v.cx) ? 'Drops the cube into the box' : 'Drops the cube on the table' };
  };

  const steps = runSteps(skill, st, hold, (step) => {
    const name = step.tool.slice(4);
    const a = step.args;
    switch (name) {
      case 'goto': {
        const place = str(a.place);
        if (place === 'home') moveTo(HOME);
        else if (place === 'handover') moveTo(handWrist);
        else if (SPOT[place]) moveTo(hover(SPOT[place]));
        else return { note: `Unknown place "${place}"`, warn: true };
        return { note: `Moves to ${place}${st.v.held ? ' carrying the cube' : ''}` };
      }
      case 'grip':
        return str(a.state) === 'close' ? close() : open();
      case 'pick_place': {
        const object = str(a.object) || 'object';
        const place = str(a.place);
        if (taken) {
          hold(MIN_STEP);
          return { note: 'The cube was already handed over, nothing to pick', warn: true };
        }
        if (place !== 'handover' && !SPOT[place]) {
          hold(MIN_STEP);
          return { note: `Unknown place "${place}"`, warn: true };
        }
        if (!st.v.held) {
          if (st.v.grip > 0.5) push(0.3, { grip: 0 });
          const c = { x: st.v.cx, y: st.v.cy };
          moveTo(hover(c));
          moveTo(grasp(c));
          close();
          moveTo(hover(c));
        }
        let res: { note: string; warn?: boolean };
        if (place === 'handover') {
          moveTo(handWrist);
          res = open();
        } else {
          const s = SPOT[place];
          moveTo(hover(s));
          moveTo(grasp(s));
          res = open();
          moveTo(hover(s));
        }
        const known = /cube|red|block/i.test(object);
        return known
          ? { note: `Picks the ${object}. ${res.note}` }
          : { note: `Preview only has the red cube; showing it in place of "${object}"`, warn: true };
      }
      case 'check': {
        const expect = str(a.expect).toLowerCase();
        push(0.35, { scan: 1 });
        hold(0.6);
        push(0.35, { scan: 0 });
        const where = taken
          ? 'with the person'
          : st.v.held
            ? 'in the gripper'
            : inBox(st.v.cx)
              ? 'in the box'
              : 'on the table';
        const want = /box/.test(expect)
          ? 'in the box'
          : /table/.test(expect)
            ? 'on the table'
            : /hand|person/.test(expect)
              ? 'with the person'
              : null;
        if (!want) return { note: `Camera check: "${a.expect}"` };
        return want === where
          ? { note: `Camera check should pass: cube is ${where}` }
          : { note: `Camera check would likely fail: cube is ${where}`, warn: true };
      }
      default:
        hold(MIN_STEP);
        return { note: `No animation for ${name}`, warn: true };
    }
  });

  return {
    device: 'arm',
    duration: st.t,
    steps,
    grid: { xs: [], ys: [] },
    sample: (t) => {
      const [a, b, u] = locate(keys, t);
      const q1 = lerp(a.v.q1, b.v.q1, u);
      const q2 = lerp(a.v.q2, b.v.q2, u);
      const pose = fk(q1, q2);
      const held = !!a.v.held;
      const cube = held
        ? { x: pose.wrist.x, y: pose.wrist.y + ARM.jaw }
        : { x: lerp(a.v.cx, b.v.cx, u), y: lerp(a.v.cy, b.v.cy, u) };
      return {
        device: 'arm',
        t,
        step: stepAt(steps, t),
        ...pose,
        grip: lerp(a.v.grip, b.v.grip, u),
        cube,
        held,
        scan: lerp(a.v.scan, b.v.scan, u),
      };
    },
  };
}

function cubeAt(p: Pt) {
  return { cx: p.x, cy: p.y };
}

// ---------- dog ----------

type DogV = { x: number; y: number; h: number; len: number; wid: number; wave: number; scan: number };

function buildDog(skill: PreviewSkill): Timeline {
  const { keys, st, push } = builder<DogV>({ x: 0, y: 0, h: 0, ...POSES.stand, scan: 0 });
  const poseAt: string[] = ['stand'];
  let pose = 'stand';
  const push2 = (dt: number, patch: Partial<DogV>, e: Ease = 'inout') => {
    push(dt, patch, e);
    poseAt.push(pose);
  };
  const hold = (dt: number) => push2(dt, {}, 'linear');

  const steps = runSteps(skill, st, hold, (step) => {
    const name = step.tool.slice(4);
    const a = step.args;
    switch (name) {
      case 'walk': {
        const m = num(a.meters);
        if (pose === 'sit' || pose === 'lie') {
          pose = 'stand';
          push2(0.5, POSES.stand);
        }
        const r = (st.v.h * Math.PI) / 180;
        push2(Math.max(0.5, Math.abs(m) / 0.6), { x: st.v.x + m * Math.cos(r), y: st.v.y + m * Math.sin(r) });
        return { note: m >= 0 ? `Walks ${m} m forward` : `Walks ${-m} m backward` };
      }
      case 'turn': {
        const d = num(a.degrees);
        push2(Math.max(0.4, Math.abs(d) / 120), { h: st.v.h + d });
        return { note: d >= 0 ? `Turns ${d}° left` : `Turns ${-d}° right` };
      }
      case 'pose': {
        const p = str(a.pose);
        if (!POSES[p]) return { note: `Unknown pose "${p}"`, warn: true };
        pose = p;
        push2(0.6, POSES[p]);
        if (p === 'wave') hold(1.2);
        return { note: { stand: 'Stands up', sit: 'Sits down', lie: 'Lies down', wave: 'Waves a paw' }[p] ?? p };
      }
      case 'look':
        push2(0.35, { scan: 1 });
        hold(0.7);
        push2(0.35, { scan: 0 });
        return { note: 'Takes a photo and describes what is in front' };
      default:
        hold(MIN_STEP);
        return { note: `No animation for ${name}`, warn: true };
    }
  });

  // zoom-to-fit view over the whole path (never zooms in past 1:1)
  const xs = keys.map((k) => k.v.x);
  const ys = keys.map((k) => k.v.y);
  const pad = 0.5;
  const minX = Math.min(...xs) - pad;
  const maxX = Math.max(...xs) + pad;
  const minY = Math.min(...ys) - pad;
  const maxY = Math.max(...ys) + pad;
  const scale = Math.min(1, W / ((maxX - minX) * DOG_PX_PER_M), H / ((maxY - minY) * DOG_PX_PER_M));
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const toScene = (x: number, y: number): Pt => ({
    x: W / 2 + (x - cx) * DOG_PX_PER_M * scale,
    y: H / 2 - (y - cy) * DOG_PX_PER_M * scale,
  });

  // grid every 0.5 m
  const grid = { xs: [] as number[], ys: [] as number[] };
  const step = 0.5 * DOG_PX_PER_M * scale;
  const o = toScene(0, 0);
  for (let x = o.x % step; x <= W; x += step) grid.xs.push(x);
  for (let y = o.y % step; y <= H; y += step) grid.ys.push(y);

  return {
    device: 'dog',
    duration: st.t,
    steps,
    grid,
    sample: (t) => {
      const [a, b, u] = locate(keys, t);
      const x = lerp(a.v.x, b.v.x, u);
      const y = lerp(a.v.y, b.v.y, u);
      const trail: Pt[] = [toScene(keys[0].v.x, keys[0].v.y)];
      for (const k of keys) {
        if (k.t >= t) break;
        const p = toScene(k.v.x, k.v.y);
        const last = trail[trail.length - 1];
        if (Math.hypot(p.x - last.x, p.y - last.y) > 0.5) trail.push(p);
      }
      const pos = toScene(x, y);
      trail.push(pos);
      const waveAmt = lerp(a.v.wave, b.v.wave, u);
      return {
        device: 'dog',
        t,
        step: stepAt(steps, t),
        pos,
        heading: lerp(a.v.h, b.v.h, u),
        len: lerp(a.v.len, b.v.len, u),
        wid: lerp(a.v.wid, b.v.wid, u),
        wave: waveAmt * (0.6 + 0.5 * Math.sin(t * 12)),
        pose: poseAt[keys.indexOf(u < 0.5 ? a : b)] ?? 'stand',
        scan: lerp(a.v.scan, b.v.scan, u),
        trail,
        scale,
      };
    },
  };
}

// -1 before the first step, steps.length once finished
function stepAt(steps: StepInfo[], t: number) {
  if (!steps.length || t < steps[0].start) return -1;
  const i = steps.findIndex((s) => t < s.end);
  return i === -1 ? steps.length : i;
}

export function buildTimeline(skill: PreviewSkill): Timeline {
  return skill.device === 'dog' ? buildDog(skill) : buildArm(skill);
}
