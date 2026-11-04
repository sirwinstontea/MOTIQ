import { useEffect, useRef, useState } from 'react';
import { Alert } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { CameraHandle } from '../components/CameraPanel';
import { look } from '../lib/vision';
import type { Voice } from './useVoice';

const KEY = 'camera-enabled';

export function useCamera(voice: Voice) {
  const ref = useRef<CameraHandle>(null);
  const [looking, setLooking] = useState(false);
  const [enabled, setEnabledState] = useState(true);

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
  const { setLooker } = voice;
  useEffect(() => {
    setLooker(async (question) => {
      if (!enabled) throw new Error('the camera is turned off in the app');
      if (!ref.current) throw new Error('no camera on screen - open Talk or Cameras');
      const { source, b64 } = await ref.current.capture();
      const answer = await look(b64, question);
      return { ok: true, source, answer, image: b64 };
    });
    return () => setLooker(null);
  }, [setLooker, enabled]);

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

  return { ref, looking, show, enabled, setEnabled };
}
