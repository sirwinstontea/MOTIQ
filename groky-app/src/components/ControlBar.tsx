import { Pressable, StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Colors, useStyles, useTheme } from '../theme';

type Props = {
  live: boolean;
  muted: boolean;
  logOpen: boolean;
  onLog: () => void;
  onMic: () => void;
  onEnd: () => void;
};

export default function ControlBar({ live, muted, logOpen, onLog, onMic, onEnd }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const tap = (fn: () => void) => () => {
    Haptics.selectionAsync();
    fn();
  };

  const micIcon = !live ? 'mic-outline' : muted ? 'mic-off-outline' : 'mic';

  return (
    <View style={s.bar}>
      <Pressable onPress={tap(onLog)} style={s.btn} hitSlop={8}>
        <Ionicons name="document-text-outline" size={25} color={logOpen ? c.text : c.dim} />
      </Pressable>

      <Pressable onPress={tap(onMic)} style={[s.mic, live && !muted && s.micLive]} hitSlop={8}>
        <Ionicons name={micIcon} size={28} color={live && !muted ? c.bg : c.text} />
      </Pressable>

      <Pressable onPress={tap(onEnd)} style={s.btn} hitSlop={8} disabled={!live}>
        <Ionicons name="close" size={28} color={live ? c.text : c.faint} />
      </Pressable>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    bar: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      alignSelf: 'center',
      width: '86%',
      maxWidth: 420,
      paddingHorizontal: 22,
      height: 76,
      borderRadius: 38,
      backgroundColor: c.panel,
      borderWidth: 1,
      borderColor: c.line,
    },
    btn: { width: 48, height: 48, alignItems: 'center', justifyContent: 'center' },
    mic: {
      width: 56,
      height: 56,
      borderRadius: 28,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: c.panelHi,
    },
    micLive: { backgroundColor: c.text },
  });
