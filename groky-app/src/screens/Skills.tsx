import { useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import SkillEditor from '../components/SkillEditor';
import TeachFromCamera from '../components/TeachFromCamera';
import { teachReady, visionReady } from '../skills/fromVision';
import { Button, IconButton, IconChip, Pill, Segmented } from '../components/ui';
import { DeviceId, devices } from '../devices';
import { useDevices } from '../hooks/useDevices';
import { useFullAccess } from '../lib/fullAccess';
import type { Run, Skills as SkillsApi } from '../hooks/useSkills';
import {
  deviceOf,
  icon,
  isLookStep,
  newSkill,
  newSkillId,
  physicalBlock,
  primLabel,
  realArmProblems,
  SkillDoc,
  stepLabel,
} from '../skills';
import { Colors, fonts, radius, type, useStyles, useTheme } from '../theme';

type Filter = 'all' | DeviceId;
type Editing = { doc: SkillDoc; json?: boolean } | null;

export default function Skills({ skills }: { skills: SkillsApi }) {
  const s = useStyles(makeStyles);
  const { width } = useWindowDimensions();
  const twoCols = width >= 1000;
  const mobile = width < 641;
  const [filter, setFilter] = useState<Filter>('all');
  const [editing, setEditing] = useState<Editing>(null);
  const [teaching, setTeaching] = useState(false);

  const list = skills.skills.filter((k) => filter === 'all' || deviceOf(k) === filter);
  const filters: { id: Filter; label: string }[] = [
    { id: 'all', label: 'All' },
    ...(Object.keys(devices) as DeviceId[]).map((id) => ({ id, label: devices[id].label })),
  ];

  const duplicate = (k: SkillDoc) => {
    const copy: SkillDoc = JSON.parse(JSON.stringify(k));
    copy.skill = { ...copy.skill, id: newSkillId(), name: `${k.skill.name} copy` };
    copy.app = { ...copy.app, builtin: false };
    setEditing({ doc: copy });
  };

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={[s.page, mobile && { paddingHorizontal: 16 }]}>
        <View style={s.head}>
          <View style={{ flex: 1 }}>
            <Text style={s.title}>Skills</Text>
            <Text style={s.sub}>
              Reusable multi-step tasks in the MOTIQ skill format. Run one here, or just ask Motion.
            </Text>
          </View>
          <Button
            label="Import"
            icon="download-outline"
            onPress={() => setEditing({ doc: newSkill('arm'), json: true })}
          />
          {(visionReady() || teachReady()) && (
            <Button label="Learn from camera" icon="eye-outline" onPress={() => setTeaching(true)} />
          )}
          <Button
            label="New skill"
            icon="add"
            variant="primary"
            onPress={() => setEditing({ doc: newSkill(filter === 'dog' ? 'dog' : 'arm') })}
          />
        </View>

        <View style={s.filters}>
          <Segmented options={filters} value={filter} onChange={setFilter} />
        </View>

        <View style={[s.grid, twoCols && s.gridTwo]}>
          {list.map((k) => (
            <View key={k.skill.id} style={twoCols ? s.half : undefined}>
              <Card skill={k} skills={skills} onEdit={() => setEditing({ doc: k })} onDuplicate={() => duplicate(k)} />
            </View>
          ))}
        </View>
      </ScrollView>

      {teaching && (
        <TeachFromCamera
          onClose={() => setTeaching(false)}
          onDraft={(doc) => {
            setTeaching(false);
            setEditing({ doc });
          }}
        />
      )}

      {editing && (
        <SkillEditor
          initial={editing.doc}
          startInJson={editing.json}
          onClose={() => setEditing(null)}
          onSave={(sk) => {
            skills.save(sk);
            setEditing(null);
          }}
        />
      )}
    </View>
  );
}

type CardProps = { skill: SkillDoc; skills: SkillsApi; onEdit: () => void; onDuplicate: () => void };

function Card({ skill, skills, onEdit, onDuplicate }: CardProps) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const { status, ready } = useDevices();
  const id = skill.skill.id;
  const device = deviceOf(skill);
  const builtin = !!skill.app?.builtin;
  const run = skills.run?.skillId === id ? skills.run : null;
  const active = run?.state === 'running';
  const busy = skills.running && !active;
  const real = !!devices[device].url;
  const offline = ready && status[device].state === 'offline';
  useFullAccess(); // re-check when full access is turned on/off
  const blocked = physicalBlock(skill, real);

  const start = () => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    skills.start(id);
  };

  const confirmDelete = () =>
    Alert.alert(`Delete "${skill.skill.name}"?`, 'This cannot be undone.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: () => skills.remove(id) },
    ]);

  const review = () => {
    if (skill.review?.approved) return skills.revoke(id);
    Alert.alert(
      `Approve "${skill.skill.name}"?`,
      'Check the steps and the preview first. Allowing the real robot lets this skill move hardware within the limits in the file. Editing the skill later removes the approval.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Approve, sim only', onPress: () => skills.approve(id, false) },
        { text: 'Approve + real robot', style: 'destructive', onPress: () => skills.approve(id, true) },
      ],
    );
  };

  const progress = run ? run.results.filter((r) => r?.ok).length / skill.steps.length : 0;
  const statusPill =
    run?.state === 'running'
      ? { label: 'running', tone: 'accent' as const }
      : run?.state === 'failed'
        ? { label: 'failed', tone: 'bad' as const }
        : run?.state === 'stopped'
          ? { label: 'stopped', tone: 'warn' as const }
          : run?.state === 'done'
            ? { label: 'done', tone: 'ok' as const }
            : null;

  // minimal card: name, one line of description, edit and run. Steps only show while running or after a run.
  // Preview, export and delete live in the editor (and delete on long press); approval only shows when the real
  // robot needs it.
  const needsApproval = !!blocked && !realArmProblems(skill).length;
  return (
    <Pressable
      onLongPress={builtin ? undefined : confirmDelete}
      style={[s.card, active && { backgroundColor: c.panel, boxShadow: `inset 0 0 0 1px ${c.accentSoft}` }]}
    >
      <View style={s.cardHead}>
        <IconChip icon={icon(skill)} color={active ? c.accent : c.text} bg={active ? c.accentSoft : undefined} />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={s.name} numberOfLines={1}>
            {skill.skill.name}
          </Text>
          {skill.skill.description ? (
            <Text style={s.meta} numberOfLines={1}>
              {skill.skill.description}
            </Text>
          ) : null}
        </View>
        {statusPill && statusPill.tone !== 'accent' && <Pill label={statusPill.label} tone={statusPill.tone} dot />}
        <IconButton
          icon={builtin ? 'copy-outline' : 'create-outline'}
          plain
          size={32}
          color={c.faint}
          onPress={builtin ? onDuplicate : onEdit}
          label={builtin ? 'Duplicate' : 'Edit'}
        />
        {active ? (
          <Button label="Stop" icon="hand-left" variant="danger" size="sm" onPress={skills.stop} />
        ) : needsApproval ? (
          <Button label="Approve" icon="shield-checkmark-outline" size="sm" variant="ghost" onPress={review} />
        ) : (
          <Button
            label={offline ? 'Offline' : 'Run'}
            icon="play"
            size="sm"
            onPress={start}
            disabled={busy || offline || !!blocked}
          />
        )}
      </View>

      {active && (
        <View style={s.progress}>
          <View
            style={[
              s.progressFill,
              { width: `${Math.round(progress * 100)}%` },
              run.state === 'failed' && { backgroundColor: c.bad },
            ]}
          />
        </View>
      )}

      {active && <Steps skill={skill} run={run} />}

      {run?.error && run.state !== 'running' && <Text style={s.runError}>{run.error}</Text>}
      {!run && blocked && !needsApproval && <Text style={s.runError}>{blocked}</Text>}
    </Pressable>
  );
}

function Steps({ skill, run }: { skill: SkillDoc; run: Run | null }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);

  return (
    <View>
      {skill.steps.map((step, i) => {
        const r = run?.results[i];
        const current = run?.state === 'running' && run.step === i && !r;
        const skipped = run && run.state !== 'running' && !r;
        const bg = current ? c.accent : r?.ok ? c.accentSoft : r ? c.bad : c.panelHi;
        return (
          <View key={step.id + i} style={[s.step, i === skill.steps.length - 1 && { borderBottomWidth: 0 }]}>
            <View style={s.stepRow}>
              <View
                style={[s.stepIcon, { backgroundColor: bg }, current && { boxShadow: `0 0 0 4px ${c.accentSoft}` }]}
              >
                {r ? (
                  <Ionicons name={r.ok ? 'checkmark' : 'close'} size={13} color={r.ok ? c.accent : c.accentInk} />
                ) : (
                  <Text style={[s.stepNum, current && { color: c.accentInk }]}>{i + 1}</Text>
                )}
              </View>
              <Text style={[s.stepText, current && { color: c.text }, skipped && { color: c.faint }]} numberOfLines={1}>
                {stepLabel(step)}
                {step.description ? <Text style={{ color: c.faint }}> {step.description}</Text> : null}
              </Text>
              {current && <Pill label="running" tone="accent" />}
              {r && (
                <Text style={[s.stepMs, !r.ok && { color: c.bad }]} numberOfLines={1}>
                  {r.ok ? `${r.ms}ms${r.attempts > 1 ? ` · ${r.attempts} tries` : ''}` : r.error}
                </Text>
              )}
            </View>
            {!isLookStep(step) && step.primitive_sequence?.length ? (
              <View style={s.prims}>
                {step.primitive_sequence.map((cl, j) => {
                  const now = current && run?.prim === j;
                  return (
                    <Text key={j} style={[s.prim, now && { color: c.accent }]} numberOfLines={1}>
                      {now ? '▸ ' : '  '}
                      {primLabel(cl)}
                    </Text>
                  );
                })}
              </View>
            ) : null}
            {r?.note && r.ok && <Text style={s.note}>{r.note}</Text>}
          </View>
        );
      })}
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    page: { paddingHorizontal: 24, paddingTop: 8, paddingBottom: 40 },
    head: { flexDirection: 'row', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap' },
    title: { ...type.title, color: c.text },
    sub: { ...type.caption, fontSize: 12, color: c.faint, marginTop: 6 },
    filters: { marginTop: 20, marginBottom: 16 },
    grid: { gap: 16 },
    gridTwo: { flexDirection: 'row', flexWrap: 'wrap' },
    half: { width: '48%', flexGrow: 1 },
    card: { backgroundColor: c.panel, borderRadius: radius.lg, padding: 20, gap: 12 },
    cardHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    name: { ...type.cardTitle, color: c.text },
    meta: { ...type.caption, color: c.faint, marginTop: 2 },
    desc: { ...type.body, color: c.dim },
    reviewRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    progress: { height: 4, borderRadius: 2, backgroundColor: c.hover, overflow: 'hidden' },
    progressFill: { height: 4, borderRadius: 2, backgroundColor: c.accent },
    previewRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    preview: { flex: 1, ...type.mono, color: c.faint },
    step: { paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: c.line, gap: 4 },
    stepRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
    stepIcon: { width: 24, height: 24, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
    stepNum: { fontFamily: fonts.mono, fontSize: 11, color: c.faint },
    stepText: { flex: 1, ...type.mono, fontSize: 12, color: c.dim },
    stepMs: { ...type.mono, fontSize: 10, color: c.faint, maxWidth: 180 },
    prims: { marginLeft: 36 },
    prim: { fontFamily: fonts.mono, fontSize: 11, lineHeight: 16, color: c.faint },
    note: { ...type.caption, fontSize: 11, color: c.faint, marginLeft: 36 },
    runError: { ...type.caption, fontSize: 12, color: c.bad },
    foot: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 4 },
    stats: { flex: 1, ...type.caption, color: c.faint },
  });
