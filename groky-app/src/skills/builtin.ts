// Built-in skills.
import type { Skill } from './index';

// pseudo tool: { seconds }
export const WAIT = 'wait';

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
