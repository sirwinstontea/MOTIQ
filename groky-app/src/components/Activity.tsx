import { useEffect, useRef, useState } from 'react';
import { Animated, FlatList, Image, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type { Status, Trace } from '../voice/types';
import { Colors, fonts, radius, type, useStyles, useTheme } from '../theme';
import { Pill } from './ui';

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

const secs = (ms: number) => (ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`);

// "What Groky is doing": Hear → Reason → Act → See → Speak, then a log of each step
export default function Activity({ traces, status }: { traces: Trace[]; status: Status }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const list = useRef<FlatList<Trace>>(null);

  const last = traces[traces.length - 1];
  const busy = status === 'thinking' || status === 'acting' || status === 'speaking';
  const stage = !last || status === 'off' ? -1 : status === 'listening' && !busy ? 0 : kinds[last.kind].stage;

  // stages already passed in this turn (since the last time we heard the user)
  const turnStart = traces.findLastIndex((t) => t.kind === 'hear');
  const passed = new Set(traces.slice(Math.max(0, turnStart)).map((t) => kinds[t.kind].stage));

  return (
    <View style={s.card}>
      <Text style={s.title}>What Groky is doing</Text>

      <View style={s.steps}>
        <View style={s.hairline} />
        {stages.map((st, i) => {
          const now = i === stage;
          const done = !now && stage >= 0 && passed.has(i) && i < stage;
          return (
            <View key={st.label} style={s.step}>
              {now ? (
                <Halo>
                  <View style={[s.stepIcon, { backgroundColor: c.accent }]}>
                    <Ionicons name={st.icon} size={15} color={c.accentInk} />
                  </View>
                </Halo>
              ) : (
                <View style={[s.stepIcon, done && { backgroundColor: c.accentSoft }]}>
                  <Ionicons name={st.icon} size={15} color={done ? c.accent : c.faint} />
                </View>
              )}
              <Text style={[s.stepLabel, done && { color: c.dim }, now && { color: c.text }]}>{st.label}</Text>
            </View>
          );
        })}
      </View>

      <FlatList
        ref={list}
        data={traces}
        keyExtractor={(t) => t.id}
        onContentSizeChange={() => list.current?.scrollToEnd({ animated: true })}
        ListEmptyComponent={<Text style={s.empty}>Start talking and every step shows up here.</Text>}
        renderItem={({ item, index }) => {
          const k = kinds[item.kind];
          const running = index === traces.length - 1 && busy && item.ok === undefined;
          const color = item.ok === false ? c.bad : running ? c.accent : c.dim;
          return (
            <View style={[s.row, index === traces.length - 1 && { borderBottomWidth: 0 }]}>
              <Ionicons name={k.icon} size={15} color={color} style={{ marginTop: 1 }} />
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={[s.rowTitle, item.ok === false && { color: c.bad }]} numberOfLines={1}>
                  {item.title}
                </Text>
                {!!item.detail && (
                  <Text style={[s.rowSub, item.kind === 'plan' && s.code]} numberOfLines={3}>
                    {item.kind === 'hear' ? `“${item.detail}”` : item.detail}
                  </Text>
                )}
                {!!item.image && (
                  <Image source={{ uri: `data:image/jpeg;base64,${item.image}` }} style={s.thumb} resizeMode="cover" />
                )}
              </View>
              {running ? (
                <Pill label="running" tone="accent" />
              ) : item.ms != null ? (
                <Text style={s.ms}>{secs(item.ms)}</Text>
              ) : null}
            </View>
          );
        }}
      />
    </View>
  );
}

// current step: accent fill with a pulsing 5px accent-soft halo
function Halo({ children }: { children: React.ReactNode }) {
  const { c } = useTheme();
  const [v] = useState(() => new Animated.Value(0));
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(v, { toValue: 1, duration: 900, useNativeDriver: true }),
        Animated.timing(v, { toValue: 0, duration: 900, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [v]);
  return (
    <View style={{ width: 34, height: 34, alignItems: 'center', justifyContent: 'center' }}>
      <Animated.View
        style={{
          position: 'absolute',
          width: 44,
          height: 44,
          borderRadius: 22,
          backgroundColor: c.accentSoft,
          opacity: v.interpolate({ inputRange: [0, 1], outputRange: [1, 0.45] }),
          transform: [{ scale: v.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1.08] }) }],
        }}
      />
      {children}
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    card: { flex: 1, backgroundColor: c.panel, borderRadius: radius.lg, padding: 16, gap: 16 },
    title: { ...type.cardTitle, color: c.text },
    steps: { flexDirection: 'row', justifyContent: 'space-between', paddingHorizontal: 4 },
    hairline: { position: 'absolute', left: 24, right: 24, top: 17, height: 1, backgroundColor: c.lineStrong },
    step: { alignItems: 'center', gap: 6, minWidth: 44 },
    stepIcon: {
      width: 34,
      height: 34,
      borderRadius: 17,
      backgroundColor: c.panelHi,
      alignItems: 'center',
      justifyContent: 'center',
    },
    stepLabel: { fontFamily: fonts.medium, fontSize: 10, lineHeight: 12, color: c.faint },
    empty: { ...type.caption, fontSize: 12, color: c.faint, textAlign: 'center', marginVertical: 24 },
    row: {
      flexDirection: 'row',
      alignItems: 'flex-start',
      gap: 12,
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: c.line,
    },
    rowTitle: { ...type.label, color: c.text },
    rowSub: { ...type.caption, color: c.faint, marginTop: 2 },
    code: { fontFamily: fonts.mono },
    ms: { ...type.caption, color: c.faint },
    thumb: { width: 140, height: 90, borderRadius: radius.sm, marginTop: 6, backgroundColor: c.page },
  });
