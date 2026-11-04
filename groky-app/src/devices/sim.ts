import type { DeviceId } from './tools';
import type { ToolResult } from './bridge';

// Fake robots so the voice side can be built without hardware.

export const simInfo: Record<DeviceId, Record<string, unknown>> = {
  dog: { battery: 76, pose: 'stand', camera: 'front' },
  arm: { gripper: 'open', position: 'home', camera: 'overhead' },
};

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function simulate(id: DeviceId, name: string, args: Record<string, unknown>): Promise<ToolResult> {
  await wait(400 + Math.random() * 600);
  if (name === 'look') return { ok: true, sim: true, seen: 'a table with an old Commodore 64 and a keyboard' };
  if (name === 'check') return { ok: Math.random() > 0.2, sim: true, expect: args.expect };
  return { ok: true, sim: true, device: id, did: name, args };
}
