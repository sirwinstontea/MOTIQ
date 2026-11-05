// Prompt -> skill draft with Grok. Grok only writes what changes per task (name, objects, steps, ...);
// makeSkill() fills every default (safety, constraints, robot profile, review). The draft is never saved or
// approved here: it opens in the editor for the user to check, preview and save.
import env from '../lib/config';
import { profiles } from '../robot/profiles';
import { dryRun } from './dryrun';
import {
  deviceOf,
  makeSkill,
  newSkillId,
  realArmConnected,
  CELL_DESTINATIONS,
  CELL_OBJECTS,
  realArmProblems,
  REAL_ARM_PRIMITIVES,
  skillIcons,
  validateSkill,
  type Icon,
} from './index';
import template from './templates/pick_place_ur5e.json';
import type { Device, EnvObject, PrimitiveCall, SkillDoc, SkillStep } from './schema';

const MODEL = process.env.EXPO_PUBLIC_GROK_SKILL_MODEL || env.visionModel;

// what Grok returns
type Draft = {
  device: Device;
  name: string;
  description: string;
  goal: string;
  success_conditions: string[];
  icon: string;
  objects: {
    id: string;
    name: string;
    category: string;
    color?: string;
    x?: number;
    y?: number;
    z?: number;
    size_m?: number;
  }[];
  steps: {
    action: string;
    target?: string;
    description: string;
    primitive_sequence?: PrimitiveCall[];
    failure_policy?: string;
  }[];
  uncertainties: string[];
  confidence: number;
};

const SCHEMA = {
  type: 'object',
  properties: {
    device: { type: 'string', enum: ['arm', 'dog'] },
    name: { type: 'string' },
    description: { type: 'string' },
    goal: { type: 'string' },
    success_conditions: { type: 'array', items: { type: 'string' } },
    icon: { type: 'string', enum: skillIcons },
    objects: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          name: { type: 'string' },
          category: { type: 'string' },
          color: { type: 'string' },
          x: { type: 'number' },
          y: { type: 'number' },
          z: { type: 'number' },
          size_m: { type: 'number' },
        },
        required: ['id', 'name', 'category'],
      },
    },
    steps: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          action: { type: 'string', enum: ['DETECT', 'PICK', 'PLACE', 'VERIFY', 'SEQUENCE'] },
          target: { type: 'string' },
          description: { type: 'string' },
          primitive_sequence: {
            type: 'array',
            items: {
              type: 'object',
              properties: { primitive: { type: 'string' }, parameters: { type: 'object' } },
              required: ['primitive'],
            },
          },
          failure_policy: {
            type: 'string',
            enum: ['STOP_AND_REOBSERVE', 'RETRY_WITH_REOBSERVATION', 'STOP_AND_REPLAN', 'REPORT_FAILURE'],
          },
        },
        required: ['action', 'description'],
      },
    },
    uncertainties: { type: 'array', items: { type: 'string' } },
    confidence: { type: 'number' },
  },
  required: [
    'device',
    'name',
    'description',
    'goal',
    'success_conditions',
    'icon',
    'objects',
    'steps',
    'uncertainties',
    'confidence',
  ],
};

function primitiveList(device: Device): string {
  return profiles[device].primitives
    .filter((p) => p.id !== 'STOP')
    .map((p) => {
      const params = Object.entries(p.parameters)
        .map(([k, v]) => {
          const bits = [v.type === 'enum' ? `one of ${v.values?.join('|')}` : v.type];
          if (v.default !== undefined) bits.push(`default ${JSON.stringify(v.default)}`);
          if (v.min !== undefined) bits.push(`min ${v.min}`);
          if (v.max !== undefined) bits.push(`max ${v.max}`);
          if (v.optional) bits.push('optional');
          return `${k}: ${bits.join(', ')}`;
        })
        .join('; ');
      return `- ${p.id}${params ? ` (${params})` : ''}: ${p.description}`;
    })
    .join('\n');
}

function systemPrompt(): string {
  const lim = profiles.arm.robot.operational_limits!;
  const pick = (template.steps as unknown as SkillStep[]).find((s) => s.action === 'PICK');
  const place = (template.steps as unknown as SkillStep[]).find((s) => s.action === 'PLACE');
  return `You turn a user's plain-language task into a robot skill draft (JSON matching the given schema).
Write ONLY the task-specific parts. Safety, constraints, robot specs and approval are filled in by code with safe defaults.

Robots:
- "arm": UR5e collaborative arm with a gripper, on a table. Units: metres, base frame (x forward, y left, z up). Table at z = 0.
- "dog": small robot dog on the floor. Pick "dog" only if the user clearly means the dog (walk, sit, patrol, ...).

Arm primitives:
${primitiveList('arm')}

Dog primitives:
${primitiveList('dog')}

Arm limits: max single step ${lim.max_step_m} m, max TCP speed ${lim.max_tcp_speed_mps} m/s, never below z = ${lim.min_z_above_table_m} m,
safe travel height ${lim.safe_travel_height_m} m, max rotation step ${lim.max_rotation_step_deg} deg.

Objects already in the arm's cell (reuse these ids when the user means them):
${JSON.stringify(template.environment.objects)}

Rules:
1. Leave out any parameter that should use its default. Only write parameters the task needs to change.
2. Use only the primitives listed for the chosen robot, with values inside the limits. Long moves = several steps.
3. Arm pick/place follows this pattern (copy it, adjust targets and distances to the object size):
   PICK:  ${JSON.stringify(pick?.primitive_sequence)}
   PLACE: ${JSON.stringify(place?.primitive_sequence)}
   PICK and PLACE steps need a "target" object id. Put a DETECT step (target = the object) before the first PICK.
   Open the gripper before descending, lift before moving sideways, finish with a SEQUENCE step containing HOME.
4. DETECT and VERIFY steps have no primitive_sequence. Dog skills use only SEQUENCE steps and no objects.
5. Never invent facts. If a position, size or object isn't given and isn't one of the known objects, use a plausible
   placeholder inside the reachable table area (x 0.25-0.6, y -0.3-0.3) AND add a line to "uncertainties" saying so.
   Anything else ambiguous also goes into "uncertainties".
6. Object ids look like "object_<word>". Step descriptions are one short sentence. The name is short (max 6 words).
7. "confidence" is 0-1: how sure you are the draft matches what the user wants.
8. If a current draft is given, apply the user's change to it and keep everything else the same.${
    realArmConnected() ? REAL_ARM_RULES : ''
  }`;
}

// the real arm (robot_controller via the bridge) only moves between taught positions
const REAL_ARM_RULES = `

THE REAL ARM IS CONNECTED. Arm skills must run on it, so for the arm:
- It only knows these objects (its camera, mounted on the arm head, searches the table for them): ${CELL_OBJECTS.join(', ')}.
  Map whatever the person used or said to the closest one (a screw/bolt -> orange_screw_03, a cup/bucket/pot ->
  blue_bucket_01, a ring/hoop -> green_ring_02, a lid/cap/disc -> white_bucket_lid_04) and say so in "uncertainties".
- PICK targets: one of those object ids. PLACE targets: exactly one of ${CELL_DESTINATIONS.join(', ')}
  (blue_bucket_01 = drop it INTO the blue bucket; <object>_slot = put it back where it was; delivery_zone = the pad).
- DETECT before each PICK makes the arm look around with its camera until it sees the object. End with VERIFY
  (target = the object) after the last PLACE, then a SEQUENCE with HOME.
- Allowed steps: DETECT, VERIFY, PICK (with target), PLACE (with target), and SEQUENCE steps that use ONLY
  these primitives: ${REAL_ARM_PRIMITIVES.filter((p) => p !== 'STOP').join(', ')}.
- PICK and PLACE run as one controller task each (the controller does the approach, grasp and release itself),
  so keep their primitive_sequence as in the pattern above; that sequence is only used by the simulator.
- NOT allowed anywhere else: MOVE_UP, MOVE_DOWN, MOVE_LEFT, MOVE_RIGHT, MOVE_FORWARD, MOVE_BACKWARD, ROTATE_*,
  MOVE_JOINT, MOVE_JOINTS, MOVE_TO_POSE, CLOSE_GRIPPER. The controller refuses them.
- If the user asks for a motion that needs them (wave, spin, nod, move 10 cm, ...), build the closest version from
  the allowed moves (e.g. "wave" = MOVE_ABOVE one object, WAIT, MOVE_ABOVE another object, WAIT, HOME) and say in
  "uncertainties" what was changed and why.`;

function outputText(j: any): string {
  if (typeof j.output_text === 'string') return j.output_text;
  for (const o of j.output ?? [])
    for (const c of o.content ?? []) if (c.type === 'output_text' && c.text) return c.text;
  return j.choices?.[0]?.message?.content ?? '';
}

function parseJson(text: string): any {
  const t = text
    .trim()
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/```$/, '');
  try {
    return JSON.parse(t);
  } catch {
    const a = t.indexOf('{');
    const b = t.lastIndexOf('}');
    if (a >= 0 && b > a) return JSON.parse(t.slice(a, b + 1));
    throw new Error('Grok did not return JSON.');
  }
}

async function ask(messages: { role: string; content: string }[]): Promise<Draft> {
  if (!env.xaiKey) throw new Error('Generating skills needs EXPO_PUBLIC_XAI_API_KEY in .env');
  const r = await fetch('https://api.x.ai/v1/responses', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${env.xaiKey}` },
    body: JSON.stringify({
      model: MODEL,
      store: false,
      input: [{ role: 'system', content: systemPrompt() }, ...messages],
      text: { format: { type: 'json_schema', name: 'skill_draft', schema: SCHEMA } },
    }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error?.message ?? j.error ?? `Grok ${r.status}`);
  return parseJson(outputText(j)) as Draft;
}

// the parts of an existing skill Grok needs to refine it
function compact(doc: SkillDoc) {
  return {
    device: deviceOf(doc),
    name: doc.skill.name,
    description: doc.skill.description,
    goal: doc.intent.goal,
    success_conditions: doc.intent.success_conditions,
    icon: doc.app?.icon,
    objects: doc.environment.objects.map((o) => ({
      id: o.id,
      name: o.name,
      category: o.category,
      color: o.attributes?.color,
      ...o.initial_state?.location,
      size_m: o.attributes?.size_m,
    })),
    steps: doc.steps.map(({ action, target, description, primitive_sequence, failure_policy }) => ({
      action,
      target,
      description,
      primitive_sequence,
      failure_policy,
    })),
  };
}

const POLICY: Record<string, string> = {
  DETECT: 'STOP_AND_REOBSERVE',
  PICK: 'RETRY_WITH_REOBSERVATION',
  PLACE: 'STOP_AND_REPLAN',
};

function build(d: Draft, prompt: string, id: string, keep?: SkillDoc): SkillDoc {
  const device: Device = d.device === 'dog' ? 'dog' : 'arm';
  const known = template.environment.objects as unknown as EnvObject[];
  const objects: EnvObject[] =
    device === 'dog'
      ? []
      : (d.objects ?? []).map((o) => {
          const base = known.find((k) => k.id === o.id);
          const container = o.category === 'container';
          return {
            ...(base ? JSON.parse(JSON.stringify(base)) : {}),
            id: o.id,
            name: o.name,
            category: o.category,
            attributes: {
              ...(base?.attributes ?? {}),
              ...(o.color ? { color: o.color } : {}),
              ...(o.size_m ? { size_m: o.size_m } : {}),
            },
            initial_state: {
              ...(base?.initial_state ?? {}),
              location: {
                x: o.x ?? base?.initial_state?.location?.x ?? 0.4,
                y: o.y ?? base?.initial_state?.location?.y ?? 0,
                z: o.z ?? base?.initial_state?.location?.z ?? (container ? 0 : 0.02),
              },
              ...(container ? {} : { orientation: { type: 'upright' } }),
            },
            detection: base?.detection ?? {
              required: true,
              class: o.category,
              ...(container ? { reidentify_before_place: true } : { reidentify_before_pick: true }),
            },
          };
        });

  const steps: SkillStep[] = (d.steps ?? []).map((s, i) => {
    const look = s.action === 'DETECT' || s.action === 'VERIFY';
    return {
      id: `step_${String(i + 1).padStart(3, '0')}`,
      action: device === 'dog' ? 'SEQUENCE' : s.action || 'SEQUENCE',
      ...(s.target && device === 'arm' ? { target: s.target } : {}),
      description: s.description,
      ...(look ? {} : { primitive_sequence: s.primitive_sequence ?? [] }),
      failure_policy: s.failure_policy || POLICY[s.action] || 'REPORT_FAILURE',
    };
  });

  // Grok sometimes uses a known cell object (e.g. as a MOVE_ABOVE waypoint) without listing it: add it
  if (device === 'arm') {
    const used = new Set<string>();
    for (const s of steps) {
      if (s.target) used.add(s.target);
      for (const c of s.primitive_sequence ?? [])
        if (typeof c.parameters?.target === 'string') used.add(c.parameters.target);
    }
    for (const oid of used) {
      const k = known.find((x) => x.id === oid);
      if (k && !objects.some((o) => o.id === oid)) objects.push(JSON.parse(JSON.stringify(k)));
      // "<object>_slot" = put it back where that object was: a flat spot at the object's taught place, so the
      // simulator can place there too (the real arm uses the controller destination of the same name)
      const base = oid.endsWith('_slot') ? known.find((x) => x.id === oid.slice(0, -5)) : undefined;
      if (base && !objects.some((o) => o.id === oid))
        objects.push({
          id: oid,
          name: `${base.name} spot`,
          category: 'container',
          attributes: { size_m: 0.16 },
          initial_state: { location: { ...(base.initial_state?.location ?? { x: 0.4, y: 0, z: 0 }), z: 0 } },
          detection: { required: false },
        });
    }
  }

  const doc = makeSkill({
    id,
    name: d.name,
    description: d.description,
    device,
    icon: (skillIcons.includes(d.icon as Icon) ? d.icon : device === 'arm' ? 'cube-outline' : 'walk-outline') as Icon,
    steps,
    goal: d.goal,
    success: d.success_conditions,
    objects,
  });
  doc.skill.source = { type: 'grok_prompt', prompt };
  doc.intent.natural_language_instruction = prompt;
  doc.interpretation = {
    model: MODEL,
    status: 'draft',
    confidence: typeof d.confidence === 'number' ? d.confidence : undefined,
    human_review_required: true,
    uncertainties: d.uncertainties ?? [],
  };
  if (keep?.steps.length) {
    // an edit of an existing skill: same skill, same origin. The change request ("make it slower") is not what the
    // skill does, so the instruction becomes Grok's updated description instead.
    doc.skill.created_at = keep.skill.created_at;
    if (keep.skill.source) doc.skill.source = keep.skill.source;
    doc.intent.natural_language_instruction = d.description || keep.intent.natural_language_instruction;
  } else if (keep) doc.skill.created_at = keep.skill.created_at;
  return doc;
}

function problems(doc: SkillDoc): string[] {
  const v = validateSkill(doc);
  if (v.errors.length) return v.errors;
  const run = dryRun(doc);
  if (run.error) return [`Dry run fails: ${run.error}`];
  return realArmConnected() ? realArmProblems(doc) : [];
}

export type Generated = { doc: SkillDoc; problems: string[]; repaired: boolean };

// current = the editor's draft; if it already has steps, Grok refines it instead of starting over
export async function generateSkill(prompt: string, current?: SkillDoc): Promise<Generated> {
  const refine = !!current?.steps.length;
  const id = current?.skill.id || newSkillId();
  const user = refine
    ? `Current draft:\n${JSON.stringify(compact(current!))}\n\nChange requested: ${prompt}`
    : `Task: ${prompt}`;
  const messages = [{ role: 'user', content: user }];

  let draft = await ask(messages);
  let doc = build(draft, prompt, id, current);
  let left = problems(doc);
  let repaired = false;

  // one repair round with the exact errors
  if (left.length) {
    draft = await ask([
      ...messages,
      { role: 'assistant', content: JSON.stringify(draft) },
      {
        role: 'user',
        content: `That draft has these problems. Fix them and return the full draft:\n- ${left.join('\n- ')}`,
      },
    ]);
    doc = build(draft, prompt, id, current);
    left = problems(doc);
    repaired = true;
  }
  return { doc, problems: left, repaired };
}
