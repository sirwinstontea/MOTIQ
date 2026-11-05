// Hardware capability check: can THIS cell (UR5e + the project's tool + the vision camera) physically run a skill?
// Ported from the former Python hardware-capability-check tool (still in git history). Answer format:
//   verdict "yes" = no blocker and nothing undecidable (conditions may still be listed)
//   verdict "no"  = a hard blocker, or required information is missing
// Unknown values (tool length, container wall height, masses, camera mount) are never invented: lengths/heights are
// scanned over a plausible range, the rest become conditions or "information needed".
// Not a safety approval and not task correctness. Pure TypeScript, no React Native imports.
import { fk, ik, makePose, M3, Pose, rad } from '../robot/kinematics';
import type { DryRun } from './dryrun';
import { deviceOf, primitivesOf } from './index';
import type { EnvObject, SkillDoc, SkillStep } from './schema';

// ---------------------------------------------------------------------------------------------- hardware profiles
// Values from official UR / Intel documents (sources were listed in the former hardware-capability-check/profiles).

export const UR5E_HW = {
  id: 'ur5e',
  name: 'Universal Robots UR5e',
  jointDeg: [
    [-360, 360],
    [-360, 360],
    [-160, 160], // elbow: newest manual (datasheet says ±360, ROS ±180); the strictest is used
    [-360, 360],
    [-360, 360],
    [-360, 360],
  ] as [number, number][],
  jointSpeedDps: 180,
  tcpSpeedMax: 4.0,
  repeatabilityMm: 0.03,
  payloadKg: 5.0,
  payloadCurveMmKg: [
    [0, 5.0],
    [350, 5.0],
    [400, 4.5],
    [450, 4.09],
    [500, 3.74],
    [550, 3.45],
    [600, 3.2],
  ] as [number, number][],
  reachM: 0.85,
  ftRangeN: 50,
  ftAccuracyN: 4,
  torqueMaxNm: [150, 150, 150, 28, 28, 28],
  torqueUsable: 0.8, // engineering margin, not from UR
  linkMassKg: [3.761, 8.058, 2.846, 1.37, 1.3, 0.365],
  linkCom: [
    [0, -0.02561, 0.00193],
    [0.2125, 0, 0.11336],
    [0.15, 0, 0.0265],
    [0, -0.0018, 0.01634],
    [0, 0.0018, 0.01634],
    [0, 0, -0.001159],
  ],
  segmentRadiusM: [0.0755, 0.06, 0.05, 0.045, 0.045, 0.04], // approximate (UR publishes none)
};

export type ToolProfile = {
  id: string;
  name: string;
  actuated: boolean;
  openingMaxM: number | null;
  gripForceN: number | null;
  controlInterface: string | null;
  passiveModes: string[]; // scoop | hook | cradle | ...
  massKg: number | null;
  cogOffsetM: number | null;
  tcpOffsetM: number | null; // flange -> contact point
  tcpSearchM: [number, number]; // scanned while tcpOffsetM is unknown
};

// The project's tool (owner statement): rigid 3D-printed plastic, no actuator. Edit here when the real tool changes.
export const CELL_TOOL: ToolProfile = {
  id: 'custom_3d_printed_gripper',
  name: 'Custom 3D-printed plastic gripper (fixed, non-actuated)',
  actuated: false,
  openingMaxM: null,
  gripForceN: null,
  controlInterface: null,
  passiveModes: [],
  massKg: null,
  cogOffsetM: null,
  tcpOffsetM: null,
  tcpSearchM: [0.05, 0.3],
};

// Computer-vision camera of the execution cell. The D435 that recorded demonstrations is not used for the verdict.
export const CELL_CAMERA = {
  id: 'realsense_d415',
  name: 'Intel RealSense Depth Camera D415',
  fovDeg: [69.4, 42.5] as [number, number],
  resolutionPx: [1280, 720] as [number, number],
  rangeM: [0.5, 3.0] as [number, number],
  mount: null as null | { type: 'fixed'; position: number[]; lookAt: number[] },
};

export const CELL = {
  tableZ: 0, // assumed
  approachM: 0.1,
  transitM: 0.05,
  containerWallScanM: [0.05, 0.1, 0.15, 0.2],
  singularityR: 0.12, // TCP must stay out of this cylinder around the base axis (same as the app's arm model)
  maxJointJumpDeg: 25,
};

// ---------------------------------------------------------------------------------------------- report types

export type Status = 'pass' | 'conditional' | 'unknown' | 'fail' | 'info';

export type Finding = {
  check: string;
  status: Status;
  message: string;
  conditions?: string[];
  needs?: string[];
};

export type StepReport = {
  index: number;
  id: string;
  action: string;
  target?: string;
  status: Status;
  findings: Finding[];
};

export type HardwareReport = {
  verdict: 'yes' | 'no';
  detail: 'feasible' | 'feasible_with_conditions' | 'needs_validation' | 'infeasible';
  summary: string;
  robot: string;
  tool: string;
  camera: string;
  steps: StepReport[];
  global: Finding[];
  blockers: (Finding & { step?: number })[];
  conditions: string[];
  needs: string[];
  assumptions: string[];
  metrics: Record<string, string | number>;
};

const ORDER: Record<Status, number> = { fail: 4, unknown: 3, conditional: 2, pass: 1, info: 0 };
const worst = (ss: Status[]): Status =>
  ss.filter((s) => s !== 'info').reduce<Status>((a, b) => (ORDER[b] > ORDER[a] ? b : a), 'info');

// ---------------------------------------------------------------------------------------------- helpers

const GRAVITY = 9.80665;
const FRICTION = 0.3; // only used to size the grip force needed
const MIN_PIXELS = 20; // pixels across an object for reliable detection
const ACTUATOR_NEED =
  'an actuated gripper (e.g. servo/pneumatic driven from the UR tool I/O), or a passive mode (scoop/hook/cradle) declared in the tool profile if its shape really provides it';

const num = (v: unknown): number | null => {
  const n = typeof v === 'string' && v.trim() === '' ? NaN : Number(v);
  return v === null || v === undefined || typeof v === 'boolean' || !Number.isFinite(n) ? null : n;
};
const mm = (m: number) => `${Math.round(m * 1000)} mm`;
const fmtRange = (vals: number[], unit = 'm') => {
  if (!vals.length) return 'none';
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  return hi > lo ? `${lo.toFixed(2)}-${hi.toFixed(2)} ${unit}` : `${lo.toFixed(2)} ${unit}`;
};
const norm180 = (d: number) => ((((d + 180) % 360) + 360) % 360) - 180;

function findValue(obj: unknown, names: string[], depth = 2): unknown {
  if (!obj || typeof obj !== 'object' || depth < 0) return undefined;
  const rec = obj as Record<string, unknown>;
  for (const n of names) if (rec[n] !== undefined) return rec[n];
  for (const v of Object.values(rec)) {
    const f = findValue(v, names, depth - 1);
    if (f !== undefined) return f;
  }
  return undefined;
}

// what a step needs from the hardware
type Spec = { canonical: string; motion: boolean; vision: boolean; hold: boolean; release: boolean };
const ALIASES: Record<string, string> = {};
const alias = (c: string, ...names: string[]) => names.forEach((n) => (ALIASES[n] = c));
alias('DETECT', 'DETECT', 'LOCATE', 'FIND', 'OBSERVE', 'SCAN', 'IDENTIFY', 'LOOK', 'SEARCH', 'REIDENTIFY');
alias('VERIFY', 'VERIFY', 'CHECK', 'INSPECT', 'CONFIRM', 'VALIDATE');
alias('PICK', 'PICK', 'PICK_UP', 'PICKUP', 'GRASP', 'GRAB', 'TAKE', 'FETCH');
alias('PLACE', 'PLACE', 'PUT', 'PUT_DOWN', 'DROP', 'DEPOSIT', 'SET_DOWN', 'STACK', 'PUT_IN', 'PUT_INTO');
alias('SEQUENCE', 'SEQUENCE');
alias('MOVE', 'MOVE', 'MOVE_TO', 'GO_TO', 'REACH', 'APPROACH', 'VISIT');
alias('HOME', 'HOME', 'GO_HOME', 'PARK');
alias('WAIT', 'WAIT', 'PAUSE');

function specOf(step: SkillStep): Spec | null {
  const c = ALIASES[String(step.action).trim().toUpperCase().replace(/[ -]/g, '_')];
  if (!c) return null;
  const prims = (step.primitive_sequence ?? []).map((p) => p.primitive);
  return {
    canonical: c,
    motion: !['DETECT', 'VERIFY', 'WAIT'].includes(c),
    vision: c === 'DETECT' || c === 'VERIFY',
    hold: c === 'PICK' || (c === 'SEQUENCE' && prims.includes('CLOSE_GRIPPER')),
    release: c === 'PLACE',
  };
}

type Obj = {
  id: string;
  name: string;
  container: boolean;
  pos: [number, number, number] | null;
  size: number | null; // stated edge length / inner footprint (m)
  height: number | null; // container wall height if stated
  mass: number | null;
  raw: EnvObject;
};

// stated size (m), or for an upright object resting on the table: twice its centre height (as the Python checker does)
function sizeOf(o: EnvObject): number | null {
  const a = (o.attributes ?? {}) as Record<string, unknown>;
  for (const k of ['size_m', 'edge_length_m', 'side_m', 'width_m']) if (num(a[k]) != null) return num(a[k]);
  const mmv = num(a.size_mm ?? a.edge_length_mm);
  if (mmv != null) return mmv / 1000;
  const z = num(o.initial_state?.location?.z);
  const ori = o.initial_state?.orientation?.type;
  if (o.category !== 'container' && z != null && z > 0 && (!ori || ori === 'upright' || ori === 'flat')) return 2 * z;
  return null;
}

function objectsOf(doc: SkillDoc): Record<string, Obj> {
  const out: Record<string, Obj> = {};
  for (const o of doc.environment?.objects ?? []) {
    const l = o.initial_state?.location;
    const a = (o.attributes ?? {}) as Record<string, unknown>;
    out[o.id] = {
      id: o.id,
      name: o.name || o.id,
      container: o.category === 'container',
      pos: l && [l.x, l.y, l.z].every((v) => num(v) !== null) ? [Number(l.x), Number(l.y), Number(l.z)] : null,
      size: sizeOf(o),
      height: num(a.height_m ?? a.wall_height_m),
      mass: num(a.mass_kg),
      raw: o,
    };
  }
  return out;
}

// ---------------------------------------------------------------------------------------------- arm math

type Frame = { R: M3; p: [number, number, number] };
const DH_A = [0, -0.425, -0.3922, 0, 0, 0];
const DH_D = [0.1625, 0, 0, 0.1333, 0.0997, 0.0996];
const DH_AL = [Math.PI / 2, 0, 0, Math.PI / 2, -Math.PI / 2, 0];
const mul = (a: M3, b: M3): M3 =>
  [0, 1, 2].map((i) => [0, 1, 2].map((j) => a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j])) as M3;
const mv = (a: M3, v: number[]) =>
  [0, 1, 2].map((i) => a[i][0] * v[0] + a[i][1] * v[1] + a[i][2] * v[2]) as [number, number, number];

// DH frames 1..6 in the base frame
function frames(qDeg: number[]): Frame[] {
  let R: M3 = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ];
  let p: [number, number, number] = [0, 0, 0];
  const out: Frame[] = [];
  for (let i = 0; i < 6; i++) {
    const q = rad(qDeg[i]);
    const [ct, st, ca, sa] = [Math.cos(q), Math.sin(q), Math.cos(DH_AL[i]), Math.sin(DH_AL[i])];
    const add = mv(R, [DH_A[i] * ct, DH_A[i] * st, DH_D[i]]);
    p = [p[0] + add[0], p[1] + add[1], p[2] + add[2]];
    R = mul(R, [
      [ct, -st * ca, st * sa],
      [st, ct * ca, -ct * sa],
      [0, sa, ca],
    ]);
    out.push({ R, p });
  }
  return out;
}

// static gravity torques (N m) for the arm + an optional payload on the tool axis
function gravityTorques(qDeg: number[], payloadKg: number, cogM: number): number[] {
  const T = frames(qDeg);
  const masses: { m: number; c: number[]; last: number }[] = UR5E_HW.linkMassKg.map((m, i) => {
    const c = mv(T[i].R, UR5E_HW.linkCom[i]);
    return { m, c: [c[0] + T[i].p[0], c[1] + T[i].p[1], c[2] + T[i].p[2]], last: i };
  });
  if (payloadKg > 0) {
    const c = mv(T[5].R, [0, 0, cogM]);
    masses.push({ m: payloadKg, c: [c[0] + T[5].p[0], c[1] + T[5].p[1], c[2] + T[5].p[2]], last: 5 });
  }
  const axes = [[0, 0, 1], ...T.slice(0, 5).map((f) => [f.R[0][2], f.R[1][2], f.R[2][2]])];
  const origins = [[0, 0, 0], ...T.slice(0, 5).map((f) => f.p)];
  const tau = [0, 0, 0, 0, 0, 0];
  for (const { m, c, last } of masses) {
    const F = [0, 0, -m * GRAVITY];
    for (let j = 0; j <= last; j++) {
      const r = [c[0] - origins[j][0], c[1] - origins[j][1], c[2] - origins[j][2]];
      const cr = [r[1] * F[2] - r[2] * F[1], r[2] * F[0] - r[0] * F[2], r[0] * F[1] - r[1] * F[0]];
      tau[j] += axes[j][0] * cr[0] + axes[j][1] * cr[1] + axes[j][2] * cr[2];
    }
  }
  return tau;
}

// largest payload (kg) whose static torque stays inside the usable joint torque at every pose; < 0 = bare arm too heavy
function payloadBudget(qs: number[][], cogM: number): number {
  const caps = UR5E_HW.torqueMaxNm.map((t) => t * UR5E_HW.torqueUsable);
  let best = Infinity;
  for (const q of qs) {
    const t0 = gravityTorques(q, 0, cogM);
    const t1 = gravityTorques(q, 1, cogM).map((v, j) => v - t0[j]);
    for (let j = 0; j < 6; j++) {
      if (Math.abs(t0[j]) > caps[j]) return -1;
      if (Math.abs(t1[j]) < 1e-9) continue;
      const hi = Math.max((-caps[j] - t0[j]) / t1[j], (caps[j] - t0[j]) / t1[j]);
      best = Math.min(best, hi);
    }
  }
  return best;
}

function ratedPayload(cogM: number): number {
  const x = Math.abs(cogM) * 1000;
  const c = UR5E_HW.payloadCurveMmKg;
  if (x > c[c.length - 1][0]) return 0;
  for (let i = 1; i < c.length; i++)
    if (x <= c[i][0]) return c[i - 1][1] + ((c[i][1] - c[i - 1][1]) * (x - c[i - 1][0])) / (c[i][0] - c[i - 1][0]);
  return c[c.length - 1][1];
}

const TOOL_DOWN_YAWS = [0, 90, 180, -90];
function toolDown(yawDeg: number): M3 {
  const c = Math.cos(rad(yawDeg));
  const s = Math.sin(rad(yawDeg));
  return [
    [c, s, 0],
    [s, -c, 0],
    [0, 0, -1],
  ];
}

type Reach = { ok: true; q: number[] } | { ok: false; reason: string };

function limitsOk(q: number[]) {
  return q.every((v, i) => v >= UR5E_HW.jointDeg[i][0] - 1e-6 && v <= UR5E_HW.jointDeg[i][1] + 1e-6);
}

function tableClear(q: number[]) {
  const T = frames(q);
  return T.every((f, i) => i === 0 || f.p[2] - UR5E_HW.segmentRadiusM[i] >= CELL.tableZ - 1e-6);
}

// flange pose for a tool-down contact point at `pt`, tool length L
function solveAt(pt: number[], L: number, seed: number[], yaw: number): Reach {
  const r = Math.hypot(pt[0], pt[1]);
  if (r < CELL.singularityR) return { ok: false, reason: 'singularity' };
  if (pt[2] < CELL.tableZ - 1e-6) return { ok: false, reason: 'below_table' };
  const target: Pose = makePose([pt[0], pt[1], pt[2] + L], toolDown(yaw));
  const q = ik(target, seed);
  if (!q) return { ok: false, reason: 'no_ik_solution' };
  const qn = q.map((v, i) => (i === 2 ? norm180(v) : v));
  if (!limitsOk(qn)) return { ok: false, reason: 'joint_limit' };
  if (!tableClear(qn)) return { ok: false, reason: 'table_collision' };
  return { ok: true, q: qn };
}

// a straight vertical approach: hover -> contact, sampled, continuous joint motion with one tool yaw
function solveApproach(hover: number[], contact: number[], L: number, seed: number[]): Reach {
  let lastReason = 'no_ik_solution';
  for (const yaw of TOOL_DOWN_YAWS) {
    let q = seed;
    let ok = true;
    for (let k = 0; k <= 4; k++) {
      const u = k / 4;
      const pt = hover.map((h, i) => h + (contact[i] - h) * u);
      const r = solveAt(pt, L, q, yaw);
      if (!r.ok) {
        lastReason = r.reason;
        ok = false;
        break;
      }
      if (k > 0 && r.q.some((v, i) => Math.abs(v - q[i]) > CELL.maxJointJumpDeg)) {
        lastReason = 'branch_flip';
        ok = false;
        break;
      }
      q = r.q;
    }
    if (ok) return { ok: true, q };
  }
  return { ok: false, reason: lastReason };
}

const REMEDY: Record<string, string> = {
  no_ik_solution:
    'move the object/box closer to the robot base (within ~0.85 m and outside the cylinder above/below the base), or use a different robot',
  joint_limit: 'a pose that avoids the joint limit (elbow ±160°), e.g. move the target away from the base',
  table_collision: 'a shorter tool or a target position away from the table edge of reach',
  singularity: 'a target position away from the base axis',
  below_table: 'a target height at or above the table surface',
  branch_flip: 'a target position where the arm does not have to flip its wrist on the way down',
};

// ---------------------------------------------------------------------------------------------- the check

type Ctx = {
  doc: SkillDoc;
  objs: Record<string, Obj>;
  specs: (Spec | null)[];
  stepF: Finding[][];
  global: Finding[];
  assumptions: string[];
  metrics: Record<string, string | number>;
  tool: ToolProfile;
};

const objOf = (ctx: Ctx, ref?: string) => (ref ? ctx.objs[ref] : undefined);

function checkSkillFile(ctx: Ctx) {
  if (!ctx.doc.steps.length)
    ctx.global.push({
      check: 'skill_file',
      status: 'unknown',
      message: 'the skill has no steps',
      needs: ['a non-empty steps list'],
    });
  for (const o of Object.values(ctx.objs))
    if (!o.pos)
      ctx.global.push({
        check: 'scene',
        status: 'unknown',
        message: `object '${o.id}' has no stated position`,
        needs: [`position of '${o.id}'`],
      });
  ctx.assumptions.push(
    'robot base at the origin of the skill frame (cell: assumed)',
    `table surface at z = ${CELL.tableZ.toFixed(3)} m (cell: assumed)`,
    'robot mounted upright (cell: assumed)',
    `two cameras: '${CELL_CAMERA.name}' is the computer-vision camera used for DETECT/VERIFY; the D435 that recorded the demonstration is not evaluated`,
    'nominal UR DH kinematics (per-robot calibration offsets are not available; positions are ±few mm)',
    'arm link radii for the table-clearance check are approximate (UR publishes none)',
  );
}

function holdFinding(ctx: Ctx, step: SkillStep, obj?: Obj): Finding {
  const t = ctx.tool;
  const g = findValue(step.parameters, ['grasp_type', 'grasp', 'hold_mode', 'grip_type']);
  const style = g === undefined ? null : String(g).trim().toLowerCase().replace(/[ -]/g, '_');
  if (t.actuated) {
    const conds: string[] = [];
    const needs: string[] = [];
    if (obj?.size != null && t.openingMaxM != null) {
      if (obj.size > t.openingMaxM)
        return {
          check: 'grasp',
          status: 'fail',
          message: `object width ${mm(obj.size)} exceeds the tool opening ${mm(t.openingMaxM)}`,
        };
    } else {
      conds.push(`tool opening >= object width${obj?.size != null ? ` (${mm(obj.size)})` : ''}`);
      needs.push(t.openingMaxM == null ? 'tool opening range' : 'object width');
    }
    if (obj?.mass != null && t.gripForceN != null) {
      const need = (obj.mass * GRAVITY) / (2 * FRICTION);
      if (t.gripForceN < need)
        return {
          check: 'grasp',
          status: 'fail',
          message: `grip force ${t.gripForceN.toFixed(1)} N is below the ${need.toFixed(1)} N needed to hold ${obj.mass} kg`,
        };
    } else {
      conds.push("grip force sufficient for the object's weight");
      needs.push('grip force of the tool and object mass');
    }
    if (!t.controlInterface) {
      conds.push('tool actuation wired to the UR tool I/O');
      needs.push('how the tool is actuated');
    }
    return conds.length
      ? {
          check: 'grasp',
          status: 'conditional',
          message: 'tool is actuated; grasp feasible if the listed values hold',
          conditions: conds,
          needs,
        }
      : { check: 'grasp', status: 'pass', message: 'tool can close on the object' };
  }
  if (style && ['suction', 'vacuum', 'suction_cup'].includes(style))
    return {
      check: 'grasp',
      status: 'fail',
      message: `'${style}' needs a vacuum source; the tool has none`,
      needs: ['a vacuum tool or another grasp type'],
    };
  const mode = style && t.passiveModes.includes(style) ? style : !style ? t.passiveModes[0] : undefined;
  if (mode)
    return {
      check: 'grasp',
      status: 'conditional',
      message: `rigid tool can hold the object by shape ('${mode}'), only if the geometry really captures it`,
      conditions: [`the tool geometry captures the object by '${mode}'`],
      needs: ['tool CAD/dimensions and object size'],
    };
  return {
    check: 'grasp',
    status: 'fail',
    message: `${style ? `'${style}' grasp` : 'holding the object'} needs a tool that can close, but '${t.name}' is rigid and declares no passive holding mode`,
    needs: [ACTUATOR_NEED],
  };
}

function releaseFinding(ctx: Ctx): Finding {
  const t = ctx.tool;
  if (t.actuated)
    return t.controlInterface
      ? { check: 'release', status: 'pass', message: 'tool can open to release the object' }
      : {
          check: 'release',
          status: 'conditional',
          message: 'tool is actuated; release works if its actuation is wired to the controller',
          conditions: ['tool actuation wired to the UR tool I/O'],
        };
  const passive = t.passiveModes.find((m) => m === 'scoop' || m === 'cradle');
  if (passive)
    return {
      check: 'release',
      status: 'conditional',
      message: `a rigid '${passive}' tool can only release by tilting`,
      conditions: ['the object slides off when the tool is tilted'],
    };
  return {
    check: 'release',
    status: 'fail',
    message: `'${t.name}' is rigid: there is no mechanism to let go of a held object`,
    needs: [ACTUATOR_NEED],
  };
}

function checkTool(ctx: Ctx) {
  let holding = false;
  ctx.doc.steps.forEach((s, i) => {
    const spec = ctx.specs[i];
    if (!spec) return;
    if (spec.hold) ctx.stepF[i].push(holdFinding(ctx, s, objOf(ctx, s.target)));
    const rel = findValue(s.parameters, ['release_after_placement', 'release', 'release_after']);
    if (spec.release && rel !== false) ctx.stepF[i].push(releaseFinding(ctx));
    // a rigid tool can't close; opening is only a problem once something is (supposed to be) held
    for (const c of s.primitive_sequence ?? []) {
      if (c.primitive === 'CLOSE_GRIPPER') holding = true;
      if (ctx.tool.actuated || (c.primitive !== 'CLOSE_GRIPPER' && c.primitive !== 'OPEN_GRIPPER')) continue;
      const blocks = c.primitive === 'CLOSE_GRIPPER' || holding;
      const msg = `${c.primitive}: '${ctx.tool.name}' has no actuator, the command does nothing`;
      if (!ctx.stepF[i].some((f) => f.message === msg))
        ctx.stepF[i].push(
          blocks
            ? { check: 'gripper_actuation', status: 'fail', message: msg, needs: [ACTUATOR_NEED] }
            : { check: 'gripper_actuation', status: 'info', message: `${msg} (harmless here: nothing is held)` },
        );
      if (c.primitive === 'OPEN_GRIPPER' && blocks) holding = false;
    }
    if (spec.hold && !(s.primitive_sequence ?? []).length) holding = true;
    if (spec.release && !(s.primitive_sequence ?? []).length) holding = false;
  });
}

function checkVision(ctx: Ctx) {
  const cam = CELL_CAMERA;
  const wanted = new Map<string, Obj>();
  for (const o of Object.values(ctx.objs)) {
    const d = o.raw.detection;
    if (d?.required || d?.reidentify_before_pick || d?.reidentify_before_place) wanted.set(o.id, o);
  }
  const reid = ctx.doc.constraints?.execution_constraints?.require_object_reidentification;
  ctx.doc.steps.forEach((s, i) => {
    const sp = ctx.specs[i];
    const o = objOf(ctx, s.target);
    if (o && sp && (sp.vision || (reid && (sp.canonical === 'PICK' || sp.canonical === 'PLACE')))) wanted.set(o.id, o);
  });
  const visionSteps = ctx.doc.steps.map((s, i) => [s, i] as const).filter(([, i]) => ctx.specs[i]?.vision);
  if (!wanted.size && !visionSteps.length) return;
  const [lo, hi] = cam.rangeM;
  const tanH = Math.tan(rad(cam.fovDeg[0]) / 2);
  for (const o of wanted.values()) {
    let usableHi = hi;
    let f: Finding | null = null;
    if (o.size != null) {
      const maxStandoff = o.size / ((MIN_PIXELS * 2 * tanH) / cam.resolutionPx[0]);
      usableHi = Math.min(hi, maxStandoff);
      if (usableHi < lo)
        f = {
          check: 'vision',
          status: 'fail',
          message: `'${o.id}' (${mm(o.size)}) cannot be resolved with >= ${MIN_PIXELS} px at the camera's ${lo} m minimum range`,
        };
    }
    if (!f && !cam.mount)
      f = {
        check: 'vision',
        status: 'conditional',
        message: `camera can detect '${o.id}' from ${lo}-${usableHi.toFixed(2)} m; coverage cannot be confirmed because the camera mounting is unknown`,
        conditions: [
          `camera mounted ${lo}-${usableHi.toFixed(2)} m from '${o.id}' with it inside the ${cam.fovDeg[0]} x ${cam.fovDeg[1]}° view`,
        ],
        needs: ['camera mounting (fixed pose or eye-in-hand offset)'],
      };
    if (!f && cam.mount && o.pos) {
      const cp = cam.mount.position;
      const z = cam.mount.lookAt.map((v, i) => v - cp[i]);
      const zn = Math.hypot(...z);
      const zu = z.map((v) => v / zn);
      let x = [zu[1], -zu[0], 0];
      const xn = Math.hypot(...x) || 1;
      x = Math.hypot(...x) < 1e-6 ? [1, 0, 0] : x.map((v) => v / xn);
      const y = [zu[1] * x[2] - zu[2] * x[1], zu[2] * x[0] - zu[0] * x[2], zu[0] * x[1] - zu[1] * x[0]];
      const v = o.pos.map((p, i) => p - cp[i]);
      const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
      const depth = dot(v, zu);
      const ah = (Math.atan2(dot(v, x), depth) * 180) / Math.PI;
      const av = (Math.atan2(dot(v, y), depth) * 180) / Math.PI;
      const rng = Math.hypot(...v);
      f =
        depth <= 0
          ? { check: 'vision', status: 'fail', message: `'${o.id}' is behind the camera` }
          : Math.abs(ah) > cam.fovDeg[0] / 2 || Math.abs(av) > cam.fovDeg[1] / 2
            ? { check: 'vision', status: 'fail', message: `'${o.id}' is outside the camera's field of view` }
            : rng < lo || rng > usableHi
              ? {
                  check: 'vision',
                  status: 'fail',
                  message: `'${o.id}' is ${rng.toFixed(2)} m from the camera; usable range is ${lo}-${usableHi.toFixed(2)} m`,
                }
              : {
                  check: 'vision',
                  status: 'pass',
                  message: `'${o.id}' is inside the view cone at ${rng.toFixed(2)} m`,
                };
    }
    if (!f) continue;
    const at = visionSteps.find(([s]) => s.target === o.id) ?? visionSteps[0];
    if (at) ctx.stepF[at[1]].push(f);
    else ctx.global.push(f);
  }
}

function checkRobotClaims(ctx: Ctx) {
  const r = UR5E_HW;
  const c = ctx.doc.robot;
  if (!c) return;
  const add = (f: Finding) => ctx.global.push(f);
  const n = (x: string) => x.toLowerCase().replace(/[^a-z0-9]/g, '');
  if (c.model && !n(c.model).includes(r.id) && !n(c.model).includes(n(r.name)) && !n(r.name).includes(n(c.model)))
    add({
      check: 'robot_claims',
      status: 'fail',
      message: `skill is written for '${c.model}', this cell has '${r.name}'`,
    });
  if (c.degrees_of_freedom != null && c.degrees_of_freedom !== 6)
    add({
      check: 'robot_claims',
      status: 'fail',
      message: `skill assumes ${c.degrees_of_freedom} DOF, the robot has 6`,
    });
  const upper = (name: string, v: unknown, doc: number, unit: string) => {
    const x = num(v);
    if (x == null) return;
    add(
      x > doc + 1e-9
        ? {
            check: 'robot_claims',
            status: 'fail',
            message: `skill assumes ${name} ${x} ${unit}, the documented value is ${doc} ${unit}`,
          }
        : {
            check: 'robot_claims',
            status: 'pass',
            message: `${name} ${x} ${unit} is within the documented ${doc} ${unit}`,
          },
    );
  };
  upper('payload', c.payload_kg, r.payloadKg, 'kg');
  upper('reach', c.reach_m, r.reachM, 'm');
  upper('max TCP speed', c.max_tcp_speed_mps, r.tcpSpeedMax, 'm/s');
  const rep = num(c.pose_repeatability_m);
  if (rep != null)
    add(
      rep * 1000 < r.repeatabilityMm - 1e-9
        ? {
            check: 'robot_claims',
            status: 'fail',
            message: `skill assumes repeatability ${rep * 1000} mm, documented is ±${r.repeatabilityMm} mm`,
          }
        : {
            check: 'robot_claims',
            status: 'pass',
            message: `repeatability ${rep * 1000} mm matches the documented ±${r.repeatabilityMm} mm`,
          },
    );
  const wider: string[] = [];
  for (const j of c.joints ?? []) {
    const lim = r.jointDeg[j.index];
    if (!lim) continue;
    if (j.range_deg && (j.range_deg[0] < lim[0] - 1e-6 || j.range_deg[1] > lim[1] + 1e-6))
      wider.push(`${j.id}: skill ${j.range_deg[0]}..${j.range_deg[1]}° vs ${lim[0]}..${lim[1]}°`);
    if (num(j.max_speed_dps) != null && j.max_speed_dps > r.jointSpeedDps)
      add({
        check: 'robot_claims',
        status: 'fail',
        message: `skill assumes ${j.id} speed ${j.max_speed_dps}°/s, documented is ${r.jointSpeedDps}°/s`,
      });
  }
  if (wider.length)
    add({
      check: 'robot_claims',
      status: 'conditional',
      message: `skill's joint ranges are wider than the manual's (elbow ±160°): ${wider.join('; ')}`,
      conditions: ['every motion also fits the stricter manual ranges (checked that way here)'],
    });
  if (c.direction_mapping?.calibrated === false)
    add({
      check: 'direction_mapping',
      status: 'conditional',
      message: "left/right/forward/back mapping to base axes is marked 'calibrated: false'",
      conditions: ['direction_mapping verified on the real cell with the teach pendant'],
      needs: ['calibrated direction mapping'],
    });
  if (c.gripper) {
    const t = ctx.tool;
    if (!t.actuated)
      add({
        check: 'gripper_claims',
        status: 'fail',
        message: `skill assumes a gripper ('${c.gripper.model}', ${mm(c.gripper.stroke_m)} stroke) but the cell tool '${t.name}' has no actuator`,
        needs: ['an actuated gripper, or a skill that needs no grasp'],
      });
    else if (t.openingMaxM != null && c.gripper.stroke_m > t.openingMaxM + 1e-9)
      add({
        check: 'gripper_claims',
        status: 'fail',
        message: `skill assumes stroke ${mm(c.gripper.stroke_m)}, the tool opens ${mm(t.openingMaxM)}`,
      });
  }
  if (Array.isArray(c.home_joints_deg) && c.home_joints_deg.length === 6) {
    const q = c.home_joints_deg;
    const p = fk(q);
    const down = frames(q)[5].R[2][2] < -0.99;
    add(
      !limitsOk(q)
        ? { check: 'home_pose', status: 'fail', message: 'home_joints_deg is outside the joint limits' }
        : p.z < CELL.tableZ + 0.05
          ? {
              check: 'home_pose',
              status: 'fail',
              message: `the home pose puts the flange only ${(p.z - CELL.tableZ).toFixed(3)} m above the table`,
            }
          : {
              check: 'home_pose',
              status: down ? 'pass' : 'conditional',
              message: `home pose is inside the limits, flange ${p.z.toFixed(2)} m above the base plane, tool ${down ? 'pointing down' : 'not pointing straight down'}`,
              conditions: down ? [] : ['tool orientation at home as intended'],
            },
    );
  }
  const ol = c.operational_limits;
  if (!ol) return;
  if (ol.max_tcp_speed_mps > r.tcpSpeedMax)
    add({
      check: 'operational_limits',
      status: 'fail',
      message: `max_tcp_speed_mps ${ol.max_tcp_speed_mps} exceeds the hardware maximum ${r.tcpSpeedMax} m/s`,
    });
  if (ol.max_joint_speed_dps > r.jointSpeedDps)
    add({
      check: 'operational_limits',
      status: 'fail',
      message: `max_joint_speed_dps ${ol.max_joint_speed_dps} exceeds the hardware maximum ${r.jointSpeedDps}°/s`,
    });
  if (ol.approach_speed_mps > ol.max_tcp_speed_mps)
    add({
      check: 'operational_limits',
      status: 'fail',
      message: 'approach_speed_mps is above max_tcp_speed_mps (contradictory limits)',
    });
  if (ol.default_step_m > ol.max_step_m)
    add({
      check: 'operational_limits',
      status: 'fail',
      message: 'default_step_m is above max_step_m (contradictory limits)',
    });
  if (ol.safe_travel_height_m <= ol.min_z_above_table_m)
    add({
      check: 'operational_limits',
      status: 'fail',
      message: 'safe_travel_height_m must exceed min_z_above_table_m',
    });
  for (const p of primitivesOf(ctx.doc)) {
    const us = p.urscript ?? '';
    for (const m of us.matchAll(/movel\(.*?a=([0-9.]+),\s*v=([0-9.]+)\)/g)) {
      if (Number(m[2]) > ol.max_tcp_speed_mps + 1e-9)
        add({
          check: 'primitive_library',
          status: 'fail',
          message: `${p.id}: fixed TCP speed v=${m[2]} m/s in its URScript exceeds max_tcp_speed_mps ${ol.max_tcp_speed_mps}`,
        });
      if (Number(m[1]) > ol.max_tcp_acceleration_mps2 + 1e-9)
        add({
          check: 'primitive_library',
          status: 'fail',
          message: `${p.id}: fixed acceleration a=${m[1]} exceeds max_tcp_acceleration_mps2 ${ol.max_tcp_acceleration_mps2}`,
        });
    }
    for (const m of us.matchAll(/movej\(.*?a=([0-9.]+),\s*v=([0-9.]+)\)/g))
      if ((Number(m[2]) * 180) / Math.PI > ol.max_joint_speed_dps + 1e-6)
        add({
          check: 'primitive_library',
          status: 'fail',
          message: `${p.id}: fixed joint speed exceeds max_joint_speed_dps ${ol.max_joint_speed_dps}`,
        });
    const d = num(p.parameters?.speed_mps?.default);
    if (d != null && d > ol.max_tcp_speed_mps + 1e-9)
      add({
        check: 'primitive_library',
        status: 'fail',
        message: `${p.id}: default speed ${d} m/s exceeds max_tcp_speed_mps ${ol.max_tcp_speed_mps}`,
      });
  }
}

// reach + path for every pick/place point, scanned over the unknown tool length and container wall height
function checkMotion(ctx: Ctx, run: DryRun | null) {
  const t = ctx.tool;
  const lengths =
    t.tcpOffsetM != null
      ? [t.tcpOffsetM]
      : Array.from(
          { length: 6 },
          (_, i) => +(t.tcpSearchM[0] + ((t.tcpSearchM[1] - t.tcpSearchM[0]) * i) / 5).toFixed(4),
        );
  const home = ctx.doc.robot?.home_joints_deg?.length === 6 ? ctx.doc.robot.home_joints_deg : [0, -90, 90, -90, -90, 0];

  // the app's own arm model (the same one the simulator and the bridge guard use)
  if (run?.error) {
    const b = run.beats.find((x) => x.error);
    const f: Finding = {
      check: 'dry_run',
      status: 'fail',
      message: `the arm model stops here: ${b?.error ?? run.error}`,
    };
    if (b) ctx.stepF[b.step].push(f);
    else ctx.global.push(f);
  }
  const elbowHit = new Set<number>();
  for (const b of run?.beats ?? []) {
    if (!b.joints || elbowHit.has(b.step)) continue;
    const q = b.joints.map((v, i) => (i === 2 ? norm180(v) : v));
    if (!limitsOk(q)) {
      elbowHit.add(b.step);
      ctx.stepF[b.step].push({
        check: 'joint_limits',
        status: 'fail',
        message: `${b.label} needs elbow ${q[2].toFixed(0)}°, outside the manual's ±160°`,
        needs: [REMEDY.joint_limit],
      });
    }
  }

  // targets
  let held: Obj | undefined;
  type Target = { step: number; label: string; contact: number[]; hover: (h: number) => number[]; usesWall: boolean };
  const targets: Target[] = [];
  ctx.doc.steps.forEach((s, i) => {
    const sp = ctx.specs[i];
    const o = objOf(ctx, s.target);
    if (!sp || !o?.pos) return;
    if (sp.canonical === 'PICK' && !o.container) {
      const sz = o.size ?? 0.05;
      const contact = [o.pos[0], o.pos[1], o.pos[2]];
      targets.push({
        step: i,
        label: 'grasp',
        contact,
        hover: () => [contact[0], contact[1], contact[2] + sz / 2 + CELL.approachM],
        usesWall: false,
      });
      held = o;
    } else if (sp.canonical === 'PLACE') {
      const hs = held?.size ?? 0.05;
      const contact = o.container ? [o.pos[0], o.pos[1], o.pos[2] + hs / 2] : [o.pos[0], o.pos[1], o.pos[2]];
      const wall = o.container && o.height == null;
      targets.push({
        step: i,
        label: 'place',
        contact,
        hover: (h) => [contact[0], contact[1], o.pos![2] + (o.container ? (o.height ?? h) : 0) + hs + CELL.transitM],
        usesWall: wall,
      });
    }
  });
  if (!targets.length) return;

  const anyWall = targets.some((x) => x.usesWall);
  const heights = anyWall ? CELL.containerWallScanM : [0];
  const okBy: Record<number, number[]> = {}; // tool lengths that work for every height, per target
  const reasons: Record<number, string> = {};
  const configs: Record<string, number[][]> = {};
  targets.forEach((tg, k) => {
    okBy[k] = [];
    for (const L of lengths) {
      let all = true;
      for (const h of tg.usesWall ? heights : [0]) {
        const r = solveApproach(tg.hover(h), tg.contact, L, home);
        if (!r.ok) {
          all = false;
          reasons[k] ??= r.reason;
          break;
        }
        (configs[L] ??= []).push(r.q);
      }
      if (all) okBy[k].push(L);
    }
  });
  const scan: string[] = [];
  if (t.tcpOffsetM == null) scan.push(`tool length ${fmtRange(lengths)}`);
  if (anyWall) scan.push(`container wall height ${fmtRange(heights)}`);
  if (scan.length) ctx.assumptions.push(`unknown values were scanned, not assumed: ${scan.join('; ')}`);

  targets.forEach((tg, k) => {
    const ok = okBy[k];
    const where = `${tg.label} at (${tg.contact.map((v) => v.toFixed(2)).join(', ')}) m`;
    const r = Math.hypot(tg.contact[0], tg.contact[1]);
    if (r > UR5E_HW.reachM)
      ctx.stepF[tg.step].push({
        check: 'documented_reach',
        status: 'conditional',
        message: `${tg.label} point is ${r.toFixed(3)} m from the base axis, beyond the stated reach of ${UR5E_HW.reachM} m`,
        conditions: ['operation outside the documented reach is acceptable (payload/accuracy not guaranteed there)'],
      });
    if (ok.length === lengths.length) {
      const conds = [
        ...(t.tcpOffsetM == null ? [`tool length (flange to contact point) within ${fmtRange(lengths)}`] : []),
        ...(tg.usesWall ? [`container wall height within ${fmtRange(heights)}`] : []),
      ];
      ctx.stepF[tg.step].push({
        check: 'reach_and_path',
        status: conds.length ? 'conditional' : 'pass',
        message: `${where}: hover and vertical approach are reachable with continuous joint motion${conds.length ? ' for every scanned value of the unknowns' : ''}`,
        conditions: conds,
      });
    } else if (!ok.length) {
      const why = reasons[k] ?? 'no_ik_solution';
      ctx.stepF[tg.step].push({
        check: 'reach_and_path',
        status: 'fail',
        message: `${where} cannot be reached tool-down (${why.replace(/_/g, ' ')}) for any tool length ${fmtRange(lengths)}`,
        needs: REMEDY[why] ? [REMEDY[why]] : [],
      });
    } else
      ctx.stepF[tg.step].push({
        check: 'reach_and_path',
        status: 'conditional',
        message: `${where} is reachable only for tool length ${fmtRange(ok)}`,
        conditions: [`tool length (flange to contact point) within ${fmtRange(ok)}`],
      });
  });

  const feasibleL = lengths.filter((L) => targets.every((_, k) => okBy[k].includes(L)));
  ctx.metrics['Tool lengths scanned'] =
    t.tcpOffsetM != null ? `${t.tcpOffsetM.toFixed(2)} m (given)` : fmtRange(lengths);
  ctx.metrics['Tool lengths that work'] = feasibleL.length ? fmtRange(feasibleL) : 'none';

  // payload along the representative tool length
  const L = feasibleL.length ? feasibleL[Math.floor(feasibleL.length / 2)] : lengths[Math.floor(lengths.length / 2)];
  const qs = [home, ...(configs[L] ?? [])];
  const cog = t.cogOffsetM ?? L;
  const rated = ratedPayload(cog);
  const torque = payloadBudget(qs, cog);
  const budget = Math.min(rated, torque);
  ctx.metrics['Payload budget (tool + object)'] = `${Math.max(0, budget).toFixed(2)} kg`;
  ctx.metrics['Rated payload at CoG offset'] = `${rated.toFixed(2)} kg @ ${(cog * 100).toFixed(0)} cm`;
  const carried = [...new Set(targets.filter((x) => x.label === 'grasp').map((x) => ctx.doc.steps[x.step].target!))]
    .map((id) => ctx.objs[id])
    .filter(Boolean);
  if (torque < 0) {
    ctx.global.push({
      check: 'payload',
      status: 'fail',
      message: "even the bare arm's gravity torque exceeds the usable joint torque on this path",
    });
  } else if (budget <= 0) {
    ctx.global.push({
      check: 'payload',
      status: 'fail',
      message: `no payload margin at CoG offset ${cog.toFixed(2)} m`,
    });
  } else if (t.massKg != null && carried.every((o) => o.mass != null)) {
    const total = t.massKg + carried.reduce((s, o) => s + (o.mass ?? 0), 0);
    ctx.global.push(
      total > budget
        ? {
            check: 'payload',
            status: 'fail',
            message: `tool + object = ${total.toFixed(2)} kg exceeds the payload budget ${budget.toFixed(2)} kg`,
          }
        : {
            check: 'payload',
            status: 'pass',
            message: `tool + object = ${total.toFixed(2)} kg is within the payload budget ${budget.toFixed(2)} kg`,
          },
    );
  } else {
    ctx.global.push({
      check: 'payload',
      status: 'conditional',
      message: `the arm can carry up to ${budget.toFixed(2)} kg (tool + object) along this path; actual masses are not stated`,
      conditions: [`tool mass + carried object mass <= ${budget.toFixed(2)} kg`],
      needs: [
        ...(t.massKg == null ? ['tool mass'] : []),
        ...carried.filter((o) => o.mass == null).map((o) => `mass of '${o.id}'`),
      ],
    });
  }
}

function checkStepRequirements(ctx: Ctx) {
  const r = UR5E_HW;
  let held: Obj | undefined;
  ctx.doc.steps.forEach((s, i) => {
    const sp = ctx.specs[i];
    const add = (f: Finding) => ctx.stepF[i].push(f);
    if (!sp) {
      add({
        check: 'action',
        status: 'unknown',
        message: `action '${s.action}' is not a known robot action; its hardware needs cannot be evaluated`,
        needs: [`definition of '${s.action}'`],
      });
      return;
    }
    const p = s.parameters;
    const force = num(findValue(p, ['force_n', 'force', 'contact_force', 'push_force']));
    if (force != null)
      add(
        force > r.ftRangeN
          ? {
              check: 'force',
              status: 'conditional',
              message: `${force} N is above the force/torque sensor range (±${r.ftRangeN} N): it can't be measured or controlled in closed loop`,
              conditions: ['open-loop force (not measured by the built-in sensor)'],
            }
          : force < r.ftAccuracyN
            ? {
                check: 'force',
                status: 'conditional',
                message: `${force} N is below the force sensor accuracy (±${r.ftAccuracyN} N)`,
                conditions: ['force regulated without sensor feedback'],
              }
            : {
                check: 'force',
                status: 'pass',
                message: `${force} N is within the force/torque sensor range (±${r.ftRangeN} N)`,
              },
      );
    const tolMm = num(findValue(p, ['tolerance_mm', 'position_tolerance_mm', 'precision_mm']));
    const tolM = num(findValue(p, ['tolerance_m', 'position_tolerance_m', 'precision_m', 'tolerance']));
    const tol = tolMm != null ? tolMm / 1000 : tolM;
    if (tol != null)
      add(
        tol * 1000 < r.repeatabilityMm
          ? {
              check: 'precision',
              status: 'fail',
              message: `required tolerance ${(tol * 1000).toFixed(3)} mm is tighter than the arm's repeatability ±${r.repeatabilityMm} mm`,
            }
          : tol < 0.001
            ? {
                check: 'precision',
                status: 'conditional',
                message: `${(tol * 1000).toFixed(2)} mm is near repeatability; absolute accuracy needs per-robot calibration`,
                conditions: ['calibrated robot and measured tool/part geometry'],
              }
            : {
                check: 'precision',
                status: 'pass',
                message: `arm repeatability ±${r.repeatabilityMm} mm is far inside the required ${(tol * 1000).toFixed(2)} mm`,
              },
      );
    const speed = num(findValue(p, ['speed_m_s', 'max_speed_m_s', 'tcp_speed_m_s', 'speed_mps']));
    if (speed != null)
      add(
        speed > r.tcpSpeedMax
          ? {
              check: 'speed',
              status: 'fail',
              message: `${speed} m/s exceeds the arm's maximum TCP speed ${r.tcpSpeedMax} m/s`,
            }
          : {
              check: 'speed',
              status: 'pass',
              message: `${speed} m/s is within the arm's maximum TCP speed ${r.tcpSpeedMax} m/s`,
            },
      );
    const tgt = objOf(ctx, s.target);
    if (sp.canonical === 'PICK') held = tgt;
    if (sp.canonical === 'PLACE' && tgt?.container && held) {
      if (held.size == null)
        add({
          check: 'container_fit',
          status: 'unknown',
          message: `size of '${held.id}' is unknown`,
          needs: [`dimensions of '${held.id}'`],
        });
      else if (tgt.size == null)
        add({
          check: 'container_fit',
          status: 'unknown',
          message: `interior size of '${tgt.id}' is unknown; '${held.id}' needs an opening of at least ${mm(held.size + 0.01)} plus the tool's width`,
          needs: [`interior dimensions of '${tgt.id}'`],
        });
      else {
        const free = tgt.size - held.size;
        add(
          free < 0.01
            ? {
                check: 'container_fit',
                status: 'fail',
                message: `only ${mm(free)} free around '${held.id}' inside '${tgt.id}' (need >= 10 mm)`,
              }
            : {
                check: 'container_fit',
                status: 'conditional',
                message: `'${held.id}' fits inside '${tgt.id}' with ${mm(free)} to spare; the tool's own width must also fit`,
                conditions: ['tool tip width fits in the remaining clearance'],
              },
        );
      }
    }
  });
}

function checkTiming(ctx: Ctx, run: DryRun | null) {
  const limit = num(
    findValue(ctx.doc.constraints, ['max_duration_s', 'time_limit_s', 'deadline_s', 'max_time_s', 'cycle_time_s'], 3),
  );
  if (!run) return;
  let prev = ctx.doc.robot?.home_joints_deg ?? run.start.joints;
  let tMin = 0;
  for (const b of run.beats) {
    if (!b.joints) continue;
    tMin += Math.max(...b.joints.map((v, i) => Math.abs(v - prev[i]))) / UR5E_HW.jointSpeedDps;
    prev = b.joints;
  }
  ctx.metrics['Minimum arm time (joint-speed bound)'] = `${tMin.toFixed(2)} s`;
  if (limit == null) return;
  ctx.global.push(
    limit < tMin
      ? {
          check: 'timing',
          status: 'fail',
          message: `${limit} s is shorter than the arm's absolute minimum ${tMin.toFixed(2)} s`,
        }
      : limit < 3 * tMin
        ? {
            check: 'timing',
            status: 'conditional',
            message: `${limit} s leaves little room over the ${tMin.toFixed(2)} s lower bound`,
            conditions: ['motion near full joint speed'],
          }
        : {
            check: 'timing',
            status: 'pass',
            message: `${limit} s is well above the arm's ${tMin.toFixed(2)} s lower bound`,
          },
  );
}

/** Hardware capability check for an arm skill; null for skills that don't run on the arm. */
export function checkHardware(doc: SkillDoc, run: DryRun | null, tool: ToolProfile = CELL_TOOL): HardwareReport | null {
  if (deviceOf(doc) !== 'arm') return null;
  const ctx: Ctx = {
    doc,
    objs: objectsOf(doc),
    specs: doc.steps.map(specOf),
    stepF: doc.steps.map(() => []),
    global: [],
    assumptions: [],
    metrics: {},
    tool,
  };
  checkSkillFile(ctx);
  checkTool(ctx);
  checkVision(ctx);
  checkRobotClaims(ctx);
  checkMotion(ctx, run);
  checkStepRequirements(ctx);
  checkTiming(ctx, run);

  const steps: StepReport[] = doc.steps.map((s, i) => {
    const fs = ctx.stepF[i].length
      ? ctx.stepF[i]
      : [{ check: 'info', status: 'info' as Status, message: 'no hardware requirement beyond the arm being powered' }];
    return {
      index: i,
      id: s.id,
      action: s.action,
      target: s.target,
      status: worst(fs.map((f) => f.status)),
      findings: fs,
    };
  });
  const all = [
    ...steps.flatMap((s) => s.findings.map((f) => ({ ...f, step: s.index }))),
    ...ctx.global.map((f) => ({ ...f, step: undefined })),
  ];
  const overall = worst(all.map((f) => f.status));
  // the cell pose/table/mounting are assumed until confirmed, so a clean run is still "with conditions"
  const detail: HardwareReport['detail'] =
    overall === 'fail' ? 'infeasible' : overall === 'unknown' ? 'needs_validation' : 'feasible_with_conditions';
  const uniq = (xs: string[]) => [...new Set(xs)];
  const conditions = uniq(all.flatMap((f) => f.conditions ?? []));
  conditions.push('the cell assumptions are correct (robot base pose, table height, upright mounting)');
  const needs = uniq(all.flatMap((f) => f.needs ?? []));
  const blockers = all.filter((f) => f.status === 'fail');
  const unknowns = all.filter((f) => f.status === 'unknown');
  const reach = all.filter((f) => f.check === 'reach_and_path' || f.check === 'dry_run' || f.check === 'joint_limits');
  let summary: string;
  if (detail === 'infeasible') {
    summary = `This skill can't run on the cell's hardware: ${blockers.length} hard blocker${blockers.length === 1 ? '' : 's'}.`;
    if (reach.length && !reach.some((f) => f.status === 'fail'))
      summary += ' The arm itself is not the blocker: reach, joint limits and path are fine.';
  } else if (detail === 'needs_validation')
    summary = `No hard blocker, but ${unknowns.length} item${unknowns.length === 1 ? '' : 's'} can't be decided without more information, so the answer is NO until they are supplied.`;
  else summary = 'No hard blocker found. The hardware can run this skill if the listed conditions hold.';

  return {
    verdict: detail === 'infeasible' || detail === 'needs_validation' ? 'no' : 'yes',
    detail,
    summary,
    robot: UR5E_HW.name,
    tool: tool.name,
    camera: CELL_CAMERA.name,
    steps,
    global: ctx.global,
    blockers,
    conditions,
    needs,
    assumptions: uniq(ctx.assumptions),
    metrics: ctx.metrics,
  };
}
