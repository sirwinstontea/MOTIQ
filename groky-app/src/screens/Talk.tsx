import { ReactNode, useState } from 'react';
import { Animated, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import Activity from '../components/Activity';
import ControlBar from '../components/ControlBar';
import Log from '../components/Log';
import Orb from '../components/Orb';
import type { Voice } from '../hooks/useVoice';
import type { Status } from '../voice/types';
import { Colors, useStyles } from '../theme';

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
  camera: (height: number) => ReactNode;
  stopButton: ReactNode;
};

export default function Talk({ voice, level, wide, camera, stopButton }: Props) {
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
    <View style={s.row}>
      <View style={{ flex: 1 }}>
        {logOpen ? (
          <Log lines={voice.lines} canSend={live} onSend={voice.say} />
        ) : (
          <View style={s.stage}>
            <Text style={s.headline} numberOfLines={4}>
              {headline}
            </Text>
            <Orb status={voice.status} level={level} />
            <Text style={s.caption}>{voice.muted && live ? 'Muted' : caption[voice.status]}</Text>
          </View>
        )}

        {stopButton}

        <ControlBar
          live={live}
          muted={voice.muted}
          logOpen={logOpen}
          onLog={() => setLogOpen((o) => !o)}
          onMic={onMic}
          onEnd={voice.end}
        />
        <Text style={s.foot}>Grok can get things wrong. Keep a hand near the stop button.</Text>
      </View>

      {wide && (
        <View style={s.side}>
          {camera(Math.min(260, height * 0.32))}
          <Activity traces={voice.traces} status={voice.status} />
        </View>
      )}
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    row: { flex: 1, flexDirection: 'row' },
    stage: { flex: 1, alignItems: 'center', justifyContent: 'space-evenly', paddingHorizontal: 28 },
    headline: { color: c.text, fontSize: 26, lineHeight: 36, textAlign: 'center', fontWeight: '500', maxWidth: 640 },
    caption: { color: c.dim, fontSize: 16, fontWeight: '600', letterSpacing: 0.3 },
    foot: { color: c.faint, fontSize: 11, textAlign: 'center', marginTop: 10, marginBottom: 4 },
    side: { width: 380, paddingRight: 16, paddingTop: 8, paddingBottom: 12, gap: 12 },
  });
