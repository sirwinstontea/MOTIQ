import { useEffect, useState } from 'react';
import { Animated, Easing, StyleSheet, View } from 'react-native';
import type { Status } from '../voice/types';
import { Colors, useStyles, useTheme } from '../theme';

const SIZE = 150;

// halo colour hints at what it's doing; the body stays neutral
const halo = (c: Colors): Record<Status, string> => ({
  off: c.faint,
  connecting: c.dim,
  listening: c.orb,
  thinking: c.warn,
  speaking: c.ok,
  acting: c.accent,
});

type Props = { status: Status; level: Animated.Value };

export default function Orb({ status, level }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const breathe = useState(() => new Animated.Value(0))[0];
  const blink = useState(() => new Animated.Value(1))[0];
  const look = useState(() => new Animated.Value(0))[0];

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(breathe, {
          toValue: 1,
          duration: 1800,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
        Animated.timing(breathe, {
          toValue: 0,
          duration: 1800,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [breathe]);

  useEffect(() => {
    let t: ReturnType<typeof setTimeout>;
    const tick = () => {
      Animated.sequence([
        Animated.timing(blink, { toValue: 0.1, duration: 70, useNativeDriver: true }),
        Animated.timing(blink, { toValue: 1, duration: 110, useNativeDriver: true }),
      ]).start();
      t = setTimeout(tick, 2500 + Math.random() * 3500);
    };
    t = setTimeout(tick, 1500);
    return () => clearTimeout(t);
  }, [blink]);

  // eyes drift sideways while it's "thinking" or moving a robot
  useEffect(() => {
    if (status !== 'thinking' && status !== 'acting') {
      Animated.spring(look, { toValue: 0, useNativeDriver: true }).start();
      return;
    }
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(look, { toValue: -6, duration: 500, useNativeDriver: true }),
        Animated.timing(look, { toValue: 6, duration: 900, useNativeDriver: true }),
        Animated.timing(look, { toValue: 0, duration: 500, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [status, look]);

  const scale = Animated.add(
    breathe.interpolate({ inputRange: [0, 1], outputRange: [0.97, 1.02] }),
    level.interpolate({ inputRange: [0, 1], outputRange: [0, 0.18], extrapolate: 'clamp' }),
  );
  const color = halo(c)[status];
  const asleep = status === 'off';

  return (
    <View style={s.wrap}>
      <Animated.View
        style={[
          s.halo,
          {
            backgroundColor: color,
            opacity: asleep ? 0.05 : 0.14,
            transform: [{ scale: Animated.multiply(scale, 1.35) }],
          },
        ]}
      />
      <Animated.View
        style={[
          s.body,
          {
            backgroundColor: c.orb,
            boxShadow: `0 0 60px 14px ${color}44`,
            opacity: asleep ? 0.35 : 1,
            transform: [{ scale }],
          },
        ]}
      >
        <Animated.View style={[s.eyes, { transform: [{ translateX: look }] }]}>
          {[0, 1].map((i) => (
            <Animated.View
              key={i}
              style={[s.eye, asleep && s.eyeClosed, { transform: [{ scaleY: asleep ? 1 : blink }] }]}
            />
          ))}
        </Animated.View>
        {status === 'acting' && <View style={s.antenna} />}
      </Animated.View>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    wrap: { width: SIZE * 1.6, height: SIZE * 1.6, alignItems: 'center', justifyContent: 'center' },
    halo: { position: 'absolute', width: SIZE, height: SIZE, borderRadius: SIZE / 2 },
    body: {
      width: SIZE,
      height: SIZE,
      borderRadius: SIZE / 2,
      alignItems: 'center',
      justifyContent: 'center',
    },
    eyes: { flexDirection: 'row', gap: 22, marginTop: -8 },
    eye: { width: 11, height: 22, borderRadius: 6, backgroundColor: c.bg },
    eyeClosed: { height: 3, marginTop: 10 },
    antenna: {
      position: 'absolute',
      bottom: 26,
      width: 26,
      height: 4,
      borderRadius: 2,
      backgroundColor: c.accent,
    },
  });
