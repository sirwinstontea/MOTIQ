import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Animated, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Colors, radius, shadowFloat, type, useStyles, useTheme } from '../theme';

export type NoteKind = 'info' | 'ok' | 'warn' | 'bad';

export type Note = {
  id: string;
  at: number;
  kind: NoteKind;
  title: string;
  body?: string;
  read?: boolean;
};

type Ctx = {
  items: Note[];
  unread: number;
  notify: (n: { kind: NoteKind; title: string; body?: string; silent?: boolean }) => void;
  markAllRead: () => void;
  remove: (id: string) => void;
  clear: () => void;
};

const NotifyCtx = createContext<Ctx>({
  items: [],
  unread: 0,
  notify: () => {},
  markAllRead: () => {},
  remove: () => {},
  clear: () => {},
});

const KEY = 'notes-v1';
const MAX = 100;
const TOAST_MS = 3500;

export const noteIcon: Record<NoteKind, keyof typeof Ionicons.glyphMap> = {
  info: 'information-circle',
  ok: 'checkmark-circle',
  warn: 'warning',
  bad: 'alert-circle',
};

export const noteColor = (c: Colors, k: NoteKind) => ({ info: c.accent, ok: c.ok, warn: c.warn, bad: c.bad })[k];

export function NotifyProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Note[]>([]);
  const [toast, setToast] = useState<Note | null>(null);
  const loaded = useRef(false);

  useEffect(() => {
    AsyncStorage.getItem(KEY)
      .then((raw) => {
        if (raw) setItems((prev) => [...prev, ...JSON.parse(raw)].slice(0, MAX));
      })
      .catch(() => {})
      .finally(() => (loaded.current = true));
  }, []);

  useEffect(() => {
    if (loaded.current) AsyncStorage.setItem(KEY, JSON.stringify(items)).catch(() => {});
  }, [items]);

  const notify = useCallback<Ctx['notify']>(({ silent, ...n }) => {
    const note: Note = {
      ...n,
      id: `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
      at: Date.now(),
    };
    setItems((prev) => [note, ...prev].slice(0, MAX));
    if (silent) return;
    setToast(note);
    if (n.kind === 'bad') Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    else if (n.kind === 'warn') Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
    else if (n.kind === 'ok') Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
  }, []);

  const markAllRead = useCallback(() => setItems((prev) => prev.map((n) => (n.read ? n : { ...n, read: true }))), []);
  const remove = useCallback((id: string) => setItems((prev) => prev.filter((n) => n.id !== id)), []);
  const clear = useCallback(() => setItems([]), []);

  const value = useMemo(
    () => ({ items, unread: items.filter((n) => !n.read).length, notify, markAllRead, remove, clear }),
    [items, notify, markAllRead, remove, clear],
  );

  return (
    <NotifyCtx.Provider value={value}>
      {children}
      {toast && <Toast key={toast.id} note={toast} onDone={() => setToast(null)} />}
    </NotifyCtx.Provider>
  );
}

export const useNotify = () => useContext(NotifyCtx);

function Toast({ note, onDone }: { note: Note; onDone: () => void }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const insets = useSafeAreaInsets();
  const [anim] = useState(() => new Animated.Value(0));

  const hide = useCallback(() => {
    Animated.timing(anim, { toValue: 0, duration: 180, useNativeDriver: true }).start(() => onDone());
  }, [anim, onDone]);

  useEffect(() => {
    Animated.spring(anim, { toValue: 1, useNativeDriver: true, friction: 8 }).start();
    const t = setTimeout(hide, TOAST_MS);
    return () => clearTimeout(t);
  }, [anim, hide]);

  const color = noteColor(c, note.kind);
  return (
    <Animated.View
      pointerEvents="box-none"
      style={[
        s.wrap,
        { top: insets.top + 8, opacity: anim },
        { transform: [{ translateY: anim.interpolate({ inputRange: [0, 1], outputRange: [-20, 0] }) }] },
      ]}
    >
      <Pressable onPress={hide} style={s.toast}>
        <Ionicons name={noteIcon[note.kind]} size={20} color={color} />
        <View style={{ flex: 1 }}>
          <Text style={s.title} numberOfLines={1}>
            {note.title}
          </Text>
          {!!note.body && (
            <Text style={s.body} numberOfLines={2}>
              {note.body}
            </Text>
          )}
        </View>
      </Pressable>
    </Animated.View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    wrap: { position: 'absolute', left: 16, right: 16, alignItems: 'center' },
    toast: {
      width: '100%',
      maxWidth: 420,
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      backgroundColor: c.panelHi,
      borderRadius: radius.lg,
      paddingVertical: 12,
      paddingHorizontal: 16,
      boxShadow: shadowFloat,
    },
    title: { ...type.label, color: c.text },
    body: { ...type.caption, fontSize: 12, color: c.dim, marginTop: 2 },
  });
