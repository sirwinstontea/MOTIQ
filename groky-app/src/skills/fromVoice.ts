// A robot command the user gave by voice -> a saved, re-runnable skill.
// Built straight from the tool calls that actually ran (no extra Grok call), so the skill is exactly what the robot
// did. It lands in the Skills tab as a draft: tapping it runs it (simulated until an operator approves it for the
// real robot, like every other skill).
import type { Icon } from './index';
import { deviceOf, makeSkill, normalizeSkill, validateSkill } from './index';
import template from './templates/pick_place_ur5e.json';
import type { Device, EnvObject, PrimitiveCall, SkillDoc, SkillStep } from './schema';

export type VoiceCommand = {
  id: string;
  text: string;
  calls: { name: string; args: Record<string, any> }[];
  // the request ran or edited a saved skill (run_skill / edit_skill): it is about that skill, so nothing new is
  // saved from it (and a partial skill saved earlier in the same request is dropped)
  skip?: boolean;
};

const known = template.environment.objects as unknown as EnvObject[];
// the cell's 4 demo objects + the delivery zone (same ids as robot_controller's scene)
const DEST = known.find((o) => o.id === 'delivery_zone')!;
const PICKABLE = known.filter((o) => o.category !== 'container');
const TEMPLATE_OBJECT = 'orange_screw_03'; // the object the template's PICK step targets
// spoken words (English + Italian) -> object; checked in order, so "bucket lid" hits the lid before the bucket
const WORDS: [RegExp, string][] = [
  [/\b(lid|cap|tappo|coperchio|white|bianco)\b/, 'white_bucket_lid_04'],
  [/\b(screw|vite|bolt|orange|arancione)\b/, 'orange_screw_03'],
  [/\b(ring|anello|green|verde)\b/, 'green_ring_02'],
  [/\b(bucket|secchio|cup|blue|blu)\b/, 'blue_bucket_01'],
];
const tmplStep = (action: string) => template.steps.find((s) => s.action === action) as unknown as SkillStep;
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));

const slug = (s: string) =>
  s
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_|_$/g, '') || 'thing';

const title = (s: string) => {
  const t = s.trim().replace(/[.!?]+$/, '');
  const cut = t.length > 44 ? t.slice(0, 44).replace(/\s+\S*$/, '') + '…' : t;
  return cut.charAt(0).toUpperCase() + cut.slice(1);
};

// stable fingerprint of what was done, to avoid saving the same command twice
export const voiceSignature = (calls: VoiceCommand['calls']) =>
  calls.map((c) => `${c.name}:${JSON.stringify(c.args ?? {})}`).join('|').toLowerCase();

// robot tools that become steps (stop, vision_, camera_look and run_skill are not robot actions)
export const isRobotTool = (name: string) => /^arm_/.test(name) && name !== 'stop';

type Ctx = { objects: EnvObject[]; uncertain: string[] };

// free-text object / destination -> an object in the skill's environment (reuses the cell's known objects)
function resolve(ctx: Ctx, raw: unknown, kind: 'object' | 'dest'): string {
  const q = String(raw ?? '').trim().toLowerCase().replace(/^the\s+/, '');
  const base = kind === 'dest' ? DEST : PICKABLE.find((o) => o.id === TEMPLATE_OBJECT)!;
  const word = kind === 'object' ? WORDS.find(([re]) => re.test(q))?.[1] : undefined;
  const hit =
    known.find((o) => o.id === q || o.name.toLowerCase() === q) ??
    (kind === 'dest' && (!q || /\b(delivery|zone|pad|box|drop)\b/.test(q)) ? DEST : undefined) ??
    (word ? known.find((o) => o.id === word) : undefined);
  const use = (o: EnvObject) => {
    if (!ctx.objects.some((x) => x.id === o.id)) ctx.objects.push(clone(o));
    return o.id;
  };
  if (hit) return use(hit);
  // a thing the cell doesn't know (e.g. a vision track number or another name): keep it, flag it
  const id = `${kind === 'dest' ? 'dest' : 'object'}_${slug(q || 'item')}`;
  if (!ctx.objects.some((x) => x.id === id)) {
    const o = clone(base);
    o.id = id;
    o.name = q || id;
    ctx.objects.push(o);
    ctx.uncertain.push(`"${q || id}" isn't a known object in the cell: its position comes from the camera at run time.`);
  }
  return id;
}

const retarget = (step: SkillStep, from: string, to: string): PrimitiveCall[] =>
  clone(step.primitive_sequence ?? []).map((c) =>
    c.parameters?.target === from ? { ...c, parameters: { ...c.parameters, target: to } } : c,
  );

function armSteps(ctx: Ctx, name: string, a: Record<string, any>): Omit<SkillStep, 'id'>[] {
  const pick = (o: string): Omit<SkillStep, 'id'>[] => [
    { action: 'DETECT', target: o, description: `Locate the ${o.replace(/^object_/, '').replace(/_/g, ' ')}.`, failure_policy: 'STOP_AND_REOBSERVE' },
    { action: 'PICK', target: o, description: `Pick up the ${o.replace(/^object_/, '').replace(/_/g, ' ')}.`,
      primitive_sequence: retarget(tmplStep('PICK'), TEMPLATE_OBJECT, o), failure_policy: 'RETRY_WITH_REOBSERVATION' },
  ];
  const place = (d: string): Omit<SkillStep, 'id'>[] => [
    { action: 'PLACE', target: d, description: `Place it at the ${d.replace(/^dest_/, '').replace(/^object_/, '').replace(/_/g, ' ')}.`,
      primitive_sequence: retarget(tmplStep('PLACE'), 'delivery_zone', d), failure_policy: 'STOP_AND_REPLAN' },
  ];
  switch (name) {
    case 'pick_and_place': {
      const o = resolve(ctx, a.object ?? a.target, 'object');
      const d = resolve(ctx, a.destination, 'dest');
      if (Number(a.shake_cycles) > 0)
        ctx.uncertain.push(`The shake (${a.shake_cycles}x) done in the original command isn't a skill step, so it's left out.`);
      return [...pick(o), ...place(d)];
    }
    case 'pick':
      return pick(resolve(ctx, a.object ?? a.target, 'object'));
    case 'place':
      return place(resolve(ctx, a.destination, 'dest'));
    case 'shake':
      ctx.uncertain.push(`A shake (${a.cycles ?? 2}x) was done by voice, but shaking isn't a skill step, so it's left out.`);
      return [];
    case 'detect':
      return [{ action: 'DETECT', target: resolve(ctx, a.target, 'object'), description: 'Look for the object.',
        failure_policy: 'STOP_AND_REOBSERVE' }];
    case 'verify':
      return [{ action: 'VERIFY', description: String(a.expect ?? 'Check the result.'),
        success_conditions: a.expect ? [String(a.expect)] : [], failure_policy: 'REPORT_FAILURE' }];
    case 'move_above': {
      const t = resolve(ctx, a.target, /box|dest|zone/i.test(String(a.target)) ? 'dest' : 'object');
      return [{ action: 'SEQUENCE', description: `Move above the ${String(a.target ?? t)}.`,
        primitive_sequence: [{ primitive: 'MOVE_ABOVE', parameters: { target: t, ...(a.height_m ? { height_m: a.height_m } : {}) } }] }];
    }
    default: {
      const prim = name.toUpperCase();
      const params = { ...a };
      if (typeof params.target === 'string') params.target = resolve(ctx, params.target, 'object');
      return [{ action: 'SEQUENCE', description: name.replace(/_/g, ' '),
        primitive_sequence: [{ primitive: prim, ...(Object.keys(params).length ? { parameters: params } : {}) }] }];
    }
  }
}

// null when nothing reusable ran, or the result would not validate
export function skillFromVoice(cmd: VoiceCommand, skillId: string): SkillDoc | null {
  const robot = cmd.calls.filter((c) => isRobotTool(c.name));
  if (!robot.length) return null;
  const device: Device = 'arm';
  const mine = robot;

  const ctx: Ctx = { objects: [], uncertain: [] };
  const raw: Omit<SkillStep, 'id'>[] = mine.flatMap((c) => {
    const n = c.name.slice(device.length + 1);
    return armSteps(ctx, n, c.args ?? {});
  });
  if (!raw.length) return null;
  const steps: SkillStep[] = raw.map((s, i) => ({ id: `step_${String(i + 1).padStart(3, '0')}`, ...s }));

  const text = cmd.text.trim();
  const doc = makeSkill({
    id: skillId,
    name: title(text),
    description: text,
    device,
    icon: (device === 'arm' ? 'mic-outline' : 'walk-outline') as Icon,
    steps,
    goal: text,
    objects: device === 'arm' ? ctx.objects : [],
  });
  doc.skill.source = { type: 'voice', prompt: text, signature: voiceSignature(mine) };
  doc.intent.natural_language_instruction = text;
  doc.interpretation = {
    status: 'draft',
    human_review_required: true,
    uncertainties: ctx.uncertain,
  };
  const out = normalizeSkill(doc);
  return validateSkill(out).errors.length || deviceOf(out) !== device ? null : out;
}
