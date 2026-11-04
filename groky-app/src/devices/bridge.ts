import env from '../lib/config';
import { simInfo, simulate } from './sim';
import { DeviceId, deviceIds, devices, splitToolName } from './tools';

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
  if (!url) return simulate(tool.id, tool.name, args);
  return post(`${url}/tools/${tool.name}`, args);
}

export async function ping(id: DeviceId): Promise<Health> {
  const url = devices[id].url;
  if (!url) return { state: 'sim', info: simInfo[id] };

  const started = Date.now();
  try {
    const res = await request(`${url}/health`, {}, 2500);
    const info = await res.json().catch(() => undefined);
    return { state: res.ok ? 'online' : 'offline', ms: Date.now() - started, info };
  } catch {
    return { state: 'offline' };
  }
}
