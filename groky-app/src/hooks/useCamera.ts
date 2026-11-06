import { useEffect, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { CameraHandle } from '../components/CameraPanel';
import { RECORD_SECONDS } from '../lib/recorder';
import { look } from '../lib/vision';
import { setArmLooker } from '../robot/runtime';
import type { SkillDoc } from '../skills';
import { skillFromRecording, teachReady } from '../skills/fromVision';
import { useNotify } from './useNotify';
import type { Voice } from './useVoice';

const KEY = 'camera-enabled';
const WATCH_EVERY = 4000; // ms between live frames, counted after each vision call finishes
const SAME = 'SAME';

// onSkill saves the skill written from a Show Motion recording (problems = what's left after Grok's repair round)
// and returns the id it is saved under (an existing skill's id if it was a copy of one)
export function useCamera(voice: Voice, onSkill?: (doc: SkillDoc, problems: string[]) => string) {
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

  // "Show Motion": show the task on camera and explain it out loud. Records video + audio for up to RECORD_SECONDS
  // (press again to stop sooner), the planner on the teach server turns it into action tokens, Grok writes the
  // skill.json and it is saved to Skills. Then Motion says what it understood and runs the skill.
  // Every step shows in "What Motion is doing"; anything that fails is an error notification.
  const { notify } = useNotify();
  const [phase, setPhase] = useState('');
  const [recording, setRecording] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const show = async () => {
    if (recording) {
      abort.current?.abort(); // stop early, keep what was recorded
      return;
    }
    if (!enabled || !ref.current || looking) return;
    if (!teachReady()) {
      notify({
        kind: 'bad',
        title: 'Show Motion needs the teach server',
        body: 'Run: python computer-vision/to-app/run.py all, then restart the app.',
      });
      return;
    }
    const ctl = new AbortController();
    abort.current = ctl;
    const recId = `show-rec-${Date.now()}`;
    const learnId = `show-learn-${Date.now()}`;
    // Motion mustn't answer the commentary while it's being recorded: mute the live mic, restore it after
    const wasMuted = voice.muted;
    if (voice.mic) voice.setMuted(true);
    setLooking(true);
    setRecording(true);
    const t0 = Date.now();
    const left = () => Math.max(0, RECORD_SECONDS - Math.floor((Date.now() - t0) / 1000));
    const showLeft = () => {
      setPhase(`Stop recording · ${left()}s`);
      voice.progress({ id: recId, kind: 'hear', title: `Recording your demonstration · ${left()}s left`, detail: 'Video + audio. Press Show Motion again to stop.', active: true });
    };
    showLeft();
    const tick = setInterval(showLeft, 500);
    let step = recId;
    try {
      const { source, rec } = await ref.current.record(RECORD_SECONDS, ctl.signal);
      clearInterval(tick);
      setRecording(false);
      if (voice.mic) voice.setMuted(wasMuted);
      const secs = Math.round((Date.now() - t0) / 1000);
      voice.progress({ id: recId, kind: 'hear', title: `Recorded your demonstration (${secs}s)`, detail: `${source} camera`, ok: true, ms: Date.now() - t0, active: false });

      step = learnId;
      const t1 = Date.now();
      setPhase('Sending video to learn…');
      voice.progress({
        id: learnId,
        kind: 'think',
        title: 'Learning the skill from your video',
        detail: 'Uploading the recording, extracting keyframes and speech, then Grok writes the skill.json',
        active: true,
      });
      // show a mid-step so a slow teach server doesn't look stuck after "Recorded"
      const learnTick = setInterval(() => {
        const secs = Math.round((Date.now() - t1) / 1000);
        setPhase(`Learning the skill… ${secs}s`);
        voice.progress({
          id: learnId,
          kind: 'think',
          title: 'Learning the skill from your video',
          detail: `Still working (${secs}s): keyframes, speech, actions → skill.json`,
          active: true,
        });
      }, 1000);
      let out;
      try {
        out = await skillFromRecording(rec, source);
      } finally {
        clearInterval(learnTick);
      }
      voice.progress({
        id: learnId,
        kind: 'think',
        title: `Learned "${out.doc.skill.name}"`,
        detail: out.summary.split('\nWrite a skill')[0],
        ok: true,
        ms: Date.now() - t1,
        active: false,
      });

      const id = onSkill?.(out.doc, out.problems) ?? out.doc.skill.id;
      const name = out.doc.skill.name;
      // Motion answers out loud and runs it; run failures come back as notifications from useSkills
      voice.teach({
        summary: out.summary,
        skillId: id,
        skillName: name,
        transcript: String((out.doc.skill.source as any)?.transcript ?? ''),
      });
    } catch (e: any) {
      const msg = String(e?.message ?? e);
      voice.progress({
        id: step,
        kind: 'error',
        title: step === recId ? 'Recording failed' : 'Could not learn a skill',
        detail: msg,
        ok: false,
        active: false,
      });
      notify({
        kind: 'bad',
        title: step === recId ? 'Show Motion: recording failed' : 'Show Motion: no skill learned',
        body: msg,
      });
    } finally {
      clearInterval(tick);
      if (voice.mic) voice.setMuted(wasMuted);
      abort.current = null;
      setRecording(false);
      setPhase('');
      setLooking(false);
    }
  };

  return { ref, looking, recording, phase, show, enabled, setEnabled, watching, setWatching };
}
