import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useDevices } from '../hooks/useDevices';
import { Colors, fonts, radius, type, useStyles, useTheme } from '../theme';

export type Tab = 'talk' | 'cameras' | 'robots' | 'skills' | 'history';

export const navItems: { id: Tab; label: string; icon: keyof typeof Ionicons.glyphMap }[] = [
  { id: 'talk', label: 'Talk', icon: 'mic-outline' },
  { id: 'cameras', label: 'Cameras', icon: 'videocam-outline' },
  { id: 'robots', label: 'Robots', icon: 'hardware-chip-outline' },
  { id: 'skills', label: 'Skills', icon: 'flash-outline' },
  { id: 'history', label: 'History', icon: 'time-outline' },
];

type Props = {
  tab: Tab;
  onTab: (t: Tab) => void;
  onSettings: () => void;
  historyCount: number;
  skillRunning?: boolean;
  compact?: boolean; // tablet: 64px icon rail
};

export function useNavBadges(historyCount: number, skillRunning?: boolean) {
  const { status } = useDevices();
  const offline = Object.values(status).filter((d) => d.state === 'offline').length;
  return (id: Tab): { count?: number; bad?: boolean; live?: boolean } =>
    id === 'history'
      ? { count: historyCount }
      : id === 'robots'
        ? { count: offline, bad: true }
        : id === 'skills'
          ? { live: skillRunning }
          : {};
}

export default function Sidebar({ tab, onTab, onSettings, historyCount, skillRunning, compact }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const badge = useNavBadges(historyCount, skillRunning);

  return (
    <View style={[s.side, compact && s.sideCompact]}>
      <Text style={[s.logo, compact && { paddingHorizontal: 0, textAlign: 'center' }]}>
        {compact ? 'g' : 'groky'}
        <Text style={{ color: c.accent }}>.</Text>
      </Text>

      <View style={{ gap: 4 }}>
        {navItems.map((it) => {
          const on = tab === it.id;
          const b = badge(it.id);
          return (
            <Pressable
              key={it.id}
              onPress={() => onTab(it.id)}
              accessibilityLabel={it.label}
              style={({ pressed }) => [s.item, compact && s.itemCompact, (on || pressed) && s.itemOn]}
            >
              <Ionicons name={it.icon} size={18} color={on ? c.accent : c.dim} />
              {!compact && <Text style={[s.label, on && s.labelOn]}>{it.label}</Text>}
              {!!b.count && (
                <View style={[s.badge, b.bad && { backgroundColor: c.bad }, compact && s.badgeCompact]}>
                  <Text style={[s.badgeText, b.bad && { color: c.accentInk }]}>{b.count}</Text>
                </View>
              )}
              {b.live && <View style={[s.live, compact && s.liveCompact]} />}
            </Pressable>
          );
        })}
      </View>

      <View style={{ flex: 1 }} />

      <Pressable
        onPress={onSettings}
        accessibilityLabel="Settings"
        style={({ pressed }) => [s.item, compact && s.itemCompact, pressed && s.itemOn]}
      >
        <Ionicons name="settings-outline" size={18} color={c.dim} />
        {!compact && <Text style={s.label}>Settings</Text>}
      </Pressable>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    side: {
      width: 220,
      paddingHorizontal: 12,
      paddingTop: 24,
      paddingBottom: 16,
      borderRightWidth: 1,
      borderRightColor: c.line,
      backgroundColor: c.bg,
    },
    sideCompact: { width: 64, paddingHorizontal: 8 },
    logo: {
      color: c.text,
      fontFamily: fonts.bold,
      fontSize: 18,
      letterSpacing: -0.72,
      paddingHorizontal: 12,
      marginBottom: 22,
    },
    item: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      paddingVertical: 10,
      paddingHorizontal: 12,
      borderRadius: radius.md,
    },
    itemCompact: { justifyContent: 'center', paddingHorizontal: 0, height: 44 },
    itemOn: { backgroundColor: c.panelHi },
    label: { ...type.label, fontFamily: fonts.medium, color: c.dim, flex: 1 },
    labelOn: { color: c.text },
    badge: {
      minWidth: 20,
      height: 18,
      borderRadius: 9,
      paddingHorizontal: 6,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: c.panelHi,
    },
    badgeCompact: { position: 'absolute', top: 4, right: 4, minWidth: 16, height: 16, paddingHorizontal: 4 },
    badgeText: { ...type.caption, fontFamily: fonts.bold, fontSize: 10, color: c.dim },
    live: { width: 8, height: 8, borderRadius: 4, backgroundColor: c.accent },
    liveCompact: { position: 'absolute', top: 8, right: 10 },
  });
