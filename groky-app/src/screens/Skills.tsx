import { useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import SkillEditor from '../components/SkillEditor';
import { Button, IconButton, IconChip, Pill, Segmented } from '../components/ui';
import { DeviceId, devices } from '../devices';
import { useDevices } from '../hooks/useDevices';
import type { Run, Skills as SkillsApi } from '../hooks/useSkills';
import { ago } from '../lib/time';
import { Skill, stepLabel } from '../skills';
import { Colors, fonts, radius, type, useStyles, useTheme } from '../theme';

type Filter = 'all' | DeviceId;

export default function Skills({ skills }: { skills: SkillsApi }) {
  const s = useStyles(makeStyles);
  const { width } = useWindowDimensions();
  const twoCols = width >= 1000;
  const mobile = width < 641;
  const [filter, setFilter] = useState<Filter>('all');
  const [editing, setEditing] = useState<Skill | 'new' | null>(null);

  const list = skills.skills.filter((k) => filter === 'all' || k.device === filter);
  const filters: { id: Filter; label: string }[] = [
    { id: 'all', label: 'All' },
    ...(Object.keys(devices) as DeviceId[]).map((id) => ({ id, label: devices[id].label })),
  ];

  const duplicate = (k: Skill) =>
    setEditing({ ...k, id: '', name: `${k.name} copy`, builtin: false, steps: k.steps.map((st) => ({ ...st })) });

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={[s.page, mobile && { paddingHorizontal: 16 }]}>
        <View style={s.head}>
          <View style={{ flex: 1 }}>
            <Text style={s.title}>Skills</Text>
            <Text style={s.sub}>Reusable multi-step tasks. Run one here, or just ask Groky to do it.</Text>
          </View>
          <Button label="New skill" icon="add" variant="primary" onPress={() => setEditing('new')} />
        </View>

        <View style={s.filters}>
          <Segmented options={filters} value={filter} onChange={setFilter} />
        </View>

        <View style={[s.grid, twoCols && s.gridTwo]}>
          {list.map((k) => (
            <View key={k.id} style={twoCols ? s.half : undefined}>
              <Card skill={k} skills={skills} onEdit={() => setEditing(k)} onDuplicate={() => duplicate(k)} />
            </View>
          ))}
        </View>
      </ScrollView>

      {editing && (
        <SkillEditor
          initial={editing === 'new' ? null : editing}
          defaultDevice={filter === 'all' ? 'arm' : filter}
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

type CardProps = { skill: Skill; skills: SkillsApi; onEdit: () => void; onDuplicate: () => void };

function Card({ skill, skills, onEdit, onDuplicate }: CardProps) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const { status, ready } = useDevices();
  const st = skills.stats[skill.id];
  const run = skills.run?.skillId === skill.id ? skills.run : null;
  const active = run?.state === 'running';
  const busy = skills.running && !active;
  const offline = ready && status[skill.device].state === 'offline';
  const [showSteps, setShowSteps] = useState(false);

  const start = () => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    skills.start(skill.id);
  };

  const confirmDelete = () =>
    Alert.alert(`Delete "${skill.name}"?`, 'This cannot be undone.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: () => skills.remove(skill.id) },
    ]);

  const expanded = showSteps || !!run;
  const progress = run ? run.results.filter((r) => r.ok).length / skill.steps.length : 0;
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

  return (
    <View style={[s.card, active && { backgroundColor: c.panel, boxShadow: `inset 0 0 0 1px ${c.accentSoft}` }]}>
      <View style={s.cardHead}>
        <IconChip icon={skill.icon} color={active ? c.accent : c.text} bg={active ? c.accentSoft : undefined} />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={s.name} numberOfLines={1}>
            {skill.name}
          </Text>
          <Text style={s.meta}>
            {devices[skill.device].label} · {skill.steps.length} steps{skill.builtin ? ' · built-in' : ''}
          </Text>
        </View>
        {statusPill && <Pill label={statusPill.label} tone={statusPill.tone} dot={statusPill.tone !== 'accent'} />}
        <IconButton
          icon={skill.builtin ? 'copy-outline' : 'create-outline'}
          plain
          size={32}
          color={c.faint}
          onPress={skill.builtin ? onDuplicate : onEdit}
          label={skill.builtin ? 'Duplicate' : 'Edit'}
        />
        {!skill.builtin && (
          <IconButton icon="trash-outline" plain size={32} color={c.faint} onPress={confirmDelete} label="Delete" />
        )}
      </View>

      <Text style={s.desc}>{skill.description || 'No description.'}</Text>

      {run && (
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

      {expanded ? (
        <Steps skill={skill} run={run} />
      ) : (
        <Pressable onPress={() => setShowSteps(true)} style={s.previewRow}>
          <Text style={s.preview} numberOfLines={1}>
            {skill.steps.map(stepLabel).join('  →  ')}
          </Text>
          <Ionicons name="chevron-down" size={14} color={c.faint} />
        </Pressable>
      )}

      <View style={s.foot}>
        <Text style={s.stats} numberOfLines={1}>
          {run && run.state !== 'running'
            ? runSummary(run)
            : st
              ? `ran ${st.runs}x · ${st.ok}/${st.runs} ok · last ${ago(st.lastAt)}`
              : 'never run'}
        </Text>
        {active ? (
          <Button label="Stop" icon="hand-left" variant="danger" size="sm" onPress={skills.stop} />
        ) : (
          <Button
            label={offline ? 'Offline' : busy ? 'Busy' : 'Run'}
            icon="play"
            size="sm"
            onPress={start}
            disabled={busy || offline}
          />
        )}
      </View>
    </View>
  );
}

function runSummary(run: Run) {
  const secs = (((run.endedAt ?? Date.now()) - run.startedAt) / 1000).toFixed(1);
  const label = { done: 'finished', failed: 'failed', stopped: 'stopped', running: 'running' }[run.state];
  return `${label} in ${secs}s${run.via === 'voice' ? ' · by Groky' : ''}`;
}

function Steps({ skill, run }: { skill: Skill; run: Run | null }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);

  return (
    <View style={s.steps}>
      {skill.steps.map((step, i) => {
        const r = run?.results[i];
        const current = run?.state === 'running' && run.step === i && !r;
        const skipped = run && run.state !== 'running' && !r;
        const bg = current ? c.accent : r?.ok ? c.accentSoft : r ? c.bad : c.panelHi;
        return (
          <View key={i} style={[s.step, i === skill.steps.length - 1 && { borderBottomWidth: 0 }]}>
            <View style={[s.stepIcon, { backgroundColor: bg }, current && { boxShadow: `0 0 0 4px ${c.accentSoft}` }]}>
              {r ? (
                <Ionicons name={r.ok ? 'checkmark' : 'close'} size={13} color={r.ok ? c.accent : c.accentInk} />
              ) : (
                <Text style={[s.stepNum, current && { color: c.accentInk }]}>{i + 1}</Text>
              )}
            </View>
            <Text style={[s.stepText, current && { color: c.text }, skipped && { color: c.faint }]} numberOfLines={1}>
              {stepLabel(step)}
            </Text>
            {current && <Pill label="running" tone="accent" />}
            {r && (
              <Text style={[s.stepMs, !r.ok && { color: c.bad }]} numberOfLines={1}>
                {r.ok ? `${r.ms}ms` : r.error}
              </Text>
            )}
          </View>
        );
      })}
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    page: { paddingHorizontal: 24, paddingTop: 8, paddingBottom: 40 },
    head: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
    title: { ...type.title, color: c.text },
    sub: { ...type.caption, fontSize: 12, color: c.faint, marginTop: 6 },
    filters: { marginTop: 20, marginBottom: 16 },
    grid: { gap: 16 },
    gridTwo: { flexDirection: 'row', flexWrap: 'wrap' },
    half: { width: '48%', flexGrow: 1 },
    card: { backgroundColor: c.panel, borderRadius: radius.lg, padding: 20, gap: 12 },
    cardHead: { flexDirection: 'row', alignItems: 'center', gap: 12 },
    name: { ...type.cardTitle, color: c.text },
    meta: { ...type.caption, color: c.faint, marginTop: 2 },
    desc: { ...type.body, color: c.dim },
    progress: { height: 4, borderRadius: 2, backgroundColor: c.hover, overflow: 'hidden' },
    progressFill: { height: 4, borderRadius: 2, backgroundColor: c.accent },
    previewRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    preview: { flex: 1, ...type.mono, color: c.faint },
    steps: {},
    step: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      paddingVertical: 8,
      borderBottomWidth: 1,
      borderBottomColor: c.line,
    },
    stepIcon: { width: 24, height: 24, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
    stepNum: { fontFamily: fonts.mono, fontSize: 11, color: c.faint },
    stepText: { flex: 1, ...type.mono, fontSize: 12, color: c.dim },
    stepMs: { ...type.mono, fontSize: 10, color: c.faint, maxWidth: 160 },
    foot: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 4 },
    stats: { flex: 1, ...type.caption, color: c.faint },
  });
