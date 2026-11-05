// Skills -> Sync Digital Twin client: MOTIQ bridge (EXPO_PUBLIC_ARM_URL) /digital-twin/* routes, which proxy to the
// local read-only twin service (robot_controller/robot_control/digital_twin).
//
// MIRROR ONLY — NO PHYSICAL MOTION. This module talks only to /digital-twin/*: it never imports the skill runner or the
// device tools, never calls a stop/motion endpoint, and "Stop Sync" ends the mirroring loop, not the robot.
import env from './config';

export type TwinState = 'DISCONNECTED' | 'READY' | 'RUNNING' | 'PAUSED_STALE' | 'ERROR' | 'STOPPED';

export type Fail = { ok: false; status: string; reason: string; state?: TwinState };
export type Res<T> = (T & { ok: true }) | Fail;

export type ObjectInfo = {
  state: 'never_seen' | 'fresh' | 'stale' | 'not_visible';
  reason: string | null;
  position_base_m: [number, number, number] | null;
  updated_at: string | null;
  confidence?: number | null;
  cad_moved?: boolean;
};

export type TwinStatus = {
  mode: 'mirror_only';
  label: string;
  target: 'simulation' | 'physical';
  state: TwinState;
  physical_motion_enabled: false;
  objects_enabled: boolean;
  started_at: string | null;
  robot: {
    source: string;
    connected: boolean;
    last_joint_sample_age_ms: number | null;
    hz: number;
    target_hz: number;
    q_deg: number[] | null;
    moving: boolean | null;
    fk_consistency: { robodk_flange_vs_ur_nominal_dh_flange_mm: number; deg: number } | null;
  };
  camera: {
    source: string;
    calibration_id?: string | null;
    last_frame_id?: string | null;
    last_frame_age_ms: number | null;
    last_reason?: string | null;
    last_detail?: string | null;
    simulation?: boolean | null;
  };
  robodk: { connected: boolean; station?: string; run_mode?: number; driver_state?: number; robot_item: string };
  alignment: { validated: boolean; validated_for: string | null };
  objects: {
    updated: number;
    stale: number;
    skipped: number;
    not_visible: number;
    unknown: number;
    per_object: Record<string, ObjectInfo>;
  };
  last_error: { status: string; reason: string; at: string } | null;
};

export type TwinCheck = {
  target: 'simulation' | 'physical';
  robot: { connected: boolean; source: string; q_deg?: number[]; moving?: boolean; reason?: string };
  robodk: { ok: boolean; station?: string; status?: string; reason?: string };
  vision: { source: string; connected?: boolean; calibration_valid?: boolean | null; calibration_id?: string | null };
  alignment: { validated: boolean; validated_for: string | null };
  can_start_robot_only: boolean;
  can_start_with_objects: boolean;
  object_blockers: string[];
};

export const digitalTwinReady = () => !!env.armUrl;

async function call<T>(method: 'GET' | 'POST', path: string, body?: { objects?: boolean }): Promise<Res<T>> {
  if (!env.armUrl) return { ok: false, status: 'not_configured', reason: 'EXPO_PUBLIC_ARM_URL is not set' };
  if (!path.startsWith('/digital-twin/')) {
    return { ok: false, status: 'bad_request', reason: 'not a digital-twin route' }; // defence in depth
  }
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 20000);
  try {
    const r = await fetch(`${env.armUrl}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Bridge-Key': env.bridgeKey },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl.signal,
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok !== true) {
      return {
        ok: false,
        status: String(j.status ?? `http_${r.status}`),
        reason: String(j.reason ?? j.error ?? `HTTP ${r.status}`),
        state: j.state,
      };
    }
    return j as Res<T>;
  } catch (e: any) {
    return {
      ok: false,
      status: 'service_unavailable',
      state: 'DISCONNECTED',
      reason: e?.name === 'AbortError' ? 'timeout' : `Can't reach the bridge: ${e?.message ?? e}`,
    };
  } finally {
    clearTimeout(t);
  }
}

export const digitalTwin = {
  status: () => call<TwinStatus>('GET', '/digital-twin/status'),
  check: () => call<TwinCheck>('POST', '/digital-twin/check'),
  snapshot: (objects: boolean) => call<Record<string, unknown>>('POST', '/digital-twin/snapshot', { objects }),
  start: (objects: boolean) => call<{ status: string; state: TwinState }>('POST', '/digital-twin/start', { objects }),
  stop: () => call<{ status: string; state: TwinState; robot_command_sent: false }>('POST', '/digital-twin/stop'),
};

export const fmtMm = (p: number[] | null | undefined) =>
  p ? p.map((v) => (v * 1000).toFixed(0)).join(', ') + ' mm' : '–';
