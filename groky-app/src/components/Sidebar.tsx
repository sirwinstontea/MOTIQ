import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useDevices } from '../hooks/useDevices';
import { Colors, useStyles, useTheme } from '../theme';

export type Tab = 'talk' | 'cameras' | 'robots' | 'history';

const items: { id: Tab; label: string; icon: keyof typeof Ionicons.glyphMap }[] = [
  { id: 'talk', label: 'Talk', icon: 'mic-outline' },
  { id: 'cameras', label: 'Cameras', icon: 'videocam-outline' },
  { id: 'robots', label: 'Robots', icon: 'hardware-chip-outline' },
  { id: 'history', label: 'History', icon: 'time-outline' },
];

type Props = {
  tab: Tab;
  onTab: (t: Tab) => void;
  onSettings: () => void;
  historyCount: number;
};

export default function Sidebar({ tab, onTab, onSettings, historyCount }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const { status } = useDevices();
  const offline = Object.values(status).filter((d) => d.state === 'offline').length;

  return (
    <View style={s.side}>
      <Text style={s.brand}>groky</Text>

      <View style={{ gap: 4 }}>
        {items.map((it) => {
          const on = tab === it.id;
          const badge = it.id === 'history' ? historyCount : it.id === 'robots' ? offline : 0;
          return (
            <Pressable key={it.id} onPress={() => onTab(it.id)} style={[s.item, on && s.itemOn]}>
              <Ionicons name={it.icon} size={20} color={on ? c.text : c.dim} />
              <Text style={[s.label, on && s.labelOn]}>{it.label}</Text>
              {badge > 0 && (
                <View style={[s.badge, it.id === 'robots' && { backgroundColor: c.bad }]}>
                  <Text style={[s.badgeText, it.id === 'robots' && { color: '#fff' }]}>{badge}</Text>
                </View>
              )}
            </Pressable>
          );
        })}
      </View>

      <View style={{ flex: 1 }} />

      <Pressable onPress={onSettings} style={s.item}>
        <Ionicons name="settings-outline" size={20} color={c.dim} />
        <Text style={s.label}>Settings</Text>
      </Pressable>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    side: {
      width: 220,
      paddingHorizontal: 12,
      paddingTop: 14,
      paddingBottom: 12,
      borderRightWidth: 1,
      borderRightColor: c.line,
      backgroundColor: c.bg,
    },
    brand: {
      color: c.text,
      fontSize: 22,
      fontWeight: '800',
      letterSpacing: -0.5,
      paddingHorizontal: 12,
      marginBottom: 22,
    },
    item: { flexDirection: 'row', alignItems: 'center', gap: 12, height: 44, paddingHorizontal: 12, borderRadius: 12 },
    itemOn: { backgroundColor: c.panel },
    label: { color: c.dim, fontSize: 15, fontWeight: '500', flex: 1 },
    labelOn: { color: c.text, fontWeight: '600' },
    badge: {
      minWidth: 22,
      height: 20,
      borderRadius: 10,
      paddingHorizontal: 6,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: c.panelHi,
    },
    badgeText: { color: c.dim, fontSize: 11, fontWeight: '700' },
  });
