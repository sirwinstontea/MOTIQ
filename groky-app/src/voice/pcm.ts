const CHUNK = 0x8000;

export function toBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK) as unknown as number[]);
  }
  return btoa(bin);
}

export function fromBase64(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// 16-bit mono PCM -> wav, since the player wants a file
export function wav(chunks: Uint8Array[], rate: number): Uint8Array {
  const len = chunks.reduce((n, c) => n + c.length, 0);
  const out = new Uint8Array(44 + len);
  const v = new DataView(out.buffer);
  const str = (o: number, s: string) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));

  str(0, 'RIFF');
  v.setUint32(4, 36 + len, true);
  str(8, 'WAVE');
  str(12, 'fmt ');
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, 'data');
  v.setUint32(40, len, true);

  let off = 44;
  for (const c of chunks) {
    out.set(c, off);
    off += c.length;
  }
  return out;
}

// rough mic level for the orb, 0..1
export function level(buf: ArrayBuffer): number {
  const s = new Int16Array(buf);
  if (!s.length) return 0;
  let sum = 0;
  for (let i = 0; i < s.length; i += 4) sum += s[i] * s[i];
  const rms = Math.sqrt(sum / (s.length / 4)) / 32768;
  return Math.min(1, rms * 6);
}

// linear resample of int16 mono, good enough for speech
export function resample(buf: ArrayBuffer, from: number, to: number): ArrayBuffer {
  if (from === to) return buf;
  const src = new Int16Array(buf);
  const n = Math.floor((src.length * to) / from);
  const out = new Int16Array(n);
  const step = from / to;
  for (let i = 0; i < n; i++) {
    const x = i * step;
    const j = Math.floor(x);
    const a = src[j] ?? 0;
    const b = src[j + 1] ?? a;
    out[i] = a + (b - a) * (x - j);
  }
  return out.buffer;
}
