import { Ionicons } from '@expo/vector-icons';
import { DeviceId, devices } from '../devices';
import { WAIT } from './builtin';

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

export { builtinSkills, WAIT } from './builtin';

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
