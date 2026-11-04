import { ReactNode } from 'react';
import { StyleSheet, useWindowDimensions, View } from 'react-native';
import Activity from '../components/Activity';
import type { Voice } from '../hooks/useVoice';

type Props = { voice: Voice; wide: boolean; camera: (height: number) => ReactNode };

export default function Cameras({ voice, wide, camera }: Props) {
  const { height } = useWindowDimensions();

  return (
    <View style={[s.page, wide && { flexDirection: 'row' }]}>
      <View style={wide && { flex: 1.5 }}>{camera(wide ? height - 190 : 260)}</View>
      <View style={s.activity}>
        <Activity traces={voice.traces} status={voice.status} />
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, padding: 16, gap: 16 },
  activity: { flex: 1, minHeight: 300 },
});
