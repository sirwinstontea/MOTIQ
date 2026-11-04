import { ReactNode } from 'react';
import { ActivityIndicator, Pressable, StyleProp, StyleSheet, Text, View, ViewStyle } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Colors, radius, type, useStyles, useTheme } from '../theme';

export type IconName = keyof typeof Ionicons.glyphMap;

// Shared building blocks from the Groky design system.

type ButtonProps = {
  label: string;
  onPress?: () => void;
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  icon?: IconName;
  disabled?: boolean;
  loading?: boolean;
  block?: boolean;
  style?: StyleProp<ViewStyle>;
};

export function Button({
  label,
  onPress,
  variant = 'secondary',
  size = 'md',
  icon,
  disabled,
  loading,
  block,
  style,
}: ButtonProps) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const bg = { primary: c.accent, secondary: c.panelHi, ghost: 'transparent', danger: c.bad }[variant];
  const pressedBg = { primary: c.accentPress, secondary: c.hover, ghost: 'transparent', danger: c.bad }[variant];
  const fg = { primary: c.accentInk, secondary: c.text, ghost: c.dim, danger: c.accentInk }[variant];
  const h = { sm: 30, md: 40, lg: 48 }[size];

  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [
        s.btn,
        { height: h, backgroundColor: pressed ? pressedBg : bg },
        size === 'sm' && s.btnSm,
        block && { alignSelf: 'stretch' },
        (disabled || loading) && { opacity: disabled ? 0.4 : 0.8 },
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator size="small" color={fg} />
      ) : (
        icon && <Ionicons name={icon} size={size === 'sm' ? 14 : 18} color={fg} />
      )}
      <Text style={[s.btnText, size === 'sm' && { fontSize: 12 }, size === 'lg' && { fontSize: 14 }, { color: fg }]}>
        {label}
      </Text>
    </Pressable>
  );
}

type IconButtonProps = {
  icon: IconName;
  onPress?: () => void;
  size?: number;
  plain?: boolean;
  color?: string;
  disabled?: boolean;
  label?: string;
  children?: ReactNode;
  style?: StyleProp<ViewStyle>;
};

export function IconButton({
  icon,
  onPress,
  size = 40,
  plain,
  color,
  disabled,
  label,
  children,
  style,
}: IconButtonProps) {
  const { c } = useTheme();
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      hitSlop={6}
      accessibilityLabel={label}
      style={({ pressed }) => [
        {
          width: size,
          height: size,
          borderRadius: size / 2,
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: plain ? 'transparent' : pressed ? c.hover : c.panelHi,
        },
        disabled && { opacity: 0.4 },
        style,
      ]}
    >
      <Ionicons name={icon} size={Math.round(size * 0.45)} color={color ?? c.text} />
      {children}
    </Pressable>
  );
}

type SegmentedProps<T extends string> = {
  options: { id: T; label: string; icon?: IconName }[];
  value: T;
  onChange: (v: T) => void;
  stretch?: boolean;
};

export function Segmented<T extends string>({ options, value, onChange, stretch }: SegmentedProps<T>) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  return (
    <View style={[s.seg, stretch && { alignSelf: 'stretch' }]}>
      {options.map((o) => {
        const on = o.id === value;
        return (
          <Pressable
            key={o.id}
            onPress={() => onChange(o.id)}
            style={[s.segBtn, stretch && { flex: 1 }, on && { backgroundColor: c.accent }]}
          >
            {o.icon && <Ionicons name={o.icon} size={13} color={on ? c.accentInk : c.dim} />}
            <Text style={[s.segText, { color: on ? c.accentInk : c.dim }]}>{o.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

type PillProps = {
  label: string;
  tone?: 'neutral' | 'accent' | 'ok' | 'warn' | 'bad';
  dot?: boolean;
  style?: StyleProp<ViewStyle>;
};

export function Pill({ label, tone = 'neutral', dot, style }: PillProps) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const color = { neutral: c.dim, accent: c.accent, ok: c.ok, warn: c.warn, bad: c.bad }[tone];
  return (
    <View style={[s.pill, { backgroundColor: tone === 'accent' ? c.accentSoft : c.panelHi }, style]}>
      {dot && <View style={[s.dot, { backgroundColor: color }]} />}
      <Text style={[s.pillText, { color }]}>{label}</Text>
    </View>
  );
}

export function Card({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  const s = useStyles(makeStyles);
  return <View style={[s.card, style]}>{children}</View>;
}

// 36px rounded icon chip used at the start of list rows and cards
export function IconChip({ icon, color, bg }: { icon: IconName; color?: string; bg?: string }) {
  const { c } = useTheme();
  return (
    <View
      style={{
        width: 36,
        height: 36,
        borderRadius: radius.sm,
        backgroundColor: bg ?? c.panelHi,
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <Ionicons name={icon} size={18} color={color ?? c.text} />
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    btn: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8,
      paddingHorizontal: 16,
      borderRadius: radius.md,
    },
    btnSm: { paddingHorizontal: 12, borderRadius: radius.sm, gap: 6 },
    btnText: { ...type.label },
    seg: {
      flexDirection: 'row',
      alignSelf: 'flex-start',
      backgroundColor: c.panelHi,
      borderRadius: radius.md,
      padding: 3,
      gap: 2,
    },
    segBtn: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
      paddingHorizontal: 14,
      height: 30,
      borderRadius: 9,
    },
    segText: { ...type.label, fontSize: 12 },
    pill: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      height: 24,
      paddingHorizontal: 10,
      borderRadius: radius.pill,
    },
    pillText: { ...type.caption, fontFamily: type.label.fontFamily },
    dot: { width: 6, height: 6, borderRadius: 3 },
    card: { backgroundColor: c.panel, borderRadius: radius.lg, padding: 16 },
  });
