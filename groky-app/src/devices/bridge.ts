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

async function request(url: string, init: RequestInit = {}, timeout = 15000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeout);
  try {
    return await fetch(url, {
      ...init,
      headers: { 'Content-Type': 'application/json', 'X-Bridge-Key': env.bridgeKey },
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

export async function stopDevice(id: DeviceId): Promise<ToolResult> {
  const url = devices[id].url;
  return url ? post(`${url}/tools/stop`, {}, 3000) : { ok: true, sim: true };
}

export async function runTool(fullName: string, args: Record<string, unknown>): Promise<ToolResult> {
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
  if (tool.id === 'arm') return runArmTool(tool.name, args, url);
  if (!url) return simulate(tool.id, tool.name, args);
  return post(`${url}/tools/${tool.name}`, args);
}

// null = simulated arm
export function armSend() {
  const url = devices.arm.url;
  return url ? (tool: string, body: Record<string, unknown>) => post(`${url}/tools/${tool}`, body) : null;
}

// the fixed physical-arm tool (bridge `ur_down_up`); not offered to Grok, only run from its built-in skill
export async function armPhysical(tool: 'ur_down_up', body: Record<string, unknown>): Promise<ToolResult> {
  const url = devices.arm.url;
  if (!url) return { ok: false, error: 'no arm bridge configured (EXPO_PUBLIC_ARM_URL)' };
  return post(`${url}/tools/${tool}`, body, 60000);
}

// arm: every primitive is checked against the UR5e model first, so nothing outside the limits reaches the bridge
async function runArmTool(name: string, args: Record<string, unknown>, url: string): Promise<ToolResult> {
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
    // whole tasks run in robot_controller; a pick can take a while in RoboDK, so wait up to 3 minutes
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
    return { state: res.ok ? 'online' : 'offline', ms: Date.now() - started, info };
  } catch {
    return { state: 'offline' };
  }
}
