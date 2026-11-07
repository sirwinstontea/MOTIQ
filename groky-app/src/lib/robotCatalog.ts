// Robots -> Arm catalog client: MOTIQ bridge (EXPO_PUBLIC_ARM_URL) /robots/* routes, which proxy to the local
// robot-chooser (../robot-chooser, 127.0.0.1:8771).
//
// CONFIG ONLY - NO MOTION. Adding or selecting an arm rewrites catalog.yaml / hardware.yaml and the controller's
// generated config (simulation backends only). It takes effect after the controller + bridge restart.
import env from './config';

export type CatalogArm = {
  id: string;
  brand: string;
  model: string;
  dof: number;
  nominal_reach_m: number;
  nominal_payload_kg: number;
  library_url: string;
  library_file: string | null;
  scene_file: string | null;
  notes: string | null;
  status: string; // "Ready" | "Config incomplete" | ...
  active: boolean;
  model_file_available: boolean;
  limits_verified: boolean;
  config_complete: boolean;
};

export type Issue = { level: string; code: string; message: string };
export type ActiveReport = { robot_id: string; robot: string; status: 'READY' | 'BLOCKED'; blockers: Issue[] };

export type NewArm = {
  id: string;
  brand: string;
  model: string;
  dof: number;
  nominal_reach_m: number;
  nominal_payload_kg: number;
  library_url: string;
  library_file?: string | null;
};

type Fail = { ok: false; code: string; message: string };
type Res<T> = (T & { ok: true }) | Fail;

export const robotCatalogReady = () => !!env.armUrl;

async function call<T>(method: 'GET' | 'POST', action: string, body?: object): Promise<Res<T>> {
  if (!env.armUrl) return { ok: false, code: 'NOT_CONFIGURED', message: 'EXPO_PUBLIC_ARM_URL is not set' };
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 20000);
  try {
    const r = await fetch(`${env.armUrl}/robots/${action}`, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Bridge-Key': env.bridgeKey },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl.signal,
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok !== true) {
      const e = typeof j.error === 'object' && j.error ? j.error : { message: j.error };
      return { ok: false, code: String(e.code ?? `HTTP_${r.status}`), message: String(e.message ?? `HTTP ${r.status}`) };
    }
    return j as Res<T>;
  } catch (e: any) {
    return {
      ok: false,
      code: 'SERVICE_UNAVAILABLE',
      message: e?.name === 'AbortError' ? 'timeout' : `Can't reach the bridge: ${e?.message ?? e}`,
    };
  } finally {
    clearTimeout(t);
  }
}

type Mutation = { robots: CatalogArm[]; report: ActiveReport; restart_required: true };

export const robotCatalog = {
  list: () => call<{ robots: CatalogArm[] }>('GET', 'catalog'),
  active: () => call<{ report: ActiveReport }>('GET', 'active'),
  select: (robot_id: string) => call<Mutation>('POST', 'select', { robot_id }),
  add: (arm: NewArm) => call<Mutation>('POST', 'add', { ...arm, pybullet_item_name: arm.model }),
};

// same rule as robot-chooser's RobotEntry.id
export const ARM_ID_RE = /^[a-z0-9][a-z0-9_]*$/;
export const suggestArmId = (brand: string, model: string) =>
  `${brand} ${model}`
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
