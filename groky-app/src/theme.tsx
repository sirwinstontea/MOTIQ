import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from 'react';
import { useColorScheme } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

const dark = {
  bg: '#0a0a0b',
  panel: '#161618',
  panelHi: '#232326',
  line: 'rgba(255,255,255,0.08)',
  text: '#f4f4f5',
  dim: '#9a9aa2',
  faint: '#55555c',
  orb: '#ffffff',
  accent: '#ff5a1f',
  ok: '#34c46e',
  warn: '#f0a93a',
  bad: '#ff4d5e',
};

export type Colors = typeof dark;

const light: Colors = {
  bg: '#ffffff',
  panel: '#f3f3f5',
  panelHi: '#e6e6ea',
  line: 'rgba(0,0,0,0.07)',
  text: '#111113',
  dim: '#6b6b73',
  faint: '#b4b4bb',
  orb: '#111113',
  accent: '#f04e12',
  ok: '#1fa458',
  warn: '#d68910',
  bad: '#e5394a',
};

export type Mode = 'system' | 'light' | 'dark';

type Theme = { c: Colors; isDark: boolean; mode: Mode; setMode: (m: Mode) => void };

const Ctx = createContext<Theme>({ c: dark, isDark: true, mode: 'system', setMode: () => {} });

const KEY = 'theme-mode';

export function ThemeProvider({ children }: { children: ReactNode }) {
  const system = useColorScheme();
  const [mode, setModeState] = useState<Mode>('system');

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

export const font = { mono: 'monospace' };
