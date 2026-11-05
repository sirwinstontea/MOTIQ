import { useState } from 'react';
import { Alert, Pressable, ScrollView, Share, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import SkillEditor from '../components/SkillEditor';
import SkillPreview from '../components/SkillPreview';
import TeachFromCamera from '../components/TeachFromCamera';
import { teachReady, visionReady } from '../skills/fromVision';
import { Button, IconButton, IconChip, Pill, Segmented } from '../components/ui';
import { DeviceId, devices } from '../devices';
import { useDevices } from '../hooks/useDevices';
import type { Run, Skills as SkillsApi } from '../hooks/useSkills';
import { ago } from '../lib/time';
import {
  deviceOf,
  exportSkill,
  icon,
  isLookStep,
  newSkill,
  newSkillId,
  physicalBlock,
  primitiveCount,
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
  const st = skills.stats[id];
  const run = skills.run?.skillId === id ? skills.run : null;
  const active = run?.state === 'running';
  const busy = skills.running && !active;
  const real = !!devices[device].url;
  const offline = ready && status[device].state === 'offline';
  const blocked = physicalBlock(skill, real);
  const [showSteps, setShowSteps] = useState(false);
  const [showPreview, setShowPreview] = useState(false);

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

  const share = async () => {
    try {
      await Share.share({ message: JSON.stringify(exportSkill(skill), null, 2), title: `${skill.skill.id}.json` });
    } catch (e: any) {
      Alert.alert('Export', String(e?.message ?? e));
    }
  };

  const expanded = showSteps || !!run;
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
  const reviewPill = skill.review?.approved
    ? skill.safety?.physical_execution_allowed
      ? { label: 'approved · real', tone: 'ok' as const }
      : { label: 'approved · sim', tone: 'accent' as const }
    : { label: skill.skill.status || 'draft', tone: 'warn' as const };

  return (
    <View style={[s.card, active && { backgroundColor: c.panel, boxShadow: `inset 0 0 0 1px ${c.accentSoft}` }]}>
      <View style={s.cardHead}>
        <IconChip icon={icon(skill)} color={active ? c.accent : c.text} bg={active ? c.accentSoft : undefined} />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={s.name} numberOfLines={1}>
            {skill.skill.name}
          </Text>
          <Text style={s.meta} numberOfLines={1}>
            {devices[device].label}
            {device === 'arm' ? ' (UR5e)' : ''} · {skill.steps.length} steps · {primitiveCount(skill)} primitives
            {builtin ? ' · built-in' : ''}
          </Text>
        </View>
        {statusPill && <Pill label={statusPill.label} tone={statusPill.tone} dot={statusPill.tone !== 'accent'} />}
        <IconButton
          icon={showPreview ? 'film' : 'film-outline'}
          plain
          size={32}
          color={showPreview ? c.accent : c.faint}
          onPress={() => setShowPreview((v) => !v)}
          label={showPreview ? 'Hide preview' : 'Preview'}
        />
        <IconButton icon="share-outline" plain size={32} color={c.faint} onPress={share} label="Export JSON" />
        <IconButton
          icon={builtin ? 'copy-outline' : 'create-outline'}
          plain
          size={32}
          color={c.faint}
          onPress={builtin ? onDuplicate : onEdit}
          label={builtin ? 'Duplicate' : 'Edit'}
        />
        {!builtin && (
          <IconButton icon="trash-outline" plain size={32} color={c.faint} onPress={confirmDelete} label="Delete" />
        )}
      </View>

      <Text style={s.desc}>{skill.skill.description || 'No description.'}</Text>

      <View style={s.reviewRow}>
        <Pill label={reviewPill.label} tone={reviewPill.tone} dot />
        {skill.interpretation?.uncertainties?.length ? (
          <Text style={s.meta}>{skill.interpretation.uncertainties.length} uncertainties</Text>
        ) : null}
        <View style={{ flex: 1 }} />
        <Button
          label={skill.review?.approved ? 'Revoke' : 'Review & approve'}
          icon={skill.review?.approved ? 'close-circle-outline' : 'shield-checkmark-outline'}
          size="sm"
          variant="ghost"
          onPress={review}
        />
      </View>

      {showPreview && <SkillPreview skill={skill} />}

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

      {run?.error && run.state !== 'running' && <Text style={s.runError}>{run.error}</Text>}

      <View style={s.foot}>
        <Text style={s.stats} numberOfLines={2}>
          {run && run.state !== 'running'
            ? runSummary(run)
            : blocked
              ? `Real robot: ${blocked}`
              : st
                ? `ran ${st.runs}x · ${st.ok}/${st.runs} ok · last ${ago(st.lastAt)}${real ? '' : ' · simulated'}`
                : real
                  ? 'never run'
                  : 'never run · runs in the simulator'}
        </Text>
        {active ? (
          <Button label="Stop" icon="hand-left" variant="danger" size="sm" onPress={skills.stop} />
        ) : (
          <Button
            label={
              offline
                ? 'Offline'
                : busy
                  ? 'Busy'
                  : blocked
                    ? realArmProblems(skill).length
                      ? 'Not on real arm'
                      : 'Needs approval'
                    : 'Run'
            }
            icon="play"
            size="sm"
            onPress={start}
            disabled={busy || offline || !!blocked}
          />
        )}
      </View>
    </View>
  );
}

function runSummary(run: Run) {
  const secs = (((run.endedAt ?? Date.now()) - run.startedAt) / 1000).toFixed(1);
  const label = { done: 'finished', failed: 'failed', stopped: 'stopped', running: 'running' }[run.state];
  return `${label} in ${secs}s${run.via === 'voice' ? ' · by Motion' : ''}`;
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
