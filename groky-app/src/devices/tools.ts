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
const dogTools: Tool[] = fromPrimitives(DOG_PRIMITIVES, 'dog');

// High-level tasks run by robot_controller through the arm bridge (../bridge). The real arm only moves between
// taught positions. Small step moves (move_up/down/..., move_joint, rotate_yaw) go to the physical arm via ur_jog.
export const ARM_TASK_TOOLS = new Set([
  'pick_and_place', 'pick', 'place', 'shake', 'find', 'scan', 'check_placed', 'overview', 'push',
  'touch', 'touch_teach_start', 'touch_teach_save',
  'move_up', 'move_down', 'move_left', 'move_right', 'move_forward', 'move_backward', 'move_joint',
  'home', 'move_above', 'open_gripper',
  'get_speed', 'set_speed', 'status', 'reset_simulation_fault',
  'scene', 'follow_path', 'circle_object', 'place_on_table', 'move_object_on_table',
]);
const genericMotionNames = new Set([
  'move_up', 'move_down', 'move_left', 'move_right', 'move_forward', 'move_backward', 'move_joint',
]);
const speedParam = { type: 'number', minimum: 1, maximum: 100, description: 'optional task-only percentage of this robot model maximum; does not change the default' };
const frameParam = { type: 'string', enum: ['base', 'tool', 'world'], description: 'reference frame; defaults to base' };
const genericMotionTools: Tool[] = [
  ...['up', 'down', 'left', 'right', 'forward', 'backward'].map((direction) => ({
    name: `move_${direction}`,
    description: `Move the selected robot TCP ${direction} on a validated straight Cartesian path, preserving orientation. RoboDK simulation only.`,
    parameters: params({
      distance_m: { type: 'number', exclusiveMinimum: 0, description: 'metres; reachability, workspace and path checks determine the limit' },
      reference_frame: frameParam,
      speed_percent: speedParam,
    }, ['distance_m']),
  })),
  {
    name: 'move_joint',
    description: 'Move one joint of the selected robot, using its actual DOF and configured limits. RoboDK simulation only. Give exactly one of delta_deg or target_deg.',
    parameters: params({
      joint_index: { type: 'integer', minimum: 1, description: 'one-based joint index; query arm_status for the active DOF' },
      delta_deg: { type: 'number', description: 'relative angle in degrees' },
      target_deg: { type: 'number', description: 'absolute angle in degrees' },
      speed_percent: speedParam,
    }, ['joint_index']),
  },
];
const objectParam = {
  type: 'string',
  description: 'object name ("orange screw", "blue bucket", "green ring", "white bucket lid") or the number the vision server shows on it',
};
const destParam = {
  type: 'string',
  description: 'where to put it: "delivery zone", "blue bucket" (RoboDK symbolic release above its current opening, not gravity-confirmed insertion), or an original named slot such as "orange_screw_03_slot"',
};

const armTools: Tool[] = [
  {
    name: 'scene',
    description: 'Read registered objects, collision tags, table dimensions/base-frame pose, and named destinations. No movement.',
    parameters: params({}),
  },
  {
    name: 'follow_path',
    description: 'Follow explicit base-frame XYZ waypoints in metres with current tool orientation fixed. All linear segments are checked before the first move. Simulation only; not global obstacle avoidance.',
    parameters: params({ waypoints: { type: 'array', minItems: 1, maxItems: 256,
      items: params({ x: { type: 'number' }, y: { type: 'number' }, z: { type: 'number' } }, ['x', 'y', 'z']) },
      speed_percent: speedParam }, ['waypoints']),
  },
  {
    name: 'circle_object',
    description: 'Draw a checked polygonal circle around a registered object at explicit radius and absolute base-frame TCP height. Includes checked straight entry from current TCP. Preserves orientation; ask for radius and height if unspecified. Simulation only.',
    parameters: params({ object: objectParam, radius_m: { type: 'number', exclusiveMinimum: 0 },
      z_m: { type: 'number' }, clockwise: { type: 'boolean' },
      max_chord_error_m: { type: 'number', exclusiveMinimum: 0,
        description: 'Optional user-approved maximum circle-to-polygon deviation in metres. Never invent or increase it for speed. Omitting preserves the existing 1 cm arc spacing. Does not change collision-check resolution.' },
      speed_percent: speedParam }, ['object', 'radius_m', 'z_m']),
  },
  ...['place_on_table', 'move_object_on_table'].map((name) => ({
    name,
    description: `${name === 'move_object_on_table' ? 'Pick a registered object and place it' : 'Place the already held object'} on the configured table. Specify location=center for the middle, OR base-frame x_m+y_m, OR direction+distance_m relative to the object. Footprint must remain on table. Simulation only.`,
    parameters: params({ ...(name === 'move_object_on_table' ? { object: objectParam } : {}),
      location: { type: 'string', enum: ['center'], description: 'Use center for the middle of the table. Controller resolves coordinates; do not guess them.' },
      x_m: { type: 'number' }, y_m: { type: 'number' }, direction: { type: 'string', enum: ['left', 'right', 'forward', 'backward'] },
      distance_m: { type: 'number', exclusiveMinimum: 0 }, speed_percent: speedParam }, name === 'move_object_on_table' ? ['object'] : []),
  })),
  {
    name: 'pick_and_place',
    description:
      'Whole task on the real arm: find the object with the camera, pick it, optionally shake it, place it at the destination. Preferred way to move an object.',
    parameters: params(
      {
        object: objectParam,
        destination: destParam,
        shake_cycles: { type: 'integer', minimum: 0, maximum: 3, description: 'gentle shakes before placing, default 0' },
        speed_percent: speedParam,
      },
      ['object'],
    ),
  },
  {
    name: 'pick',
    description: 'Find the object with the camera and pick it up (verifies the grip).',
    parameters: params({ object: objectParam, speed_percent: speedParam }, ['object']),
  },
  {
    name: 'place',
    description: 'Place the held object at a destination and release it.',
    parameters: params({ destination: destParam, speed_percent: speedParam }),
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
    parameters: params({ cycles: { type: 'integer', minimum: 1, maximum: 3, description: 'default 2' }, speed_percent: speedParam }),
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
      'In RoboDK, push a registered object in an explicit base-frame direction for distance_m, using its taught push posture. Supply direction and distance together. Contact allowed only by the scene policy; table and protected obstacles remain checked. Omit both only to run the legacy taught path. Never split into repeated 5 cm pushes.',
    parameters: params({ object: objectParam,
      direction: { type: 'string', enum: ['left', 'right', 'forward', 'backward'] },
      distance_m: { type: 'number', exclusiveMinimum: 0 }, speed_percent: speedParam }, ['object']),
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
  ...fromPrimitives(ARM_PRIMITIVES, 'arm')
    .filter((t) => !genericMotionNames.has(t.name))
    .map((t) =>
      ['home', 'move_above'].includes(t.name)
        ? {
            ...t,
            parameters: {
              ...t.parameters,
              properties: {
                ...((t.parameters.properties as Record<string, unknown>) ?? {}),
                speed_percent: speedParam,
              },
            },
          }
        : t,
    ),
  ...genericMotionTools,
  {
    name: 'reset_simulation_fault',
    description: 'Explicitly clear a latched RoboDK simulation fault after user approval and controller readiness checks. Never moves the robot, retries a command, changes limits, or resets physical hardware. If refused, report the actual reason.',
    parameters: params({}),
  },
  {
    name: 'get_speed',
    description: 'Get the selected robot default velocity percentage and the controller-applied motion limits.',
    parameters: params({}),
  },
  {
    name: 'set_speed',
    description: 'Set the selected robot default velocity for subsequent tasks, as 1 to 100 percent of its verified model limits.',
    parameters: params({ percent: { type: 'number', minimum: 1, maximum: 100 } }, ['percent']),
  },
  {
    name: 'status',
    description: 'Get the active robot model, DOF, backend, simulation mode, capabilities, current speed and state.',
    parameters: params({}),
  },
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
