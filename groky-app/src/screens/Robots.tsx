import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { devices, DeviceId, stopDevice } from '../devices';
import { useDevices } from '../hooks/useDevices';
import type { Line } from '../voice/types';
import { Colors, font, useStyles, useTheme } from '../theme';
import { ago } from '../lib/time';

type ToolLine = Extract<Line, { kind: 'tool' }>;

export default function Robots({ lines }: { lines: Line[] }) {
  const s = useStyles(makeStyles);
  const { width } = useWindowDimensions();
  const twoCols = width >= 1000;

  return (
    <ScrollView contentContainerStyle={s.page}>
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

  const color = !ready ? c.faint : { online: c.ok, sim: c.warn, offline: c.bad }[st.state];
  const info = Object.entries(st.info ?? {}).filter(([k]) => k !== 'ok');
  const battery = typeof st.info?.battery === 'number' ? (st.info.battery as number) : null;

  const stop = async () => {
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
    setStopping(true);
    await stopDevice(id);
    setStopping(false);
  };

  return (
    <View style={s.card}>
      <View style={s.cardHead}>
        <Ionicons name={id === 'dog' ? 'paw-outline' : 'construct-outline'} size={22} color={c.text} />
        <Text style={s.name}>{d.label}</Text>
        <View style={[s.pill, { backgroundColor: color + '22' }]}>
          <View style={[s.dot, { backgroundColor: color }]} />
          <Text style={[s.pillText, { color }]}>{ready ? st.state : 'checking'}</Text>
        </View>
        <View style={{ flex: 1 }} />
        <Pressable onPress={stop} style={s.stop} disabled={stopping}>
          <Ionicons name="hand-left" size={14} color="#fff" />
          <Text style={s.stopText}>{stopping ? '...' : 'Stop'}</Text>
        </Pressable>
      </View>

      <Text style={s.url}>
        {d.url || 'simulated - set EXPO_PUBLIC_' + id.toUpperCase() + '_URL to connect'}
        {st.ms != null && `  ·  ${st.ms} ms`}
        {st.state === 'offline' && `  ·  last seen ${ago(st.lastOnline)}`}
      </Text>

      <View style={s.stats}>
        <Stat label="succeeded" value={`${ok}/${done.length}`} />
        <Stat label="avg time" value={avg != null ? `${avg}ms` : '-'} />
        <Stat label="now" value={running ? running.name.slice(id.length + 1) : 'idle'} hot={!!running} />
      </View>

      {battery != null && (
        <View style={s.battRow}>
          <Text style={s.k}>battery</Text>
          <View style={s.battTrack}>
            <View
              style={[
                s.battFill,
                { width: `${Math.max(0, Math.min(100, battery))}%`, backgroundColor: battery < 20 ? c.bad : c.ok },
              ]}
            />
          </View>
          <Text style={s.v}>{battery}%</Text>
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

      <Text style={s.section}>Recent actions</Text>
      {recent.length === 0 && <Text style={s.empty}>Nothing yet.</Text>}
      {recent.map((a) => (
        <View key={a.id} style={s.action}>
          <View style={[s.dot, { backgroundColor: a.ok === undefined ? c.warn : a.ok ? c.ok : c.bad }]} />
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
    page: { padding: 24, paddingBottom: 40 },
    title: { color: c.text, fontSize: 28, fontWeight: '700' },
    sub: { color: c.dim, fontSize: 14, marginTop: 4, marginBottom: 20 },
    grid: { gap: 16 },
    card: {
      flex: 1,
      backgroundColor: c.panel,
      borderRadius: 20,
      padding: 18,
      borderWidth: 1,
      borderColor: c.line,
    },
    cardHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    name: { color: c.text, fontSize: 20, fontWeight: '700' },
    pill: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, height: 24, borderRadius: 12 },
    pillText: { fontSize: 12, fontWeight: '700' },
    dot: { width: 7, height: 7, borderRadius: 4 },
    stop: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 5,
      backgroundColor: c.bad,
      paddingHorizontal: 12,
      height: 30,
      borderRadius: 15,
    },
    stopText: { color: '#fff', fontWeight: '700', fontSize: 13 },
    url: { color: c.faint, fontSize: 12, fontFamily: font.mono, marginTop: 8 },
    stats: { flexDirection: 'row', gap: 8, marginTop: 14, marginBottom: 6 },
    stat: { flex: 1, backgroundColor: c.bg, borderRadius: 12, paddingVertical: 10, alignItems: 'center' },
    statValue: { color: c.text, fontSize: 17, fontWeight: '700', fontFamily: font.mono },
    statLabel: { color: c.dim, fontSize: 11, marginTop: 2 },
    battRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8 },
    battTrack: { flex: 1, height: 6, borderRadius: 3, backgroundColor: c.panelHi, overflow: 'hidden' },
    battFill: { height: 6, borderRadius: 3 },
    kv: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      paddingVertical: 8,
      borderBottomWidth: 1,
      borderBottomColor: c.line,
    },
    k: { color: c.dim, fontSize: 13, width: 80 },
    v: { color: c.text, fontSize: 13, fontWeight: '500', flexShrink: 1 },
    section: {
      color: c.dim,
      fontSize: 12,
      fontWeight: '600',
      textTransform: 'uppercase',
      letterSpacing: 0.8,
      marginTop: 16,
      marginBottom: 6,
    },
    empty: { color: c.faint, fontSize: 13 },
    action: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 5 },
    actionName: { flex: 1, color: c.text, fontFamily: font.mono, fontSize: 13 },
    actionMeta: { color: c.faint, fontSize: 11, fontFamily: font.mono },
  });
