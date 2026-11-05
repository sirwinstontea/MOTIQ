import { Platform } from 'react-native';
import { FileSystemUploadType, uploadAsync } from 'expo-file-system/legacy';
import env from '../lib/config';
import type { Recording } from '../lib/recorder';
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
  xyz_base_m?: [number, number, number] | null; // hand-eye calibrated arm camera, robot base frame
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

// body parts: the oggetti_mani model detects braccio / mano / polso (arm, hand, wrist); never task objects
const HAND = /\b(hand|wrist|mano|mani|polso|braccio|person|persona)\b/i;
const MOVE_MM = 30; // a move smaller than this is tracker noise
const MOVE_NORM = 0.05; // same, in image units, when the arm is not calibrated

const keyOf = (o: VisionObject) => `${(o.name || o.label).toLowerCase()}|${(o.color ?? '').toLowerCase()}`;
// best position first: robot base frame from the hand-eye calibrated arm camera (metres), then the ArUco table
// calibration (mm), then the image
const posOf = (o: VisionObject): Pos =>
  o.xyz_base_m
    ? { x: o.xyz_base_m[0] * 1000, y: o.xyz_base_m[1] * 1000, mm: true }
    : o.coords_mm
      ? { x: o.coords_mm.x, y: o.coords_mm.y, mm: true }
      : { x: o.bbox[0] + o.bbox[2] / 2, y: o.bbox[1] + o.bbox[3] / 2, mm: false };
const dist = (a: Pos, b: Pos) => Math.hypot(a.x - b.x, a.y - b.y);
const moved = (t: Track) => dist(t.first, t.last) > (t.first.mm && t.last.mm ? MOVE_MM : MOVE_NORM);
// the vision server names colours in Italian; skills (and DETECT matching in robot/runtime.ts) use English
const EN: Record<string, string> = { ross: 'red', blu: 'blue', verd: 'green', giall: 'yellow', ner: 'black', bianc: 'white', arancion: 'orange', viola: 'purple', grigi: 'grey', marron: 'brown', rosa: 'pink' };
const english = (c: string) => Object.entries(EN).find(([it]) => c.toLowerCase().startsWith(it))?.[1] ?? c;
// the demo pieces the custom model knows (tappo_bianco, anello_verde, vite_arancione, secchio_blu)
// (same names the bridge and the teach server use: orange screw, blue bucket, green ring, white bucket lid)
const NOUN: Record<string, string> = { tappo: 'bucket lid', anello: 'ring', vite: 'screw', secchio: 'bucket' };
const isColor = (w: string) => english(w) !== w;
// "vite_arancione" / "vite arancione" + colour "arancione" -> "orange screw"
export const describeObject = (t: Pick<Track, 'label' | 'color'>) => {
  const words = t.label.toLowerCase().split(/[_\s]+/).filter(Boolean);
  const color = t.color ?? words.find(isColor) ?? null;
  const noun = words.filter((w) => !isColor(w)).map((w) => NOUN[w] ?? w).join(' ') || t.label;
  return color ? `${english(color)} ${noun}` : noun;
};

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
    // same rules as the teach server's video summary (computer-vision-to-app/teach_server.py)
    "The arm's camera is on its head and it knows four objects: orange screw, blue bucket, green ring, white bucket lid; map what the person handled to the closest of these. Putting something in a cup/bucket/container means into the blue bucket; putting it to one side or on a pad means the delivery zone.",
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

// ---------------------------------------------------------------- from a recorded video
// The teach server runs computer-vision/vision/planner on the video: motion keyframes + Grok vision + Grok
// speech-to-text -> action tokens (pick / place / rotate ..., with objects and what was said). It returns a plain
// summary of them, which goes through the same skill generator as the live demonstration.

export const teachReady = () => !!env.teachServer;

export type PickedVideo = { uri: string; file?: Blob; mimeType?: string | null; name?: string };

// sends one request to the teach server, then writes the skill from its summary of the action tokens
// `fileUri` (phone/tablet): the recorded file is streamed from disk by expo-file-system. React Native's fetch can't
// send a Blob body on Android (the server got bytes that weren't the video: "video non leggibile").
async function teach(path: string, init: RequestInit, note: string, source: Record<string, unknown>, fileUri?: string) {
  if (!env.teachServer) throw new Error('No teach server set (EXPO_PUBLIC_TEACH_SERVER). Run computer-vision-to-app/run.py all.');
  const url = `${env.teachServer}${path}`;
  let status: number;
  let text: string;
  try {
    if (fileUri) {
      const up = await uploadAsync(url, fileUri, {
        httpMethod: 'POST',
        uploadType: FileSystemUploadType.BINARY_CONTENT,
        headers: (init.headers as Record<string, string>) ?? {},
      });
      status = up.status;
      text = up.body;
    } else {
      const r = await fetch(url, { method: 'POST', ...init });
      status = r.status;
      text = await r.text();
    }
  } catch (e: any) {
    const why = e?.message ? `: ${e.message}` : '';
    throw new Error(`Can't reach the teach server at ${env.teachServer}${why}`);
  }
  let j: any = {};
  try {
    j = JSON.parse(text);
  } catch {
    throw new Error(
      status >= 400
        ? `teach server ${status}: ${(text || '').slice(0, 200) || 'empty response'}`
        : `teach server returned non-JSON (${(text || '').slice(0, 120)})`,
    );
  }
  if (status >= 400 || !j.ok) throw new Error(j.error ?? `teach server ${status}`);
  const tokens = (j.tokens?.tokens ?? []) as unknown[];
  if (!tokens.length) throw new Error('No actions recognised in the video. Try a clearer, slower demonstration.');
  const summary: string = j.summary;
  if (!summary?.trim()) throw new Error('Teach server returned no summary for the demonstration.');
  const prompt = note.trim() ? `${summary}\nThe person also said: "${note.trim()}"` : summary;
  const out = await generateSkill(prompt);
  const doc = out.doc;
  doc.skill.source = { ...source, summary, note: note.trim() || undefined, tokens: tokens.length, transcript: j.tokens?.transcript || undefined };
  doc.app = { ...doc.app, icon: 'film-outline' };
  doc.interpretation = { ...(doc.interpretation ?? {}), status: 'draft', human_review_required: true } as typeof doc.interpretation;
  return { ...out, doc, summary };
}

function videoContentType(mime?: string | null): string {
  // strip codecs=... so the teach server picks the right file extension
  const raw = (mime || 'video/mp4').split(';')[0].trim().toLowerCase();
  return raw.startsWith('video/') ? raw : 'video/mp4';
}

export async function skillFromVideo(video: PickedVideo, note = ''): Promise<Generated & { summary: string }> {
  const q = note.trim() ? `?goal=${encodeURIComponent(note.trim())}` : '';
  const headers = { 'Content-Type': videoContentType(video.mimeType) };
  const src = { type: 'video', video: video.name };
  // native with a file on disk: upload the file itself (no Blob)
  if (Platform.OS !== 'web' && !video.file && video.uri) return teach(`/teach${q}`, { headers }, note, src, video.uri);
  let body: Blob;
  if (video.file) {
    body = video.file;
  } else if (video.uri) {
    try {
      body = await (await fetch(video.uri)).blob();
    } catch (e: any) {
      throw new Error(`Could not read the recorded video: ${e?.message ?? e}`);
    }
  } else {
    throw new Error('No video to send — recording produced neither a file nor a blob.');
  }
  if (!body.size) throw new Error('Recorded video is empty — try Show Motion again.');
  return teach(`/teach${q}`, { headers, body }, note, src);
}

// "Show Motion": the 15 s recording from CameraPanel.record (lib/recorder.ts)
export async function skillFromRecording(rec: Recording, camera: string, note = ''): Promise<Generated & { summary: string }> {
  if (rec.kind === 'video') {
    return skillFromVideo(
      { uri: rec.uri ?? '', file: rec.blob, mimeType: rec.mime, name: `${camera} recording` },
      note,
    );
  }
  if (!rec.frames?.length || rec.frames.length < 2) {
    throw new Error('Not enough camera frames were captured to learn a skill.');
  }
  const body = JSON.stringify({
    frames: rec.frames,
    fps: rec.fps,
    audio: rec.audio,
    audio_mime: rec.audioMime,
    goal: note.trim() || undefined,
  });
  return teach('/teach_frames', { headers: { 'Content-Type': 'application/json' }, body }, note, {
    type: 'video',
    video: `${camera} recording`,
  });
}
