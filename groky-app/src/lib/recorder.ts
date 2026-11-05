// "Show Motion" records a demonstration (video + audio) that the planner turns into a skill.
// Two shapes, depending on the camera:
//  - video:  the tablet's own camera records a real video file with sound
//  - frames: a jpeg-url camera (vision server, robot bridge) can't record, so the app grabs its frames and records
//            the tablet microphone alongside; the teach server joins them into one video.

export const RECORD_SECONDS = 15;
export const FRAME_FPS = 5;
// below this the teach server's ffmpeg probe fails with "video non leggibile"
export const MIN_VIDEO_BYTES = 2_000;
export const MIN_AUDIO_BYTES = 200;

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
// Caller must free the device first when the Expo CameraView preview is holding it (see CameraPanel).
export async function recordWeb(video: boolean, seconds: number, signal?: AbortSignal): Promise<{ blob: Blob; mime: string }> {
  const MR = (globalThis as any).MediaRecorder;
  if (!MR || !navigator.mediaDevices?.getUserMedia) throw new Error('this browser cannot record');

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
      video: video ? { width: { ideal: 1280 }, height: { ideal: 720 } } : false,
    });
  } catch (e: any) {
    const name = e?.name || 'Error';
    if (name === 'NotReadableError' || name === 'TrackStartError') {
      throw new Error('camera is busy (close other apps using it, or wait for the preview to release)');
    }
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
      throw new Error('camera / microphone permission denied');
    }
    throw new Error(`could not open ${video ? 'camera' : 'microphone'}: ${e?.message ?? name}`);
  }

  const mime = pickMime(video);
  const chunks: Blob[] = [];
  let rec: InstanceType<typeof MR>;
  try {
    rec = new MR(stream, mime ? { mimeType: mime } : undefined);
  } catch (e: any) {
    stream.getTracks().forEach((t) => t.stop());
    throw new Error(`MediaRecorder failed: ${e?.message ?? e}`);
  }

  rec.ondataavailable = (e: any) => e.data?.size && chunks.push(e.data);
  const done = new Promise<void>((r) => {
    rec.onstop = () => r();
    rec.onerror = () => r();
  });

  try {
    rec.start(250); // short timeslice so an early stop still has chunks
    await Promise.race([wait(seconds * 1000), new Promise<void>((r) => signal?.addEventListener('abort', () => r(), { once: true }))]);
    if (rec.state === 'recording') {
      try {
        rec.requestData();
      } catch {
        // older browsers
      }
      rec.stop();
    }
    await done;
  } finally {
    stream.getTracks().forEach((t) => t.stop());
  }

  const type = mime.split(';')[0] || (video ? 'video/webm' : 'audio/webm');
  const blob = new Blob(chunks, { type });
  const min = video ? MIN_VIDEO_BYTES : MIN_AUDIO_BYTES;
  if (blob.size < min) {
    throw new Error(
      signal?.aborted
        ? 'recording stopped too early — hold Show Motion a couple of seconds before stopping'
        : `recording was empty (${blob.size} bytes). Try again, or use Skills → Learn from camera → Upload video.`,
    );
  }
  return { blob, mime: type };
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
