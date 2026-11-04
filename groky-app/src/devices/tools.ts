import env from '../lib/config';
import { ARM_PRIMITIVES, DOG_PRIMITIVES, isRequired, limitFor } from '../robot/profiles';
import type { Primitive } from '../skills/schema';

export type DeviceId = 'dog' | 'arm';

type Tool = {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
};

const params = (props: Record<string, unknown>, required: string[] = []) => ({
  type: 'object',
  properties: props,
  required,
});

// primitive spec -> JSON schema for Grok, with the operational limits folded into min/max
function schemaFor(p: Primitive, device: DeviceId) {
  const props: Record<string, unknown> = {};
  const required: string[] = [];
  for (const [key, spec] of Object.entries(p.parameters)) {
    const lim = device === 'arm' ? limitFor(key) : {};
    const min = Math.max(spec.min ?? -Infinity, lim.min ?? -Infinity);
    const max = Math.min(spec.max ?? Infinity, lim.max ?? Infinity);
    const desc = [spec.note, spec.default !== undefined && `default ${JSON.stringify(spec.default)}`]
      .filter(Boolean)
      .join('; ');
    if (spec.type === 'number')
      props[key] = {
        type: 'number',
        ...(Number.isFinite(min) && { minimum: min }),
        ...(Number.isFinite(max) && { maximum: max }),
        ...(desc && { description: desc }),
      };
    else if (spec.type === 'enum') props[key] = { type: 'string', enum: spec.values };
    else if (spec.type === 'array')
      props[key] = { type: 'array', items: { type: 'number' }, minItems: spec.length, maxItems: spec.length };
    else if (spec.type === 'object_id')
      props[key] = { type: 'string', description: 'object id or name from the scene, e.g. object_cube or "red cube"' };
    else props[key] = { type: 'string' };
    if (isRequired(spec)) required.push(key);
  }
  return params(props, required);
}

// WAIT is handled locally and STOP is the global stop tool, so Grok doesn't get them per device
const HIDDEN = new Set(['WAIT', 'STOP']);

const fromPrimitives = (list: Primitive[], device: DeviceId): Tool[] =>
  list
    .filter((p) => !HIDDEN.has(p.id))
    .map((p) => ({
      name: p.id.toLowerCase(),
      description:
        p.description +
        (p.voice_aliases_it?.length ? ` Italian: ${p.voice_aliases_it.map((a) => `"${a}"`).join(', ')}.` : ''),
      parameters: schemaFor(p, device),
    }));

// Names map 1:1 to POST {bridge}/tools/{name}. Arm names are the UR5e primitives from the skill template.
const dogTools: Tool[] = fromPrimitives(DOG_PRIMITIVES, 'dog');

const armTools: Tool[] = [
  ...fromPrimitives(ARM_PRIMITIVES, 'arm'),
  {
    name: 'detect',
    description: 'Look for an object in the arm workspace before moving to it.',
    parameters: params({ target: { type: 'string', description: 'object id or name, e.g. "red cube"' } }, ['target']),
  },
  {
    name: 'verify',
    description: 'Check with the camera that something is true, e.g. after placing an object.',
    parameters: params({ expect: { type: 'string', description: 'e.g. "the red cube is inside the blue box"' } }, [
      'expect',
    ]),
  },
];

export const devices: Record<DeviceId, { label: string; url: string; tools: Tool[] }> = {
  dog: { label: 'Dog', url: env.dogUrl, tools: dogTools },
  arm: { label: 'Arm', url: env.armUrl, tools: armTools },
};

export const deviceIds = Object.keys(devices) as DeviceId[];

const stopTool: Tool = {
  name: 'stop',
  description: 'Emergency stop for every device. Call immediately if the user says stop, wait, halt or similar.',
  parameters: params({}),
};

// Grok gets one flat list: dog_walk, arm_move_left, ...
export function grokTools() {
  const list = deviceIds.flatMap((id) =>
    devices[id].tools.map((t) => ({ type: 'function', ...t, name: `${id}_${t.name}` })),
  );
  return [...list, { type: 'function', ...stopTool }];
}

export function splitToolName(full: string): { id: DeviceId; name: string } | null {
  const i = full.indexOf('_');
  const id = full.slice(0, i) as DeviceId;
  return i > 0 && devices[id] ? { id, name: full.slice(i + 1) } : null;
}
