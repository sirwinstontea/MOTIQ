import { createAudioPlayer, AudioPlayer } from 'expo-audio';
import { File, Paths } from 'expo-file-system';
import { SAMPLE_RATE } from '../lib/config';
import { wav } from './pcm';

// Grok streams audio in small base64 pieces. We buffer one reply and play it as a wav.
// Not gapless streaming, but it works in Expo Go and replies come back fast enough.
export class Player {
  private chunks: Uint8Array[] = [];
  private player: AudioPlayer | null = null;
  private file: File | null = null;
  private n = 0;
  onDone: () => void = () => {};

  push(pcm: Uint8Array) {
    this.chunks.push(pcm);
  }

  get pending() {
    return this.chunks.length > 0;
  }

  async flush() {
    if (!this.chunks.length) return this.onDone();
    const data = wav(this.chunks, SAMPLE_RATE);
    this.chunks = [];
    this.cleanup();

    const f = new File(Paths.cache, `reply-${this.n++ % 4}.wav`);
    if (f.exists) f.delete();
    f.create();
    await f.write(data);
    this.file = f;

    const p = createAudioPlayer({ uri: f.uri });
    p.addListener('playbackStatusUpdate', (s) => {
      if (s.didJustFinish) {
        this.cleanup();
        this.onDone();
      }
    });
    this.player = p;
    p.play();
  }

  stop() {
    this.chunks = [];
    this.cleanup();
  }

  private cleanup() {
    this.player?.remove();
    this.player = null;
    try {
      this.file?.delete();
    } catch {}
    this.file = null;
  }
}
