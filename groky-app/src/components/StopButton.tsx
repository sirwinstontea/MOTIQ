import { Pressable, StyleSheet, Text } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Colors, useStyles } from '../theme';

export default function StopButton({ onPress }: { onPress: () => void }) {
  const s = useStyles(makeStyles);

  const press = () => {
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
    onPress();
  };

  return (
    <Pressable onPress={press} style={({ pressed }) => [s.stop, pressed && { opacity: 0.7 }]}>
      <Ionicons name="hand-left" size={16} color="#fff" />
      <Text style={s.text}>STOP ROBOTS</Text>
    </Pressable>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    stop: {
      alignSelf: 'center',
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      backgroundColor: c.bad,
      paddingHorizontal: 18,
      height: 38,
      borderRadius: 19,
      marginVertical: 12,
    },
    text: { color: '#fff', fontWeight: '800', fontSize: 13, letterSpacing: 1 },
  });
