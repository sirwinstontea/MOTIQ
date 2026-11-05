// Skills in the MOTIQ skill format (see schema.ts and templates/pick_place_ur5e.json).
import { Ionicons } from '@expo/vector-icons';
import env from '../lib/config';
import { fullAccess } from '../lib/fullAccess';
import { defaultOpts, resolveParams, type ArmOpts } from '../robot/arm';
import { ARM_LIMITS, profiles } from '../robot/profiles';
import template from './templates/pick_place_ur5e.json';
import type { Device, EnvObject, Primitive, PrimitiveCall, SkillDoc, SkillStep } from './schema';

export * from './schema';

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v));
export type Icon = keyof typeof Ionicons.glyphMap;

export const skillIcons: Icon[] = [
  'flash-outline',
  'cube-outline',
  'hand-right-outline',
  'refresh-outline',
  'happy-outline',
  'walk-outline',
  'bed-outline',
  'search-outline',
  'gift-outline',
  'trash-bin-outline',
  'star-outline',
  'sparkles-outline',
];

export const STEP_ACTIONS = ['OVERVIEW', 'DETECT', 'PICK', 'PLACE', 'VISIT', 'TOUCH', 'PUSH', 'VERIFY', 'SEQUENCE'] as const;
export const FAILURE_POLICIES = ['STOP_AND_REOBSERVE', 'RETRY_WITH_REOBSERVATION', 'STOP_AND_REPLAN', 'REPORT_FAILURE'];
// steps that look instead of moving (OVERVIEW = the arm camera looks at the table from where it is, no motion)
export const isLookStep = (s: SkillStep) => s.action === 'DETECT' || s.action === 'VERIFY' || s.action === 'OVERVIEW';

// ---------- reading a skill ----------

// MOTIQ drives one robot: the UR5e arm.
export function deviceOf(_doc: Pick<SkillDoc, 'robot'>): Device {
  return 'arm';
}

// A skill made for another robot (the old robot dog, or anything not the arm): never run it as an arm skill.
export function foreignRobot(doc: unknown): string | null {
  const r = (doc as Partial<SkillDoc> | null)?.robot as { device?: string; model?: string } | undefined;
  if (r?.device && r.device !== 'arm') return r.device;
  return /dog/i.test(r?.model ?? '') ? 'dog' : null;
}

// the skill's own primitive list if it has one, otherwise the robot profile's
export function primitivesOf(doc: Pick<SkillDoc, 'robot' | 'action_primitives'>): Primitive[] {
  const own = doc.action_primitives;
  return own?.length ? own : profiles[deviceOf(doc)].primitives;
}

export function armOpts(doc: SkillDoc): ArmOpts {
  const c = doc.constraints?.execution_constraints ?? {};
  return {
    limits: { ...ARM_LIMITS, ...(doc.robot?.operational_limits ?? {}) },
    liftFirst: c.lift_before_horizontal_travel !== false,
    openBeforeDescend: c.open_gripper_before_descending !== false,
    enforceLimits:
      c.respect_operational_limits !== false && doc.safety?.reject_moves_outside_operational_limits !== false,
  };
}

export const skillId = (doc: SkillDoc) => doc.skill.id;
export const icon = (doc: SkillDoc): Icon => (doc.app?.icon as Icon) ?? 'flash-outline';
export const primitiveCount = (doc: SkillDoc) => doc.steps.reduce((n, s) => n + (s.primitive_sequence?.length ?? 0), 0);

export function primLabel(c: PrimitiveCall): string {
  const vals = Object.entries(c.parameters ?? {})
    .filter(([, v]) => v !== '' && v != null)
    .map(([k, v]) =>
      k === 'target' ? String(v).replace(/^object_/, '') : Array.isArray(v) ? `[${v.join(', ')}]` : String(v),
    );
  const name = c.primitive.toLowerCase().replace(/_/g, ' ');
  return vals.length ? `${name} · ${vals.join(' · ')}` : name;
}

export function stepLabel(s: SkillStep): string {
  const target = s.target ? ` ${s.target.replace(/^object_/, '')}` : '';
  if (s.action === 'SEQUENCE' && s.primitive_sequence?.length === 1) return primLabel(s.primitive_sequence[0]);
  return `${s.action.toLowerCase()}${target}`;
}

// ---------- what the real arm can do ----------

// The real arm goes through the bridge: PICK / PLACE steps with a target become one robot_controller task each;
// inside SEQUENCE steps only these primitives reach it. Small steps (MOVE_UP ..., MOVE_JOINT, ROTATE_YAW) move the
// physical UR through ur_jog. ROTATE_ROLL / PITCH, MOVE_JOINTS, MOVE_TO_POSE, CLOSE_GRIPPER stay simulator-only.
export const REAL_ARM_PRIMITIVES = [
  'MOVE_ABOVE', 'HOME', 'OPEN_GRIPPER', 'WAIT', 'STOP',
  'MOVE_UP', 'MOVE_DOWN', 'MOVE_LEFT', 'MOVE_RIGHT', 'MOVE_FORWARD', 'MOVE_BACKWARD', 'MOVE_JOINT', 'ROTATE_YAW',
];
export const realArmConnected = () => !!env.armUrl;

// The real arm's cell (robot_controller config/scene.example.yaml): the 4 objects its camera knows and the places
// it can put a held object. PLACE targets on the real arm must be one of these.
export const CELL_OBJECTS = (template.environment.objects as unknown as EnvObject[])
  .filter((o) => o.category !== 'container')
  .map((o) => o.id);
export const CELL_DESTINATIONS = [
  'delivery_zone',
  'blue_bucket_01', // drops the held object INTO the blue bucket
  ...CELL_OBJECTS.map((id) => `${id}_slot`), // back where that object was picked
];

// Objects with a taught push path in robot_controller (config/scene.example.yaml `push:`). The 7 mm green ring has
// none: the fingers would have to go below the 15 mm workspace floor.
export const PUSHABLE_OBJECTS = ['blue_bucket_01', 'orange_screw_03', 'white_bucket_lid_04'];

// What the app's simulator shows for a PUSH step: a rough preview (it has no contact physics, and CLOSE_GRIPPER on
// nothing would count as a failed grasp there). With the arm bridge connected the step is ONE robot_controller task
// (push_object: closes the gripper itself, above -> beside -> push 5 cm -> lift, every segment checked).
export const pushSimSequence = (target: string): PrimitiveCall[] => [
  { primitive: 'MOVE_ABOVE', parameters: { target, height_m: 0.15 } },
  { primitive: 'MOVE_DOWN', parameters: { distance_m: 0.05, speed_mps: 0.05 } },
  { primitive: 'MOVE_FORWARD', parameters: { distance_m: 0.05, speed_mps: 0.05 } },
  { primitive: 'MOVE_UP', parameters: { distance_m: 0.05 } },
];

// steps the real arm can't run, as readable messages (empty = fine)
export function realArmProblems(doc: SkillDoc): string[] {
  if (deviceOf(doc) !== 'arm') return [];
  const out: string[] = [];
  doc.steps.forEach((s, i) => {
    if (isLookStep(s)) return;
    if (s.action === 'PICK' && s.target && !CELL_OBJECTS.includes(s.target))
      return void out.push(`Step ${i + 1}: the real arm can only pick ${CELL_OBJECTS.join(', ')} (not "${s.target}").`);
    if (s.action === 'PLACE' && s.target && !CELL_DESTINATIONS.includes(s.target))
      return void out.push(
        `Step ${i + 1}: the real arm can place at ${CELL_DESTINATIONS.join(', ')} (not "${s.target}").`,
      );
    if ((s.action === 'PICK' || s.action === 'PLACE') && s.target) return;
    if (s.action === 'PUSH')
      return void (s.target && PUSHABLE_OBJECTS.includes(s.target)
        ? undefined
        : out.push(`Step ${i + 1}: the arm can push ${PUSHABLE_OBJECTS.join(', ')} (not "${s.target ?? 'nothing'}").`));
    if (s.physical_tool === 'ur_down_up' || s.physical_tool === 'ur_go_to_pose') return; // one bridge tool, see runner.ts
    if (s.action === 'TOUCH') return; // bridge touch: camera-guided + force-guarded contact, see runner.ts
    if (s.action === 'VISIT') return; // bridge ur_visit: the arm camera guides the arm over it, see runner.ts
    const bad = [
      ...new Set((s.primitive_sequence ?? []).map((c) => c.primitive).filter((p) => !REAL_ARM_PRIMITIVES.includes(p))),
    ];
    if (bad.length)
      out.push(
        `Step ${i + 1}: ${bad.map((p) => p.toLowerCase().replace(/_/g, ' ')).join(', ')} only works in the simulator. ` +
          'The real arm can pick, place, move above an object, open the gripper, wait, go home, take small steps ' +
          '(up, down, left, right, forward, back), turn one joint and spin the gripper.',
      );
  });
  return out;
}

// "Run on real arm" while the PC stack is SIM=1: the PHYSICAL UR5e only does what it can on its own (camera + wrist
// force sensor): look, locate, touch, push, small steps, joints. It has no gripper, and the controller (pick, place,
// home, move above) drives the simulation only -> those skills run in RoboDK.
// (HOME is skipped on the real arm, see runner.ts: its locate / touch / push already go back to where they started)
const PHYSICAL_ONLY_PRIMITIVES = REAL_ARM_PRIMITIVES.filter((p) => !['MOVE_ABOVE', 'OPEN_GRIPPER'].includes(p));
export function physicalArmProblems(doc: SkillDoc): string[] {
  const out: string[] = [];
  doc.steps.forEach((s, i) => {
    if (s.action === 'DETECT' || s.action === 'OVERVIEW') return;
    if (s.action === 'VERIFY') {
      if (doc.steps.slice(0, i).some((p) => p.action === 'PLACE'))
        out.push(`Step ${i + 1}: checking a place needs pick and place, which run in RoboDK only.`);
      return;
    }
    if (['VISIT', 'TOUCH', 'PUSH'].includes(s.action) || s.physical_tool === 'ur_down_up' || s.physical_tool === 'ur_go_to_pose')
      return;
    if (['PICK', 'PLACE', 'SHAKE'].includes(s.action))
      return void out.push(
        `Step ${i + 1}: ${s.action.toLowerCase()} runs in RoboDK only (the real arm has no gripper). ` +
          'On the real arm: locate, touch, push, small steps and joint moves.',
      );
    const bad = [
      ...new Set((s.primitive_sequence ?? []).map((c) => c.primitive).filter((p) => !PHYSICAL_ONLY_PRIMITIVES.includes(p))),
    ];
    if (bad.length || !s.primitive_sequence?.length)
      out.push(
        `Step ${i + 1}: ${bad.length ? bad.map((p) => p.toLowerCase().replace(/_/g, ' ')).join(', ') : s.action.toLowerCase()} ` +
          'runs in RoboDK only. On the real arm: locate, touch, push, small steps and joint moves.',
      );
  });
  return out;
}

// ---------- safety gates ----------

// why the skill may not move a real robot, or null
// roboDK: the bridge runs SIM=1 (URSim + RoboDK, the physical arm is never used), so no real-robot approval is
// needed; the bridge still refuses if it is in fact on the physical arm (X-Require-Sim)
export function physicalBlock(doc: SkillDoc, real: boolean, roboDK = false): string | null {
  if (!real) return null;
  const arm = realArmProblems(doc);
  if (arm.length) return `${arm[0]} Edit the skill (or ask Grok to change it) to run it on the arm.`;
  if (fullAccess() || roboDK) {
    // operator turned on full access: no per-skill approval, but a broken skill still doesn't run
    const errs = validateSkill(doc).errors;
    return errs.length ? `the skill has errors: ${errs[0]}` : null;
  }
  if (!doc.safety?.physical_execution_allowed)
    return 'physical execution is not allowed for this skill yet. Approve it in the Skills tab first.';
  if (doc.safety?.requires_operator_approval !== false && !doc.review?.approved)
    return 'this skill needs operator approval in the Skills tab before it can move the real robot.';
  const v = validateSkill(doc);
  if (v.errors.length) return `the skill has errors: ${v.errors[0]}`;
  return null;
}

// ---------- validation ----------

export type Validation = { errors: string[]; warnings: string[] };

// structural checks: names, primitives, parameters, object references. Feasibility comes from dryRun().
export function validateSkill(doc: SkillDoc): Validation {
  const errors: string[] = [];
  const warnings: string[] = [];
  if (!doc?.skill?.name?.trim()) errors.push('Give the skill a name.');
  if (!doc?.skill?.id?.trim()) errors.push('The skill needs an id.');
  if (!doc?.steps?.length) errors.push('Add at least one step.');
  if (errors.length) return { errors, warnings };

  const device = deviceOf(doc);
  const prims = primitivesOf(doc);
  const objects = new Set((doc.environment?.objects ?? []).map((o) => o.id));
  const enforce = armOpts(doc).enforceLimits && device === 'arm';

  doc.steps.forEach((s, i) => {
    const at = `Step ${i + 1} (${s.action})`;
    if (!s.action) errors.push(`Step ${i + 1}: missing action.`);
    if (s.target && device === 'arm' && !objects.has(s.target)) errors.push(`${at}: unknown target "${s.target}".`);
    if (s.action === 'DETECT' && !s.target) errors.push(`${at}: say which object to detect.`);
    if (s.action === 'VISIT' && !s.target) errors.push(`${at}: say which object to visit.`);
    if (s.action === 'PUSH' && !s.target) errors.push(`${at}: say which object to push.`);
    if (s.action === 'TOUCH' && !s.target) errors.push(`${at}: say which object to touch.`);
    if (!isLookStep(s) && !['VISIT', 'TOUCH', 'PUSH'].includes(s.action) && !s.primitive_sequence?.length)
      errors.push(`${at}: add at least one primitive.`);
    s.primitive_sequence?.forEach((c, j) => {
      const p = prims.find((x) => x.id === c.primitive);
      if (!p) return errors.push(`${at}, primitive ${j + 1}: ${c.primitive} isn't a ${device} primitive.`);
      const r = resolveParams(p, c.parameters ?? {}, enforce);
      if ('error' in r) errors.push(`${at}, primitive ${j + 1}: ${r.error}.`);
      const t = c.parameters?.target;
      if (p.parameters.target && typeof t === 'string' && !objects.has(t))
        errors.push(`${at}, primitive ${j + 1}: unknown object "${t}".`);
    });
  });

  if (device === 'arm') {
    if (doc.robot?.direction_mapping?.calibrated === false)
      warnings.push('Left/right/forward directions are not calibrated on the real cell yet.');
    if (/TO_BE_CONFIRMED/.test(doc.robot?.gripper?.model ?? '')) warnings.push('Gripper model is not confirmed.');
  }
  const unc = doc.interpretation?.uncertainties?.length ?? 0;
  if (unc) warnings.push(`${unc} open uncertaint${unc === 1 ? 'y' : 'ies'} listed in the skill.`);
  return { errors, warnings };
}

// ---------- duplicates ----------

// key order doesn't matter: {a,b} and {b,a} are the same
const stable = (v: unknown): string =>
  Array.isArray(v)
    ? `[${v.map(stable).join(',')}]`
    : v && typeof v === 'object'
      ? `{${Object.keys(v as object)
          .sort()
          .filter((k) => (v as any)[k] !== undefined)
          .map((k) => `${JSON.stringify(k)}:${stable((v as any)[k])}`)
          .join(',')}}`
      : JSON.stringify(v);

// What a skill makes the robot do (device + steps), ignoring names, wording, ids and review state.
// Two skills with the same fingerprint are the same skill.
export const contentSignature = (doc: SkillDoc) =>
  stable({
    device: deviceOf(doc),
    steps: doc.steps.map((s) => ({
      action: s.action,
      target: s.target,
      prims: (s.primitive_sequence ?? []).map((c) => ({ p: c.primitive, a: c.parameters ?? {} })),
    })),
  }).toLowerCase();

// the other saved skill that does exactly the same thing, if any
export const findDuplicate = (doc: SkillDoc, all: SkillDoc[]) => {
  if (!doc.steps.length) return undefined;
  const sig = contentSignature(doc);
  return all.find((s) => s.skill.id !== doc.skill.id && contentSignature(s) === sig);
};

// ---------- building and importing ----------

const now = () => new Date().toISOString().replace(/\.\d+Z$/, 'Z');
export const newSkillId = () => `skill_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;

const DEFAULT_SAFETY: SkillDoc['safety'] = {
  physical_execution_allowed: false,
  requires_feasibility_check: true,
  requires_operator_approval: true,
  requires_robot_controller_limits: true,
  unknown_parameters_policy: 'NEEDS_VALIDATION',
  stop_primitive_always_available: true,
  reject_moves_outside_operational_limits: true,
};

const DEFAULT_CONSTRAINTS: SkillDoc['constraints'] = {
  object_constraints: [],
  execution_constraints: {
    collision_avoidance: true,
    require_object_reidentification: true,
    require_workspace_validation: true,
    require_grasp_validation: true,
    allow_adaptive_replanning: false,
    open_gripper_before_descending: true,
    lift_before_horizontal_travel: true,
    respect_operational_limits: true,
  },
};

type Make = {
  id: string;
  name: string;
  description: string;
  device: Device;
  icon: Icon;
  steps: SkillStep[];
  goal?: string;
  success?: string[];
  objects?: EnvObject[];
  builtin?: boolean;
};

export function makeSkill(m: Make): SkillDoc {
  const t = now();
  return {
    schema_version: '1.1.0',
    skill: {
      id: m.id,
      name: m.name,
      version: '1.0.0',
      status: 'draft',
      description: m.description,
      created_at: t,
      updated_at: t,
    },
    intent: {
      goal: m.goal ?? m.description,
      natural_language_instruction: m.description,
      success_conditions: m.success ?? [],
    },
    robot: { device: m.device, model: profiles[m.device].robot.model },
    environment: {
      coordinate_system: 'world',
      units: 'meters',
      workspace: m.device === 'arm' ? { type: 'tabletop', surface: 'table' } : { type: 'floor' },
      objects: m.objects ?? [],
    },
    constraints: clone(DEFAULT_CONSTRAINTS),
    steps: m.steps,
    safety: { ...DEFAULT_SAFETY },
    review: { revision: 1, approved: false, approved_by: null, approval_timestamp: null },
    app: { icon: m.icon, builtin: m.builtin },
  };
}

export function newSkill(device: Device): SkillDoc {
  const doc = makeSkill({ id: newSkillId(), name: '', description: '', device, icon: skillIcons[0], steps: [] });
  if (device === 'arm') doc.environment.objects = clone(template.environment.objects) as EnvObject[];
  return doc;
}

// accept a pasted / imported skill: fill what's missing, keep everything else as is
export function normalizeSkill(raw: unknown): SkillDoc {
  if (!raw || typeof raw !== 'object') throw new Error('Not a skill: expected a JSON object.');
  const r = raw as Partial<SkillDoc>;
  if (!r.skill || !Array.isArray(r.steps)) throw new Error('Not a skill: it needs "skill" and "steps".');
  const other = foreignRobot(r);
  if (other) throw new Error(`This skill is for the robot ${other}; MOTIQ only runs skills for the UR5e arm.`);
  const device = deviceOf(r as SkillDoc);
  const base = makeSkill({
    id: r.skill.id || newSkillId(),
    name: r.skill.name ?? '',
    description: r.skill.description ?? '',
    device,
    icon: (r.app?.icon as Icon) ?? 'cube-outline',
    steps: [],
  });
  return {
    ...base,
    ...r,
    skill: { ...base.skill, ...r.skill },
    intent: { ...base.intent, ...(r.intent ?? {}) },
    robot: { ...(r.robot ?? base.robot), device },
    environment: { ...base.environment, ...(r.environment ?? {}), objects: r.environment?.objects ?? [] },
    constraints: {
      ...base.constraints,
      ...(r.constraints ?? {}),
      execution_constraints: {
        ...base.constraints.execution_constraints,
        ...(r.constraints?.execution_constraints ?? {}),
      },
    },
    steps: r.steps.map((s, i) => ({
      ...s,
      id: s.id || `step_${String(i + 1).padStart(3, '0')}`,
      action: s.action || 'SEQUENCE',
    })),
    safety: { ...base.safety, ...(r.safety ?? {}) },
    review: { ...base.review, ...(r.review ?? {}) },
    app: { ...base.app, ...(r.app ?? {}), builtin: false },
  };
}

// full self-contained file: robot profile and primitives included, like the template
export function exportSkill(doc: SkillDoc): SkillDoc {
  const device = deviceOf(doc);
  const { app, ...rest } = doc;
  return {
    ...rest,
    robot: { ...profiles[device].robot, ...doc.robot, device },
    action_primitives: primitivesOf(doc),
    app: { icon: app?.icon },
  };
}

// saving an edit is a new revision and drops any approval
export function touched(doc: SkillDoc): SkillDoc {
  return {
    ...doc,
    skill: { ...doc.skill, updated_at: now(), status: doc.skill.status === 'approved' ? 'draft' : doc.skill.status },
    review: { revision: (doc.review?.revision ?? 0) + 1, approved: false, approved_by: null, approval_timestamp: null },
    safety: { ...doc.safety, physical_execution_allowed: false },
  };
}

export function approved(doc: SkillDoc, by: string, physical: boolean): SkillDoc {
  return {
    ...doc,
    skill: { ...doc.skill, status: 'approved' },
    review: { ...doc.review, approved: true, approved_by: by, approval_timestamp: now() },
    safety: { ...doc.safety, physical_execution_allowed: physical },
  };
}

export function revoked(doc: SkillDoc): SkillDoc {
  return {
    ...doc,
    skill: { ...doc.skill, status: 'draft' },
    review: { ...doc.review, approved: false, approved_by: null, approval_timestamp: null },
    safety: { ...doc.safety, physical_execution_allowed: false },
  };
}

// ---------- built-ins ----------
// Only the 4 cell objects: one overview, locate each object, push the three pushable ones. Every other skill is
// recorded (Show Motion / Learn from camera) or made by Grok, never hard-coded here.

const seq = (id: string, description: string, calls: PrimitiveCall[]): SkillStep => ({
  id,
  action: 'SEQUENCE',
  description,
  primitive_sequence: calls,
  failure_policy: 'REPORT_FAILURE',
});

const armObjects = clone(template.environment.objects) as unknown as EnvObject[];
const objName = (id: string) => armObjects.find((o) => o.id === id)?.name ?? id.replace(/_/g, ' ');
const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

const overview = makeSkill({
  id: 'arm_overview',
  name: 'Overview: what is on the table',
  description:
    'The arm camera looks at the table from where the arm is now and reports which of the 4 objects it sees. The arm does not move.',
  device: 'arm',
  icon: 'eye-outline',
  builtin: true,
  objects: armObjects,
  success: ['Every object on the table is reported as seen or not in view.'],
  steps: [{ id: 'step_001', action: 'OVERVIEW', description: 'Look at the table, list the objects.', failure_policy: 'STOP_AND_REOBSERVE' }],
});

// Real arm: the vision server guides the arm over the object (sideways only, same height, 5 cm/s, never down), it
// holds, then goes back to where it started. The primitives are what the app's simulator runs.
const locate = (oid: string) =>
  makeSkill({
    id: `arm_locate_${oid}`,
    name: `Locate the ${objName(oid)}`,
    description: `The arm camera finds the ${objName(oid)} and the arm moves over it (sideways only, same height, slow), waits 2 s and goes back to where it started. STOP halts it.`,
    device: 'arm',
    icon: 'locate-outline',
    builtin: true,
    objects: armObjects,
    success: [`The arm went over the ${objName(oid)} and is back at its starting pose.`],
    steps: [
      {
        id: 'step_001',
        action: 'VISIT',
        target: oid,
        description: `Go over the ${objName(oid)}, wait, come back.`,
        parameters: { hold_s: 2, come_back: true },
        failure_policy: 'REPORT_FAILURE',
        primitive_sequence: [
          { primitive: 'MOVE_ABOVE', parameters: { target: oid, height_m: 0.15 } },
          { primitive: 'WAIT', parameters: { seconds: 2 } },
        ],
      },
    ],
  });

// Real arm: hover over the object like Locate, put the taught touching point over it, go down at 10 mm/s until the
// UR5e wrist force sensor feels contact (15 N, checked on the robot), lift and go back. Needs the touch offset taught
// once (Grok: "teach the touch offset"). The primitives are what the app's simulator runs.
export const touchSimSequence = (target: string): PrimitiveCall[] => [
  { primitive: 'MOVE_ABOVE', parameters: { target, height_m: 0.15 } },
  { primitive: 'MOVE_DOWN', parameters: { distance_m: 0.05, speed_mps: 0.05 } },
  { primitive: 'MOVE_UP', parameters: { distance_m: 0.05 } },
];

const touch = (oid: string) =>
  makeSkill({
    id: `arm_touch_${oid}`,
    name: `Touch the ${objName(oid)}`,
    description: `The arm camera finds the ${objName(oid)}, the arm goes over it, comes down slowly (10 mm/s) until its force sensor feels the ${objName(oid)}, lifts and goes back to where it started. STOP halts it. Needs the touch offset taught once.`,
    device: 'arm',
    icon: 'hand-left-outline',
    builtin: true,
    objects: armObjects,
    success: [`The arm touched the ${objName(oid)} and is back at its starting pose.`],
    steps: [
      {
        id: 'step_001',
        action: 'TOUCH',
        target: oid,
        description: `Go over the ${objName(oid)}, come down until it touches, lift, come back.`,
        failure_policy: 'REPORT_FAILURE',
        primitive_sequence: touchSimSequence(oid),
      },
    ],
  });

const PUSH_DIRECTION: Record<string, string> = {
  blue_bucket_01: 'sideways, away from the ring',
  orange_screw_03: 'towards the robot',
  white_bucket_lid_04: 'sideways, away from the screw',
};

// Simulation first: robot_controller runs it in RoboDK / URSim on the object's taught push path. The path is a
// placeholder for the demo station; teach it on the real table before running it on the physical arm.
const push = (oid: string) =>
  makeSkill({
    id: `arm_push_${oid}`,
    name: `Push the ${objName(oid)}`,
    description: `Real arm: the camera finds the ${objName(oid)}, the arm comes down beside it until it feels the table (force sensor), pushes it 5 cm ${PUSH_DIRECTION[oid]}, lifts and goes back (needs the touch offset taught once). SIM=1: RoboDK / URSim on the taught push path.`,
    device: 'arm',
    icon: 'arrow-forward-circle-outline',
    builtin: true,
    objects: armObjects,
    success: [`The ${objName(oid)} moved 5 cm ${PUSH_DIRECTION[oid]}.`, 'The arm is home.'],
    steps: [
      {
        id: 'step_001',
        action: 'PUSH',
        target: oid,
        description: `${cap(objName(oid))}: come down beside it, push 5 cm, lift.`,
        failure_policy: 'REPORT_FAILURE',
        primitive_sequence: pushSimSequence(oid),
      },
      seq('step_002', 'Back home.', [{ primitive: 'HOME' }]),
    ],
  });

// Simulation only (the real UR5e has no gripper): RoboDK / URSim via robot_controller. PICK reads the screw's real
// pose from the RoboDK station; PLACE blue_bucket_01 -> the bridge's blue_bucket_01_inside destination. The
// primitive_sequences are what the app's own simulator runs.
const screwInBucket: SkillDoc = (() => {
  const doc = makeSkill({
    id: 'arm_sim_screw_in_bucket',
    name: 'Put the orange screw in the blue bucket',
    description:
      'Simulation (RoboDK / URSim): the arm picks up the orange screw, drops it inside the blue bucket, checks it was released there and goes home. The real arm has no gripper, so this runs in RoboDK only.',
    device: 'arm',
    icon: 'cube-outline',
    builtin: true,
    objects: armObjects,
    success: ['The orange screw is inside the blue bucket.', 'The gripper is empty.', 'The arm is home.'],
    steps: [
      {
        id: 'step_001',
        action: 'PICK',
        target: 'orange_screw_03',
        description: 'Pick up the orange screw from the top.',
        parameters: { grasp_type: 'top_grasp', preserve_orientation: true },
        failure_policy: 'REPORT_FAILURE',
        primitive_sequence: [
          { primitive: 'OPEN_GRIPPER' },
          { primitive: 'MOVE_ABOVE', parameters: { target: 'orange_screw_03', height_m: 0.15 } },
          { primitive: 'MOVE_DOWN', parameters: { distance_m: 0.13, speed_mps: 0.05 } },
          { primitive: 'CLOSE_GRIPPER', parameters: { force_pct: 70 } },
          { primitive: 'MOVE_UP', parameters: { distance_m: 0.13 } },
        ],
      },
      {
        id: 'step_002',
        action: 'PLACE',
        target: 'blue_bucket_01',
        description: 'Drop the screw inside the blue bucket.',
        parameters: { placement_mode: 'inside_container', release_after_placement: true },
        failure_policy: 'REPORT_FAILURE',
        primitive_sequence: [
          { primitive: 'MOVE_ABOVE', parameters: { target: 'blue_bucket_01', height_m: 0.15 } },
          { primitive: 'MOVE_DOWN', parameters: { distance_m: 0.05, speed_mps: 0.05 } },
          { primitive: 'OPEN_GRIPPER' },
          { primitive: 'MOVE_UP', parameters: { distance_m: 0.05 } },
        ],
      },
      {
        id: 'step_003',
        action: 'VERIFY',
        target: 'orange_screw_03',
        description: 'Check the screw was released in the blue bucket.',
        parameters: { check_containment: true },
        success_conditions: ['The orange screw is inside the blue bucket.', 'The gripper is empty.'],
        failure_policy: 'REPORT_FAILURE',
      },
      seq('step_004', 'Back home.', [{ primitive: 'HOME' }]),
    ],
  });
  doc.constraints = {
    ...doc.constraints,
    object_constraints: [{ type: 'containment', object: 'orange_screw_03', container: 'blue_bucket_01' }],
  };
  return doc;
})();

export const builtinSkills: SkillDoc[] = [
  overview,
  ...CELL_OBJECTS.map(locate),
  ...CELL_OBJECTS.map(touch),
  ...PUSHABLE_OBJECTS.map(push),
  screwInBucket,
];

export { defaultOpts };
