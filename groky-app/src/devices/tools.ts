import env from '../lib/config';
import { ARM_PRIMITIVES, isRequired, limitFor } from '../robot/profiles';
import type { Primitive } from '../skills/schema';

export type DeviceId = 'arm';

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
      props[key] = { type: 'string', description: 'object id or name from the scene: blue_bucket_01 ("blue bucket"), green_ring_02 ("green ring"), orange_screw_03 ("orange screw"), white_bucket_lid_04 ("white bucket lid"), delivery_zone' };
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
// High-level tasks run by robot_controller. The real arm only moves between
// taught positions. Small step moves (move_up/down/..., move_joint, rotate_yaw) go to the physical arm via ur_jog.
export const ARM_TASK_TOOLS = new Set([
  'pick_and_place', 'pick', 'place', 'shake', 'find', 'scan', 'check_placed', 'overview', 'push',
  'touch', 'touch_teach_start', 'touch_teach_save',
]);
const objectParam = {
  type: 'string',
  description: 'object name ("orange screw", "blue bucket", "green ring", "white bucket lid") or the number the vision server shows on it',
};
const destParam = {
  type: 'string',
  description: 'where to put it: "delivery zone", "blue bucket" (drops it into the bucket), or the spot an object came from like "orange_screw_03_slot"',
};

const armTools: Tool[] = [
  {
    name: 'pick_and_place',
    description:
      'Whole task on the real arm: find the object with the camera, pick it, optionally shake it, place it at the destination. Preferred way to move an object.',
    parameters: params(
      {
        object: objectParam,
        destination: destParam,
        shake_cycles: { type: 'integer', minimum: 0, maximum: 3, description: 'gentle shakes before placing, default 0' },
      },
      ['object'],
    ),
  },
  {
    name: 'pick',
    description: 'Find the object with the camera and pick it up (verifies the grip).',
    parameters: params({ object: objectParam }, ['object']),
  },
  {
    name: 'place',
    description: 'Place the held object at a destination and release it.',
    parameters: params({ destination: destParam }),
  },
  {
    name: 'find',
    description:
      'The camera is on the arm head: move the arm through its look poses until the camera sees this object. Picks already do this by themselves; use it when asked to look for / find something.',
    parameters: params({ object: objectParam }, ['object']),
  },
  {
    name: 'scan',
    description: 'Look over the whole table with the arm camera and report which of the four known objects are there.',
    parameters: params({}),
  },
  {
    name: 'check_placed',
    description: 'After placing: look at the destination with the arm camera and confirm the object is there.',
    parameters: params({ object: objectParam, destination: destParam }, ['object', 'destination']),
  },
  {
    name: 'shake',
    description: 'Gently shake the held object side to side.',
    parameters: params({ cycles: { type: 'integer', minimum: 1, maximum: 3, description: 'default 2' } }),
  },
  {
    name: 'touch',
    description:
      'Real arm: the camera finds the object, the arm goes over it, comes down slowly until its force sensor feels the object, lifts and goes back to where it started. Needs the touch offset taught once (touch_teach_start + touch_teach_save).',
    parameters: params(
      {
        object: objectParam,
        max_down_mm: { type: 'number', minimum: 10, maximum: 400, description: 'give up after going this far down, default 250' },
        force_n: { type: 'number', minimum: 5, maximum: 40, description: 'contact force that counts as touching, default 15' },
      },
      ['object'],
    ),
  },
  {
    name: 'push',
    description:
      'Push an object about 5 cm (blue bucket, orange screw or white lid; the ring is too flat). Real arm: the camera finds it, the arm comes down beside it until it feels the table, pushes, lifts and goes back. Needs the touch offset taught once.',
    parameters: params({ object: objectParam }, ['object']),
  },
  {
    name: 'touch_teach_start',
    description:
      'Teach the touch offset, step 1: the arm camera centres over the object and the arm stays there. Then the user (or you, with small move_left/right/forward/backward steps they ask for) moves the touching part right above the object, without going down, then call touch_teach_save.',
    parameters: params({ object: objectParam }, ['object']),
  },
  {
    name: 'touch_teach_save',
    description: 'Teach the touch offset, step 2: save where the touching part is now relative to where the camera was centred. Does not move the arm.',
    parameters: params({}),
  },
  ...fromPrimitives(ARM_PRIMITIVES, 'arm'),
  {
    name: 'detect',
    description: 'Look for an object in the arm workspace before moving to it.',
    parameters: params({ target: { type: 'string', description: 'object id or name, e.g. "orange screw"' } }, ['target']),
  },
  {
    name: 'verify',
    description: 'Check with the camera that something is true, e.g. after placing an object.',
    parameters: params({ expect: { type: 'string', description: 'e.g. "the orange screw is in the delivery zone"' } }, [
      'expect',
    ]),
  },
];

export const devices: Record<DeviceId, { label: string; url: string; tools: Tool[] }> = {
  arm: { label: 'Arm', url: env.armUrl, tools: armTools },
};

export const deviceIds = Object.keys(devices) as DeviceId[];

const stopTool: Tool = {
  name: 'stop',
  description: 'Emergency stop for every device. Call immediately if the user says stop, wait, halt or similar.',
  parameters: params({}),
};

// Grok gets one flat list: arm_move_left, arm_pick, ...
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
