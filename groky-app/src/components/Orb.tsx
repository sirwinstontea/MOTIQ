import { useEffect, useState } from 'react';
import { Animated, Easing, StyleSheet, View } from 'react-native';
import type { Status } from '../voice/types';
import { Colors, useStyles, useTheme } from '../theme';

// Assistant face: white-to-coral glowing sphere with two dark eyes.
// Breathes slowly (2.4s, 1 -> 1.04) while a session is live and swells with the mic level.

type Props = { status: Status; level: Animated.Value; size?: number };

export default function Orb({ status, level, size = 130 }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [breathe] = useState(() => new Animated.Value(0));
  const [blink] = useState(() => new Animated.Value(1));
  const asleep = status === 'off';

  useEffect(() => {
    if (asleep) {
      breathe.setValue(0);
      return;
    }
    const half = { duration: 1200, easing: Easing.inOut(Easing.sin), useNativeDriver: true };
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(breathe, { toValue: 1, ...half }),
        Animated.timing(breathe, { toValue: 0, ...half }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [breathe, asleep]);

  useEffect(() => {
    let t: ReturnType<typeof setTimeout>;
    const tick = () => {
      Animated.sequence([
        Animated.timing(blink, { toValue: 0.1, duration: 70, useNativeDriver: true }),
        Animated.timing(blink, { toValue: 1, duration: 110, useNativeDriver: true }),
      ]).start();
      t = setTimeout(tick, 3000 + Math.random() * 4000);
    };
    t = setTimeout(tick, 1500);
    return () => clearTimeout(t);
  }, [blink]);

  const scale = Animated.add(
    breathe.interpolate({ inputRange: [0, 1], outputRange: [1, 1.04] }),
    level.interpolate({ inputRange: [0, 1], outputRange: [0, 0.16], extrapolate: 'clamp' }),
  );

  // eye geometry scales with the mockup (110px orb: 9x13 eyes, 40px in from each side, 44px from top)
  const k = size / 110;
  const eye = { width: 9 * k, height: 13 * k, borderRadius: 6.5 * k, top: 44 * k };

  return (
    <View style={{ width: size * 1.7, height: size * 1.7, alignItems: 'center', justifyContent: 'center' }}>
      <Animated.View
        style={[
          s.orb,
          {
            width: size,
            height: size,
            borderRadius: size / 2,
            opacity: asleep ? 0.55 : 1,
            boxShadow: `0 0 ${60 * k}px ${18 * k}px rgba(232,132,106,${asleep ? 0.18 : 0.45})`,
            transform: [{ scale }],
          },
        ]}
      >
        {[40, null].map((left, i) => (
          <Animated.View
            key={i}
            style={[
              s.eye,
              eye,
              left != null ? { left: left * k } : { right: 40 * k },
              asleep ? { height: 3 * k, top: eye.top + 6 * k } : { transform: [{ scaleY: blink }] },
              { backgroundColor: c.page },
            ]}
          />
        ))}
      </Animated.View>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    orb: {
      backgroundColor: c.accent,
      backgroundImage: `radial-gradient(circle at 50% 45%, #ffffff 0%, #ffe6dc 35%, ${c.accent} 70%, ${c.accent} 100%)`,
    },
    eye: { position: 'absolute' },
  });
