import env from '../lib/config';
import { generateSkill, Generated } from './generate';

// Teach by showing: watch the computer-vision server (../../computer-vision/API_APP.md, GET /state/{source}) while a
// person does the task in front of the camera, turn what moved where into a plain task description, and let the
// normal skill generator (Grok + validation + dry run) write the skill. The result is a draft that needs review.

type VisionObject = {
  id: number;
  label: string;
  name: string | null;
  conf: number;
  bbox: [number, number, number, number];
  color: string | null;
  coords_mm: { x: number; y: number; angle_deg: number } | null;
};
type FramePayload = { frame_id: number; ts: number; objects: VisionObject[] };

type Pos = { x: number; y: number; mm: boolean };
type Track = {
  key: string;
  label: string;
  color: string | null;
  frames: number;
  first: Pos;
  last: Pos;
  firstSeen: number;
  lastSeen: number;
};

export type Observation = {
  frames: number;
  seconds: number;
  hands: number; // frames with a hand in view
  tracks: Track[];
};

const HAND = /\b(hand|mano|mani|person|persona)\b/i;
const MOVE_MM = 30; // a move smaller than this is tracker noise
const MOVE_NORM = 0.05; // same, in image units, when the arm is not calibrated

const keyOf = (o: VisionObject) => `${(o.name || o.label).toLowerCase()}|${(o.color ?? '').toLowerCase()}`;
const posOf = (o: VisionObject): Pos =>
  o.coords_mm
    ? { x: o.coords_mm.x, y: o.coords_mm.y, mm: true }
    : { x: o.bbox[0] + o.bbox[2] / 2, y: o.bbox[1] + o.bbox[3] / 2, mm: false };
const dist = (a: Pos, b: Pos) => Math.hypot(a.x - b.x, a.y - b.y);
const moved = (t: Track) => dist(t.first, t.last) > (t.first.mm && t.last.mm ? MOVE_MM : MOVE_NORM);
// the vision server names colours in Italian; skills (and DETECT matching in robot/runtime.ts) use English
const EN: Record<string, string> = { ross: 'red', blu: 'blue', verd: 'green', giall: 'yellow', ner: 'black', bianc: 'white', arancion: 'orange', viola: 'purple', grigi: 'grey', marron: 'brown', rosa: 'pink' };
const english = (c: string) => Object.entries(EN).find(([it]) => c.toLowerCase().startsWith(it))?.[1] ?? c;
export const describeObject = (t: Pick<Track, 'label' | 'color'>) => (t.color ? `${english(t.color)} ${t.label}` : t.label);

// Collects frames. Tracks are grouped by name + colour, not by track number, because the number changes when the
// tracker loses an object behind a hand (which is exactly what happens during a demonstration).
export class Watcher {
  private tracks = new Map<string, Track>();
  private frames = 0;
  private hands = 0;
  private started = Date.now();
  private lastFrame = -1;

  add(frame: FramePayload) {
    if (frame.frame_id === this.lastFrame) return;
    this.lastFrame = frame.frame_id;
    this.frames++;
    const now = Date.now();
    let hand = false;
    for (const o of frame.objects ?? []) {
      if (HAND.test(o.label) || HAND.test(o.name ?? '')) {
        hand = true;
        continue;
      }
      if (o.conf < 0.35) continue;
      const k = keyOf(o);
      const p = posOf(o);
      const t = this.tracks.get(k);
      if (!t) {
        this.tracks.set(k, { key: k, label: o.name || o.label, color: o.color, frames: 1, first: p, last: p, firstSeen: now, lastSeen: now });
      } else {
        t.frames++;
        t.last = p;
        t.lastSeen = now;
      }
    }
    if (hand) this.hands++;
  }

  result(): Observation {
    // ignore flickers: an object must be seen in at least 3 frames or 10% of them
    const min = Math.max(3, Math.round(this.frames * 0.1));
    return {
      frames: this.frames,
      seconds: Math.round((Date.now() - this.started) / 1000),
      hands: this.hands,
      tracks: [...this.tracks.values()].filter((t) => t.frames >= min),
    };
  }
}

async function fetchFrame(source: string, signal?: AbortSignal): Promise<FramePayload> {
  const r = await fetch(`${env.visionServer}/state/${source}`, { signal });
  if (!r.ok) throw new Error(`vision server ${r.status}`);
  return r.json();
}

export const visionReady = () => !!env.visionServer;

export async function visionHealthy(): Promise<string | null> {
  if (!env.visionServer) return 'No vision server set (EXPO_PUBLIC_VISION_SERVER).';
  try {
    const r = await fetch(`${env.visionServer}/health`);
    return r.ok ? null : `Vision server answered ${r.status}.`;
  } catch {
    return `Can't reach the vision server at ${env.visionServer}.`;
  }
}

// Polls the server until stop() is called. onFrame gets the live object count for the UI.
export function watch(source = 'arm', onFrame?: (objects: number, frames: number) => void, hz = 5) {
  const w = new Watcher();
  const ctl = new AbortController();
  let running = true;
  let errors = 0;
  const loop = (async () => {
    while (running) {
      const t0 = Date.now();
      try {
        const f = await fetchFrame(source, ctl.signal);
        w.add(f);
        errors = 0;
        onFrame?.(f.objects?.length ?? 0, w.result().frames);
      } catch (e) {
        if (!running) break;
        if (++errors >= 10) throw e;
      }
      await new Promise((r) => setTimeout(r, Math.max(0, 1000 / hz - (Date.now() - t0))));
    }
  })();
  return {
    async stop(): Promise<Observation> {
      running = false;
      ctl.abort();
      await loop.catch(() => undefined);
      return w.result();
    },
    done: loop,
  };
}

// Plain-language summary of the demonstration. Exported so it can be shown to the user before Grok sees it.
export function summarize(obs: Observation): { text: string; problems: string[] } {
  const problems: string[] = [];
  if (obs.frames < 5) problems.push('The camera sent almost no frames. Is the vision server running?');
  if (!obs.tracks.length) problems.push('No objects were recognised during the demonstration.');
  const movers = obs.tracks.filter(moved);
  const still = obs.tracks.filter((t) => !moved(t));
  if (obs.tracks.length && !movers.length) problems.push('Nothing moved enough to tell what the task is.');

  const unit = (p: Pos) => (p.mm ? `(${Math.round(p.x)} mm, ${Math.round(p.y)} mm)` : `(${p.x.toFixed(2)}, ${p.y.toFixed(2)} of the image)`);
  const lines: string[] = [];
  for (const m of movers) {
    // where it ended up: next to the closest object that did not move (the destination), if one is close
    let near: Track | undefined;
    let best = Infinity;
    for (const s of still) {
      const d = dist(m.last, s.last);
      if (d < best) [best, near] = [d, s];
    }
    const close = near && best < (m.last.mm ? 120 : 0.2);
    const vanished = m.lastSeen < Date.now() - 1500 && obs.seconds > 3;
    let where = close ? `into / onto the ${describeObject(near!)}` : `to ${unit(m.last)}`;
    if (vanished && close) where = `into the ${describeObject(near!)} (it was hidden there at the end)`;
    lines.push(`- The ${describeObject(m)} was moved from ${unit(m.first)} ${where}.`);
  }
  const order = movers.sort((a, b) => a.firstSeen - b.firstSeen);
  const text = [
    `A person demonstrated a task in front of the robot arm camera for ${obs.seconds} s.`,
    `Objects seen: ${obs.tracks.map(describeObject).join(', ') || 'none'}.`,
    lines.length ? `What changed, in order:\n${lines.join('\n')}` : 'No object changed place.',
    obs.hands ? 'A human hand did the moving; the robot should do the same moves with its gripper.' : '',
    order.length > 1 ? 'Keep the same order.' : '',
    'Write a skill that makes the robot arm reproduce this: detect each object first, pick it, place it at the same destination, verify, then go home.',
  ]
    .filter(Boolean)
    .join('\n');
  return { text, problems };
}

// note = what the person said or typed about the demo (optional, sharpens Grok's reading of it)
export async function skillFromVision(obs: Observation, note = ''): Promise<Generated & { summary: string }> {
  const { text, problems } = summarize(obs);
  if (problems.length && !obs.tracks.some(moved)) throw new Error(problems.join(' '));
  const prompt = note.trim() ? `${text}\nThe person also said: "${note.trim()}"` : text;
  const out = await generateSkill(prompt);
  const doc = out.doc;
  doc.skill.source = { type: 'vision', summary: text, note: note.trim() || undefined, frames: obs.frames, seconds: obs.seconds };
  doc.app = { ...doc.app, icon: 'eye-outline' };
  doc.interpretation = {
    ...(doc.interpretation ?? {}),
    status: 'draft',
    human_review_required: true,
  } as typeof doc.interpretation;
  return { ...out, doc, summary: text };
}
