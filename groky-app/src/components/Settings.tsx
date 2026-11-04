import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import env from '../lib/config';
import { devices } from '../devices';
import { Colors, Mode, useStyles, useTheme } from '../theme';

const modes: { id: Mode; label: string; icon: keyof typeof Ionicons.glyphMap }[] = [
  { id: 'system', label: 'System', icon: 'phone-portrait-outline' },
  { id: 'light', label: 'Light', icon: 'sunny-outline' },
  { id: 'dark', label: 'Dark', icon: 'moon-outline' },
];

type Props = { open: boolean; onClose: () => void };

export default function Settings({ open, onClose }: Props) {
  const { c, mode, setMode } = useTheme();
  const s = useStyles(makeStyles);

  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose}>
        <Pressable style={s.card} onPress={() => {}}>
          <View style={s.head}>
            <Text style={s.title}>Settings</Text>
            <Pressable onPress={onClose} hitSlop={10}>
              <Ionicons name="close" size={22} color={c.dim} />
            </Pressable>
          </View>

          <Text style={s.section}>Appearance</Text>
          <View style={s.segment}>
            {modes.map((m) => {
              const on = mode === m.id;
              return (
                <Pressable key={m.id} onPress={() => setMode(m.id)} style={[s.seg, on && s.segOn]}>
                  <Ionicons name={m.icon} size={16} color={on ? c.bg : c.dim} />
                  <Text style={[s.segText, on && { color: c.bg }]}>{m.label}</Text>
                </Pressable>
              );
            })}
          </View>

          <Text style={s.section}>Connections</Text>
          <Row label="Grok voice" value={`${env.voiceModel} · ${env.voice}`} />
          <Row label="Auth" value={env.tokenUrl ? 'token server' : env.xaiKey ? 'api key (dev)' : 'not set'} />
          {Object.values(devices).map((d) => (
            <Row key={d.label} label={d.label} value={d.url || 'simulated'} />
          ))}
          <Text style={s.hint}>Edit .env and restart Expo to change these.</Text>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  const s = useStyles(makeStyles);
  return (
    <View style={s.row}>
      <Text style={s.rowLabel}>{label}</Text>
      <Text style={s.rowValue} numberOfLines={1}>
        {value}
      </Text>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    backdrop: {
      flex: 1,
      backgroundColor: 'rgba(0,0,0,0.45)',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 20,
    },
    card: {
      width: '100%',
      maxWidth: 420,
      backgroundColor: c.bg,
      borderRadius: 22,
      padding: 20,
      borderWidth: 1,
      borderColor: c.line,
    },
    head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
    title: { color: c.text, fontSize: 20, fontWeight: '700' },
    section: {
      color: c.dim,
      fontSize: 12,
      fontWeight: '600',
      textTransform: 'uppercase',
      letterSpacing: 0.8,
      marginTop: 18,
      marginBottom: 8,
    },
    segment: { flexDirection: 'row', backgroundColor: c.panel, borderRadius: 12, padding: 4, gap: 4 },
    seg: {
      flex: 1,
      flexDirection: 'row',
      gap: 6,
      alignItems: 'center',
      justifyContent: 'center',
      height: 38,
      borderRadius: 9,
    },
    segOn: { backgroundColor: c.text },
    segText: { color: c.dim, fontSize: 14, fontWeight: '600' },
    row: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      paddingVertical: 9,
      borderBottomWidth: 1,
      borderBottomColor: c.line,
      gap: 12,
    },
    rowLabel: { color: c.text, fontSize: 14 },
    rowValue: { color: c.dim, fontSize: 13, flexShrink: 1 },
    hint: { color: c.faint, fontSize: 12, marginTop: 12 },
  });
