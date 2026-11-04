import { ReactNode } from 'react';
import { StyleSheet, useWindowDimensions, View } from 'react-native';
import Activity from '../components/Activity';
import type { Voice } from '../hooks/useVoice';

type Props = { voice: Voice; wide: boolean; camera: (height: number) => ReactNode };

// camera card (~60%) + step tracker on desktop; full-width camera on mobile
export default function Cameras({ voice, wide, camera }: Props) {
  const { height, width } = useWindowDimensions();
  const mobile = width < 641;

  return (
    <View style={[s.page, mobile && s.pageMobile, wide && { flexDirection: 'row' }]}>
      <View style={wide && { flex: 1.6 }}>{camera(wide ? Math.max(260, height - 260) : 260)}</View>
      <View style={s.activity}>
        <Activity traces={voice.traces} status={voice.status} />
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, paddingHorizontal: 24, paddingTop: 8, paddingBottom: 24, gap: 16 },
  pageMobile: { paddingHorizontal: 16, paddingBottom: 12 },
  activity: { flex: 1, minHeight: 300 },
});
