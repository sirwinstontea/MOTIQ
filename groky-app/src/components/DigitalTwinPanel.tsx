// Skills -> Sync Digital Twin: read-only control surface for the RoboDK mirror of the UR5e + RealSense objects.
// Check / Snapshot / Start Live Sync / Stop Sync / status. MIRROR ONLY — NO PHYSICAL MOTION: this component only uses
// lib/digitalTwin (bridge /digital-twin/*). Stop Sync stops the mirroring loop; it is NOT the robot stop.
import { useCallback, useEffect, useState } from 'react';
import { Modal, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { digitalTwin, fmtMm, TwinCheck, TwinState, TwinStatus } from '../lib/digitalTwin';
import { Colors, radius, shadowFloat, type, useStyles, useTheme } from '../theme';
import { Button, IconButton, Pill } from './ui';

export const MIRROR_LABEL = 'MIRROR ONLY — NO PHYSICAL MOTION';

export const stateTone = (st: TwinState | 'Offline'): 'ok' | 'warn' | 'bad' | 'neutral' | 'accent' =>
  st === 'RUNNING' ? 'ok' : st === 'READY' ? 'accent' : st === 'PAUSED_STALE' ? 'warn' : st === 'ERROR' ? 'bad' : 'neutral';

const OBJ_TONE = { fresh: 'ok', stale: 'warn', not_visible: 'neutral', never_seen: 'neutral' } as const;

type Msg = { tone: 'ok' | 'warn' | 'bad'; text: string } | null;

export default function DigitalTwinPanel({ onClose }: { onClose: () => void }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [status, setStatus] = useState<TwinStatus | null>(null);
  const [offline, setOffline] = useState<string | null>(null);
  const [check, setCheck] = useState<TwinCheck | null>(null);
  const [objects, setObjects] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);

  const apply = useCallback((r: Awaited<ReturnType<typeof digitalTwin.status>>) => {
    if (r.ok) {
      setStatus(r);
      setOffline(null);
    } else {
      setOffline(r.reason);
    }
  }, []);
  const refresh = useCallback(() => digitalTwin.status().then(apply), [apply]);

  useEffect(() => {
    let live = true;
    const poll = () => digitalTwin.status().then((r) => live && apply(r));
    poll();
    const t = setInterval(poll, 1000); // status poll; the UI stays usable while the sync runs
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [apply]);

  const act = async (name: string, fn: () => Promise<any>, okText: (r: any) => string) => {
    setBusy(name);
    setMsg(null);
    const r = await fn();
    setBusy(null);
    setMsg(r.ok ? { tone: 'ok', text: okText(r) } : { tone: 'bad', text: `${r.status}: ${r.reason}` });
    if (name === 'check' && r.ok) setCheck(r);
    refresh();
  };

  const state: TwinState | 'Offline' = offline ? 'Offline' : (status?.state ?? 'DISCONNECTED');
  const running = state === 'RUNNING' || state === 'PAUSED_STALE';
  const canObjects = check ? check.can_start_with_objects : true;
  const startBlocked = !!offline || (check !== null && !check.can_start_robot_only) || (objects && !canObjects);

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View style={s.backdrop}>
        <SafeAreaView edges={['top', 'bottom']} style={s.wrap}>
          <View style={s.card}>
            <View style={s.head}>
              <Text style={s.title}>Sync Digital Twin (UR5e + RealSense)</Text>
              <Pill label={state} tone={stateTone(state)} dot />
              <IconButton icon="close" size={32} onPress={onClose} label="Close" />
            </View>

            <View style={[s.banner, { borderColor: c.warn }]}>
              <Ionicons name="eye-outline" size={18} color={c.warn} />
              <Text style={[s.bannerText, { color: c.warn }]}>{MIRROR_LABEL}</Text>
              <Pill label={status?.target === 'physical' ? 'REAL ROBOT → ROBODK' : 'SIMULATION'} tone="neutral" />
            </View>

            <ScrollView contentContainerStyle={{ gap: 12, paddingBottom: 8 }}>
              <Text style={s.caption}>
                Real world → RoboDK only. Move the UR5e from its pendant; RoboDK (simulation mode, no driver) follows.
                Objects appear as magenta markers at the measured visible-surface point (grey = last known, not
                current). Stop Sync stops mirroring only — it does not stop or move the robot.
              </Text>

              <View style={s.row}>
                <Switch value={objects} onValueChange={setObjects} disabled={running} />
                <Text style={s.body}>Include objects (needs validated calibration + base alignment)</Text>
              </View>

              <View style={s.buttons}>
                <Button label="Check" icon="checkmark-done-outline" size="sm" loading={busy === 'check'}
                  onPress={() => act('check', digitalTwin.check, (r) =>
                    r.can_start_with_objects ? 'All prerequisites OK' :
                    r.can_start_robot_only ? `Robot mirror OK; objects blocked: ${r.object_blockers.join('; ')}` :
                    'Not ready: see details below')} />
                <Button label="Snapshot" icon="camera-outline" size="sm" disabled={running || !!offline}
                  loading={busy === 'snapshot'}
                  onPress={() => act('snapshot', () => digitalTwin.snapshot(objects), (r) =>
                    `Joints mirrored${r.objects ? `, objects: ${r.objects.ok ? `${r.objects.updated.length} updated` : r.objects.reason}` : ' (robot only)'}`)} />
                <Button label="Start Live Sync" icon="play" size="sm" variant="primary"
                  disabled={running || startBlocked} loading={busy === 'start'}
                  onPress={() => act('start', () => digitalTwin.start(objects), (r) =>
                    r.status === 'already_running' ? 'Already running' : 'Live sync started')} />
                <Button label="Stop Sync" icon="pause" size="sm" disabled={!running} loading={busy === 'stop'}
                  onPress={() => act('stop', digitalTwin.stop, () => 'Mirroring stopped (no robot command sent)')} />
              </View>

              {msg && <Text style={[s.body, { color: msg.tone === 'ok' ? c.ok : msg.tone === 'warn' ? c.warn : c.bad }]}>{msg.text}</Text>}
              {offline && <Text style={[s.body, { color: c.bad }]}>Twin service unavailable: {offline}</Text>}
              {status?.last_error && (
                <Text style={[s.body, { color: c.bad }]}>
                  {status.last_error.status}: {status.last_error.reason}
                </Text>
              )}

              {check && (
                <View style={s.box}>
                  <Text style={s.label}>PREREQUISITES</Text>
                  <Line ok={check.robot.connected} text={`Robot telemetry (${check.robot.source})${check.robot.reason ? `: ${check.robot.reason}` : ''}`} />
                  <Line ok={check.robodk.ok} text={`RoboDK ${check.robodk.station ?? ''} ${check.robodk.ok ? 'simulate mode, no driver' : `${check.robodk.status}: ${check.robodk.reason}`}`} />
                  <Line ok={check.vision.source === 'none' ? null : !!check.vision.connected && check.vision.calibration_valid !== false}
                    text={`Vision ${check.vision.source}${check.vision.calibration_id ? ` · ${check.vision.calibration_id}` : ''}`} />
                  <Line ok={check.alignment.validated} text={`Base alignment ${check.alignment.validated ? `validated (${check.alignment.validated_for})` : 'NOT validated'}`} />
                  {check.object_blockers.map((b) => <Text key={b} style={[s.caption, { color: c.warn }]}>• {b}</Text>)}
                </View>
              )}

              {status && (
                <View style={s.stats}>
                  <Stat label="Robot" value={status.robot.connected ? `${status.robot.hz.toFixed(1)} Hz` : 'offline'}
                    sub={status.robot.last_joint_sample_age_ms == null ? '–' : `sample ${status.robot.last_joint_sample_age_ms.toFixed(0)} ms ago`} />
                  <Stat label="RoboDK" value={status.robodk.connected ? (status.robodk.station ?? 'connected') : '–'}
                    sub={status.robodk.robot_item} />
                  <Stat label="Camera" value={status.camera.calibration_id ?? status.camera.source}
                    sub={status.camera.last_reason ?? (status.camera.last_frame_age_ms == null ? '–' : `frame ${status.camera.last_frame_age_ms.toFixed(0)} ms ago`)} />
                  <Stat label="Objects" value={`${status.objects.updated} upd · ${status.objects.stale} stale`}
                    sub={`${status.objects.skipped} skipped · ${status.objects.unknown} unknown`} />
                </View>
              )}

              {status?.robot.q_deg && (
                <Text style={s.mono}>q (deg)  {status.robot.q_deg.map((v) => v.toFixed(1)).join('  ')}</Text>
              )}

              {status && (
                <View style={s.box}>
                  <Text style={s.label}>OBJECTS (visible-surface point, UR base)</Text>
                  {Object.entries(status.objects.per_object).map(([id, o]) => (
                    <View key={id} style={s.row}>
                      <Pill label={o.state.replace('_', ' ')} tone={OBJ_TONE[o.state]} dot />
                      <Text style={[s.body, { flex: 1 }]} numberOfLines={1}>{id}</Text>
                      <Text style={s.caption} numberOfLines={1}>
                        {o.position_base_m ? fmtMm(o.position_base_m) : ''}{o.reason ? `  ${o.reason}` : ''}
                      </Text>
                    </View>
                  ))}
                </View>
              )}
            </ScrollView>
            <Text style={s.foot}>
              {MIRROR_LABEL}. physical_motion_enabled: {String(status?.physical_motion_enabled ?? false)}
            </Text>
          </View>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

function Line({ ok, text }: { ok: boolean | null; text: string }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const icon = ok === null ? 'remove-circle-outline' : ok ? 'checkmark-circle' : 'close-circle';
  return (
    <View style={s.row}>
      <Ionicons name={icon} size={16} color={ok === null ? c.faint : ok ? c.ok : c.bad} />
      <Text style={[s.caption, { flex: 1 }]}>{text}</Text>
    </View>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub: string }) {
  const s = useStyles(makeStyles);
  return (
    <View style={s.statBox}>
      <Text style={s.label}>{label.toUpperCase()}</Text>
      <Text style={s.body} numberOfLines={1}>{value}</Text>
      <Text style={s.caption} numberOfLines={1}>{sub}</Text>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    backdrop: { flex: 1, backgroundColor: c.scrim },
    wrap: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 16 },
    card: {
      width: '100%',
      maxWidth: 760,
      maxHeight: '100%',
      flexShrink: 1,
      backgroundColor: c.panel,
      borderRadius: radius.lg,
      padding: 20,
      boxShadow: shadowFloat,
    },
    head: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 10 },
    title: { ...type.h2, color: c.text, flex: 1 },
    banner: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      borderWidth: 1.5,
      borderRadius: radius.md,
      paddingHorizontal: 12,
      paddingVertical: 8,
      marginBottom: 12,
    },
    bannerText: { ...type.overline, flex: 1, fontSize: 13 },
    row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    buttons: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
    box: { backgroundColor: c.panelHi, borderRadius: radius.md, padding: 12, gap: 8 },
    label: { ...type.overline, color: c.faint },
    body: { ...type.body, color: c.text },
    caption: { ...type.caption, color: c.dim },
    mono: { ...type.mono, color: c.text },
    stats: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },
    statBox: { flexGrow: 1, minWidth: 140, backgroundColor: c.panelHi, borderRadius: radius.md, padding: 10, gap: 2 },
    foot: { ...type.caption, color: c.faint, marginTop: 10 },
  });
