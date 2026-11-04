import { useEffect, useRef, useState } from 'react';
import { Image, Modal, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import env from '../lib/config';
import { frameUrl } from '../lib/cameras';
import type { SkillDoc } from '../skills';
import { Observation, skillFromVision, summarize, visionHealthy, watch } from '../skills/fromVision';
import { Colors, radius, shadowFloat, type, useStyles, useTheme } from '../theme';
import { Button, IconButton } from './ui';

type Props = { onClose: () => void; onDraft: (doc: SkillDoc) => void };
type Phase = 'ready' | 'watching' | 'review' | 'writing';

// Show the robot a task: record what the vision server sees, then Grok turns it into a skill draft
// that opens in the skill editor for review (nothing is saved or run until the user does that).
export default function TeachFromCamera({ onClose, onDraft }: Props) {
  const s = useStyles(makeStyles);
  const { c } = useTheme();
  const [phase, setPhase] = useState<Phase>('ready');
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState({ objects: 0, frames: 0 });
  const [tick, setTick] = useState(0);
  const [obs, setObs] = useState<Observation | null>(null);
  const [note, setNote] = useState('');
  const job = useRef<ReturnType<typeof watch> | null>(null);

  useEffect(() => {
    visionHealthy().then(setError);
    const t = setInterval(() => setTick((n) => n + 1), 500);
    return () => {
      clearInterval(t);
      job.current?.stop();
    };
  }, []);

  const start = async () => {
    const bad = await visionHealthy();
    if (bad) return setError(bad);
    setError(null);
    setObs(null);
    setPhase('watching');
    const j = watch('arm', (objects, frames) => setLive({ objects, frames }));
    job.current = j;
    j.done.catch(async (e) => {
      if (job.current !== j) return;
      job.current = null;
      setError(`Lost the vision server: ${e?.message ?? e}`);
      setObs(await j.stop());
      setPhase('review');
    });
  };

  const stop = async () => {
    const j = job.current;
    job.current = null;
    if (!j) return;
    setObs(await j.stop());
    setPhase('review');
  };

  const write = async () => {
    if (!obs) return;
    setPhase('writing');
    setError(null);
    try {
      const out = await skillFromVision(obs, note);
      if (out.problems.length) setError(`Draft has problems to fix in the editor: ${out.problems[0]}`);
      onDraft(out.doc);
    } catch (e: any) {
      setError(String(e?.message ?? e));
      setPhase('review');
    }
  };

  const summary = obs ? summarize(obs) : null;

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View style={s.backdrop}>
        <SafeAreaView edges={['top', 'bottom']} style={s.wrap}>
          <View style={s.card}>
            <View style={s.head}>
              <Text style={s.title}>Learn from camera</Text>
              <IconButton icon="close" size={32} onPress={onClose} label="Close" />
            </View>
            <ScrollView contentContainerStyle={{ gap: 12, paddingBottom: 8 }}>
              <Text style={s.hint}>
                Press Start, do the task by hand in front of the arm camera (for example: put the red cube in the blue
                box), then press Stop. Grok writes a skill from what moved where. You review it before saving.
              </Text>
              {env.visionServer ? (
                <Image
                  source={{ uri: frameUrl(`${env.visionServer}/frame/arm.jpg?raw=false`, tick) }}
                  style={s.frame}
                  resizeMode="contain"
                />
              ) : null}
              {phase === 'watching' && (
                <Text style={[s.status, { color: c.accent }]}>
                  ● Watching: {live.objects} objects in view, {live.frames} frames
                </Text>
              )}
              {summary && phase !== 'watching' && (
                <View style={s.box}>
                  <Text style={s.label}>What the camera saw</Text>
                  <Text style={s.body}>{summary.text.split('\nWrite a skill')[0]}</Text>
                  {summary.problems.map((p) => (
                    <Text key={p} style={[s.body, { color: c.warn }]}>
                      {p}
                    </Text>
                  ))}
                  <TextInput
                    value={note}
                    onChangeText={setNote}
                    placeholder="Optional: say what the task is, e.g. sort the screws into the bucket"
                    placeholderTextColor={c.faint}
                    style={s.input}
                  />
                </View>
              )}
              {error && <Text style={[s.body, { color: c.bad }]}>{error}</Text>}
            </ScrollView>
            <View style={s.foot}>
              {phase === 'watching' ? (
                <Button label="Stop" icon="stop" variant="primary" onPress={stop} />
              ) : (
                <Button label={obs ? 'Record again' : 'Start'} icon="videocam-outline" onPress={start} disabled={phase === 'writing'} />
              )}
              {obs && phase !== 'watching' && (
                <Button label="Make skill" icon="sparkles-outline" variant="primary" loading={phase === 'writing'} onPress={write} />
              )}
            </View>
          </View>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    backdrop: { flex: 1, backgroundColor: c.scrim },
    wrap: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 16 },
    card: { width: '100%', maxWidth: 620, maxHeight: '100%', flexShrink: 1, backgroundColor: c.panel, borderRadius: radius.lg, padding: 20, boxShadow: shadowFloat },
    head: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 8 },
    title: { ...type.h2, color: c.text, flex: 1 },
    hint: { ...type.caption, color: c.dim },
    frame: { width: '100%', aspectRatio: 4 / 3, backgroundColor: c.page, borderRadius: radius.md },
    status: { ...type.label },
    box: { backgroundColor: c.panelHi, borderRadius: radius.md, padding: 12, gap: 8 },
    label: { ...type.overline, color: c.faint },
    body: { ...type.body, color: c.text },
    input: { backgroundColor: c.panel, borderRadius: radius.md, borderWidth: 1, borderColor: c.lineStrong, paddingHorizontal: 14, height: 44, ...type.body, color: c.text },
    foot: { flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 12 },
  });
