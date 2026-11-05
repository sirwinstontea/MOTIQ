// What a UR5e primitive will do, before it is sent anywhere.
//
// applyArm() checks one primitive against the operational limits, joint ranges, reach and the base singularity,
// and predicts the arm and object state afterwards. The same function drives the simulator, the safety check in
// front of the real bridge, and the skill preview, so all three agree.
//
// Not modelled: collisions with anything but the table plane, gripper fingers, payload, real controller timing.
// The UR safety configuration stays the hard limit on the real cell.
import type { EnvObject, OperationalLimits, Primitive, Xyz } from '../skills/schema';
import {
  deg,
  dist3,
  fk,
  ik,
  lerpPose,
  matToRotvec,
  makePose,
  Pose,
  poseR,
  rad,
  rotateInTool,
  SHOULDER_Z,
  toolTiltDeg,
} from './kinematics';
import { ARM_HOME, ARM_LIMITS, ARM_PRIMITIVES, isRequired, limitFor, UR5E } from './profiles';

export type WorldObject = {
  id: string;
  name: string;
  category: string;
  size: number; // edge length (m); for a container, its inner footprint
  location: Xyz; // centre for objects, floor for containers
  upright: boolean;
  inside: string | null; // container id
  measured: boolean; // true = position came from a calibrated detection, not the skill file
  labels?: string[]; // vision model classes for it (e.g. "vite_arancione")
  grip?: number; // width the fingers close on (m), e.g. a bucket's wall; defaults to size
};
export type World = Record<string, WorldObject>;

export type ArmState = {
  joints: number[]; // degrees
  tcp: Pose;
  gripper: { width: number; closed: boolean; holding: string | null; holdDz: number };
};

export type ArmOpts = {
  limits: OperationalLimits;
  liftFirst: boolean; // lift_before_horizontal_travel
  openBeforeDescend: boolean; // open_gripper_before_descending
  enforceLimits: boolean; // respect_operational_limits / reject_moves_outside_operational_limits
};

export const defaultOpts: ArmOpts = {
  limits: ARM_LIMITS,
  liftFirst: true,
  openBeforeDescend: true,
  enforceLimits: true,
};

export type Keyframe = { tcp: Pose; grip: number; world: World; dt: number };

export type Outcome = {
  ok: boolean;
  error?: string;
  note: string;
  state: ArmState;
  world: World;
  urscript: string[];
  result: Record<string, unknown>;
  frames: Keyframe[];
  seconds: number;
};

const SINGULARITY_R = 0.12; // keep the TCP out of the cylinder around the base axis
const MAX_JOINT_JUMP = 25; // deg between 2 cm path samples = wrist flip / singularity
const UPRIGHT_TILT = 15; // deg of tool tilt that still releases an object upright
const STROKE = UR5E.gripper?.stroke_m ?? 0.085;
const REACH = UR5E.reach_m ?? 0.85;
const JOINT_ACC = 1.0; // rad/s², as in the template
const TRAVEL_DPS = 30;

export const initialArm = (): ArmState => ({
  joints: [...ARM_HOME],
  tcp: fk(ARM_HOME),
  gripper: { width: STROKE, closed: false, holding: null, holdDz: 0 },
});

export function worldFromEnv(objects: EnvObject[]): World {
  const w: World = {};
  for (const o of objects) {
    const container = o.category === 'container';
    w[o.id] = {
      id: o.id,
      name: o.name,
      category: o.category,
      size: Number(o.attributes?.size_m) || (container ? 0.16 : 0.04),
      location: { ...(o.initial_state?.location ?? { x: 0.4, y: 0, z: container ? 0 : 0.02 }) },
      upright: o.initial_state?.orientation?.type !== 'tilted',
      inside: null,
      measured: false,
      labels: o.detection?.class ? [o.detection.class] : undefined,
      grip: Number(o.attributes?.grip_width_m) || undefined,
    };
  }
  return w;
}

const f4 = (n: number) => (Math.abs(n) < 5e-5 ? '0' : n.toFixed(4));
const pstr = (p: Pose) => `p[${[p.x, p.y, p.z, p.rx, p.ry, p.rz].map(f4).join(', ')}]`;
const qstr = (q: number[]) => `[${q.map((v) => f4(rad(v))).join(', ')}]`;
const cm = (m: number) => `${Math.round(m * 1000) / 10} cm`;
const cloneWorld = (w: World): World =>
  Object.fromEntries(Object.entries(w).map(([k, o]) => [k, { ...o, location: { ...o.location } }]));
const cloneState = (s: ArmState): ArmState => ({ joints: [...s.joints], tcp: { ...s.tcp }, gripper: { ...s.gripper } });
const gripCmd = (cmd: string | undefined, fallback: string) => (cmd ?? fallback).split('|')[0].trim();

// ---------- params ----------

export function resolveParams(
  prim: Primitive,
  raw: Record<string, unknown> = {},
  enforceLimits = true,
): { params: Record<string, unknown> } | { error: string } {
  const params: Record<string, unknown> = {};
  for (const [key, spec] of Object.entries(prim.parameters)) {
    let v = raw[key];
    if (v === '' || v === null) v = undefined;
    if (v === undefined) v = spec.default;
    if (v === undefined) {
      if (isRequired(spec)) return { error: `${prim.id}: ${key} is required` };
      continue;
    }
    if (spec.type === 'number') {
      const n = Number(v);
      if (!Number.isFinite(n)) return { error: `${prim.id}: ${key} must be a number` };
      const lim = enforceLimits ? limitFor(key) : {};
      const lo = Math.max(spec.min ?? -Infinity, lim.min ?? -Infinity);
      const hi = Math.min(spec.max ?? Infinity, lim.max ?? Infinity);
      if (n < lo - 1e-9 || n > hi + 1e-9) {
        const range = [Number.isFinite(lo) ? lo : null, Number.isFinite(hi) ? hi : null];
        return {
          error: `${prim.id}: ${key} = ${n} is outside ${range[0] ?? '-∞'} to ${range[1] ?? '∞'}${lim.max !== undefined ? ' (operational limit)' : ''}`,
        };
      }
      params[key] = n;
    } else if (spec.type === 'enum') {
      if (!spec.values?.includes(String(v)))
        return { error: `${prim.id}: ${key} must be one of ${spec.values?.join(', ')}` };
      params[key] = String(v);
    } else if (spec.type === 'array') {
      const arr = Array.isArray(v)
        ? v.map(Number)
        : String(v)
            .split(/[\s,]+/)
            .filter(Boolean)
            .map(Number);
      if (spec.length && arr.length !== spec.length)
        return { error: `${prim.id}: ${key} needs ${spec.length} numbers` };
      if (arr.some((n) => !Number.isFinite(n))) return { error: `${prim.id}: ${key} must be numbers` };
      params[key] = arr;
    } else {
      params[key] = String(v);
    }
  }
  return { params };
}

// ---------- motion checks ----------

type Seg = { ok: true; joints: number[]; seconds: number; frames: Pose[] } | { ok: false; error: string };

function checkPose(p: Pose, opts: ArmOpts): string | null {
  if (p.z < opts.limits.min_z_above_table_m - 1e-6)
    return `TCP would go to z = ${cm(p.z)}, below the ${cm(opts.limits.min_z_above_table_m)} table limit`;
  if (Math.hypot(p.x, p.y) < SINGULARITY_R)
    return `path passes within ${cm(SINGULARITY_R)} of the base axis (singularity, the controller would stop)`;
  if (Math.hypot(p.x, p.y, p.z - SHOULDER_Z) > REACH) return `(${f4(p.x)}, ${f4(p.y)}, ${f4(p.z)}) is out of reach`;
  return null;
}

function checkJoints(q: number[]): string | null {
  for (const j of UR5E.joints ?? []) {
    const v = q[j.index];
    if (v < j.range_deg[0] || v > j.range_deg[1]) return `${j.id} would reach ${Math.round(v)}°, outside its range`;
  }
  return null;
}

// movel: straight TCP line, IK tracked every 2 cm so flips and singularities show up
function linear(from: ArmState, to: Pose, speed: number, opts: ArmOpts): Seg {
  const d = dist3(from.tcp, to);
  const ang = deg(Math.hypot(...matToRotvec(multT(poseR(from.tcp), poseR(to)))));
  const n = Math.max(1, Math.ceil(d / 0.02), Math.ceil(ang / 5));
  let q = from.joints;
  const frames: Pose[] = [];
  for (let i = 1; i <= n; i++) {
    const p = lerpPose(from.tcp, to, i / n);
    const bad = checkPose(p, opts);
    if (bad) return { ok: false, error: bad };
    const next = ik(p, q);
    if (!next)
      return {
        ok: false,
        error:
          Math.hypot(p.x, p.y) < 0.2
            ? 'this straight line passes too close to the base axis (singularity); go via MOVE_ABOVE, HOME or a joint move'
            : `no joint solution at (${f4(p.x)}, ${f4(p.y)}, ${f4(p.z)})`,
      };
    if (next.some((v, k) => Math.abs(v - q[k]) > MAX_JOINT_JUMP))
      return { ok: false, error: 'the wrist would flip along this straight line (near a singularity)' };
    q = next;
    if (i % 3 === 0 || i === n) frames.push(p);
  }
  const bad = checkJoints(q);
  if (bad) return { ok: false, error: bad };
  return { ok: true, joints: q, seconds: Math.max(d / speed, rad(ang) / 1.0, 0.2), frames };
}

const multT = (a: ReturnType<typeof poseR>, b: ReturnType<typeof poseR>) =>
  [0, 1, 2].map((i) => [0, 1, 2].map((j) => a[0][i] * b[0][j] + a[1][i] * b[1][j] + a[2][i] * b[2][j])) as ReturnType<
    typeof poseR
  >;

// movej: joint interpolation, TCP sampled every few degrees for the table limit
function joint(from: ArmState, target: number[], dps: number, opts: ArmOpts): Seg {
  const bad = checkJoints(target);
  if (bad) return { ok: false, error: bad };
  const span = Math.max(...target.map((v, i) => Math.abs(v - from.joints[i])));
  const n = Math.max(1, Math.ceil(span / 5));
  const frames: Pose[] = [];
  for (let i = 1; i <= n; i++) {
    const q = from.joints.map((v, k) => v + ((target[k] - v) * i) / n);
    const p = fk(q);
    if (p.z < opts.limits.min_z_above_table_m - 1e-6)
      return { ok: false, error: `the joint move would sweep the tool down to z = ${cm(p.z)} (table limit)` };
    if (i % 3 === 0 || i === n) frames.push(p);
  }
  return { ok: true, joints: target, seconds: Math.max(span / dps, 0.2), frames };
}

// ---------- world ----------

function carry(state: ArmState, world: World) {
  const id = state.gripper.holding;
  if (!id || !world[id]) return;
  const o = world[id];
  o.location = { x: state.tcp.x, y: state.tcp.y, z: state.tcp.z - state.gripper.holdDz };
  o.inside = null;
}

function containerAt(world: World, o: WorldObject) {
  const half = o.size / 2;
  for (const c of Object.values(world)) {
    if (c.category !== 'container' || c.id === o.id) continue;
    const dx = Math.abs(o.location.x - c.location.x);
    const dy = Math.abs(o.location.y - c.location.y);
    const inner = c.size / 2 - half;
    if (dx <= inner && dy <= inner) return { c, edge: false };
    if (dx <= c.size / 2 + half && dy <= c.size / 2 + half) return { c, edge: true };
  }
  return null;
}

export function evaluate(world: World, state: ArmState, objectId: string, containerId?: string) {
  const o = world[objectId];
  if (!o) return { inside: false, upright: false, held: false, where: 'unknown' };
  const held = state.gripper.holding === objectId;
  const where = held ? 'in the gripper' : o.inside ? `inside the ${world[o.inside]?.name ?? o.inside}` : 'on the table';
  return { inside: containerId ? o.inside === containerId : !!o.inside, upright: o.upright, held, where };
}

// ---------- primitives ----------

const DIRS = ['forward', 'backward', 'left', 'right', 'up', 'down'] as const;

export function applyArm(
  primId: string,
  rawParams: Record<string, unknown> | undefined,
  start: ArmState,
  startWorld: World,
  opts: ArmOpts = defaultOpts,
  primitives: Primitive[] = ARM_PRIMITIVES,
): Outcome {
  const fail = (error: string): Outcome => ({
    ok: false,
    error,
    note: error,
    state: start,
    world: startWorld,
    urscript: [],
    result: {},
    frames: [],
    seconds: 0,
  });
  const prim = primitives.find((p) => p.id === primId);
  if (!prim) return fail(`unknown primitive ${primId}`);
  const r = resolveParams(prim, rawParams, opts.enforceLimits);
  if ('error' in r) return fail(r.error);
  const P = r.params as Record<string, any>;

  let state = cloneState(start);
  const world = cloneWorld(startWorld);
  const urscript: string[] = [];
  const frames: Keyframe[] = [];
  const result: Record<string, unknown> = {};
  let seconds = 0;
  let note = '';
  const grip = () => (state.gripper.closed ? 1 : 0);
  const keyframes = (poses: Pose[], segSeconds: number) => {
    poses.forEach((p) => {
      const s = { ...state, tcp: p };
      carry(s, world);
      frames.push({ tcp: p, grip: grip(), world: cloneWorld(world), dt: segSeconds / poses.length });
    });
  };
  const commit = (seg: Extract<Seg, { ok: true }>, tcp: Pose) => {
    keyframes(seg.frames, seg.seconds);
    state = { ...state, joints: seg.joints, tcp };
    carry(state, world);
    seconds += seg.seconds;
  };
  const moveLinear = (to: Pose, speed: number): string | null => {
    const seg = linear(state, to, speed, opts);
    if (!seg.ok) return seg.error;
    commit(seg, to);
    return null;
  };
  const moveJoint = (q: number[], dps: number): string | null => {
    const seg = joint(state, q, dps, opts);
    if (!seg.ok) return seg.error;
    commit(seg, fk(q));
    return null;
  };
  const acc = opts.limits.max_tcp_acceleration_mps2;

  switch (prim.id) {
    case 'OPEN_GRIPPER': {
      const width = Math.min(STROKE, Number(P.width_m ?? STROKE));
      urscript.push(gripCmd(UR5E.gripper?.open_command_urscript, 'rq_open()'));
      const held = state.gripper.holding ? world[state.gripper.holding] : null;
      state.gripper = { width, closed: false, holding: null, holdDz: 0 };
      seconds = 0.5;
      if (!held) {
        note = 'Gripper opens';
        break;
      }
      const half = held.size / 2;
      const spot = containerAt(world, held);
      const floor = spot && !spot.edge ? spot.c.location.z + half : half;
      const drop = held.location.z - floor;
      const tilted = toolTiltDeg(state.tcp) > UPRIGHT_TILT;
      held.location = { ...held.location, z: floor };
      held.inside = spot && !spot.edge ? spot.c.id : null;
      held.upright = held.upright && !tilted && !spot?.edge && drop < 0.1;
      note = spot?.edge
        ? `Lets go over the edge of the ${spot.c.name}: the ${held.name} would land on the rim`
        : `Lets go: the ${held.name} drops ${cm(Math.max(0, drop))} ${held.inside ? `into the ${spot!.c.name}` : 'onto the table'}${held.upright ? '' : ', not upright'}`;
      result.released = held.id;
      break;
    }
    case 'CLOSE_GRIPPER': {
      urscript.push(gripCmd(UR5E.gripper?.close_command_urscript, 'rq_close()'));
      seconds = 0.6;
      if (state.gripper.holding) {
        note = 'Already holding something';
        result.object_detected = true;
        break;
      }
      const tcp = state.tcp;
      const target = Object.values(world).find(
        (o) =>
          o.category !== 'container' &&
          Math.hypot(o.location.x - tcp.x, o.location.y - tcp.y) <= 0.02 &&
          tcp.z - o.location.z >= -0.01 &&
          tcp.z - o.location.z <= 0.03 &&
          (o.grip ?? o.size) <= STROKE,
      );
      state.gripper = {
        width: target ? (target.grip ?? target.size) : 0,
        closed: true,
        holding: target?.id ?? null,
        holdDz: target ? tcp.z - target.location.z : 0,
      };
      result.object_detected = !!target;
      note = target ? `Grabs the ${target.name}` : 'Gripper closes on nothing';
      break;
    }
    case 'MOVE_FORWARD':
    case 'MOVE_BACKWARD':
    case 'MOVE_LEFT':
    case 'MOVE_RIGHT':
    case 'MOVE_UP':
    case 'MOVE_DOWN': {
      const dir = prim.direction ?? DIRS.find((d) => prim.id.endsWith(d.toUpperCase()))!;
      const map = UR5E.direction_mapping?.[dir];
      if (!map) return fail(`no direction mapping for ${dir}`);
      const d = Number(P.distance_m);
      const v = { x: 0, y: 0, z: 0 };
      v[map.axis] = map.sign * d;
      if (dir === 'down' && opts.openBeforeDescend && state.gripper.closed && !state.gripper.holding)
        return fail('the gripper must be open before descending (open_gripper_before_descending)');
      const to = { ...state.tcp, x: state.tcp.x + v.x, y: state.tcp.y + v.y, z: state.tcp.z + v.z };
      const err = moveLinear(to, Number(P.speed_mps));
      if (err) return fail(`${prim.id}: ${err}`);
      urscript.push(
        `movel(pose_add(get_actual_tcp_pose(), p[${f4(v.x)}, ${f4(v.y)}, ${f4(v.z)}, 0, 0, 0]), a=${acc}, v=${P.speed_mps})`,
      );
      note = `Moves ${cm(d)} ${dir}${state.gripper.holding ? ` with the ${world[state.gripper.holding]?.name}` : ''}`;
      break;
    }
    case 'ROTATE_ROLL':
    case 'ROTATE_PITCH':
    case 'ROTATE_YAW': {
      const axis = prim.id === 'ROTATE_ROLL' ? 0 : prim.id === 'ROTATE_PITCH' ? 1 : 2;
      const a = Number(P.angle_deg);
      const to = rotateInTool(state.tcp, axis, rad(a));
      const err = moveLinear(to, 0.1);
      if (err) return fail(`${prim.id}: ${err}`);
      const vec = [0, 0, 0];
      vec[axis] = rad(a);
      urscript.push(`movel(pose_trans(get_actual_tcp_pose(), p[0, 0, 0, ${vec.map(f4).join(', ')}]), a=${acc}, v=0.1)`);
      note = `Turns the gripper ${a}° (${['roll', 'pitch', 'yaw'][axis]})`;
      if (state.gripper.holding && axis !== 2 && toolTiltDeg(state.tcp) > UPRIGHT_TILT)
        note += `, tilting the ${world[state.gripper.holding]?.name}`;
      break;
    }
    case 'MOVE_JOINT': {
      const j = (UR5E.joints ?? []).find((x) => x.id === P.joint);
      if (!j) return fail(`unknown joint ${P.joint}`);
      const q = [...state.joints];
      q[j.index] = P.target_deg !== undefined ? Number(P.target_deg) : q[j.index] + Number(P.delta_deg);
      const err = moveJoint(q, Number(P.speed_dps));
      if (err) return fail(`MOVE_JOINT: ${err}`);
      urscript.push(`movej(${qstr(q)}, a=${JOINT_ACC}, v=${f4(rad(Number(P.speed_dps)))})`);
      note = `Turns ${j.name_it ?? j.id} to ${Math.round(q[j.index])}°`;
      break;
    }
    case 'MOVE_JOINTS': {
      const q = (P.joints_deg as number[]).map(Number);
      const err = moveJoint(q, Number(P.speed_dps));
      if (err) return fail(`MOVE_JOINTS: ${err}`);
      urscript.push(`movej(${qstr(q)}, a=${JOINT_ACC}, v=${f4(rad(Number(P.speed_dps)))})`);
      note = 'Moves all joints to a set position';
      break;
    }
    case 'HOME': {
      const err = moveJoint([...ARM_HOME], TRAVEL_DPS);
      if (err) return fail(`HOME: ${err}`);
      urscript.push(`movej(${qstr(ARM_HOME)}, a=${JOINT_ACC}, v=${f4(rad(TRAVEL_DPS))})`);
      note = 'Goes home, tool pointing down';
      break;
    }
    case 'MOVE_TO_POSE': {
      const cur = state.tcp;
      const keepR = P.rx === undefined && P.ry === undefined && P.rz === undefined;
      const target: Pose = keepR
        ? { ...cur, x: P.x, y: P.y, z: P.z }
        : { x: P.x, y: P.y, z: P.z, rx: P.rx ?? cur.rx, ry: P.ry ?? cur.ry, rz: P.rz ?? cur.rz };
      const speed = Number(P.speed_mps);
      const horiz = Math.hypot(target.x - cur.x, target.y - cur.y);
      const safe = opts.limits.safe_travel_height_m;
      const legs: Pose[] = [];
      if (opts.liftFirst && horiz > opts.limits.default_step_m && Math.min(cur.z, target.z) < safe) {
        const h = Math.max(safe, cur.z, target.z);
        if (cur.z < h - 1e-4) legs.push({ ...cur, z: h });
        legs.push({ ...target, z: h });
        if (target.z < h - 1e-4) legs.push(target);
      } else legs.push(target);
      for (const leg of legs) {
        const err = moveLinear(leg, speed);
        if (err) return fail(`MOVE_TO_POSE: ${err}`);
        urscript.push(`movel(${pstr(leg)}, a=${acc}, v=${speed})`);
      }
      note = `Moves to (${f4(P.x)}, ${f4(P.y)}, ${f4(P.z)})${legs.length > 1 ? ', lifting first' : ''}`;
      break;
    }
    case 'MOVE_ABOVE': {
      const o = world[P.target];
      if (!o) return fail(`MOVE_ABOVE: no object "${P.target}". Known: ${Object.keys(world).join(', ') || 'none'}`);
      if (state.gripper.holding === o.id) return fail(`MOVE_ABOVE: already holding the ${o.name}`);
      const h = Number(P.height_m);
      const target: Pose = { ...state.tcp, x: o.location.x, y: o.location.y, z: o.location.z + h };
      if (opts.liftFirst && state.tcp.z < target.z - 0.005) {
        const up = { ...state.tcp, z: target.z };
        const err = moveLinear(up, 0.1);
        if (err) return fail(`MOVE_ABOVE (lift): ${err}`);
        urscript.push(
          `movel(pose_add(get_actual_tcp_pose(), p[0, 0, ${f4(up.z - start.tcp.z)}, 0, 0, 0]), a=${acc}, v=0.1)`,
        );
      }
      const bad = checkPose(target, opts);
      if (bad && !bad.startsWith('path passes')) return fail(`MOVE_ABOVE: ${bad}`);
      // joint-space travel: a straight line from home would cross the base axis
      const q = ik(target, state.joints);
      if (!q) return fail(`MOVE_ABOVE: can't reach above the ${o.name}`);
      const err = moveJoint(q, TRAVEL_DPS);
      if (err) return fail(`MOVE_ABOVE: ${err}`);
      state.tcp = target;
      urscript.push(
        `movej(get_inverse_kin(${pstr(target)}, qnear=get_actual_joint_positions()), a=${JOINT_ACC}, v=${f4(rad(TRAVEL_DPS))})`,
      );
      note = `Goes ${cm(h)} above the ${o.name}${o.measured ? '' : ' (position from the skill file)'}`;
      break;
    }
    case 'STOP':
      urscript.push('stopl(2.0)');
      note = 'Stops';
      break;
    case 'WAIT': {
      const s = Number(P.seconds);
      urscript.push(`sleep(${s})`);
      seconds = s;
      frames.push({ tcp: state.tcp, grip: grip(), world: cloneWorld(world), dt: s });
      note = `Waits ${s}s`;
      break;
    }
    default:
      return fail(`${prim.id} has no implementation yet`);
  }

  if (!frames.length)
    frames.push({ tcp: state.tcp, grip: grip(), world: cloneWorld(world), dt: Math.max(seconds, 0.3) });
  result.tcp_pose = [state.tcp.x, state.tcp.y, state.tcp.z, state.tcp.rx, state.tcp.ry, state.tcp.rz].map(
    (n) => +n.toFixed(4),
  );
  result.joints_deg = state.joints.map((n) => +n.toFixed(2));
  result.params = P;
  return { ok: true, note, state, world, urscript, result, frames, seconds };
}

// take what the real controller reports over our prediction
export function syncFromBridge(state: ArmState, res: Record<string, unknown>): ArmState {
  const next = cloneState(state);
  if (Array.isArray(res.joints_deg) && res.joints_deg.length === 6) {
    next.joints = res.joints_deg.map(Number);
    next.tcp = fk(next.joints);
  }
  if (Array.isArray(res.tcp_pose) && res.tcp_pose.length === 6) {
    const [x, y, z, rx, ry, rz] = res.tcp_pose.map(Number);
    next.tcp = { x, y, z, rx, ry, rz };
  }
  if (res.object_detected === false && next.gripper.closed)
    next.gripper = { ...next.gripper, holding: null, holdDz: 0 };
  return next;
}

export { makePose };
