import { ReactNode, useState } from 'react';
import { Animated, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import Activity from '../components/Activity';
import ControlBar from '../components/ControlBar';
import Log from '../components/Log';
import Orb from '../components/Orb';
import type { Voice } from '../hooks/useVoice';
import type { Status } from '../voice/types';
import { Colors, radius, type, useStyles } from '../theme';

const caption: Record<Status, string> = {
  off: 'Tap the mic to wake me up',
  connecting: 'Connecting',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Talking',
  acting: 'On it',
};

type Props = {
  voice: Voice;
  level: Animated.Value;
  wide: boolean;
  mobile: boolean;
  camera: (height: number) => ReactNode;
  stopButton: ReactNode;
};

export default function Talk({ voice, level, wide, mobile, camera, stopButton }: Props) {
  const s = useStyles(makeStyles);
  const { height } = useWindowDimensions();
  const [logOpen, setLogOpen] = useState(false);

  const live = voice.status !== 'off';
  const lastGrok = voice.lines.findLast((l) => l.kind === 'grok');
  const headline = lastGrok?.kind === 'grok' ? lastGrok.text : 'hi, what should we build today?';

  const onMic = () => {
    if (!live) voice.start();
    else voice.setMuted(!voice.muted);
  };

  return (
    <View style={[s.body, mobile && s.bodyMobile]}>
      <View style={[s.stage, !mobile && s.stageGlow]}>
        {logOpen ? (
          <View style={{ flex: 1, alignSelf: 'stretch' }}>
            <Log lines={voice.lines} canSend={live} onSend={voice.say} />
          </View>
        ) : (
          <>
            <Text style={[s.headline, mobile && { fontSize: 22, lineHeight: 27 }]} numberOfLines={4}>
              {headline}
            </Text>
            <View style={s.center}>
              <Orb status={voice.status} level={level} size={mobile ? 110 : 130} />
              <Text style={s.caption}>{voice.muted && live ? 'Muted' : caption[voice.status]}</Text>
            </View>
          </>
        )}

        <View style={s.bottom}>
          {stopButton}
          <ControlBar
            live={live}
            muted={voice.muted}
            logOpen={logOpen}
            onLog={() => setLogOpen((o) => !o)}
            onMic={onMic}
            onEnd={voice.end}
          />
          <Text style={s.foot}>Groky can get things wrong. Keep a hand near the stop button.</Text>
        </View>
      </View>

      {wide && (
        <View style={s.side}>
          {camera(Math.min(200, height * 0.25))}
          <Activity traces={voice.traces} status={voice.status} />
        </View>
      )}
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    body: { flex: 1, flexDirection: 'row', gap: 16, paddingHorizontal: 24, paddingTop: 8, paddingBottom: 24 },
    bodyMobile: { paddingHorizontal: 16, paddingBottom: 12 },
    stage: {
      flex: 1,
      alignItems: 'center',
      justifyContent: 'space-between',
      borderRadius: radius.lg,
      paddingTop: 28,
      paddingBottom: 16,
      paddingHorizontal: 16,
    },
    stageGlow: {
      backgroundColor: c.bg,
      backgroundImage: `radial-gradient(60% 45% at 50% 55%, ${c.accentSoft}, ${c.bg} 75%)`,
    },
    headline: { ...type.greeting, color: c.text, textAlign: 'center', maxWidth: 640 },
    center: { alignItems: 'center', gap: 6 },
    caption: { ...type.caption, fontSize: 12, color: c.faint },
    bottom: { alignSelf: 'stretch', alignItems: 'center', gap: 8 },
    foot: { fontFamily: type.caption.fontFamily, fontSize: 10, lineHeight: 13, color: c.faint, textAlign: 'center' },
    side: { width: 340, gap: 16 },
  });
