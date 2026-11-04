import { useEffect, useRef, useState } from 'react';
import { Alert } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { CameraHandle } from '../components/CameraPanel';
import { look } from '../lib/vision';
import { setArmLooker } from '../robot/runtime';
import type { Voice } from './useVoice';

const KEY = 'camera-enabled';
const WATCH_EVERY = 4000; // ms between live frames, counted after each vision call finishes
const SAME = 'SAME';

export function useCamera(voice: Voice) {
  const ref = useRef<CameraHandle>(null);
  const [looking, setLooking] = useState(false);
  const [enabled, setEnabledState] = useState(true);
  const [watching, setWatching] = useState(false);
  const lastSeen = useRef('');

  useEffect(() => {
    AsyncStorage.getItem(KEY)
      .then((v) => v === 'off' && setEnabledState(false))
      .catch(() => {});
  }, []);

  const setEnabled = (on: boolean) => {
    setEnabledState(on);
    AsyncStorage.setItem(KEY, on ? 'on' : 'off').catch(() => {});
  };

  // lets Grok call camera_look by itself
  // also used by skill DETECT / VERIFY steps and arm_detect / arm_verify
  const { setLooker } = voice;
  useEffect(() => {
    const looker = async (question: string) => {
      if (!enabled) throw new Error('the camera is turned off in the app');
      if (!ref.current) throw new Error('no camera on screen - open Talk or Cameras');
      const { source, b64 } = await ref.current.capture();
      const answer = await look(b64, question);
      return { ok: true, source, answer, image: b64 };
    };
    setLooker(looker);
    setArmLooker(looker);
    return () => {
      setLooker(null);
      setArmLooker(null);
    };
  }, [setLooker, enabled]);

  // live watch: while a session is up, keep looking and pass along only what changed
  const live = voice.status !== 'off';
  const { observe } = voice;
  useEffect(() => {
    if (!watching || !enabled || !live) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        if (ref.current) {
          const { source, b64 } = await ref.current.capture();
          const prev = lastSeen.current;
          const question = prev
            ? `Earlier the scene was: "${prev}". If nothing important changed, reply exactly ${SAME}. Otherwise describe the scene now.`
            : 'Describe the scene.';
          const answer = await look(b64, question, 'low');
          if (!stopped && !answer.toUpperCase().startsWith(SAME)) {
            lastSeen.current = answer;
            observe(source, answer, b64);
          }
        }
      } catch {
        // a dropped frame is fine, try again next tick
      }
      if (!stopped) timer = setTimeout(tick, WATCH_EVERY);
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [watching, enabled, live, observe]);

  // a new session starts with a fresh picture
  useEffect(() => {
    if (!live) lastSeen.current = '';
  }, [live]);

  const show = async () => {
    if (!enabled || !ref.current) return;
    setLooking(true);
    try {
      const { source, b64 } = await ref.current.capture();
      const answer = await look(b64, 'What do you see?');
      if (voice.status === 'off') voice.start();
      voice.share(source, answer, b64);
    } catch (e: any) {
      Alert.alert('Camera', String(e?.message ?? e));
    } finally {
      setLooking(false);
    }
  };

  return { ref, looking, show, enabled, setEnabled, watching, setWatching };
}
