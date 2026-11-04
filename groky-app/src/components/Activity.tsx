import { useEffect, useRef, useState } from 'react';
import { Animated, FlatList, Image, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type { Status, Trace } from '../voice/types';
import { Colors, font, useStyles, useTheme } from '../theme';

type Icon = keyof typeof Ionicons.glyphMap;

const kinds: Record<Trace['kind'], { icon: Icon; stage: number }> = {
  hear: { icon: 'ear-outline', stage: 0 },
  think: { icon: 'sparkles-outline', stage: 1 },
  plan: { icon: 'git-branch-outline', stage: 1 },
  tool: { icon: 'hardware-chip-outline', stage: 2 },
  vision: { icon: 'eye-outline', stage: 3 },
  speak: { icon: 'volume-high-outline', stage: 4 },
  error: { icon: 'alert-circle-outline', stage: -1 },
};

const stages: { label: string; icon: Icon }[] = [
  { label: 'Hear', icon: 'ear-outline' },
  { label: 'Reason', icon: 'sparkles-outline' },
  { label: 'Act', icon: 'hardware-chip-outline' },
  { label: 'See', icon: 'eye-outline' },
  { label: 'Speak', icon: 'volume-high-outline' },
];

const clock = (t: number) => new Date(t).toTimeString().slice(0, 8);

export default function Activity({ traces, status }: { traces: Trace[]; status: Status }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const list = useRef<FlatList<Trace>>(null);

  const last = traces[traces.length - 1];
  const busy = status === 'thinking' || status === 'acting' || status === 'speaking';
  const stage = !last || status === 'off' ? -1 : status === 'listening' && !busy ? 0 : kinds[last.kind].stage;

  return (
    <View style={s.wrap}>
      <View style={s.head}>
        <Text style={s.title}>What Groky is doing</Text>
        {busy && <Pulse color={c.accent} />}
      </View>

      <View style={s.pipe}>
        {stages.map((st, i) => {
          const on = i === stage;
          return (
            <View key={st.label} style={s.pipeItem}>
              <View style={[s.pipeIcon, on && { backgroundColor: c.accent, borderColor: c.accent }]}>
                <Ionicons name={st.icon} size={15} color={on ? '#fff' : c.faint} />
              </View>
              <Text style={[s.pipeLabel, on && { color: c.text }]}>{st.label}</Text>
              {i < stages.length - 1 && <View style={s.pipeLine} />}
            </View>
          );
        })}
      </View>

      <FlatList
        ref={list}
        data={traces}
        keyExtractor={(t) => t.id}
        onContentSizeChange={() => list.current?.scrollToEnd({ animated: true })}
        contentContainerStyle={{ paddingBottom: 8 }}
        ListEmptyComponent={<Text style={s.empty}>Start talking and every step shows up here.</Text>}
        renderItem={({ item, index }) => {
          const k = kinds[item.kind];
          const color =
            item.ok === false ? c.bad : item.ok ? c.ok : index === traces.length - 1 && busy ? c.accent : c.dim;
          return (
            <View style={s.row}>
              <View style={s.rail}>
                <View style={[s.node, { borderColor: color }]}>
                  <Ionicons name={k.icon} size={13} color={color} />
                </View>
                {index < traces.length - 1 && <View style={s.railLine} />}
              </View>
              <View style={s.body}>
                <View style={s.titleRow}>
                  <Text style={s.rowTitle} numberOfLines={1}>
                    {item.title}
                  </Text>
                  <Text style={s.meta}>
                    {item.ms != null ? `${item.ms}ms  ` : ''}
                    {clock(item.at)}
                  </Text>
                </View>
                {!!item.detail && (
                  <Text style={[s.detail, item.kind === 'plan' && s.code]} numberOfLines={3}>
                    {item.detail}
                  </Text>
                )}
                {!!item.image && (
                  <Image source={{ uri: `data:image/jpeg;base64,${item.image}` }} style={s.thumb} resizeMode="cover" />
                )}
              </View>
            </View>
          );
        }}
      />
    </View>
  );
}

function Pulse({ color }: { color: string }) {
  const v = useState(() => new Animated.Value(0))[0];
  useEffect(() => {
    const loop = Animated.loop(Animated.timing(v, { toValue: 1, duration: 1100, useNativeDriver: true }));
    loop.start();
    return () => loop.stop();
  }, [v]);
  return (
    <View style={{ width: 12, height: 12, alignItems: 'center', justifyContent: 'center' }}>
      <Animated.View
        style={{
          position: 'absolute',
          width: 12,
          height: 12,
          borderRadius: 6,
          backgroundColor: color,
          opacity: v.interpolate({ inputRange: [0, 1], outputRange: [0.5, 0] }),
          transform: [{ scale: v.interpolate({ inputRange: [0, 1], outputRange: [0.6, 1.8] }) }],
        }}
      />
      <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: color }} />
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    wrap: { flex: 1, backgroundColor: c.panel, borderRadius: 18, padding: 14, borderWidth: 1, borderColor: c.line },
    head: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
    title: { color: c.text, fontSize: 15, fontWeight: '700' },
    pipe: { flexDirection: 'row', marginBottom: 14 },
    pipeItem: { flex: 1, alignItems: 'center' },
    pipeIcon: {
      width: 32,
      height: 32,
      borderRadius: 16,
      borderWidth: 1,
      borderColor: c.line,
      backgroundColor: c.bg,
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1,
    },
    pipeLabel: { color: c.faint, fontSize: 11, fontWeight: '600', marginTop: 4 },
    pipeLine: { position: 'absolute', top: 16, left: '60%', right: '-40%', height: 1, backgroundColor: c.line },
    empty: { color: c.faint, fontSize: 13, textAlign: 'center', marginTop: 20 },
    row: { flexDirection: 'row', gap: 10 },
    rail: { alignItems: 'center', width: 26 },
    node: {
      width: 26,
      height: 26,
      borderRadius: 13,
      borderWidth: 1.5,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: c.bg,
    },
    railLine: { flex: 1, width: 1.5, backgroundColor: c.line, marginVertical: 2 },
    body: { flex: 1, paddingBottom: 14 },
    titleRow: { flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 26 },
    rowTitle: { flex: 1, color: c.text, fontSize: 14, fontWeight: '600' },
    meta: { color: c.faint, fontSize: 11, fontFamily: font.mono },
    detail: { color: c.dim, fontSize: 13, lineHeight: 18, marginTop: 2 },
    code: { fontFamily: font.mono, fontSize: 12 },
    thumb: { width: 140, height: 90, borderRadius: 8, marginTop: 6, backgroundColor: '#000' },
  });
