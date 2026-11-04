import { StyleSheet, Text, View } from 'react-native';
import { devices, DeviceId } from '../devices';
import { useDevices } from '../hooks/useDevices';
import { Colors, useStyles, useTheme } from '../theme';

export default function DeviceChips() {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const { status, ready } = useDevices();
  const dot = { sim: c.warn, online: c.ok, offline: c.bad };

  return (
    <View style={s.row}>
      {(Object.keys(devices) as DeviceId[]).map((id) => (
        <View key={id} style={s.chip}>
          <View style={[s.dot, { backgroundColor: ready ? dot[status[id].state] : c.faint }]} />
          <Text style={s.label}>{devices[id].label}</Text>
          <Text style={s.state}>{ready ? status[id].state : '...'}</Text>
        </View>
      ))}
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    row: { flexDirection: 'row', gap: 8 },
    chip: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      paddingHorizontal: 12,
      height: 32,
      borderRadius: 16,
      backgroundColor: c.panel,
      borderWidth: 1,
      borderColor: c.line,
    },
    dot: { width: 7, height: 7, borderRadius: 4 },
    label: { color: c.text, fontSize: 13, fontWeight: '600' },
    state: { color: c.dim, fontSize: 12 },
  });
