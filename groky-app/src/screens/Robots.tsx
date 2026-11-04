import { useState } from 'react';
import { ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import * as Haptics from 'expo-haptics';
import { devices, DeviceId, stopDevice } from '../devices';
import { useDevices } from '../hooks/useDevices';
import type { Line } from '../voice/types';
import { Button, IconChip, Pill } from '../components/ui';
import { Colors, fonts, radius, type, useStyles, useTheme } from '../theme';
import { ago } from '../lib/time';

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
    </ScrollView>
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

  const info = Object.entries(st.info ?? {}).filter(([k]) => k !== 'ok');
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
        <IconChip icon={id === 'dog' ? 'paw-outline' : 'construct-outline'} />
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
          <Text style={s.actionMeta}>{a.ms != null ? `${a.ms}ms · ${ago(a.at)}` : 'running'}</Text>
        </View>
      ))}
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
    empty: { ...type.caption, fontSize: 12, color: c.faint },
    dot: { width: 6, height: 6, borderRadius: 3 },
    action: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 2 },
    actionName: { flex: 1, ...type.mono, fontSize: 12, color: c.text },
    actionMeta: { ...type.mono, fontSize: 10, color: c.faint },
  });
