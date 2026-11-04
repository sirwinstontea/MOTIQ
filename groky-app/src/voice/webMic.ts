// Browser microphone as 16-bit mono PCM buffers (expo-audio's useAudioStream has no web version).
export type MicBuffer = { data: ArrayBuffer; sampleRate: number };

export type WebMic = { stop: () => void };

export async function startWebMic(onBuffer: (b: MicBuffer) => void): Promise<WebMic> {
  const media = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const AC = (window as any).AudioContext ?? (window as any).webkitAudioContext;
  const ctx: AudioContext = new AC();
  if (ctx.state === 'suspended') await ctx.resume().catch(() => {});

  const source = ctx.createMediaStreamSource(media);
  const proc = ctx.createScriptProcessor(2048, 1, 1);
  proc.onaudioprocess = (e) => {
    const f = e.inputBuffer.getChannelData(0);
    const out = new Int16Array(f.length);
    for (let i = 0; i < f.length; i++) {
      const s = Math.max(-1, Math.min(1, f[i]));
      out[i] = s < 0 ? s * 32768 : s * 32767;
    }
    onBuffer({ data: out.buffer, sampleRate: ctx.sampleRate });
  };
  // a script processor only runs while it is connected onward; the output stays silent
  const mute = ctx.createGain();
  mute.gain.value = 0;
  source.connect(proc);
  proc.connect(mute);
  mute.connect(ctx.destination);

  return {
    stop() {
      proc.onaudioprocess = null;
      try {
        source.disconnect();
        proc.disconnect();
        mute.disconnect();
      } catch {}
      media.getTracks().forEach((t) => t.stop());
      ctx.close().catch(() => {});
    },
  };
}
