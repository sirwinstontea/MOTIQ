// Learned skills (from a demonstration video or the camera) are also kept on the robot PC, by the teach server
// (computer-vision-to-app/teach_server.py -> computer-vision/data/skills/learned/<id>.json). The tablet keeps its own
// copy in AsyncStorage; this is the durable one that survives a reinstall and shows up on any tablet.
import env from '../lib/config';
import type { SkillDoc } from './schema';

export const isLearned = (doc: SkillDoc) => ['video', 'vision'].includes(String(doc.skill.source?.type ?? ''));

async function call(path: string, init?: RequestInit, timeout = 5000): Promise<any> {
  if (!env.teachServer) return null;
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeout);
  try {
    const r = await fetch(`${env.teachServer}${path}`, { ...init, signal: ctl.signal });
    return r.ok ? await r.json().catch(() => null) : null;
  } catch {
    return null; // the PC copy is a backup: never fail the app over it
  } finally {
    clearTimeout(t);
  }
}

export async function fetchLearned(): Promise<SkillDoc[]> {
  const j = await call('/skills');
  return Array.isArray(j?.skills) ? (j.skills as SkillDoc[]) : [];
}

export const storeLearned = (doc: SkillDoc) =>
  call('/skills', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(doc) });

export const deleteLearned = (id: string) => call(`/skills/${encodeURIComponent(id)}`, { method: 'DELETE' });
