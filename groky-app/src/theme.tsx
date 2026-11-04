import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from 'react';
import { TextStyle, useColorScheme } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

// Groky design system tokens. Dark is the primary theme.
const dark = {
  page: '#0b0c0d', // bg-000: camera viewports, deepest surface
  bg: '#131416', // bg-100: screen
  panel: '#1a1b1e', // bg-200: cards, lists, inputs, nav
  panelHi: '#232428', // bg-300: controls on cards, icon chips, tiles
  hover: '#2d2f33', // bg-400: pressed controls, progress track
  line: '#26282c',
  lineStrong: '#3a3c41',
  text: '#f3f1ee', // ink
  dim: '#a3a5aa', // ink-2
  faint: '#7a7c82', // ink-3
  orb: '#ffffff',
  accent: '#e8846a',
  accentPress: '#d4704f',
  accentInk: '#1a0f0b',
  accentSoft: '#3a2420',
  ok: '#5fd08f',
  warn: '#f2c45a',
  bad: '#f06a6a',
  scrim: 'rgba(0,0,0,0.6)',
};

export type Colors = typeof dark;

const light: Colors = {
  page: '#0b0c0d',
  bg: '#f6f5f3',
  panel: '#ffffff',
  panelHi: '#efedea',
  hover: '#e4e1dd',
  line: '#e8e5e1',
  lineStrong: '#d3cfca',
  text: '#1a1b1e',
  dim: '#5d5f64',
  faint: '#85878c',
  orb: '#ffffff',
  accent: '#e0775b',
  accentPress: '#c9623f',
  accentInk: '#1a0f0b',
  accentSoft: '#f9e2da',
  ok: '#23a35e',
  warn: '#c08a10',
  bad: '#e04f4f',
  scrim: 'rgba(0,0,0,0.45)',
};

export const fonts = {
  regular: 'Inter_400Regular',
  medium: 'Inter_500Medium',
  semibold: 'Inter_600SemiBold',
  bold: 'Inter_700Bold',
  mono: 'JetBrainsMono_500Medium',
};

// type scale from the design system (letterSpacing in px = em * size)
export const type = {
  title: { fontFamily: fonts.bold, fontSize: 32, lineHeight: 34, letterSpacing: -0.96 },
  greeting: { fontFamily: fonts.semibold, fontSize: 26, lineHeight: 31, letterSpacing: -0.52 },
  h2: { fontFamily: fonts.bold, fontSize: 20, lineHeight: 24, letterSpacing: -0.4 },
  cardTitle: { fontFamily: fonts.semibold, fontSize: 15, lineHeight: 20, letterSpacing: -0.15 },
  body: { fontFamily: fonts.regular, fontSize: 14, lineHeight: 20 },
  label: { fontFamily: fonts.semibold, fontSize: 13, lineHeight: 16 },
  caption: { fontFamily: fonts.medium, fontSize: 11, lineHeight: 14 },
  overline: {
    fontFamily: fonts.semibold,
    fontSize: 10,
    lineHeight: 12,
    letterSpacing: 0.8,
    textTransform: 'uppercase',
  },
  mono: { fontFamily: fonts.mono, fontSize: 11, lineHeight: 14 },
  stat: { fontFamily: fonts.mono, fontSize: 18, lineHeight: 22, letterSpacing: -0.36 },
} satisfies Record<string, TextStyle>;

export const radius = { sm: 8, md: 12, lg: 20, xl: 28, pill: 999 };
export const shadowFloat = '0 12px 32px rgba(0,0,0,0.45)';

export type Mode = 'system' | 'light' | 'dark';

type Theme = { c: Colors; isDark: boolean; mode: Mode; setMode: (m: Mode) => void };

const Ctx = createContext<Theme>({ c: dark, isDark: true, mode: 'dark', setMode: () => {} });

const KEY = 'theme-mode';

export function ThemeProvider({ children }: { children: ReactNode }) {
  const system = useColorScheme();
  const [mode, setModeState] = useState<Mode>('dark');

  useEffect(() => {
    AsyncStorage.getItem(KEY).then((v) => {
      if (v === 'light' || v === 'dark' || v === 'system') setModeState(v);
    });
  }, []);

  const value = useMemo<Theme>(() => {
    const isDark = mode === 'system' ? system !== 'light' : mode === 'dark';
    return {
      c: isDark ? dark : light,
      isDark,
      mode,
      setMode: (m) => {
        setModeState(m);
        AsyncStorage.setItem(KEY, m);
      },
    };
  }, [mode, system]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);

// styles that depend on the palette, rebuilt only when it changes
export function useStyles<T>(make: (c: Colors) => T): T {
  const { c } = useTheme();
  return useMemo(() => make(c), [c, make]);
}

export const font = { mono: fonts.mono };
