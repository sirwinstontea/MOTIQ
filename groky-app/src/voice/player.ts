import { createAudioPlayer, AudioPlayer } from 'expo-audio';
import { File, Paths } from 'expo-file-system';
import { SAMPLE_RATE } from '../lib/config';
import { wav } from './pcm';

// Audio streams in as small base64 PCM pieces. Expo Go has no raw PCM output, so we cut the stream into wav
// segments: the first one starts after ~0.4s of audio arrives (instead of waiting for the whole reply), and
// while it plays the next segment is written and loaded so the switch-over is near gapless. Generation is
// faster than realtime, so later segments get long and there are only a few joins per reply.
const BYTES_PER_SEC = SAMPLE_RATE * 2; // 16-bit mono
const FIRST_BYTES = BYTES_PER_SEC * 0.2;
const NEXT_BYTES = BYTES_PER_SEC * 0.8;

type Seg = { player: AudioPlayer; file: File };

export class Player {
  private chunks: Uint8Array[] = [];
  private bytes = 0;
  private current: Seg | null = null;
  private next: Seg | null = null;
  private preparing = false;
  private ended = false; // the reply has finished arriving
  private gen = 0; // bumped by stop() so in-flight segment writes are dropped
  private n = 0;
  onStart: () => void = () => {};
  onDone: () => void = () => {};

  push(pcm: Uint8Array) {
    this.chunks.push(pcm);
    this.bytes += pcm.length;
    this.ended = false;
    this.pump();
  }

  // anything queued, loading or playing
  get pending() {
    return this.bytes > 0 || this.preparing || !!this.current || !!this.next;
  }

  // the reply is complete: play out whatever is left, then onDone
  async flush() {
    this.ended = true;
    if (!this.pending) return this.finish();
    this.pump();
  }

  stop() {
    this.gen++;
    this.chunks = [];
    this.bytes = 0;
    this.preparing = false;
    this.ended = false;
    this.release(this.current);
    this.release(this.next);
    this.current = this.next = null;
  }

  private pump() {
    if (this.preparing || this.next || !this.bytes) return;
    const need = this.current ? NEXT_BYTES : FIRST_BYTES;
    if (!this.ended && this.bytes < need) return;
    this.prepare();
  }

  private async prepare() {
    const gen = this.gen;
    const data = wav(this.chunks, SAMPLE_RATE);
    this.chunks = [];
    this.bytes = 0;
    this.preparing = true;

    let seg: Seg | null = null;
    try {
      const file = new File(Paths.cache, `reply-${this.n++ % 8}.wav`);
      if (file.exists) file.delete();
      file.create();
      await file.write(data);
      if (gen !== this.gen) return void file.delete();
      const player = createAudioPlayer({ uri: file.uri });
      player.volume = 1;
      seg = { player, file };
      player.addListener('playbackStatusUpdate', (s) => {
        if (s.didJustFinish && this.current === seg) this.advance();
      });
    } catch {
      // a bad segment shouldn't hang the turn
    } finally {
      if (gen === this.gen) this.preparing = false;
    }
    if (gen !== this.gen) return this.release(seg);

    if (seg) {
      if (this.current) this.next = seg;
      else this.play(seg);
    }
    this.pump();
    if (!this.pending && this.ended) this.finish();
  }

  private play(seg: Seg) {
    this.current = seg;
    this.onStart();
    seg.player.play();
  }

  private advance() {
    this.release(this.current);
    this.current = null;
    if (this.next) {
      const seg = this.next;
      this.next = null;
      this.play(seg);
    }
    this.pump();
    if (!this.pending && this.ended) this.finish();
  }

  private finish() {
    this.ended = false;
    this.onDone();
  }

  private release(seg: Seg | null) {
    if (!seg) return;
    seg.player.remove();
    try {
      seg.file.delete();
    } catch {}
  }
}
