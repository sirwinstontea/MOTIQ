import { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, useWindowDimensions, View } from 'react-native';
import * as Haptics from 'expo-haptics';
import { devices, DeviceId, getArmSpeed, setArmSpeed, stopDevice } from '../devices';
import { useDevices } from '../hooks/useDevices';
import type { Line } from '../voice/types';
import { Button, IconButton, IconChip, Pill, Slider } from '../components/ui';
import { Colors, fonts, radius, type, useStyles, useTheme } from '../theme';
import { ago } from '../lib/time';
import { ARM_ID_RE, CatalogArm, NewArm, robotCatalog, robotCatalogReady, suggestArmId } from '../lib/robotCatalog';

type ToolLine = Extract<Line, { kind: 'tool' }>;

export default function Robots({ lines }: { lines: Line[] }) {
  const s = useStyles(makeStyles);
  const { width } = useWindowDimensions();
  const twoCols = width >= 1000;

  return (
    <ScrollView contentContainerStyle={[s.page, width < 641 && { paddingHorizontal: 16 }]}>
      <Text style={s.title}>Robots</Text>
      <Text style={s.sub}>Live status, refreshed every few seconds.</Text>
      <View style={[s.grid, twoCols && { flexDirection: 'row' }]}>
        {(Object.keys(devices) as DeviceId[]).map((id) => (
          <Card key={id} id={id} lines={lines} />
        ))}
      </View>
      <ArmCatalog />
    </ScrollView>
  );
}

// ---- arm catalog (robot-chooser via the bridge): add any arm, pick the active one. Config only, no motion.

const EMPTY_FORM = { brand: '', model: '', id: '', dof: '6', reach: '', payload: '', url: '', file: '' };

function ArmCatalog() {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [arms, setArms] = useState<CatalogArm[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ text: string; bad?: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState(EMPTY_FORM);
  const [idTouched, setIdTouched] = useState(false);

  const load = async () => {
    const r = await robotCatalog.list();
    if (r.ok) {
      setArms(r.robots);
      setError(null);
    } else setError(r.message);
  };

  useEffect(() => {
    if (robotCatalogReady()) load();
  }, []);

  if (!robotCatalogReady()) return null;

  const set = (k: keyof typeof EMPTY_FORM) => (v: string) =>
    setF((o) => {
      const n = { ...o, [k]: v };
      if (!idTouched && (k === 'brand' || k === 'model')) n.id = suggestArmId(n.brand, n.model);
      return n;
    });

  const num = (v: string) => Number(v.replace(',', '.'));
  const problem = (() => {
    if (!f.brand.trim() || !f.model.trim()) return 'brand and model are required';
    if (!ARM_ID_RE.test(f.id)) return 'id: lowercase letters, digits and _ only';
    if (arms?.some((a) => a.id === f.id)) return `${f.id} is already in the catalog`;
    const dof = num(f.dof);
    if (!Number.isInteger(dof) || dof < 1 || dof > 12) return 'axes: a whole number 1-12';
    if (!(num(f.reach) > 0)) return 'reach must be > 0 (m)';
    if (!(num(f.payload) > 0)) return 'payload must be > 0 (kg)';
    if (!/^https:\/\//.test(f.url.trim())) return 'link must be an https:// page (RoboDK library or datasheet)';
    if (f.file.trim() && !/^[^/\\]+\.robot$/.test(f.file.trim())) return 'RoboDK file: a plain <name>.robot file name';
    return null;
  })();

  const add = async () => {
    if (problem) return;
    Haptics.selectionAsync();
    setBusy('add');
    const arm: NewArm = {
      id: f.id,
      brand: f.brand.trim(),
      model: f.model.trim(),
      dof: num(f.dof),
      nominal_reach_m: num(f.reach),
      nominal_payload_kg: num(f.payload),
      library_url: f.url.trim(),
      library_file: f.file.trim() || null,
    };
    const r = await robotCatalog.add(arm);
    setBusy(null);
    if (r.ok) {
      setArms(r.robots);
      setF(EMPTY_FORM);
      setIdTouched(false);
      setAdding(false);
      setMsg({ text: `${arm.id} added. Fill in its limits, scene and workspace in robot-chooser before selecting it.` });
    } else setMsg({ text: r.message, bad: true });
  };

  const select = async (a: CatalogArm) => {
    Haptics.selectionAsync();
    setBusy(a.id);
    const r = await robotCatalog.select(a.id);
    setBusy(null);
    if (r.ok) {
      setArms(r.robots);
      const blocked = r.report?.status !== 'READY';
      setMsg({
        text: blocked
          ? `${a.id} selected but BLOCKED: ${r.report.blockers.map((b) => b.message).join('; ')}`
          : `${a.id} selected. Restart the stack (run_all.sh) to use it.`,
        bad: blocked,
      });
    } else setMsg({ text: r.message, bad: true });
  };

  return (
    <View style={[s.card, { marginTop: 16 }]}>
      <View style={s.cardHead}>
        <IconChip icon="git-branch-outline" />
        <Text style={s.name}>Arm catalog</Text>
        <View style={{ flex: 1 }} />
        <Button label="Refresh" icon="refresh" size="sm" variant="ghost" onPress={load} />
        <Button
          label={adding ? 'Cancel' : 'Add arm'}
          icon={adding ? 'close' : 'add'}
          size="sm"
          variant={adding ? 'secondary' : 'primary'}
          onPress={() => setAdding((x) => !x)}
        />
      </View>
      <Text style={s.speedHint}>
        One active arm per controller. Changes rewrite the config files (simulation only) and apply after a restart.
      </Text>

      {error && <Text style={[s.speedHint, { color: c.bad }]}>{error}</Text>}
      {msg && <Text style={[s.speedHint, { color: msg.bad ? c.bad : c.ok }]}>{msg.text}</Text>}

      {adding && (
        <View style={s.speedBox}>
          <View style={s.formRow}>
            <Field label="brand" value={f.brand} onChange={set('brand')} placeholder="KUKA" />
            <Field label="model" value={f.model} onChange={set('model')} placeholder="KR 3 R540" />
          </View>
          <View style={s.formRow}>
            <Field
              label="id"
              value={f.id}
              onChange={(v) => {
                setIdTouched(true);
                set('id')(v);
              }}
              placeholder="kuka_kr_3_r540"
            />
            <Field label="axes" value={f.dof} onChange={set('dof')} placeholder="6" numeric small />
          </View>
          <View style={s.formRow}>
            <Field label="reach (m)" value={f.reach} onChange={set('reach')} placeholder="0.541" numeric />
            <Field label="payload (kg)" value={f.payload} onChange={set('payload')} placeholder="3" numeric />
          </View>
          <Field
            label="RoboDK / datasheet link"
            value={f.url}
            onChange={set('url')}
            placeholder="https://robodk.com/robot/KUKA/KR-3-R540"
          />
          <Field
            label="RoboDK file in robot_controller/stations/ (optional)"
            value={f.file}
            onChange={set('file')}
            placeholder="KR 3 R540.robot"
          />
          <Text style={[s.speedHint, problem && { color: c.warn }]}>
            {problem ?? 'Joint limits are never guessed: the arm stays blocked until they are verified in robot-chooser.'}
          </Text>
          <Button label="Add to catalog" icon="checkmark" variant="primary" disabled={!!problem} loading={busy === 'add'} onPress={add} />
        </View>
      )}

      {arms === null && !error && <Text style={s.empty}>Loading...</Text>}
      {arms?.map((a) => (
        <View key={a.id} style={s.kv}>
          <View style={{ flexShrink: 1, gap: 2 }}>
            <Text style={s.v} numberOfLines={1}>
              {a.brand} {a.model}
              <Text style={{ color: c.faint }}>  {a.id}</Text>
            </Text>
            <Text style={s.k} numberOfLines={1}>
              {a.dof} axes · {a.nominal_reach_m} m · {a.nominal_payload_kg} kg
              {a.library_file && !a.model_file_available ? ' · .robot file missing' : ''}
            </Text>
          </View>
          <View style={s.armRight}>
            <Pill label={a.status} tone={a.status === 'Ready' ? 'ok' : 'accent'} style={{ height: 22 }} />
            {a.active ? (
              <Pill label="active" tone="ok" dot style={{ height: 22 }} />
            ) : (
              <Button label="Select" size="sm" disabled={!!busy} loading={busy === a.id} onPress={() => select(a)} />
            )}
          </View>
        </View>
      ))}
    </View>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  numeric,
  small,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  numeric?: boolean;
  small?: boolean;
}) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  return (
    <View style={[{ gap: 4 }, small ? { width: 80 } : { flex: 1 }]}>
      <Text style={s.k}>{label}</Text>
      <TextInput
        value={value}
        onChangeText={onChange}
        placeholder={placeholder}
        placeholderTextColor={c.faint}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType={numeric ? 'decimal-pad' : 'default'}
        style={s.input}
      />
    </View>
  );
}

function Card({ id, lines }: { id: DeviceId; lines: Line[] }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const { status, ready } = useDevices();
  const [stopping, setStopping] = useState(false);
  const d = devices[id];
  const st = status[id];

  const actions = lines.filter((l): l is ToolLine => l.kind === 'tool' && l.name.startsWith(`${id}_`));
  const done = actions.filter((a) => a.ok !== undefined);
  const ok = done.filter((a) => a.ok).length;
  const avg = done.length ? Math.round(done.reduce((n, a) => n + (a.ms ?? 0), 0) / done.length) : null;
  const running = actions.find((a) => a.ok === undefined);
  const recent = actions.slice(-5).reverse();

  const info = Object.entries(st.info ?? {}).filter(([k]) => k !== 'ok' && k !== 'speed_percent');
  const battery = typeof st.info?.battery === 'number' ? (st.info.battery as number) : null;

  const stop = async () => {
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
    setStopping(true);
    await stopDevice(id);
    setStopping(false);
  };

  const badge = !ready ? 'checking' : st.state;
  const badgeTone = st.state === 'offline' ? 'bad' : st.state === 'online' ? 'ok' : 'accent';

  return (
    <View style={s.card}>
      <View style={s.cardHead}>
        <IconChip icon="construct-outline" />
        <Text style={s.name}>{d.label}</Text>
        <Pill label={badge} tone={badgeTone} dot={st.state !== 'sim'} style={{ height: 22 }} />
        <View style={{ flex: 1 }} />
        <Button
          label={stopping ? '...' : 'Stop'}
          icon="hand-left"
          variant="danger"
          size="sm"
          onPress={stop}
          disabled={stopping}
        />
      </View>

      <Text style={s.url} numberOfLines={1}>
        {d.url || 'simulated - set EXPO_PUBLIC_' + id.toUpperCase() + '_URL to connect'}
        {st.ms != null && `  ·  ${st.ms} ms`}
        {st.state === 'offline' && `  ·  last seen ${ago(st.lastOnline)}`}
      </Text>

      <View style={s.tiles}>
        <Stat label="succeeded" value={`${ok}/${done.length}`} />
        <Stat label="avg time" value={avg != null ? `${avg}ms` : '—'} />
        <Stat label="now" value={running ? running.name.slice(id.length + 1) : 'idle'} hot />
      </View>

      {id === 'arm' && <ArmSpeed online={st.state === 'online'} busy={!!running} />}

      <View>
        {battery != null && (
          <View style={s.kv}>
            <Text style={s.k}>battery</Text>
            <View style={s.battRow}>
              <View style={s.battTrack}>
                <View
                  style={[
                    s.battFill,
                    {
                      width: `${Math.max(0, Math.min(100, battery))}%`,
                      backgroundColor: battery < 20 ? c.warn : c.accent,
                    },
                  ]}
                />
              </View>
              <Text style={[s.v, battery < 20 && { color: c.warn }]}>{battery}%</Text>
            </View>
          </View>
        )}
        {info
          .filter(([k]) => k !== 'battery')
          .map(([k, v]) => (
            <View key={k} style={s.kv}>
              <Text style={s.k}>{k}</Text>
              <Text style={s.v} numberOfLines={1}>
                {typeof v === 'object' ? JSON.stringify(v) : String(v)}
              </Text>
            </View>
          ))}
      </View>

      <Text style={s.overline}>Recent actions</Text>
      {recent.length === 0 && <Text style={s.empty}>Nothing yet.</Text>}
      {recent.map((a) => (
        <View key={a.id} style={s.action}>
          <View style={[s.dot, { backgroundColor: a.ok === undefined ? c.accent : a.ok ? c.ok : c.bad }]} />
          <Text style={s.actionName} numberOfLines={1}>
            {a.name.slice(id.length + 1)}
            <Text style={{ color: c.faint }}> {a.args !== '{}' ? a.args : ''}</Text>
          </Text>
          <Text style={s.actionMeta}>
            {a.speed != null && <Text style={{ color: c.accent }}>{a.speed}% · </Text>}
            {a.ms != null ? `${a.ms}ms · ${ago(a.at)}` : 'running'}
          </Text>
        </View>
      ))}
    </View>
  );
}

const PRESETS = [10, 25, 50, 100];
const STEP = 5;

// arm speed as % of the UR5e maximums (default 10). The controller applies it from the next task
// and refuses changes while a task is moving.
function ArmSpeed({ online, busy }: { online: boolean; busy: boolean }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [speed, setSpeed] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ text: string; bad?: boolean } | null>(null);

  useEffect(() => {
    if (!online) return;
    let alive = true;
    const load = () =>
      getArmSpeed().then((r) => alive && typeof r.speed_percent === 'number' && setSpeed(r.speed_percent));
    load();
    const t = setInterval(load, 5000); // picks up changes made from the dashboard
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [online]);

  const apply = async (pct: number) => {
    const next = Math.max(1, Math.min(100, Math.round(pct)));
    if (next === speed) return;
    Haptics.selectionAsync();
    setSaving(true);
    const r = await setArmSpeed(next);
    setSaving(false);
    if (r.ok && typeof r.speed_percent === 'number') {
      setSpeed(r.speed_percent);
      setMsg({ text: `Next tasks run at ${r.speed_percent}%` });
    } else setMsg({ text: String(r.error ?? 'could not change speed'), bad: true });
    setTimeout(() => setMsg(null), 4000);
  };

  if (!online) {
    return (
      <View style={s.speedBox}>
        <Text style={s.k}>speed</Text>
        <Text style={s.speedHint}>connect the arm bridge to adjust (default 10%)</Text>
      </View>
    );
  }

  const locked = busy || saving || speed == null;
  const shown = speed ?? 10;
  return (
    <View style={s.speedBox}>
      <View style={s.speedHead}>
        <Text style={s.k}>speed</Text>
        <View style={{ flex: 1 }} />
        <IconButton
          icon="remove"
          size={30}
          label="Slower"
          disabled={locked || shown <= 1}
          onPress={() => apply(shown - STEP)}
        />
        <Text style={s.speedValue}>{speed != null ? `${speed}%` : '—'}</Text>
        <IconButton
          icon="add"
          size={30}
          label="Faster"
          disabled={locked || shown >= 100}
          onPress={() => apply(shown + STEP)}
        />
      </View>
      <Slider
        value={shown}
        min={1}
        max={100}
        step={STEP}
        disabled={locked}
        color={shown > 50 ? c.warn : c.accent}
        onChange={(v) => setSpeed(v)}
        onCommit={(v) => apply(v)}
      />
      <View style={s.presets}>
        {PRESETS.map((p) => (
          <Button
            key={p}
            label={`${p}%`}
            size="sm"
            variant={p === speed ? 'primary' : 'secondary'}
            disabled={locked}
            onPress={() => apply(p)}
            style={{ flex: 1 }}
          />
        ))}
      </View>
      <Text style={[s.speedHint, msg?.bad && { color: c.bad }]}>
        {busy ? 'task running - change the speed when it finishes' : (msg?.text ?? '% of the UR5e maximum speed')}
      </Text>
    </View>
  );
}

function Stat({ label, value, hot }: { label: string; value: string; hot?: boolean }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  return (
    <View style={s.stat}>
      <Text style={[s.statValue, hot && { color: c.accent }]} numberOfLines={1}>
        {value}
      </Text>
      <Text style={s.statLabel}>{label}</Text>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    page: { paddingHorizontal: 24, paddingTop: 8, paddingBottom: 40 },
    title: { ...type.title, color: c.text },
    sub: { ...type.caption, fontSize: 12, color: c.faint, marginTop: 6, marginBottom: 20 },
    grid: { gap: 16 },
    card: { flex: 1, backgroundColor: c.panel, borderRadius: radius.lg, padding: 20, gap: 12 },
    cardHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    name: { ...type.cardTitle, fontSize: 17, color: c.text },
    url: { ...type.mono, color: c.faint, marginTop: -4 },
    tiles: { flexDirection: 'row', gap: 8 },
    stat: { flex: 1, backgroundColor: c.panelHi, borderRadius: radius.md, padding: 12, alignItems: 'center' },
    statValue: { ...type.stat, color: c.text },
    statLabel: { fontFamily: fonts.medium, fontSize: 10, lineHeight: 13, color: c.faint, marginTop: 2 },
    kv: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      gap: 12,
      paddingVertical: 10,
      borderBottomWidth: 1,
      borderBottomColor: c.line,
    },
    k: { ...type.caption, fontSize: 12, color: c.faint },
    v: { ...type.label, fontSize: 12, color: c.text, flexShrink: 1 },
    battRow: { flexDirection: 'row', alignItems: 'center', gap: 10, width: '60%' },
    battTrack: { flex: 1, height: 4, borderRadius: 2, backgroundColor: c.hover, overflow: 'hidden' },
    battFill: { height: 4, borderRadius: 2 },
    overline: { ...type.overline, color: c.faint, marginTop: 4 },
    speedBox: { backgroundColor: c.panelHi, borderRadius: radius.md, padding: 12, gap: 8 },
    speedHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    speedValue: { ...type.stat, color: c.text, minWidth: 56, textAlign: 'center' },
    speedHint: { ...type.caption, fontSize: 11, color: c.faint },
    presets: { flexDirection: 'row', gap: 6 },
    empty: { ...type.caption, fontSize: 12, color: c.faint },
    dot: { width: 6, height: 6, borderRadius: 3 },
    action: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 2 },
    actionName: { flex: 1, ...type.mono, fontSize: 12, color: c.text },
    actionMeta: { ...type.mono, fontSize: 10, color: c.faint },
    formRow: { flexDirection: 'row', gap: 8 },
    armRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    input: {
      backgroundColor: c.panel,
      borderRadius: radius.md,
      borderWidth: 1,
      borderColor: c.lineStrong,
      paddingHorizontal: 12,
      height: 40,
      ...type.body,
      color: c.text,
    },
  });
