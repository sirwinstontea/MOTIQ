import env from '../lib/config';

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

// Names map 1:1 to POST {bridge}/tools/{name}.
// TODO: adjust once we know the actual hardware (Wed morning hardware tour)
const dogTools: Tool[] = [
  {
    name: 'walk',
    description: 'Walk the robot dog. Positive meters forward, negative backward.',
    parameters: params({ meters: { type: 'number', minimum: -2, maximum: 2 } }, ['meters']),
  },
  {
    name: 'turn',
    description: 'Turn in place. Positive degrees = left.',
    parameters: params({ degrees: { type: 'number', minimum: -180, maximum: 180 } }, ['degrees']),
  },
  {
    name: 'pose',
    description: 'Put the dog in a pose.',
    parameters: params({ pose: { type: 'string', enum: ['stand', 'sit', 'lie', 'wave'] } }, ['pose']),
  },
  {
    name: 'look',
    description: "Take a photo with the dog's camera and describe what is in front of it.",
    parameters: params({}),
  },
];

const armTools: Tool[] = [
  {
    name: 'goto',
    description: 'Move the arm to a named, pre-taught position.',
    parameters: params({ place: { type: 'string', enum: ['home', 'table', 'box', 'handover'] } }, ['place']),
  },
  {
    name: 'grip',
    description: 'Open or close the gripper.',
    parameters: params({ state: { type: 'string', enum: ['open', 'close'] } }, ['state']),
  },
  {
    name: 'pick_place',
    description: 'Pick up an object seen by the camera and put it at a target place.',
    parameters: params(
      {
        object: { type: 'string', description: 'e.g. "red cube"' },
        place: { type: 'string', enum: ['table', 'box', 'handover'] },
      },
      ['object', 'place'],
    ),
  },
  {
    name: 'check',
    description: 'Photo of the workspace, verified by vision. Use after pick_place to confirm it worked.',
    parameters: params({ expect: { type: 'string' } }, ['expect']),
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

// Grok gets one flat list: dog_walk, arm_grip, ...
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
