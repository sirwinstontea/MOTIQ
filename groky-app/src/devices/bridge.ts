import env from '../lib/config';
import { armWorld, detect, resolveObject, runArm, verify } from '../robot/runtime';
import { simInfo, simulate } from './sim';
import { ARM_TASK_TOOLS, DeviceId, deviceIds, devices, splitToolName } from './tools';

export type ToolResult = { ok: boolean; sim?: boolean; [k: string]: unknown };

export type Health = {
  state: 'sim' | 'online' | 'offline';
  ms?: number;
  info?: Record<string, unknown>; // whatever the bridge returns on /health
};

// last /health answer per device: is that bridge on SIM=1 (PyBullet cell, physical arm never used)?
const bridgeSim: Partial<Record<DeviceId, boolean>> = {};
/** true when the PC stack runs SIM=1, so "real" runs go to the PyBullet cell and never to the physical arm */
export const bridgeIsSim = (id: DeviceId) => bridgeSim[id] === true;

// SIM=1 stacks also hold the PHYSICAL UR5e for "Run on real arm": its last /health status (null = no physical arm)
export type RealArmStatus = { reachable: boolean; ready: boolean; ip?: string; note?: string; mode?: string; safety?: string };
const bridgeReal: Partial<Record<DeviceId, RealArmStatus | null>> = {};
export const bridgeRealArm = (id: DeviceId): RealArmStatus | null => bridgeReal[id] ?? null;

// while a "Run on real arm" skill runs, every arm-bridge request carries X-Arm-Target: real (and never X-Require-Sim):
// the SIM=1 bridge then runs that tool on the physical arm. Everything else (voice, Run sim) stays in PyBullet.
let armTargetReal = false;
export const setArmTargetReal = (on: boolean) => {
  armTargetReal = on;
};

async function request(url: string, init: RequestInit = {}, timeout = 15000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeout);
  // the app trusts the bridge's SIM flag (it skips the real-robot approval for sim runs), so it also tells the
  // bridge: if that bridge turns out to drive the physical arm, it refuses instead of moving it
  const toArm = url.startsWith(devices.arm.url ?? '\0');
  const simOnly: Record<string, string> =
    toArm && armTargetReal ? { 'X-Arm-Target': 'real' } : bridgeSim.arm && toArm ? { 'X-Require-Sim': '1' } : {};
  try {
    return await fetch(url, {
      ...init,
      headers: { 'Content-Type': 'application/json', 'X-Bridge-Key': env.bridgeKey, ...simOnly },
      signal: ctl.signal,
    });
  } finally {
    clearTimeout(t);
  }
}

async function post(url: string, body: unknown, timeout?: number): Promise<ToolResult> {
  try {
    const res = await request(url, { method: 'POST', body: JSON.stringify(body) }, timeout);
    const data = await res.json().catch(() => ({}));
    return res.ok ? { ok: true, ...data } : { ok: false, status: res.status, ...data };
  } catch (e: any) {
    return { ok: false, error: e?.name === 'AbortError' ? 'timeout' : String(e?.message ?? e) };
  }
}

// Puts the PyBullet cell back to the start: arm home, gripper open, parts on their spots.
// Works when a move faulted while the gripper was still holding something.
export async function resetArm(): Promise<ToolResult> {
  const url = devices.arm.url;
  if (!url) return { ok: true, sim: true, did: 'no arm connected' };
  return post(`${url}/tools/reset`, {}, 20000);
}

export async function stopDevice(id: DeviceId): Promise<ToolResult> {
  const url = devices[id].url;
  return url ? post(`${url}/tools/stop`, {}, 3000) : { ok: true, sim: true };
}

// opts.armReal: the user approved this voice plan on the REAL arm while the bridge runs SIM=1, so the arm calls
// carry X-Arm-Target: real. Without it, a SIM=1 bridge runs them in the PyBullet cell.
export async function runTool(
  fullName: string,
  args: Record<string, unknown>,
  opts: { armReal?: boolean } = {},
): Promise<ToolResult> {
  if (fullName === 'stop') {
    const results = await Promise.all(deviceIds.map(stopDevice));
    return { ok: results.every((r) => r.ok), stopped: deviceIds };
  }

  const tool = splitToolName(fullName);
  if (!tool) return { ok: false, error: `unknown tool ${fullName}` };

  const url = devices[tool.id].url;
  if (tool.name === 'wait') {
    await new Promise((r) => setTimeout(r, Math.min(10, Math.max(0, Number(args.seconds ?? 0.5))) * 1000));
    return { ok: true, waited: args.seconds };
  }
  if (tool.id === 'arm') return queuedArm(tool.name, args, url, !!opts.armReal);
  if (!url) return simulate(tool.id, tool.name, args);
  return post(`${url}/tools/${tool.name}`, args);
}

// null = simulated arm. Step moves run on the physical arm at 5 cm/s, so allow a minute.
export function armSend() {
  const url = devices.arm.url;
  return url ? (tool: string, body: Record<string, unknown>) => post(`${url}/tools/${tool}`, body, 60000) : null;
}

// Grok often sends several arm calls in one turn ("5 cm up, then down") and the session runs them together.
// The arm can only do one thing at a time, so queue them in the order Grok asked. Stop bypasses the queue.
let armQueue: Promise<unknown> = Promise.resolve();
function queuedArm(name: string, args: Record<string, unknown>, url: string, real: boolean): Promise<ToolResult> {
  // the queue runs one arm call at a time, so the real-arm header is switched on only for this call
  const next = armQueue.then(async () => {
    if (!real) return runArmTool(name, args, url);
    setArmTargetReal(true);
    try {
      return { ...(await runArmTool(name, args, url)), target: 'real' };
    } finally {
      setArmTargetReal(false);
    }
  });
  armQueue = next.catch(() => {});
  return next;
}

// the fixed physical-arm tool (bridge `ur_down_up`); not offered to Grok, only run from its built-in skill
export async function armPhysical(tool: 'ur_down_up' | 'ur_visit' | 'go_to_pose', body: Record<string, unknown>): Promise<ToolResult> {
  const url = devices.arm.url;
  if (!url) return { ok: false, error: 'no arm bridge configured (EXPO_PUBLIC_ARM_URL)' };
  return post(`${url}/tools/${tool}`, body, tool === 'ur_visit' ? 180000 : 60000);
}

// arm: every primitive is checked against the UR5e model first, so nothing outside the limits reaches the bridge
async function runArmTool(name: string, args: Record<string, unknown>, url: string): Promise<ToolResult> {
  // camera on the arm head: on the real arm the bridge moves the camera around until it sees the object
  if (name === 'detect' && url) return post(`${url}/tools/find`, { object: args.target ?? args.object }, 180000);
  if (name === 'detect') {
    const id = resolveObject(String(args.target ?? ''));
    if (!id)
      return {
        ok: false,
        error: `unknown object "${args.target}"`,
        known: Object.values(armWorld()).map((o) => o.name),
      };
    const d = await detect(id, !!url);
    return { ok: d.ok, sim: !url || undefined, object: id, seen: d.note, measured: d.measured, error: d.error };
  }
  if (ARM_TASK_TOOLS.has(name)) {
    // whole tasks run in robot_controller; a pick can take a while in the sim, so wait up to 3 minutes
    if (url) return post(`${url}/tools/${name}`, args, 180000);
    return simulate('arm', name, args);
  }
  if (name === 'verify') {
    const v = await verify([String(args.expect ?? '')], !!url);
    return { ok: v.ok, sim: !url || undefined, seen: v.note, error: v.error };
  }
  return runArm(name.toUpperCase(), args, armSend());
}

// arm speed lives in robot_controller (% of the UR5e maximums, default 10). Changes apply from the next task.
export async function getArmSpeed(): Promise<ToolResult & { speed_percent?: number }> {
  const url = devices.arm.url;
  if (!url) return { ok: false, sim: true, error: 'arm is simulated' };
  try {
    const res = await request(`${url}/speed`, {}, 3000);
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, ...data };
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}

export async function setArmSpeed(percent: number): Promise<ToolResult & { speed_percent?: number }> {
  const url = devices.arm.url;
  if (!url) return { ok: false, sim: true, error: 'arm is simulated' };
  return post(`${url}/speed`, { percent }, 5000);
}

export async function ping(id: DeviceId): Promise<Health> {
  const url = devices[id].url;
  if (!url) return { state: 'sim', info: simInfo(id) };

  const started = Date.now();
  try {
    const res = await request(`${url}/health`, {}, 2500);
    const info = await res.json().catch(() => undefined);
    const pa = (info as { physical_arm?: { sim?: unknown } } | undefined)?.physical_arm;
    if (pa) bridgeSim[id] = pa.sim === true;
    if (info && typeof info === 'object') bridgeReal[id] = ((info as { real_arm?: RealArmStatus }).real_arm ?? null);
    return { state: res.ok ? 'online' : 'offline', ms: Date.now() - started, info };
  } catch {
    return { state: 'offline' };
  }
}
