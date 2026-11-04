import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert } from 'react-native';
import { requestRecordingPermissionsAsync, setAudioModeAsync, useAudioStream } from 'expo-audio';
import { SAMPLE_RATE } from '../lib/config';
import { level, resample } from '../voice/pcm';
import { VoiceSession } from '../voice/session';
import type { Line, Looker, Status, Trace } from '../voice/types';

type Options = {
  onLevel?: (v: number) => void;
  onAsked?: (text: string, via: 'voice' | 'typed') => void;
};

// add or replace by id, keep the last `max`
function upsert<T extends { id: string }>(list: T[], item: T, max: number, merge = false): T[] {
  const i = list.findIndex((x) => x.id === item.id);
  if (i === -1) return [...list, item].slice(-max);
  const next = list.slice();
  next[i] = merge ? { ...list[i], ...item } : item;
  return next;
}

export function useVoice({ onLevel, onAsked }: Options = {}) {
  const [status, setStatus] = useState<Status>('off');
  const [lines, setLines] = useState<Line[]>([]);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [muted, setMuted] = useState(false);

  const mutedRef = useRef(muted);
  const levelRef = useRef(onLevel);
  useEffect(() => {
    mutedRef.current = muted;
    levelRef.current = onLevel;
  }, [muted, onLevel]);

  const [session] = useState(
    () =>
      new VoiceSession({
        status: setStatus,
        line: (l) => setLines((prev) => upsert(prev, l, 200)),
        trace: (t) => setTraces((prev) => upsert(prev, t, 40, true)),
      }),
  );

  useEffect(() => {
    session.setAskedHandler(onAsked ?? null);
  }, [session, onAsked]);

  const { stream } = useAudioStream({
    sampleRate: SAMPLE_RATE,
    channels: 1,
    encoding: 'int16',
    onBuffer: (b) => {
      if (mutedRef.current) return levelRef.current?.(0);
      levelRef.current?.(session.wantsAudio ? level(b.data) : 0);
      // iOS often ignores the requested rate and hands us 48k
      session.appendAudio(resample(b.data, b.sampleRate, SAMPLE_RATE));
    },
  });

  const start = useCallback(async () => {
    const perm = await requestRecordingPermissionsAsync();
    if (!perm.granted) {
      Alert.alert('Microphone', 'Groky needs the mic to hear you.');
      return;
    }
    await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
    await session.start();
    await stream.start();
  }, [session, stream]);

  const end = useCallback(() => {
    stream.stop();
    session.stop();
    levelRef.current?.(0);
  }, [session, stream]);

  useEffect(() => end, [end]);

  const actions = useMemo(
    () => ({
      say: (text: string) => session.say(text),
      share: (source: string, answer: string, image?: string) => session.share(source, answer, image),
      setLooker: (fn: Looker | null) => session.setLooker(fn),
      emergencyStop: () => session.emergencyStop(),
      clear: () => {
        setLines([]);
        setTraces([]);
      },
    }),
    [session],
  );

  return { status, lines, traces, muted, setMuted, start, end, ...actions };
}

export type Voice = ReturnType<typeof useVoice>;
