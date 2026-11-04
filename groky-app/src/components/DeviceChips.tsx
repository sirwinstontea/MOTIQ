import { StyleSheet, Text, View } from 'react-native';
import { devices, DeviceId } from '../devices';
import { useDevices } from '../hooks/useDevices';
import { Colors, fonts, radius, type, useStyles, useTheme } from '../theme';

// robot chips in the top bar: status dot + name + state badge
export default function DeviceChips({ compact }: { compact?: boolean }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const { status, ready } = useDevices();
  const dot = { sim: c.ok, online: c.ok, offline: c.bad };

  return (
    <View style={s.row}>
      {(Object.keys(devices) as DeviceId[]).map((id) => {
        const st = status[id].state;
        const badge = !ready ? '...' : st === 'online' ? null : st;
        return (
          <View key={id} style={[s.chip, compact && { paddingHorizontal: 10 }]}>
            <View style={[s.dot, { backgroundColor: ready ? dot[st] : c.faint }]} />
            <Text style={s.label}>{devices[id].label}</Text>
            {badge && (
              <View style={[s.badge, st === 'offline' && { backgroundColor: c.panelHi }]}>
                <Text style={[s.badgeText, st === 'offline' && { color: c.bad }]}>{badge}</Text>
              </View>
            )}
          </View>
        );
      })}
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    row: { flexDirection: 'row', gap: 8, flexShrink: 1 },
    chip: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      paddingLeft: 10,
      paddingRight: 12,
      height: 30,
      borderRadius: radius.pill,
      backgroundColor: c.panel,
    },
    dot: { width: 6, height: 6, borderRadius: 3 },
    label: { ...type.label, fontSize: 12, color: c.text },
    badge: { backgroundColor: c.accentSoft, paddingHorizontal: 6, paddingVertical: 2, borderRadius: radius.pill },
    badgeText: { fontFamily: fonts.medium, fontSize: 10, lineHeight: 12, color: c.accent },
  });
