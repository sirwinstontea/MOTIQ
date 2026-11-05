import { useEffect, useState } from 'react';
import { Alert, Pressable, ScrollView, Share, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import CalibrationWizard from '../components/CalibrationWizard';
import DigitalTwinPanel, { MIRROR_LABEL, stateTone } from '../components/DigitalTwinPanel';
import SkillEditor from '../components/SkillEditor';
import SkillPreview from '../components/SkillPreview';
import TeachFromCamera from '../components/TeachFromCamera';
import { teachReady, visionReady } from '../skills/fromVision';
import { Button, IconButton, IconChip, Pill } from '../components/ui';
import { bridgeRealArm, devices } from '../devices';
import { useDevices } from '../hooks/useDevices';
import { useFullAccess } from '../lib/fullAccess';
import { confirmAction } from '../lib/confirm';
import { ago } from '../lib/time';
import { isLearned } from '../skills/store';
import { calibration, calibrationReady, CardState } from '../lib/calibration';
import { digitalTwin, digitalTwinReady, TwinState } from '../lib/digitalTwin';
import type { Run, Skills as SkillsApi } from '../hooks/useSkills';
import type { RunTarget } from '../skills/runner';
import {
  deviceOf,
  exportSkill,
  icon,
  isLookStep,
  newSkill,
  newSkillId,
  physicalArmProblems,
  physicalBlock,
  primLabel,
  primitiveCount,
  realArmProblems,
  SkillDoc,
  stepLabel,
} from '../skills';
import { Colors, fonts, radius, type, useStyles, useTheme } from '../theme';

type Editing = { doc: SkillDoc; json?: boolean } | null;

export default function Skills({ skills }: { skills: SkillsApi }) {
  const s = useStyles(makeStyles);
  const { width } = useWindowDimensions();
  const twoCols = width >= 1000;
  const mobile = width < 641;
  const [editing, setEditing] = useState<Editing>(null);
  const [teaching, setTeaching] = useState(false);
  const [calibrating, setCalibrating] = useState(false);
  const [twinOpen, setTwinOpen] = useState(false);

  const list = skills.skills;

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
            onPress={() => setEditing({ doc: newSkill('arm') })}
          />
        </View>


        <View style={[s.grid, twoCols && s.gridTwo]}>
          {calibrationReady() && (
            <View style={twoCols ? s.half : undefined}>
              <CalibrationCard onStart={() => setCalibrating(true)} refreshKey={calibrating} />
            </View>
          )}
          {digitalTwinReady() && (
            <View style={twoCols ? s.half : undefined}>
              <DigitalTwinCard onOpen={() => setTwinOpen(true)} refreshKey={twinOpen} />
            </View>
          )}
          {list.map((k) => (
            <View key={k.skill.id} style={twoCols ? s.half : undefined}>
              <Card skill={k} skills={skills} onEdit={() => setEditing({ doc: k })} onDuplicate={() => duplicate(k)} />
            </View>
          ))}
        </View>
      </ScrollView>

      {calibrating && <CalibrationWizard onClose={() => setCalibrating(false)} />}

      {twinOpen && <DigitalTwinPanel onClose={() => setTwinOpen(false)} />}

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

// Calibration is a workflow, not a motion skill: its own card and wizard, never a SkillDoc and never runSkill().
function CalibrationCard({ onStart, refreshKey }: { onStart: () => void; refreshKey: boolean }) {
  const s = useStyles(makeStyles);
  const [state, setState] = useState<CardState | 'Offline'>('Not calibrated');
  const [detail, setDetail] = useState('');
  const [mode, setMode] = useState('');

  useEffect(() => {
    if (refreshKey) return; // refresh when the wizard closes
    let live = true;
    calibration.status().then((r) => {
      if (!live) return;
      if (!r.ok) {
        setState('Offline');
        setDetail(r.reason);
        return;
      }
      setState(r.card_state);
      setMode(r.mode_label);
      const rec = r.active_calibration.record;
      setDetail(
        r.active_calibration.valid && rec
          ? `${rec.calibration_id} · hold-out ${rec.quality.holdout_error_mm ?? '–'} mm`
          : (r.active_calibration.reason ?? 'Arm camera (D435) to UR5e flange, read-only'),
      );
    });
    return () => {
      live = false;
    };
  }, [refreshKey]);

  const tone =
    state === 'Calibrated' ? 'ok' : state === 'Error' || state === 'Offline' ? 'bad' : state === 'Not calibrated' ? 'neutral' : 'warn';
  return (
    <View style={s.card}>
      <View style={s.cardHead}>
        <IconChip icon="scan-outline" />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={s.name} numberOfLines={1}>
            Calibration{mode ? ` · ${mode}` : ''}
          </Text>
          <Text style={s.meta} numberOfLines={1}>
            {detail}
          </Text>
        </View>
        <Pill label={state} tone={tone as any} dot />
        <Button label="Start Calibration" icon="scan-outline" size="sm" onPress={onStart} disabled={state === 'Offline'} />
      </View>
    </View>
  );
}

// Sync Digital Twin is a read-only service, not a motion skill: its own card and panel, never a SkillDoc, never
// runSkill(), and its Stop never calls the robot stop. MIRROR ONLY — NO PHYSICAL MOTION.
function DigitalTwinCard({ onOpen, refreshKey }: { onOpen: () => void; refreshKey: boolean }) {
  const s = useStyles(makeStyles);
  const [state, setState] = useState<TwinState | 'Offline'>('DISCONNECTED');
  const [detail, setDetail] = useState(MIRROR_LABEL);

  useEffect(() => {
    if (refreshKey) return; // the panel polls while open; refresh when it closes
    let live = true;
    const poll = () =>
      digitalTwin.status().then((r) => {
        if (!live) return;
        if (!r.ok) {
          setState('Offline');
          setDetail(r.reason);
          return;
        }
        setState(r.state);
        setDetail(
          r.state === 'RUNNING' || r.state === 'PAUSED_STALE'
            ? `${MIRROR_LABEL} · ${r.robot.hz.toFixed(1)} Hz · ${r.objects.updated} objects`
            : `${MIRROR_LABEL} · ${r.target}`,
        );
      });
    poll();
    const t = setInterval(poll, 5000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [refreshKey]);

  return (
    <View style={s.card}>
      <View style={s.cardHead}>
        <IconChip icon="git-compare-outline" />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={s.name} numberOfLines={1}>
            Sync Digital Twin (UR5e + RealSense)
          </Text>
          <Text style={s.meta} numberOfLines={1}>
            {detail}
          </Text>
        </View>
        <Pill label={state} tone={stateTone(state)} dot />
        <Button label="Open" icon="open-outline" size="sm" onPress={onOpen} />
      </View>
    </View>
  );
}

type CardProps = { skill: SkillDoc; skills: SkillsApi; onEdit: () => void; onDuplicate: () => void };

function Card({ skill, skills, onEdit, onDuplicate }: CardProps) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const { status, ready } = useDevices();
  const [open, setOpen] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [choosing, setChoosing] = useState(false); // Run asks: simulator or real arm
  const id = skill.skill.id;
  const device = deviceOf(skill);
  const builtin = !!skill.app?.builtin;
  const run = skills.run?.skillId === id ? skills.run : null;
  const active = run?.state === 'running';
  const busy = skills.running && !active;
  const real = !!devices[device].url;
  const offline = ready && status[device].state === 'offline';
  // the PC stack runs SIM=1: the bridge's "real arm" is URSim mirrored into RoboDK, not the physical UR
  const roboDK = (status[device].info as { physical_arm?: { sim?: boolean } } | undefined)?.physical_arm?.sim === true;
  const realName = roboDK ? 'RoboDK' : 'real arm';
  const st = skills.stats[id];
  useFullAccess(); // re-check when full access is turned on/off
  const blocked = physicalBlock(skill, real, roboDK);

  const start = (where: RunTarget) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    setChoosing(false);
    skills.start(id, 'tap', where);
  };

  // only skills someone made or recorded can be deleted; built-ins are part of the app
  const confirmDelete = () =>
    confirmAction(
      `Delete "${skill.skill.name}"?`,
      isLearned(skill)
        ? 'It is also removed from the robot PC (learned skills store). This cannot be undone.'
        : 'This cannot be undone.',
      'Delete',
      () => skills.remove(id),
    );

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
      await Share.share({ message: JSON.stringify(exportSkill(skill), null, 2), title: `${id}.json` });
    } catch (e: any) {
      Alert.alert('Export', String(e?.message ?? e));
    }
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
  if (statusPill && run)
    statusPill.label += run.where === 'robot' ? ' · real arm' : run.where === 'real' ? ` · ${realName}` : ' · tablet sim';
  const reviewPill = skill.review?.approved
    ? skill.safety?.physical_execution_allowed
      ? { label: 'approved · real', tone: 'ok' as const }
      : { label: 'approved · sim', tone: 'accent' as const }
    : { label: skill.skill.status || 'draft', tone: 'warn' as const };
  const src = skill.skill.source as SourceInfo | undefined;
  const uncertainties = skill.interpretation?.uncertainties ?? [];

  // compact by default: name, one line of description, details, edit, delete and run. Details open the full
  // description, where the skill came from (video / camera / voice / Grok), approval, preview, steps and run stats.
  const needsApproval = !!blocked && !realArmProblems(skill).length;
  // why the real arm can't run it right now (null = it can)
  const realWhyNot = !real
    ? 'not connected (no arm bridge configured)'
    : offline
      ? 'the arm bridge is offline'
      : blocked;
  const directSim = roboDK && !realWhyNot;
  // SIM=1 stack with the physical UR5e attached too: "Real arm" runs it on the physical arm (camera + force moves
  // only, real-robot approval required), next to "Run in RoboDK"
  const realArm = roboDK && !offline ? bridgeRealArm(device) : null;
  const physicalWhyNot = !realArm
    ? null
    : !realArm.reachable
      ? `the real arm is not connected (${realArm.note ?? 'check the ethernet cable'})`
      : !realArm.ready
        ? `the real arm is not ready (${[realArm.mode, realArm.safety].filter(Boolean).join(', ')}). Power on + brake release on the pendant.`
        : (physicalArmProblems(skill)[0] ?? physicalBlock(skill, true, false));
  const runPhysical = () => {
    if (!physicalWhyNot)
      return Alert.alert(
        `Run "${skill.skill.name}" on the REAL arm?`,
        'The physical UR5e will move. Keep the emergency stop in reach.',
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Run on real arm', style: 'destructive', onPress: () => start('robot') },
        ],
      );
    const approvable = !physicalArmProblems(skill).length && realArm?.ready && !skill.review?.approved;
    Alert.alert(
      "Can't run on the real arm",
      physicalWhyNot,
      approvable
        ? [
            { text: 'OK', style: 'cancel' },
            { text: 'Approve for real robot', style: 'destructive', onPress: () => skills.approve(id, true) },
          ]
        : [{ text: 'OK' }],
    );
  };
  return (
    <View style={[s.card, active && { backgroundColor: c.panel, boxShadow: `inset 0 0 0 1px ${c.accentSoft}` }]}>
      <View style={s.cardHead}>
        <IconChip icon={icon(skill)} color={active ? c.accent : c.text} bg={active ? c.accentSoft : undefined} />
        <Pressable style={{ flex: 1, minWidth: 0 }} onPress={() => setOpen((v) => !v)}>
          <Text style={s.name} numberOfLines={1}>
            {skill.skill.name}
          </Text>
          {skill.skill.description && !open ? (
            <Text style={s.meta} numberOfLines={1}>
              {skill.skill.description}
            </Text>
          ) : null}
        </Pressable>
        {statusPill && statusPill.tone !== 'accent' && <Pill label={statusPill.label} tone={statusPill.tone} dot />}
        <IconButton
          icon={open ? 'chevron-up' : 'chevron-down'}
          plain
          size={32}
          color={open ? c.accent : c.faint}
          onPress={() => setOpen((v) => !v)}
          label={open ? 'Hide details' : 'Details'}
        />
        <IconButton
          icon={builtin ? 'copy-outline' : 'create-outline'}
          plain
          size={32}
          color={c.faint}
          onPress={builtin ? onDuplicate : onEdit}
          label={builtin ? 'Duplicate' : 'Edit'}
        />
        {!builtin && (
          <IconButton
            icon="trash-outline"
            plain
            size={32}
            color={c.faint}
            onPress={confirmDelete}
            label="Delete"
            disabled={active}
          />
        )}
        {active ? (
          <Button label="Stop" icon="hand-left" variant="danger" size="sm" onPress={skills.stop} />
        ) : (
          <Button
            label={choosing ? 'Cancel' : directSim ? 'Run in RoboDK' : 'Run'}
            icon={choosing ? 'close' : 'play'}
            size="sm"
            variant={choosing ? 'ghost' : directSim ? 'primary' : 'secondary'}
            // SIM=1 demo: one tap runs it on URSim + RoboDK (the bridge starts URSim if needed); otherwise ask where
            onPress={() => (directSim ? start('real') : setChoosing((v) => !v))}
            disabled={busy}
          />
        )}
        {!active && !choosing && realArm && (
          <Button
            label="Run on real arm"
            icon="hardware-chip-outline"
            size="sm"
            variant="secondary"
            onPress={runPhysical}
            disabled={busy}
          />
        )}
      </View>

      {choosing && !active && (
        <View style={s.chooser}>
          <Text style={s.chooserTitle}>Where should it run?</Text>
          <View style={s.reviewRow}>
            <Button label="Tablet sim" icon="tablet-landscape-outline" size="sm" onPress={() => start('sim')} />
            <Button
              label={roboDK ? 'RoboDK' : 'Real arm'}
              icon={roboDK ? 'desktop-outline' : 'hardware-chip-outline'}
              size="sm"
              variant="primary"
              onPress={() => start('real')}
              disabled={!!realWhyNot}
            />
            {real && !offline && needsApproval && (
              <Button label="Approve" icon="shield-checkmark-outline" size="sm" variant="ghost" onPress={review} />
            )}
          </View>
          <Text style={s.meta}>Tablet sim: runs on this tablet only, nothing is sent to the PC or RoboDK.</Text>
          {realWhyNot ? (
            <Text style={[s.meta, { color: c.warn }]}>{roboDK ? 'RoboDK' : 'Real arm'}: {realWhyNot}</Text>
          ) : roboDK ? (
            <Text style={s.meta}>
              RoboDK: runs on the PC simulation (URSim mirrored live into RoboDK, simulated gripper). The physical arm
              does not move.
            </Text>
          ) : (
            <Text style={[s.meta, { color: c.warn }]}>
              Real arm: locate and small steps move the physical UR5e; pick, place and push run through the robot
              controller. Keep the stop button in reach.
            </Text>
          )}
        </View>
      )}

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

      {open && (
        <View style={s.details}>
          <Text style={s.desc}>{skill.skill.description || 'No description.'}</Text>
          <Text style={s.meta}>
            UR5e · {skill.steps.length} step{skill.steps.length === 1 ? '' : 's'} · {primitiveCount(skill)} primitives ·{' '}
            {builtin ? 'built-in' : sourceLabel(src)}
          </Text>

          {src && (src.summary || src.transcript || src.note || src.prompt) ? (
            <View style={s.source}>
              <Text style={s.sourceHead}>
                <Ionicons name={sourceIcon(src)} size={13} color={c.dim} /> {sourceLabel(src)}
                {src.video ? ` · ${src.video}` : ''}
                {src.tokens ? ` · ${src.tokens} actions recognised` : ''}
                {src.seconds ? ` · ${Number(src.seconds).toFixed(0)} s watched` : ''}
              </Text>
              {src.summary ? <Text style={s.sourceText}>{src.summary}</Text> : null}
              {src.transcript ? <Text style={s.sourceText}>They said: “{src.transcript}”</Text> : null}
              {src.note ? <Text style={s.sourceText}>Your note: “{src.note}”</Text> : null}
              {src.prompt && !src.summary ? <Text style={s.sourceText}>“{src.prompt}”</Text> : null}
            </View>
          ) : null}

          <View style={s.reviewRow}>
            <Pill label={reviewPill.label} tone={reviewPill.tone} dot />
            {uncertainties.length ? (
              <Text style={s.meta}>
                {uncertainties.length} uncertaint{uncertainties.length === 1 ? 'y' : 'ies'}
              </Text>
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
          {uncertainties.map((u, k) => (
            <Text key={k} style={[s.meta, { color: c.warn }]}>
              • {u}
            </Text>
          ))}

          <View style={s.reviewRow}>
            <Button
              label={showPreview ? 'Hide preview' : 'Preview'}
              icon={showPreview ? 'film' : 'film-outline'}
              size="sm"
              variant="ghost"
              onPress={() => setShowPreview((v) => !v)}
            />
            <Button label="Export JSON" icon="share-outline" size="sm" variant="ghost" onPress={share} />
          </View>
          {showPreview && <SkillPreview skill={skill} />}

          {!active && <Steps skill={skill} run={run} />}

          <Text style={s.stats}>
            {run && run.state !== 'running'
              ? runSummary(run, realName)
              : st
                ? `ran ${st.runs}× · ${st.ok}/${st.runs} ok · last ${ago(st.lastAt)}${real ? '' : ' · simulated'}`
                : real
                  ? 'never run'
                  : 'never run · runs in the simulator'}
          </Text>
        </View>
      )}

      {active && <Steps skill={skill} run={run} />}

      {run?.error && run.state !== 'running' && <Text style={s.runError}>{run.error}</Text>}
    </View>
  );
}

type SourceInfo = {
  type?: string;
  video?: string;
  summary?: string;
  transcript?: string;
  note?: string;
  prompt?: string;
  tokens?: number;
  seconds?: number;
};

function sourceLabel(src?: SourceInfo): string {
  switch (src?.type) {
    case 'video':
      return 'learned from a video';
    case 'vision':
      return 'learned from the camera';
    case 'voice':
      return 'made by voice';
    case 'grok_prompt':
      return 'written by Grok';
    default:
      return 'made by hand';
  }
}

function sourceIcon(src?: SourceInfo): keyof typeof Ionicons.glyphMap {
  return src?.type === 'video'
    ? 'film-outline'
    : src?.type === 'vision'
      ? 'eye-outline'
      : src?.type === 'voice'
        ? 'mic-outline'
        : 'sparkles-outline';
}

function runSummary(run: Run, realName = 'real arm') {
  const secs = (((run.endedAt ?? Date.now()) - run.startedAt) / 1000).toFixed(1);
  const label = { done: 'finished', failed: 'failed', stopped: 'stopped', running: 'running' }[run.state];
  return `${label} in ${secs}s ${run.where === 'robot' ? 'on the real arm' : run.where === 'real' ? (realName === 'RoboDK' ? 'in RoboDK' : 'on the real arm') : 'in the tablet sim'}${run.via === 'voice' ? ' · by Motion' : ''}`;
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
    details: { gap: 10 },
    chooser: { backgroundColor: c.panelHi, borderRadius: radius.md, padding: 12, gap: 8 },
    chooserTitle: { ...type.body, color: c.text, fontWeight: '600' },
    source: { backgroundColor: c.panelHi, borderRadius: radius.md, padding: 12, gap: 6 },
    sourceHead: { ...type.caption, color: c.dim },
    sourceText: { ...type.body, color: c.text },
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
