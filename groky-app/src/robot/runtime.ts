// The app's live picture of the arm and the objects around it, plus seeing (DETECT / VERIFY).
//
// Every arm primitive goes through runArm(): checked and predicted by applyArm(), then simulated or sent to the
// bridge. The prediction is committed afterwards, overridden by whatever pose the bridge reports.
import env from '../lib/config';
import template from '../skills/templates/pick_place_ur5e.json';
import type { EnvObject } from '../skills/schema';
import type { Looker } from '../voice/types';
import {
  applyArm,
  ArmOpts,
  ArmState,
  defaultOpts,
  evaluate,
  initialArm,
  syncFromBridge,
  World,
  worldFromEnv,
} from './arm';

let state: ArmState = initialArm();
let world: World = worldFromEnv(template.environment.objects as EnvObject[]);
let looker: Looker | null = null;

export const armState = () => state;
export const armWorld = () => world;
export const setWorld = (w: World) => (world = w);
export const resetArm = () => (state = initialArm());
export const setArmLooker = (fn: Looker | null) => (looker = fn);

export type Send = (name: string, body: Record<string, unknown>) => Promise<{ ok: boolean; [k: string]: unknown }>;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// sim: wait a shortened version of the predicted time so the demo feels real but stays quick
// object by id, or by a name Grok heard ("orange screw", "the delivery zone")
export function resolveObject(q: string): string | undefined {
  if (world[q]) return q;
  const l = q.toLowerCase().replace(/^the /, '').trim();
  const all = Object.values(world);
  return (
    all.find((o) => o.name.toLowerCase() === l)?.id ??
    all.find((o) => l.includes(o.name.toLowerCase()) || o.name.toLowerCase().includes(l))?.id ??
    all.find((o) => l.split(/\s+/).some((w) => w.length > 2 && o.name.toLowerCase().includes(w)))?.id
  );
}

export async function runArm(
  id: string,
  params: Record<string, unknown>,
  send: Send | null,
  opts: ArmOpts = defaultOpts,
) {
  if (typeof params.target === 'string') params = { ...params, target: resolveObject(params.target) ?? params.target };
  const out = applyArm(id, params, state, world, opts);
  if (!out.ok) return { ok: false, error: out.error, refused: true };

  if (!send) {
    await sleep(Math.min(2500, 300 + out.seconds * 400));
    state = out.state;
    world = out.world;
    return { ok: true, sim: true, did: out.note, ...out.result };
  }

  const res = await send(id.toLowerCase(), { ...(out.result.params as object), urscript: out.urscript.join('\n') });
  if (!res.ok) return res;
  state = syncFromBridge(out.state, res);
  world = out.world;
  if (res.object_detected === false && out.result.object_detected) {
    // controller says the grasp missed; nothing is attached
    return { ...res, ok: true, did: 'Gripper closed but the controller detected no object', object_detected: false };
  }
  return { did: out.note, ...out.result, ...res, ok: true };
}

// ---------- seeing ----------

const IT_COLORS: Record<string, string> = {
  red: 'rosso',
  blue: 'blu',
  green: 'verde',
  yellow: 'giallo',
  black: 'nero',
  white: 'bianco',
  orange: 'arancione',
  purple: 'viola',
};

type VisionObject = {
  id: number;
  label: string;
  name: string | null;
  color: string | null;
  coords_mm: { x: number; y: number; angle_deg: number } | null;
};

export type Detection = { ok: boolean; error?: string; note: string; measured: boolean };

// 1. vision server (computer-vision/API_APP.md) when configured, 2. Grok vision on the selected camera,
// 3. in simulation, the position written in the skill file
export async function detect(objectId: string, real: boolean): Promise<Detection> {
  const o = world[objectId];
  if (!o) return { ok: false, error: `no object "${objectId}" in the scene`, note: '', measured: false };
  if (!real) return { ok: true, note: `simulated: the ${o.name} is where the skill file says`, measured: false };

  if (env.visionServer) {
    try {
      const r = await fetch(`${env.visionServer}/state/arm`);
      const frame = (await r.json()) as { objects?: VisionObject[] };
      const words = o.name.toLowerCase().split(/\s+/);
      const match = (frame.objects ?? []).find((v) => {
        const label = `${v.label} ${v.name ?? ''}`.toLowerCase();
        const colorOk = words.some((w) => IT_COLORS[w] && v.color?.toLowerCase().startsWith(IT_COLORS[w].slice(0, 4)));
        const shapeOk =
          words.some((w) => label.includes(w)) ||
          label.includes(o.category) ||
          (o.labels ?? []).some((l) => label.includes(l.toLowerCase()));
        return shapeOk && (colorOk || !words.some((w) => IT_COLORS[w]));
      });
      if (match) {
        if (match.coords_mm) {
          world = {
            ...world,
            [o.id]: {
              ...o,
              location: { ...o.location, x: match.coords_mm.x / 1000, y: match.coords_mm.y / 1000 },
              measured: true,
            },
          };
          return { ok: true, note: `vision server found the ${o.name} (#${match.id})`, measured: true };
        }
        return {
          ok: true,
          note: `vision server sees the ${o.name}, but the arm isn't calibrated yet (no coordinates)`,
          measured: false,
        };
      }
    } catch {
      // fall through to the camera
    }
  }

  if (!looker) return { ok: false, error: 'no camera on screen to look for it', note: '', measured: false };
  try {
    const res = await looker(`Is there a ${o.name} visible? Start with yes or no, then say roughly where.`);
    if (!/^\s*yes/i.test(res.answer))
      return { ok: false, error: `can't see the ${o.name}: ${res.answer}`, note: '', measured: false };
    return { ok: true, note: `camera sees it: ${res.answer}`, measured: false };
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e), note: '', measured: false };
  }
}

export async function verify(
  conditions: string[],
  real: boolean,
  check?: { object: string; container?: string; containment: boolean; orientation: boolean },
): Promise<{ ok: boolean; note: string; error?: string }> {
  if (!real) {
    if (!check) return { ok: true, note: 'simulated check' };
    const e = evaluate(world, state, check.object, check.container);
    const name = world[check.object]?.name ?? check.object;
    const problems = [
      check.containment && !e.inside && `the ${name} is ${e.where}`,
      check.orientation && !e.upright && `the ${name} is not upright`,
      e.held && `the gripper is still holding it`,
    ].filter(Boolean);
    return problems.length
      ? { ok: false, note: problems.join('; '), error: problems.join('; ') }
      : { ok: true, note: `simulated check passed: the ${name} is ${e.where}${check.orientation ? ', upright' : ''}` };
  }
  if (!looker) return { ok: false, note: '', error: 'no camera on screen to verify with' };
  try {
    const q = `Check all of these: ${conditions.join(' ')} Start with yes if every one holds, otherwise no, then say why.`;
    const res = await looker(q);
    const ok = /^\s*yes/i.test(res.answer);
    return ok ? { ok, note: res.answer } : { ok, note: res.answer, error: res.answer };
  } catch (e: any) {
    return { ok: false, note: '', error: String(e?.message ?? e) };
  }
}
