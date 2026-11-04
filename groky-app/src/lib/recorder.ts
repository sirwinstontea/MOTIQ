// "Show Motion" records a demonstration (video + audio) that the planner turns into a skill.
// Two shapes, depending on the camera:
//  - video:  the tablet's own camera records a real video file with sound
//  - frames: a jpeg-url camera (vision server, robot bridge) can't record, so the app grabs its frames and records
//            the tablet microphone alongside; the teach server joins them into one video.

export const RECORD_SECONDS = 15;
export const FRAME_FPS = 5;

export type Recording =
  | { kind: 'video'; uri?: string; blob?: Blob; mime: string }
  | { kind: 'frames'; frames: string[]; fps: number; audio?: string; audioMime?: string };

export const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',')[1] ?? '');
    r.onerror = () => reject(r.error);
    r.readAsDataURL(blob);
  });
}

function pickMime(video: boolean): string {
  const MR = (globalThis as any).MediaRecorder;
  const options = video
    ? ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm', 'video/mp4']
    : ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
  return options.find((m) => MR?.isTypeSupported?.(m)) ?? '';
}

// Browser: record camera and/or microphone for `seconds` with MediaRecorder.
export async function recordWeb(video: boolean, seconds: number, signal?: AbortSignal): Promise<{ blob: Blob; mime: string }> {
  const MR = (globalThis as any).MediaRecorder;
  if (!MR || !navigator.mediaDevices?.getUserMedia) throw new Error('this browser cannot record');
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true },
    video: video ? { width: { ideal: 1280 }, height: { ideal: 720 } } : false,
  });
  const mime = pickMime(video);
  const rec = new MR(stream, mime ? { mimeType: mime } : undefined);
  const chunks: Blob[] = [];
  rec.ondataavailable = (e: any) => e.data?.size && chunks.push(e.data);
  const done = new Promise<void>((r) => (rec.onstop = () => r()));
  rec.start(1000);
  await Promise.race([wait(seconds * 1000), new Promise((r) => signal?.addEventListener('abort', r))]);
  rec.stop();
  await done;
  stream.getTracks().forEach((t) => t.stop());
  const type = mime.split(';')[0] || (video ? 'video/webm' : 'audio/webm');
  return { blob: new Blob(chunks, { type }), mime: type };
}

// Grab jpeg frames (base64) from a url camera at `fps` for `seconds`. Missed frames are skipped.
export async function grabFrames(
  grab: () => Promise<string>,
  seconds: number,
  fps: number,
  signal?: AbortSignal,
): Promise<string[]> {
  const frames: string[] = [];
  const end = Date.now() + seconds * 1000;
  while (Date.now() < end && !signal?.aborted) {
    const t0 = Date.now();
    try {
      frames.push(await grab());
    } catch {
      // dropped frame
    }
    await wait(Math.max(0, 1000 / fps - (Date.now() - t0)));
  }
  return frames;
}
