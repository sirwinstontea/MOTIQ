import { useEffect, useRef, useState } from 'react';
import { Animated, Easing, StyleSheet, View } from 'react-native';
import type { Status } from '../voice/types';
import { Colors, useStyles, useTheme } from '../theme';

// Assistant face: white-to-coral glowing sphere with two dark eyes.
// Idle (status off) rests dim with nearly closed eyes; waking (mic tap) eases open over ~700ms,
// then breathes slowly and swells with the mic level.

type Props = { status: Status; level: Animated.Value; size?: number };

export default function Orb({ status, level, size = 130 }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [breathe] = useState(() => new Animated.Value(0));
  const [blink] = useState(() => new Animated.Value(1));
  const [wake] = useState(() => new Animated.Value(status === 'off' ? 0 : 1));
  const asleep = status === 'off';
  const prevAsleep = useRef(asleep);

  // Smooth idle ↔ awake (mic tap). Native driver: opacity + scale only.
  useEffect(() => {
    const waking = prevAsleep.current && !asleep;
    const sleeping = !prevAsleep.current && asleep;
    prevAsleep.current = asleep;
    Animated.timing(wake, {
      toValue: asleep ? 0 : 1,
      duration: waking ? 720 : sleeping ? 480 : 560,
      easing: waking ? Easing.bezier(0.22, 1, 0.36, 1) : Easing.bezier(0.4, 0, 0.2, 1),
      useNativeDriver: true,
    }).start();
  }, [asleep, wake]);

  useEffect(() => {
    if (asleep) {
      breathe.stopAnimation();
      breathe.setValue(0);
      return;
    }
    const half = { duration: 1400, easing: Easing.inOut(Easing.sin), useNativeDriver: true };
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(breathe, { toValue: 1, ...half }),
        Animated.timing(breathe, { toValue: 0, ...half }),
      ]),
    );
    // let the wake ease finish before the breathe loop is obvious
    const delay = setTimeout(() => loop.start(), 280);
    return () => {
      clearTimeout(delay);
      loop.stop();
    };
  }, [breathe, asleep]);

  useEffect(() => {
    if (asleep) return;
    let t: ReturnType<typeof setTimeout>;
    const tick = () => {
      Animated.sequence([
        Animated.timing(blink, { toValue: 0.1, duration: 90, easing: Easing.out(Easing.quad), useNativeDriver: true }),
        Animated.timing(blink, { toValue: 1, duration: 140, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
      ]).start();
      t = setTimeout(tick, 3200 + Math.random() * 4200);
    };
    t = setTimeout(tick, 1800);
    return () => clearTimeout(t);
  }, [blink, asleep]);

  const breatheScale = Animated.add(
    breathe.interpolate({ inputRange: [0, 1], outputRange: [1, 1.04] }),
    level.interpolate({ inputRange: [0, 1], outputRange: [0, 0.16], extrapolate: 'clamp' }),
  );
  const wakeScale = wake.interpolate({ inputRange: [0, 1], outputRange: [0.9, 1] });
  const scale = Animated.multiply(breatheScale, wakeScale);
  const orbOpacity = wake.interpolate({ inputRange: [0, 1], outputRange: [0.5, 1] });
  const eyeOpen = wake.interpolate({ inputRange: [0, 0.35, 1], outputRange: [0.12, 0.55, 1] });
  const eyeScaleY = Animated.multiply(eyeOpen, blink);

  const k = size / 110;
  const eye = { width: 9 * k, height: 13 * k, borderRadius: 6.5 * k, top: 44 * k };
  const glow = wake.interpolate({
    inputRange: [0, 1],
    outputRange: [0.16, 0.45],
  });

  // boxShadow can't use native Animated on all targets; keep a static mid glow and fade the orb itself
  return (
    <View style={{ width: size * 1.7, height: size * 1.7, alignItems: 'center', justifyContent: 'center' }}>
      <Animated.View
        style={[
          s.orb,
          {
            width: size,
            height: size,
            borderRadius: size / 2,
            opacity: orbOpacity,
            boxShadow: `0 0 ${60 * k}px ${18 * k}px rgba(232,132,106,0.35)`,
            transform: [{ scale }],
          },
        ]}
      >
        {/* soft outer pulse that strengthens as it wakes (web-friendly opacity layer) */}
        <Animated.View
          pointerEvents="none"
            style={[
            StyleSheet.absoluteFill,
            {
              borderRadius: size / 2,
              opacity: glow,
              boxShadow: `0 0 ${72 * k}px ${22 * k}px rgba(232,132,106,0.55)`,
            },
          ]}
        />
        {[40, null].map((left, i) => (
          <Animated.View
            key={i}
            style={[
              s.eye,
              eye,
              left != null ? { left: left * k } : { right: 40 * k },
              { backgroundColor: c.page, transform: [{ scaleY: eyeScaleY }] },
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
      overflow: 'visible',
    },
    eye: { position: 'absolute' },
  });
