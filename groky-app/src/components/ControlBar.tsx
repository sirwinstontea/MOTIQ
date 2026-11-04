import { Pressable, StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Colors, radius, shadowFloat, useStyles, useTheme } from '../theme';
import { IconButton } from './ui';

type Props = {
  live: boolean;
  muted: boolean;
  logOpen: boolean;
  onLog: () => void;
  onMic: () => void;
  onEnd: () => void;
};

// floating pill dock: transcript · accent mic · end session
export default function ControlBar({ live, muted, logOpen, onLog, onMic, onEnd }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const tap = (fn: () => void) => () => {
    Haptics.selectionAsync();
    fn();
  };

  const micIcon = !live ? 'mic-outline' : muted ? 'mic-off-outline' : 'mic';
  const micOn = !muted;

  return (
    <View style={s.dock}>
      <IconButton
        icon={logOpen ? 'document-text' : 'document-text-outline'}
        plain
        color={logOpen ? c.accent : c.dim}
        onPress={tap(onLog)}
        label="Transcript"
      />

      <Pressable
        onPress={tap(onMic)}
        accessibilityLabel={!live ? 'Start talking' : muted ? 'Unmute' : 'Mute'}
        style={({ pressed }) => [
          s.mic,
          { backgroundColor: micOn ? (pressed ? c.accentPress : c.accent) : c.hover },
          live && micOn && { boxShadow: `0 0 0 6px ${c.accentSoft}` },
        ]}
      >
        <Ionicons name={micIcon} size={22} color={micOn ? c.accentInk : c.text} />
      </Pressable>

      <IconButton
        icon="close"
        plain
        color={live ? c.text : c.faint}
        disabled={!live}
        onPress={tap(onEnd)}
        label="End session"
      />
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    dock: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-around',
      alignSelf: 'center',
      width: '100%',
      maxWidth: 320,
      height: 64,
      borderRadius: radius.pill,
      backgroundColor: c.panelHi,
      boxShadow: shadowFloat,
    },
    mic: { width: 48, height: 48, borderRadius: 24, alignItems: 'center', justifyContent: 'center' },
  });
