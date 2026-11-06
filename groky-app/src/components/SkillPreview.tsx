import { useEffect, useMemo, useState } from 'react';
import { LayoutChangeEvent, Pressable, StyleSheet, Text, View, ViewStyle } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { ARM, ArmFrame, buildTimeline, H, Pt, Timeline, W } from '../preview/scene';
import type { SkillDoc } from '../skills';
import { Colors, fonts, radius, type, useStyles, useTheme } from '../theme';
import { IconButton } from './ui';

type Props = { skill: SkillDoc; autoPlay?: boolean };

// Animated 2D preview of what a skill intends to do. Conceptual only: see src/preview/scene.ts.
export default function SkillPreview({ skill, autoPlay = true }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  // rebuild only when the steps actually change (the editor passes a new object every render)
  const key = JSON.stringify([skill.robot, skill.action_primitives, skill.environment, skill.constraints, skill.steps]);
  const tl = useMemo<Timeline>(() => {
    const [robot, action_primitives, environment, constraints, steps] = JSON.parse(key);
    return buildTimeline({ ...skill, robot, action_primitives, environment, constraints, steps });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(autoPlay);
  const [width, setWidth] = useState(0);

  // restart when the steps change
  const [shownKey, setShownKey] = useState(key);
  if (shownKey !== key) {
    setShownKey(key);
    setT(0);
  }

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      if (last) {
        const dt = Math.min(0.1, (now - last) / 1000);
        setT((prev) => (prev + dt > tl.duration ? 0 : prev + dt));
      }
      last = now;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, tl]);

  const f = tl.sample(Math.min(t, tl.duration));
  const k = width / W;
  const cur = tl.steps[f.step];
  const caption =
    f.step < 0
      ? { title: 'Ready', note: 'Starting position', warn: false }
      : !cur
        ? doneCaption(tl)
        : { title: `${f.step + 1}. ${cur.label}`, note: cur.note, warn: cur.warn };

  return (
    <View style={s.wrap}>
      <View style={s.scene} onLayout={(e: LayoutChangeEvent) => setWidth(e.nativeEvent.layout.width)}>
        {width > 0 && <ArmScene f={f} k={k} c={c} />}
        <View style={s.badge}>
          <Text style={s.badgeText}>CONCEPTUAL 2D</Text>
        </View>
      </View>

      <View style={s.trackWrap}>
        <View style={s.track}>
          <View style={[s.fill, { width: `${(100 * f.t) / tl.duration}%` }]} />
        </View>
        <View
          pointerEvents="none"
          style={[
            s.scrubThumb,
            {
              left: `${Math.max(0, Math.min(100, (100 * f.t) / tl.duration))}%`,
              marginLeft: -7,
              borderColor: c.accent,
              backgroundColor: c.text,
            },
          ]}
        />
      </View>

      <View style={s.controls}>
        <IconButton
          icon={playing ? 'pause' : 'play'}
          size={32}
          onPress={() => setPlaying((p) => !p)}
          label={playing ? 'Pause' : 'Play'}
        />
        <IconButton
          icon="refresh"
          size={32}
          plain
          color={c.dim}
          onPress={() => {
            setT(0);
            setPlaying(true);
          }}
          label="Restart"
        />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={s.capTitle} numberOfLines={1}>
            {caption.title}
          </Text>
          <Text style={[s.capNote, caption.warn && { color: c.warn }]} numberOfLines={2}>
            {caption.note}
          </Text>
        </View>
        <Text style={s.time}>
          {f.t.toFixed(1)} / {tl.duration.toFixed(1)}s
        </Text>
      </View>

      <View style={s.chips}>
        {tl.steps.map((st) => {
          const on = st.index === f.step;
          const done = st.index < f.step;
          return (
            <Pressable
              key={st.index}
              onPress={() => setT(st.start + 0.001)}
              style={[s.chip, on && { backgroundColor: c.accentSoft }, st.warn && done && { borderColor: c.warn }]}
            >
              {done ? (
                <Ionicons name={st.warn ? 'alert' : 'checkmark'} size={12} color={st.warn ? c.warn : c.accent} />
              ) : (
                <Text style={[s.chipNum, on && { color: c.accent }]}>{st.index + 1}</Text>
              )}
              <Text style={[s.chipText, on && { color: c.text }]} numberOfLines={1}>
                {st.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <Text style={s.disclaimer}>
        Replays the UR5e model&apos;s dry run (limits, reach, singularities) in a 2D sketch. No collision checking.
      </Text>
    </View>
  );
}

function doneCaption(tl: Timeline) {
  const warns = tl.steps.filter((x) => x.warn).length;
  return {
    title: 'Done',
    note: warns ? `${tl.steps.length} steps · ${warns} to review` : `${tl.steps.length} steps, nothing flagged`,
    warn: warns > 0,
  };
}

// ---------- drawing primitives (absolute Views in scene units * k) ----------

function Rect({
  x0,
  y0,
  x1,
  y1,
  k,
  style,
}: {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  k: number;
  style: ViewStyle;
}) {
  return (
    <View
      style={[{ position: 'absolute', left: x0 * k, top: y0 * k, width: (x1 - x0) * k, height: (y1 - y0) * k }, style]}
    />
  );
}

function Dot({ p, r, k, color }: { p: Pt; r: number; k: number; color: string }) {
  return (
    <View
      style={{
        position: 'absolute',
        left: (p.x - r) * k,
        top: (p.y - r) * k,
        width: 2 * r * k,
        height: 2 * r * k,
        borderRadius: r * k,
        backgroundColor: color,
      }}
    />
  );
}

// rounded bar from a to b
function Bar({ a, b, w, k, color, opacity }: { a: Pt; b: Pt; w: number; k: number; color: string; opacity?: number }) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) + w; // + w for round caps
  return (
    <View
      style={{
        position: 'absolute',
        left: ((a.x + b.x) / 2 - len / 2) * k,
        top: ((a.y + b.y) / 2 - w / 2) * k,
        width: len * k,
        height: w * k,
        borderRadius: (w * k) / 2,
        backgroundColor: color,
        opacity,
        transform: [{ rotate: `${Math.atan2(dy, dx)}rad` }],
      }}
    />
  );
}

function Label({ x, y, k, text, c }: { x: number; y: number; k: number; text: string; c: Colors }) {
  return (
    <Text
      style={{
        position: 'absolute',
        left: (x - 40) * k,
        top: (y - 6) * k,
        width: 80 * k,
        textAlign: 'center',
        fontFamily: fonts.mono,
        fontSize: Math.max(8, 10 * k),
        color: c.faint,
      }}
    >
      {text}
    </Text>
  );
}

// ---------- arm (side view) ----------

function ArmScene({ f, k, c }: { f: ArmFrame; k: number; c: Colors }) {
  const { base, tableY } = ARM;
  const gap = 19 + (13.5 - 19) * f.grip;
  const w = f.wrist;
  const containers = f.objects.filter((o) => o.container);
  const things = f.objects.filter((o) => !o.container);
  return (
    <>
      <Rect x0={0} y0={tableY} x1={W} y1={H} k={k} style={{ backgroundColor: c.hover }} />
      <Label x={40} y={tableY + 22} k={k} text="table" c={c} />
      {f.objects.map((o) => (
        <Label key={`l${o.id}`} x={o.x} y={tableY + 22} k={k} text={o.label} c={c} />
      ))}

      {containers.map((o) => (
        <Rect
          key={`b${o.id}`}
          x0={o.x - o.w / 2}
          y0={o.y - o.h}
          x1={o.x + o.w / 2}
          y1={o.y}
          k={k}
          style={{ backgroundColor: o.color, opacity: 0.35 }}
        />
      ))}
      {things.map((o) => (
        <Rect
          key={`o${o.id}`}
          x0={o.x - o.w / 2}
          y0={o.y - o.h / 2}
          x1={o.x + o.w / 2}
          y1={o.y + o.h / 2}
          k={k}
          style={{
            backgroundColor: o.color,
            borderColor: c.lineStrong,
            borderWidth: Math.max(1, k),
            borderRadius: 2 * k,
          }}
        />
      ))}
      {containers.map((o) => (
        <View key={`w${o.id}`}>
          <Rect
            x0={o.x - o.w / 2}
            y0={o.y - 4}
            x1={o.x + o.w / 2}
            y1={o.y}
            k={k}
            style={{ backgroundColor: o.color }}
          />
          <Rect
            x0={o.x - o.w / 2}
            y0={o.y - o.h}
            x1={o.x - o.w / 2 + 3}
            y1={o.y}
            k={k}
            style={{ backgroundColor: o.color }}
          />
          <Rect
            x0={o.x + o.w / 2 - 3}
            y0={o.y - o.h}
            x1={o.x + o.w / 2}
            y1={o.y}
            k={k}
            style={{ backgroundColor: o.color }}
          />
        </View>
      ))}

      <Rect
        x0={base.x - 20}
        y0={base.y - 44}
        x1={base.x + 20}
        y1={base.y}
        k={k}
        style={{ backgroundColor: c.lineStrong, borderRadius: 6 * k }}
      />
      <Bar a={f.shoulder} b={f.elbow} w={14} k={k} color="#8b8e94" />
      <Bar a={f.elbow} b={w} w={11} k={k} color="#8b8e94" />
      <Dot p={f.shoulder} r={6} k={k} color={c.text} />
      <Dot p={f.elbow} r={5} k={k} color={c.text} />

      <Rect
        x0={w.x - gap - 3}
        y0={w.y + 4}
        x1={w.x + gap + 3}
        y1={w.y + 8}
        k={k}
        style={{ backgroundColor: c.accent }}
      />
      <Rect
        x0={w.x - gap - 2}
        y0={w.y + 4}
        x1={w.x - gap + 2}
        y1={w.y + ARM.jaw}
        k={k}
        style={{ backgroundColor: c.accent, borderRadius: 1.5 * k }}
      />
      <Rect
        x0={w.x + gap - 2}
        y0={w.y + 4}
        x1={w.x + gap + 2}
        y1={w.y + ARM.jaw}
        k={k}
        style={{ backgroundColor: c.accent, borderRadius: 1.5 * k }}
      />
      <Dot p={w} r={7} k={k} color={c.text} />

      {f.scan > 0.01 && <Scan scan={f.scan} k={k} c={c} label="camera check" />}
    </>
  );
}

function Scan({ scan, k, c, label }: { scan: number; k: number; c: Colors; label: string }) {
  return (
    <>
      <Rect x0={0} y0={0} x1={W} y1={H} k={k} style={{ backgroundColor: c.accent, opacity: 0.12 * scan }} />
      <Rect
        x0={6}
        y0={6}
        x1={W - 6}
        y1={H - 6}
        k={k}
        style={{ borderColor: c.accent, borderWidth: 2, borderRadius: 10 * k, opacity: scan }}
      />
      <Text
        style={{
          position: 'absolute',
          left: 14 * k,
          top: 12 * k,
          fontFamily: fonts.mono,
          fontSize: 11,
          color: c.accent,
          opacity: scan,
        }}
      >
        ● {label}
      </Text>
    </>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    wrap: { gap: 10 },
    scene: {
      width: '100%',
      aspectRatio: W / H,
      backgroundColor: c.panelHi,
      borderRadius: radius.md,
      overflow: 'hidden',
    },
    badge: {
      position: 'absolute',
      right: 8,
      bottom: 8,
      paddingHorizontal: 8,
      height: 20,
      borderRadius: radius.pill,
      justifyContent: 'center',
      backgroundColor: c.accentSoft,
    },
    badgeText: { ...type.overline, fontSize: 9, color: c.accent },
    trackWrap: { height: 20, justifyContent: 'center' },
    track: { height: 6, borderRadius: 3, backgroundColor: c.hover, overflow: 'hidden' },
    fill: { height: 6, borderRadius: 3, backgroundColor: c.accent },
    scrubThumb: {
      position: 'absolute',
      top: 3,
      width: 14,
      height: 14,
      borderRadius: 7,
      borderWidth: 2,
    },
    controls: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    capTitle: { ...type.label, color: c.text },
    capNote: { ...type.caption, fontSize: 12, color: c.dim, marginTop: 2 },
    time: { ...type.mono, color: c.faint },
    chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
    chip: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      maxWidth: '100%',
      paddingHorizontal: 10,
      height: 26,
      borderRadius: radius.pill,
      borderWidth: 1,
      borderColor: 'transparent',
      backgroundColor: c.hover,
    },
    chipNum: { fontFamily: fonts.mono, fontSize: 10, color: c.faint },
    chipText: { fontFamily: fonts.mono, fontSize: 11, color: c.dim, flexShrink: 1 },
    disclaimer: { ...type.caption, color: c.faint },
  });
