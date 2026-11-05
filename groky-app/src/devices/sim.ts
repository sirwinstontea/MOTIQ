import type { DeviceId } from './tools';
import type { ToolResult } from './bridge';
import { armState } from '../robot/runtime';

// Fake robot so the voice side can be built without hardware. The arm is simulated in src/robot.

export function simInfo(id: DeviceId): Record<string, unknown> {
  const s = armState();
  return {
    model: 'UR5e (sim)',
    gripper: s.gripper.holding ? `holding ${s.gripper.holding}` : s.gripper.closed ? 'closed' : 'open',
    tcp: `${s.tcp.x.toFixed(2)}, ${s.tcp.y.toFixed(2)}, ${s.tcp.z.toFixed(2)} m`,
  };
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function simulate(id: DeviceId, name: string, args: Record<string, unknown>): Promise<ToolResult> {
  await wait(400 + Math.random() * 600);
  if (name === 'look') return { ok: true, sim: true, seen: 'a table with an old Commodore 64 and a keyboard' };
  return { ok: true, sim: true, device: id, did: name, args };
}
