import { Ionicons } from '@expo/vector-icons';
import { DeviceId, devices } from '../devices';

export type Icon = keyof typeof Ionicons.glyphMap;

// tool is the full grok name ("arm_grip") so steps run through runTool unchanged,
// or the pseudo tool "wait" ({ seconds })
export type Step = { tool: string; args: Record<string, unknown> };

export type Skill = {
  id: string;
  name: string;
  description: string;
  device: DeviceId;
  icon: Icon;
  steps: Step[];
  builtin?: boolean;
};

export const WAIT = 'wait';

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

export const builtinSkills: Skill[] = [
  {
    id: 'pick_box',
    name: 'Pick & box',
    description: 'Pick up the red cube, drop it in the box and verify with the camera.',
    device: 'arm',
    icon: 'cube-outline',
    builtin: true,
    steps: [
      { tool: 'arm_grip', args: { state: 'open' } },
      { tool: 'arm_pick_place', args: { object: 'red cube', place: 'box' } },
      { tool: 'arm_check', args: { expect: 'red cube is in the box' } },
      { tool: 'arm_goto', args: { place: 'home' } },
    ],
  },
  {
    id: 'handover',
    name: 'Hand it over',
    description: 'Bring what the arm is holding to the handover spot and let go.',
    device: 'arm',
    icon: 'hand-right-outline',
    builtin: true,
    steps: [
      { tool: 'arm_goto', args: { place: 'handover' } },
      { tool: WAIT, args: { seconds: 2 } },
      { tool: 'arm_grip', args: { state: 'open' } },
      { tool: 'arm_goto', args: { place: 'home' } },
    ],
  },
  {
    id: 'arm_reset',
    name: 'Reset arm',
    description: 'Open the gripper and park the arm at home.',
    device: 'arm',
    icon: 'refresh-outline',
    builtin: true,
    steps: [
      { tool: 'arm_grip', args: { state: 'open' } },
      { tool: 'arm_goto', args: { place: 'home' } },
    ],
  },
  {
    id: 'greet',
    name: 'Greet visitors',
    description: 'Stand up, wave and take a look at who is there.',
    device: 'dog',
    icon: 'happy-outline',
    builtin: true,
    steps: [
      { tool: 'dog_pose', args: { pose: 'stand' } },
      { tool: 'dog_pose', args: { pose: 'wave' } },
      { tool: 'dog_look', args: {} },
    ],
  },
  {
    id: 'patrol',
    name: 'Short patrol',
    description: 'Walk a meter out and back, turning around at each end.',
    device: 'dog',
    icon: 'walk-outline',
    builtin: true,
    steps: [
      { tool: 'dog_walk', args: { meters: 1 } },
      { tool: 'dog_turn', args: { degrees: 180 } },
      { tool: 'dog_walk', args: { meters: 1 } },
      { tool: 'dog_turn', args: { degrees: 180 } },
    ],
  },
  {
    id: 'rest',
    name: 'Rest',
    description: 'Sit down, then lie down.',
    device: 'dog',
    icon: 'bed-outline',
    builtin: true,
    steps: [
      { tool: 'dog_pose', args: { pose: 'sit' } },
      { tool: 'dog_pose', args: { pose: 'lie' } },
    ],
  },
];

export type Field = {
  key: string;
  type: 'number' | 'string';
  options?: string[];
  min?: number;
  max?: number;
  required: boolean;
  hint?: string;
};

const waitFields: Field[] = [{ key: 'seconds', type: 'number', min: 0, max: 30, required: true }];

// arg fields for a step, read from the tool's JSON schema
export function fieldsFor(tool: string): Field[] {
  if (tool === WAIT) return waitFields;
  const t = toolDef(tool);
  if (!t) return [];
  const p = t.parameters as { properties?: Record<string, any>; required?: string[] };
  return Object.entries(p.properties ?? {}).map(([key, s]) => ({
    key,
    type: s.type === 'number' ? 'number' : 'string',
    options: s.enum,
    min: s.minimum,
    max: s.maximum,
    required: !!p.required?.includes(key),
    hint: s.description,
  }));
}

export function toolDef(tool: string) {
  const i = tool.indexOf('_');
  const id = tool.slice(0, i) as DeviceId;
  return devices[id]?.tools.find((t) => t.name === tool.slice(i + 1));
}

export function stepLabel(step: Step): string {
  const name = step.tool === WAIT ? 'wait' : step.tool.slice(step.tool.indexOf('_') + 1).replace(/_/g, ' ');
  const vals = Object.values(step.args).filter((v) => v !== '' && v != null);
  if (step.tool === WAIT) return `wait ${step.args.seconds}s`;
  return vals.length ? `${name} · ${vals.join(' · ')}` : name;
}

// null if fine, otherwise what's wrong
export function validate(skill: Pick<Skill, 'name' | 'steps'>): string | null {
  if (!skill.name.trim()) return 'Give the skill a name.';
  if (!skill.steps.length) return 'Add at least one step.';
  for (const [i, step] of skill.steps.entries()) {
    for (const f of fieldsFor(step.tool)) {
      const v = step.args[f.key];
      if (f.required && (v === undefined || v === '')) return `Step ${i + 1}: ${f.key} is required.`;
      if (f.type === 'number' && v !== undefined && v !== '') {
        const n = Number(v);
        if (Number.isNaN(n)) return `Step ${i + 1}: ${f.key} must be a number.`;
        if ((f.min != null && n < f.min) || (f.max != null && n > f.max))
          return `Step ${i + 1}: ${f.key} must be between ${f.min} and ${f.max}.`;
      }
    }
  }
  return null;
}
