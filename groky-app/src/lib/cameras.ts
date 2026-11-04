import env from './config';
import { devices } from '../devices';
import { toBase64 } from '../voice/pcm';

export type Camera = { id: string; label: string; url?: string }; // no url = the tablet's own camera

function parseExtra(raw: string): Camera[] {
  return raw
    .split(',')
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p, i) => {
      const eq = p.indexOf('=');
      const label = eq > 0 ? p.slice(0, eq).trim() : `Cam ${i + 1}`;
      const url = eq > 0 ? p.slice(eq + 1).trim() : p;
      return { id: `extra-${i}`, label, url };
    });
}

export const cameras: Camera[] = [
  { id: 'tablet', label: 'Tablet' },
  ...Object.entries(devices)
    .filter(([, d]) => d.url)
    .map(([id, d]) => ({ id, label: d.label, url: `${d.url}/camera.jpg` })),
  ...parseExtra(env.cameras),
];

export const camHeaders = env.bridgeKey ? { 'X-Bridge-Key': env.bridgeKey } : undefined;

export const frameUrl = (url: string, n: number) => `${url}${url.includes('?') ? '&' : '?'}t=${n}`;

export async function snapshot(url: string): Promise<string> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 5000);
  try {
    const r = await fetch(frameUrl(url, Date.now()), { headers: camHeaders, signal: ctl.signal });
    if (!r.ok) throw new Error(`camera ${r.status}`);
    return toBase64(await r.arrayBuffer());
  } finally {
    clearTimeout(t);
  }
}
