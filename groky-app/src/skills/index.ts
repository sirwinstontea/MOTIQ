// Skills in the MOTIQ skill format (see schema.ts and templates/pick_place_ur5e.json).
import { Ionicons } from '@expo/vector-icons';
import env from '../lib/config';
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

export const STEP_ACTIONS = ['DETECT', 'PICK', 'PLACE', 'VERIFY', 'SEQUENCE'] as const;
export const FAILURE_POLICIES = ['STOP_AND_REOBSERVE', 'RETRY_WITH_REOBSERVATION', 'STOP_AND_REPLAN', 'REPORT_FAILURE'];
// steps that look instead of moving
export const isLookStep = (s: SkillStep) => s.action === 'DETECT' || s.action === 'VERIFY';

// ---------- reading a skill ----------

export function deviceOf(doc: Pick<SkillDoc, 'robot'>): Device {
  if (doc.robot?.device === 'arm' || doc.robot?.device === 'dog') return doc.robot.device;
  return /dog/i.test(doc.robot?.model ?? '') ? 'dog' : 'arm';
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

// The real arm goes through robot_controller (../bridge), which only moves between taught positions:
// PICK / PLACE steps with a target become one controller task each; inside SEQUENCE steps only these primitives
// reach it. Everything else (MOVE_UP, ROTATE_*, MOVE_TO_POSE, ...) runs in the simulator only.
export const REAL_ARM_PRIMITIVES = ['MOVE_ABOVE', 'HOME', 'OPEN_GRIPPER', 'WAIT', 'STOP'];
export const realArmConnected = () => !!env.armUrl;

// steps the real arm can't run, as readable messages (empty = fine)
export function realArmProblems(doc: SkillDoc): string[] {
  if (deviceOf(doc) !== 'arm') return [];
  const out: string[] = [];
  doc.steps.forEach((s, i) => {
    if (isLookStep(s)) return;
    if ((s.action === 'PICK' || s.action === 'PLACE') && s.target) return;
    const bad = [
      ...new Set((s.primitive_sequence ?? []).map((c) => c.primitive).filter((p) => !REAL_ARM_PRIMITIVES.includes(p))),
    ];
    if (bad.length)
      out.push(
        `Step ${i + 1}: ${bad.map((p) => p.toLowerCase().replace(/_/g, ' ')).join(', ')} only works in the simulator. ` +
          'The real arm can only pick, place, move above an object, open the gripper, wait and go home.',
      );
  });
  return out;
}

// ---------- safety gates ----------

// why the skill may not move a real robot, or null
export function physicalBlock(doc: SkillDoc, real: boolean): string | null {
  if (!real) return null;
  const arm = realArmProblems(doc);
  if (arm.length) return `${arm[0]} Edit the skill (or ask Grok to change it) to run it on the arm.`;
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
    if (!isLookStep(s) && !s.primitive_sequence?.length) errors.push(`${at}: add at least one primitive.`);
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
  const device = deviceOf(r as SkillDoc);
  const base = makeSkill({
    id: r.skill.id || newSkillId(),
    name: r.skill.name ?? '',
    description: r.skill.description ?? '',
    device,
    icon: (r.app?.icon as Icon) ?? (device === 'arm' ? 'cube-outline' : 'walk-outline'),
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

// ---------- old format (skills-v1) ----------

type V1 = {
  id: string;
  name: string;
  description: string;
  device: Device;
  icon: string;
  steps: { tool: string; args: Record<string, unknown> }[];
};

export function fromV1(old: V1): SkillDoc {
  const calls: PrimitiveCall[] = [];
  let dropped = 0;
  for (const st of old.steps) {
    const name = st.tool.slice(st.tool.indexOf('_') + 1);
    if (st.tool === 'wait') calls.push({ primitive: 'WAIT', parameters: { seconds: st.args.seconds } });
    else if (old.device === 'dog') calls.push({ primitive: name.toUpperCase(), parameters: st.args });
    else if (name === 'grip') calls.push({ primitive: st.args.state === 'close' ? 'CLOSE_GRIPPER' : 'OPEN_GRIPPER' });
    else if (name === 'goto' && st.args.place === 'home') calls.push({ primitive: 'HOME' });
    else dropped++;
  }
  const doc = makeSkill({
    id: old.id,
    name: old.name,
    description: old.description,
    device: old.device,
    icon: old.icon as Icon,
    steps: calls.length
      ? [{ id: 'step_001', action: 'SEQUENCE', primitive_sequence: calls, failure_policy: 'REPORT_FAILURE' }]
      : [],
  });
  if (dropped)
    doc.interpretation = {
      uncertainties: [`${dropped} step(s) from the old format had no UR5e primitive and were dropped.`],
    };
  return doc;
}

// ---------- built-ins ----------

const seq = (id: string, description: string, calls: PrimitiveCall[]): SkillStep => ({
  id,
  action: 'SEQUENCE',
  description,
  primitive_sequence: calls,
  failure_policy: 'REPORT_FAILURE',
});

const pickPlace: SkillDoc = {
  ...(clone(template) as unknown as SkillDoc),
  robot: { ...(template.robot as unknown as SkillDoc['robot']), device: 'arm' },
  app: { icon: 'cube-outline', builtin: true },
};
delete pickPlace.action_primitives; // same as the profile

// Skills that run on the real arm through robot_controller (../bridge). PICK / PLACE steps become one controller
// task each when the arm is connected; their primitive_sequence is what the simulator runs.
const templateSteps = template.steps as unknown as SkillStep[];
const armObjects = clone(template.environment.objects) as unknown as EnvObject[];
const simSeq = (stepId: string) => clone(templateSteps.find((s) => s.id === stepId)?.primitive_sequence ?? []);

const controllerSkills: SkillDoc[] = [
  makeSkill({
    id: 'arm_visit_cube',
    name: 'Visit the cube',
    description: 'Move above the red cube, pause, and go back home. No camera needed: a safe first test.',
    device: 'arm',
    icon: 'search-outline',
    builtin: true,
    objects: armObjects,
    steps: [
      seq('step_001', 'Go above the cube and pause.', [
        { primitive: 'MOVE_ABOVE', parameters: { target: 'object_cube' } },
        { primitive: 'WAIT', parameters: { seconds: 1 } },
      ]),
      seq('step_002', 'Back home.', [{ primitive: 'HOME' }]),
    ],
  }),
  makeSkill({
    id: 'arm_controller_pick_place',
    name: 'Pick & place (arm controller)',
    description:
      'The camera confirms the object, the arm picks it, places it in the delivery zone and goes home. Each step is one checked controller task.',
    device: 'arm',
    icon: 'gift-outline',
    builtin: true,
    objects: armObjects,
    success: ['The object is in the delivery zone.', 'The gripper is open and the arm is home.'],
    steps: [
      {
        id: 'step_001',
        action: 'PICK',
        target: 'object_cube',
        description: 'Find it with the camera and pick it up (grip verified).',
        primitive_sequence: simSeq('step_002'),
        failure_policy: 'REPORT_FAILURE',
      },
      {
        id: 'step_002',
        action: 'PLACE',
        target: 'object_box',
        description: 'Place it in the delivery zone and release.',
        primitive_sequence: simSeq('step_003'),
        failure_policy: 'REPORT_FAILURE',
      },
      seq('step_003', 'Back home.', [{ primitive: 'HOME' }]),
    ],
  }),
];

export const builtinSkills: SkillDoc[] = [
  ...controllerSkills,
  pickPlace,
  makeSkill({
    id: 'handover',
    name: 'Hand it over',
    description: 'Bring what the arm is holding to the handover spot, wait, let go and go home.',
    device: 'arm',
    icon: 'hand-right-outline',
    builtin: true,
    steps: [
      seq('step_001', 'Carry it to the handover spot at a safe height.', [
        { primitive: 'MOVE_TO_POSE', parameters: { x: 0.3, y: -0.4, z: 0.3, speed_mps: 0.15 } },
        { primitive: 'WAIT', parameters: { seconds: 2 } },
        { primitive: 'OPEN_GRIPPER' },
      ]),
      seq('step_002', 'Back home.', [{ primitive: 'HOME' }]),
    ],
  }),
  makeSkill({
    id: 'arm_reset',
    name: 'Reset arm',
    description: 'Open the gripper and park the arm at home.',
    device: 'arm',
    icon: 'refresh-outline',
    builtin: true,
    steps: [seq('step_001', 'Open and go home.', [{ primitive: 'OPEN_GRIPPER' }, { primitive: 'HOME' }])],
  }),
  makeSkill({
    id: 'greet',
    name: 'Greet visitors',
    description: 'Stand up, wave and take a look at who is there.',
    device: 'dog',
    icon: 'happy-outline',
    builtin: true,
    steps: [
      seq('step_001', 'Stand and wave.', [
        { primitive: 'POSE', parameters: { pose: 'stand' } },
        { primitive: 'POSE', parameters: { pose: 'wave' } },
        { primitive: 'LOOK' },
      ]),
    ],
  }),
  makeSkill({
    id: 'patrol',
    name: 'Short patrol',
    description: 'Walk a meter out and back, turning around at each end.',
    device: 'dog',
    icon: 'walk-outline',
    builtin: true,
    steps: [
      seq('step_001', 'Out and back.', [
        { primitive: 'WALK', parameters: { meters: 1 } },
        { primitive: 'TURN', parameters: { degrees: 180 } },
        { primitive: 'WALK', parameters: { meters: 1 } },
        { primitive: 'TURN', parameters: { degrees: 180 } },
      ]),
    ],
  }),
  makeSkill({
    id: 'rest',
    name: 'Rest',
    description: 'Sit down, then lie down.',
    device: 'dog',
    icon: 'bed-outline',
    builtin: true,
    steps: [
      seq('step_001', 'Sit, then lie down.', [
        { primitive: 'POSE', parameters: { pose: 'sit' } },
        { primitive: 'POSE', parameters: { pose: 'lie' } },
      ]),
    ],
  }),
];

export { defaultOpts };
