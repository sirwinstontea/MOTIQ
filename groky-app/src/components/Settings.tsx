import { Modal, Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import env from '../lib/config';
import { devices } from '../devices';
import { Colors, Mode, radius, shadowFloat, type, useStyles, useTheme } from '../theme';
import { IconButton, Segmented } from './ui';

const modes: { id: Mode; label: string }[] = [
  { id: 'system', label: 'System' },
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
];

type Props = { open: boolean; onClose: () => void };

// modal on a 60% scrim (desktop), full-height sheet (mobile)
export default function Settings({ open, onClose }: Props) {
  const { c, mode, setMode } = useTheme();
  const s = useStyles(makeStyles);
  const mobile = useWindowDimensions().width < 641;
  const auth = env.tokenUrl ? 'token server' : env.xaiKey ? 'api key (dev)' : 'not set';

  return (
    <Modal visible={open} transparent animationType={mobile ? 'slide' : 'fade'} onRequestClose={onClose}>
      <Pressable style={[s.backdrop, mobile && s.backdropMobile]} onPress={onClose}>
        <Pressable style={[s.card, mobile && s.cardMobile]} onPress={() => {}}>
          <View style={s.head}>
            <Text style={s.title}>Settings</Text>
            <IconButton icon="close" size={32} onPress={onClose} label="Close" />
          </View>

          <Text style={[s.overline, { marginTop: 0 }]}>Appearance</Text>
          <Segmented options={modes} value={mode} onChange={setMode} stretch />

          <Text style={s.overline}>Connections</Text>
          <Row label="Grok voice" value={`${env.voiceModel} · ${env.voice}`} />
          <Row label="Auth" value={auth} color={auth === 'not set' ? c.warn : undefined} />
          {Object.values(devices).map((d, i, all) => (
            <Row key={d.label} label={d.label} value={d.url || 'simulated'} last={i === all.length - 1} />
          ))}
          <Text style={s.hint}>Edit .env and restart Expo to change these.</Text>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

function Row({ label, value, color, last }: { label: string; value: string; color?: string; last?: boolean }) {
  const s = useStyles(makeStyles);
  return (
    <View style={[s.row, last && { borderBottomWidth: 0 }]}>
      <Text style={s.rowLabel}>{label}</Text>
      <Text style={[s.rowValue, color && { color }]} numberOfLines={1}>
        {value}
      </Text>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    backdrop: {
      flex: 1,
      backgroundColor: c.scrim,
      alignItems: 'center',
      justifyContent: 'center',
      padding: 20,
    },
    backdropMobile: { justifyContent: 'flex-end', padding: 0 },
    card: {
      width: '100%',
      maxWidth: 380,
      backgroundColor: c.panel,
      borderRadius: radius.lg,
      padding: 24,
      boxShadow: shadowFloat,
    },
    cardMobile: {
      maxWidth: undefined,
      minHeight: '85%',
      borderBottomLeftRadius: 0,
      borderBottomRightRadius: 0,
      paddingBottom: 40,
    },
    head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 },
    title: { ...type.h2, color: c.text },
    overline: { ...type.overline, color: c.faint, marginTop: 20, marginBottom: 8 },
    row: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      paddingVertical: 10,
      borderBottomWidth: 1,
      borderBottomColor: c.line,
      gap: 12,
    },
    rowLabel: { ...type.label, fontSize: 12, color: c.text },
    rowValue: { ...type.caption, fontSize: 12, color: c.faint, flexShrink: 1 },
    hint: { ...type.caption, color: c.faint, marginTop: 14 },
  });
