// Conceptual 2D preview of a skill: turns its steps into an animation timeline.
//
// Pure TypeScript with no React Native imports; rendered by components/SkillPreview.
//
// Arm: a side view (x forward, z up) of the dry run in src/skills/dryrun.ts, so it shows exactly what the UR5e
// model predicts, including where a step would be refused. The 2-link drawing is a sketch of the real 6 joints.
// It is not a physics simulation.
import { dryRun } from '../skills/dryrun';
import type { SkillDoc } from '../skills/schema';
import type { Pose } from '../robot/kinematics';
import type { World } from '../robot/arm';

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

export type Frame = ArmFrame & { t: number; step: number };

export type Timeline = {
  device: 'arm';
  duration: number;
  steps: StepInfo[];
  sample: (t: number) => Frame;
};

type Ease = 'inout' | 'in' | 'linear';
type Key<V> = { t: number; step: number; ease: Ease; v: V };

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const lerp = (a: number, b: number, u: number) => a + (b - a) * u;

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

// keyframe builder
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

// -1 before the first step, steps.length once finished
function stepAt(steps: StepInfo[], t: number) {
  if (!steps.length || t < steps[0].start) return -1;
  const i = steps.findIndex((s) => t < s.end);
  return i === -1 ? steps.length : i;
}

export function buildTimeline(doc: SkillDoc): Timeline {
  return buildArm(doc);
}
