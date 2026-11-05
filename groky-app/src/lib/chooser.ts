// Client for robot-chooser (../robot-chooser). Settings uses this so the tablet can pick an arm
// model / sim backend; the chooser rewrites robot_controller config on the PC.
import AsyncStorage from '@react-native-async-storage/async-storage';
import env from './config';

const URL_KEY = 'chooser-url-v1';

export type ChooserRobot = {
  id: string;
  brand: string;
  model: string;
  dof: number;
  status: string;
  active?: boolean;
  limits_verified?: boolean;
  config_complete?: boolean;
  nominal_reach_m?: number;
  nominal_payload_kg?: number;
};

export type ChooserReport = {
  label?: string;
  status?: string;
  blockers?: { code?: string; message?: string }[];
  warnings?: { code?: string; message?: string }[];
};

export type ChooserState = {
  catalog: ChooserRobot[];
  hardware: { active_robot?: string; backend?: string; speed_percent?: number };
  report: ChooserReport;
  selection?: { status?: string; robot_id?: string } | null;
};

async function baseUrl(): Promise<string> {
  const stored = (await AsyncStorage.getItem(URL_KEY).catch(() => null))?.trim();
  return (stored || env.chooserUrl || '').replace(/\/$/, '');
}

export async function getChooserUrl(): Promise<string> {
  return baseUrl();
}

export async function setChooserUrl(url: string) {
  const v = url.trim().replace(/\/$/, '');
  if (v) await AsyncStorage.setItem(URL_KEY, v);
  else await AsyncStorage.removeItem(URL_KEY);
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const base = await baseUrl();
  if (!base) throw new Error('Set the robot-chooser URL in Settings (e.g. http://192.168.1.50:8770)');
  const r = await fetch(`${base}${path}`, {
    ...init,
    headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...(init?.headers || {}) },
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j?.ok === false) {
    const msg = j?.error?.message || j?.error || r.statusText || `HTTP ${r.status}`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return j as T;
}

export function fetchChooserState() {
  return req<ChooserState & { ok: boolean }>('/api/state');
}

export function selectChooserRobot(robotId: string) {
  return req<ChooserState & { ok: boolean }>('/api/select', {
    method: 'POST',
    body: JSON.stringify({ robot_id: robotId }),
  });
}

export function patchChooserHardware(patch: Record<string, unknown>) {
  return req<ChooserState & { ok: boolean }>('/api/hardware', {
    method: 'POST',
    body: JSON.stringify(patch),
  });
}
