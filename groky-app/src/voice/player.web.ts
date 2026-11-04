import { SAMPLE_RATE } from '../lib/config';

// Browser version of the reply player: expo-audio has no raw PCM output and no files on web, so the 16-bit PCM
// pieces are scheduled straight onto a Web Audio clock, back to back, which is gapless.
export class Player {
  private ctx: AudioContext | null = null;
  private at = 0; // when the next piece should start
  private active = new Set<AudioBufferSourceNode>();
  private ended = false; // the reply has finished arriving
  private started = false;
  onStart: () => void = () => {};
  onDone: () => void = () => {};

  private context() {
    if (!this.ctx) {
      const AC = (window as any).AudioContext ?? (window as any).webkitAudioContext;
      try {
        this.ctx = new AC({ sampleRate: SAMPLE_RATE });
      } catch {
        this.ctx = new AC();
      }
    }
    const ctx = this.ctx as AudioContext;
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    return ctx;
  }

  push(pcm: Uint8Array) {
    const n = Math.floor(pcm.length / 2);
    if (!n) return;
    const ctx = this.context();
    const src = new Int16Array(pcm.buffer, pcm.byteOffset, n);
    const buf = ctx.createBuffer(1, n, SAMPLE_RATE); // the browser resamples to its own rate
    const ch = buf.getChannelData(0);
    for (let i = 0; i < n; i++) ch[i] = src[i] / 32768;

    const node = ctx.createBufferSource();
    node.buffer = buf;
    node.connect(ctx.destination);
    const when = Math.max(this.at, ctx.currentTime + 0.05);
    node.start(when);
    this.at = when + buf.duration;
    this.active.add(node);
    this.ended = false;
    node.onended = () => {
      this.active.delete(node);
      if (!this.active.size && this.ended) this.finish();
    };
    if (!this.started) {
      this.started = true;
      this.onStart();
    }
  }

  get pending() {
    return this.active.size > 0;
  }

  async flush() {
    this.ended = true;
    if (!this.pending) this.finish();
  }

  stop() {
    for (const node of this.active) {
      node.onended = null;
      try {
        node.stop();
      } catch {}
    }
    this.active.clear();
    this.at = 0;
    this.ended = false;
    this.started = false;
  }

  private finish() {
    this.ended = false;
    this.started = false;
    this.at = 0;
    this.onDone();
  }
}
