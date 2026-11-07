import env from './config';
import { devices } from '../devices';
import { toBase64 } from '../voice/pcm';

// no url = the tablet's own camera; open = a plain image url that wants no bridge key (the vision server)
export type Camera = { id: string; label: string; url?: string; open?: boolean };

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

// The computer-vision server draws boxes and track numbers on its frames (/frame/<source>.jpg). List them as cameras
// so the Cameras tab shows what vision sees. Skipped for a robot whose bridge already proxies that same frame.
// `webcam` here is the camera on the arm inside the simulation, shown as Sim camera.
function visionCameras(): Camera[] {
  if (!env.visionServer) return [];
  const names = env.visionSources
    .split(',')
    .map((x) => x.trim().toLowerCase())
    .filter((x) => x === 'arm' || x === 'webcam');
  const armListed = names.includes('arm') && !devices.arm?.url;
  return names.flatMap((x) => {
    if (x === 'webcam') {
      if (armListed) return [];
      return [{
        id: 'vision-arm',
        label: 'Sim camera',
        url: `${env.visionServer}/frame/arm.jpg?raw=false`,
        open: true,
      }];
    }
    return [{
      id: `vision-${x}`,
      label: `${devices.arm?.label ?? x} vision`,
      url: `${env.visionServer}/frame/${x}.jpg?raw=false`,
      open: true,
    }];
  });
}

export const cameras: Camera[] = [
  ...visionCameras(),
  { id: 'tablet', label: 'Tablet' },
  ...Object.entries(devices)
    .filter(([, d]) => d.url)
    .map(([id, d]) => ({ id, label: d.label, url: `${d.url}/camera.jpg` })),
  ...parseExtra(env.cameras),
];

export const camHeaders = env.bridgeKey ? { 'X-Bridge-Key': env.bridgeKey } : undefined;

export const frameUrl = (url: string, n: number) => `${url}${url.includes('?') ? '&' : '?'}t=${n}`;

export const headersFor = (cam: Pick<Camera, 'open'>) => (cam.open ? undefined : camHeaders);

export async function snapshot(url: string, open = false): Promise<string> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 5000);
  try {
    const r = await fetch(frameUrl(url, Date.now()), { headers: open ? undefined : camHeaders, signal: ctl.signal });
    if (!r.ok) throw new Error(`camera ${r.status}`);
    return toBase64(await r.arrayBuffer());
  } finally {
    clearTimeout(t);
  }
}
