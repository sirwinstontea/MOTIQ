import { useCallback, useEffect, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';

export type Ask = {
  id: string;
  text: string;
  at: number;
  count: number;
  via: 'voice' | 'typed';
  pinned?: boolean;
};

const KEY = 'history-v1';
const MAX = 300;

const norm = (t: string) =>
  t
    .toLowerCase()
    .replace(/[.,!?]+$/g, '')
    .replace(/\s+/g, ' ')
    .trim();

export function useHistory() {
  const [items, setItems] = useState<Ask[]>([]);
  const loaded = useRef(false);

  useEffect(() => {
    AsyncStorage.getItem(KEY)
      .then((raw) => {
        if (raw) setItems(JSON.parse(raw));
      })
      .catch(() => {})
      .finally(() => (loaded.current = true));
  }, []);

  useEffect(() => {
    if (loaded.current) AsyncStorage.setItem(KEY, JSON.stringify(items)).catch(() => {});
  }, [items]);

  const add = useCallback((text: string, via: Ask['via']) => {
    const t = text.trim();
    if (t.length < 2) return;
    setItems((prev) => {
      const key = norm(t);
      const hit = prev.find((p) => norm(p.text) === key);
      const rest = prev.filter((p) => p !== hit);
      const item: Ask = hit
        ? { ...hit, text: t, at: Date.now(), count: hit.count + 1, via }
        : {
            id: `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
            text: t,
            at: Date.now(),
            count: 1,
            via,
          };
      return [item, ...rest].slice(0, MAX);
    });
  }, []);

  const remove = useCallback((id: string) => setItems((prev) => prev.filter((p) => p.id !== id)), []);
  const togglePin = useCallback(
    (id: string) => setItems((prev) => prev.map((p) => (p.id === id ? { ...p, pinned: !p.pinned } : p))),
    [],
  );
  const clear = useCallback(() => setItems((prev) => prev.filter((p) => p.pinned)), []);

  return { items, add, remove, togglePin, clear };
}
