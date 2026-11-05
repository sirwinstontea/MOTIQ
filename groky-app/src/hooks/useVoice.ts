import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Platform } from 'react-native';
import { requestRecordingPermissionsAsync, setAudioModeAsync, useAudioStream } from 'expo-audio';
import { SAMPLE_RATE } from '../lib/config';
import { level, resample } from '../voice/pcm';
import { VoiceSession } from '../voice/session';
import { startWebMic, WebMic } from '../voice/webMic';
import type { Line, Looker, SkillRunner, Status, Trace } from '../voice/types';
import type { VoiceCommand } from '../skills/fromVoice';

type Options = {
  onLevel?: (v: number) => void;
  onAsked?: (text: string, via: 'voice' | 'typed') => void;
  // a spoken request moved a robot; called again as more of its actions finish
  onCommand?: (cmd: VoiceCommand) => void;
  // a robot action Grok ran failed
  onToolError?: (what: string, error: string) => void;
};

// add or replace by id, keep the last `max`
function upsert<T extends { id: string }>(list: T[], item: T, max: number, merge = false): T[] {
  const i = list.findIndex((x) => x.id === item.id);
  if (i === -1) return [...list, item].slice(-max);
  const next = list.slice();
  next[i] = merge ? { ...list[i], ...item } : item;
  return next;
}

export function useVoice({ onLevel, onAsked, onCommand, onToolError }: Options = {}) {
  const [status, setStatus] = useState<Status>('off');
  const [lines, setLines] = useState<Line[]>([]);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [muted, setMuted] = useState(false);
  const [mic, setMic] = useState(false); // mic streaming; false = typed chat only
  const streaming = useRef(false);

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

  useEffect(() => {
    session.setCommandHandler(onCommand ?? null);
  }, [session, onCommand]);

  useEffect(() => {
    session.setToolErrorHandler(onToolError ?? null);
  }, [session, onToolError]);

  const onBuffer = (b: { data: ArrayBuffer; sampleRate: number }) => {
    if (mutedRef.current) return levelRef.current?.(0);
    levelRef.current?.(session.wantsAudio ? level(b.data) : 0);
    // iOS often ignores the requested rate and hands us 48k; browsers give 44.1k/48k
    session.appendAudio(resample(b.data, b.sampleRate, SAMPLE_RATE));
  };
  const onBufferRef = useRef(onBuffer);
  onBufferRef.current = onBuffer;

  // native: expo-audio's stream. On web it is null, so the browser mic (webMic) is used instead.
  const { stream: nativeStream } = useAudioStream({
    sampleRate: SAMPLE_RATE,
    channels: 1,
    encoding: 'int16',
    onBuffer: (b) => onBufferRef.current(b),
  });
  const webMic = useRef<WebMic | null>(null);
  const stream = useMemo(
    () => ({
      start: async () => {
        if (Platform.OS === 'web') webMic.current = await startWebMic((b) => onBufferRef.current(b));
        else await nativeStream.start();
      },
      stop: () => {
        if (Platform.OS === 'web') {
          webMic.current?.stop();
          webMic.current = null;
        } else nativeStream.stop();
      },
    }),
    [nativeStream],
  );

  // voice: connect if needed, then turn the mic on (also upgrades a typed-only session)
  const start = useCallback(async () => {
    if (Platform.OS !== 'web') {
      const perm = await requestRecordingPermissionsAsync();
      if (!perm.granted) {
        Alert.alert('Microphone', 'Motion needs the mic to hear you.');
        return;
      }
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
    }
    await session.start();
    if (session.status === 'off') return; // couldn't connect, the error is already in the log
    try {
      await stream.start();
    } catch (e) {
      session.stop();
      session.fail?.(`Microphone: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    streaming.current = true;
    setMic(true);
    setMuted(false);
  }, [session, stream]);

  const end = useCallback(() => {
    if (streaming.current) stream.stop();
    streaming.current = false;
    session.stop();
    setMic(false);
    levelRef.current?.(0);
  }, [session, stream]);

  // typed chat: connects without the mic if no session is running; Grok still answers out loud
  const say = useCallback(
    (text: string) => {
      if (session.status === 'off') {
        setAudioModeAsync({ playsInSilentMode: true }).catch(() => {});
        session.start();
      }
      session.say(text); // queued until connected
    },
    [session],
  );

  // the session can drop on its own (network, server); keep the mic in step
  if (status === 'off' && mic) setMic(false);
  useEffect(() => {
    if (status === 'off' && streaming.current) {
      stream.stop();
      streaming.current = false;
    }
  }, [status, stream]);

  useEffect(() => end, [end]);

  const actions = useMemo(
    () => ({
      share: (source: string, answer: string, image?: string) => session.share(source, answer, image),
      observe: (source: string, answer: string, image?: string) => session.observe(source, answer, image),
      progress: (t: Parameters<VoiceSession['progress']>[0]) => session.progress(t),
      // connects without the mic if needed (like typed chat); Motion still answers out loud
      teach: (d: Parameters<VoiceSession['teach']>[0]) => {
        if (session.status === 'off') {
          setAudioModeAsync({ playsInSilentMode: true }).catch(() => {});
          session.start();
        }
        session.teach(d); // queued until connected
      },
      setLooker: (fn: Looker | null) => session.setLooker(fn),
      setSkills: (runner: SkillRunner | null) => session.setSkills(runner),
      refreshTools: () => session.refreshTools(),
      emergencyStop: () => session.emergencyStop(),
      planDecision: (ok: boolean, target?: 'sim' | 'real') => session.planDecision(ok, target),
      clear: () => {
        setLines([]);
        setTraces([]);
      },
    }),
    [session],
  );

  return { status, lines, traces, muted, setMuted, mic, start, end, say, ...actions };
}

export type Voice = ReturnType<typeof useVoice>;
