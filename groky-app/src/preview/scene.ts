// Conceptual 2D preview of a skill: turns its steps into an animation timeline.
//
// Pure TypeScript with no React Native imports; rendered by components/SkillPreview.
//
// Arm: a side view (x forward, z up) of the dry run in src/skills/dryrun.ts, so it shows exactly what the UR5e
// model predicts, including where a step would be refused. The 2-link drawing is a sketch of the real 6 joints.
// Dog: top-down floor sketch. Neither is a physics simulation.
import { deviceOf, isLookStep } from '../skills';
import { dryRun } from '../skills/dryrun';
import type { SkillDoc } from '../skills/schema';
import type { Pose } from '../robot/kinematics';
import type { World } from '../robot/arm';

export type PreviewStep = { tool: string; args: Record<string, unknown> };
type DogSkill = { device: 'dog'; steps: PreviewStep[] };
export type Pt = { x: number; y: number };

// scene size in scene units; renderers scale it to fit
export const W = 400;
export const H = 240;

// ---------- arm scene (side view, y down) ----------

const PX = 220; // scene units per metre
export const ARM = {
  tableY: 190,
  base: { x: 150, y: 190 },
  shoulder: { x: 150, y: 190 - 0.1625 * PX },
  l1: 0.425 * PX,
  l2: 0.3922 * PX,
  jaw: 22, // wrist dot to fingertips (the TCP)
};

const toScene = (x: number, z: number): Pt => ({ x: ARM.base.x + x * PX, y: ARM.tableY - z * PX });

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

export type SceneObject = {
  id: string;
  container: boolean;
  x: number; // centre
  y: number; // centre for objects, floor for containers
  w: number;
  h: number;
  color: string;
  label: string;
};

export type ArmFrame = {
  device: 'arm';
  shoulder: Pt;
  elbow: Pt;
  wrist: Pt;
  grip: number; // 0 open, 1 closed
  objects: SceneObject[];
  scan: number; // 0..1, camera overlay
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
  skill: DogSkill,
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

const COLORS: Record<string, string> = {
  red: '#e5484d',
  blue: '#3e63dd',
  green: '#30a46c',
  yellow: '#f5c400',
  orange: '#f76b15',
  purple: '#8e4ec6',
  black: '#2b2b2b',
  white: '#e8e8e8',
};

type ArmV = { wx: number; wy: number; grip: number; scan: number; objs: Record<string, Pt> };

function buildArm(doc: SkillDoc): Timeline {
  const run = dryRun(doc);
  const env = doc.environment?.objects ?? [];
  const wristOf = (p: Pose): Pt => {
    const t = toScene(p.x, p.z);
    return { x: t.x, y: t.y - ARM.jaw };
  };
  const objsOf = (w: World) =>
    Object.fromEntries(Object.values(w).map((o) => [o.id, toScene(o.location.x, o.location.z)]));
  const w0 = wristOf(run.start.tcp);
  const { keys, st, push } = builder<ArmV>({ wx: w0.x, wy: w0.y, grip: 0, scan: 0, objs: objsOf(run.world) });
  const hold = (dt: number) => push(dt, {}, 'linear');

  const infos: StepInfo[] = [];
  hold(LEAD);
  run.beats.forEach((beat, index) => {
    st.step = index;
    const start = st.t;
    if (beat.scan) {
      push(0.3, { scan: 1 });
      hold(0.6);
      push(0.3, { scan: 0 });
    } else {
      const total = clamp(beat.seconds * 0.45, MIN_STEP, 2.5);
      const sum = beat.frames.reduce((n, f) => n + f.dt, 0) || 1;
      beat.frames.forEach((f) => {
        const w = wristOf(f.tcp);
        push(
          Math.max(0.05, (total * f.dt) / sum),
          { wx: w.x, wy: w.y, grip: f.grip, objs: objsOf(f.world) },
          beat.frames.length > 1 ? 'linear' : 'inout',
        );
      });
      if (beat.error) hold(0.8);
    }
    if (st.t - start < MIN_STEP) hold(MIN_STEP - (st.t - start));
    infos.push({
      index,
      label: beat.label,
      note: beat.error ? `Refused: ${beat.error}` : beat.note,
      warn: beat.warn,
      start,
      end: st.t,
    });
  });
  st.step = run.beats.length;
  hold(TAIL);

  const statics = Object.values(run.world).map((o) => {
    const e = env.find((x) => x.id === o.id);
    const color = COLORS[String(e?.attributes?.color ?? '').toLowerCase()];
    const container = o.category === 'container';
    return {
      id: o.id,
      container,
      w: Math.max(container ? 24 : 10, o.size * PX),
      h: container ? 0.1 * PX : Math.max(10, o.size * PX),
      color: color ?? (container ? '#b98a5a' : '#e5484d'),
      label: o.name,
    };
  });

  return {
    device: 'arm',
    duration: st.t,
    steps: infos,
    grid: { xs: [], ys: [] },
    sample: (t) => {
      const [a, b, u] = locate(keys, t);
      const wrist = { x: lerp(a.v.wx, b.v.wx, u), y: lerp(a.v.wy, b.v.wy, u) };
      const q = ik(wrist);
      const pose = fk(q.q1, q.q2);
      return {
        device: 'arm',
        t,
        step: stepAt(infos, t),
        ...pose,
        grip: lerp(a.v.grip, b.v.grip, u),
        scan: lerp(a.v.scan, b.v.scan, u),
        objects: statics.map((o) => {
          const pa = a.v.objs[o.id];
          const pb = b.v.objs[o.id] ?? pa;
          return { ...o, x: lerp(pa.x, pb.x, u), y: lerp(pa.y, pb.y, u) };
        }),
      };
    },
  };
}

// ---------- dog ----------

type DogV = { x: number; y: number; h: number; len: number; wid: number; wave: number; scan: number };

function buildDog(skill: DogSkill): Timeline {
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

// dog skills flatten to their primitive calls, in the names the dog sketch already understands
function dogSteps(doc: SkillDoc): PreviewStep[] {
  return doc.steps.flatMap((s) =>
    isLookStep(s)
      ? []
      : (s.primitive_sequence ?? []).map((c) =>
          c.primitive === 'WAIT'
            ? { tool: 'wait', args: c.parameters ?? {} }
            : { tool: `dog_${c.primitive.toLowerCase()}`, args: c.parameters ?? {} },
        ),
  );
}

export function buildTimeline(doc: SkillDoc): Timeline {
  return deviceOf(doc) === 'dog' ? buildDog({ device: 'dog', steps: dogSteps(doc) }) : buildArm(doc);
}
