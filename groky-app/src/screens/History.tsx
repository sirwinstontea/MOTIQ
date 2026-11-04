import { useMemo, useState } from 'react';
import { Alert, FlatList, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type { Ask } from '../hooks/useHistory';
import { Colors, useStyles, useTheme } from '../theme';
import { ago } from '../lib/time';

type Props = {
  items: Ask[];
  onRun: (text: string) => void;
  onPin: (id: string) => void;
  onRemove: (id: string) => void;
  onClear: () => void;
};

export default function History({ items, onRun, onPin, onRemove, onClear }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [q, setQ] = useState('');

  const list = useMemo(() => {
    const f = q.trim().toLowerCase();
    const hits = f ? items.filter((i) => i.text.toLowerCase().includes(f)) : items;
    return [...hits.filter((i) => i.pinned), ...hits.filter((i) => !i.pinned)];
  }, [items, q]);

  const confirmClear = () =>
    Alert.alert('Clear history?', 'Pinned requests are kept.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Clear', style: 'destructive', onPress: onClear },
    ]);

  return (
    <View style={s.page}>
      <View style={s.head}>
        <View style={{ flex: 1 }}>
          <Text style={s.title}>History</Text>
          <Text style={s.sub}>Everything you asked. Tap one to send it again.</Text>
        </View>
        {items.length > 0 && (
          <Pressable onPress={confirmClear} hitSlop={8}>
            <Text style={s.clear}>Clear</Text>
          </Pressable>
        )}
      </View>

      <View style={s.search}>
        <Ionicons name="search" size={16} color={c.faint} />
        <TextInput
          value={q}
          onChangeText={setQ}
          placeholder="Search"
          placeholderTextColor={c.faint}
          style={s.searchInput}
        />
      </View>

      <FlatList
        data={list}
        keyExtractor={(i) => i.id}
        contentContainerStyle={{ gap: 8, paddingBottom: 30 }}
        ListEmptyComponent={
          <Text style={s.empty}>{q ? 'No matches.' : 'Nothing yet. Things you say to Groky show up here.'}</Text>
        }
        renderItem={({ item }) => (
          <Pressable onPress={() => onRun(item.text)} style={({ pressed }) => [s.row, pressed && { opacity: 0.6 }]}>
            <Ionicons
              name={item.via === 'voice' ? 'mic-outline' : 'chatbox-outline'}
              size={18}
              color={c.dim}
              style={{ marginTop: 2 }}
            />
            <View style={{ flex: 1 }}>
              <Text style={s.text}>{item.text}</Text>
              <Text style={s.meta}>
                {ago(item.at)}
                {item.count > 1 && `  ·  asked ${item.count}x`}
              </Text>
            </View>
            <Pressable onPress={() => onPin(item.id)} hitSlop={8} style={s.icon}>
              <Ionicons name={item.pinned ? 'star' : 'star-outline'} size={18} color={item.pinned ? c.warn : c.faint} />
            </Pressable>
            <Pressable onPress={() => onRemove(item.id)} hitSlop={8} style={s.icon}>
              <Ionicons name="trash-outline" size={18} color={c.faint} />
            </Pressable>
            <View style={s.run}>
              <Ionicons name="play" size={14} color={c.bg} />
            </View>
          </Pressable>
        )}
      />
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    page: { flex: 1, padding: 24, paddingBottom: 0 },
    head: { flexDirection: 'row', alignItems: 'flex-start' },
    title: { color: c.text, fontSize: 28, fontWeight: '700' },
    sub: { color: c.dim, fontSize: 14, marginTop: 4 },
    clear: { color: c.bad, fontSize: 14, fontWeight: '600', marginTop: 8 },
    search: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      backgroundColor: c.panel,
      borderRadius: 12,
      paddingHorizontal: 12,
      height: 42,
      marginTop: 18,
      marginBottom: 14,
    },
    searchInput: { flex: 1, color: c.text, fontSize: 15 },
    empty: { color: c.faint, textAlign: 'center', marginTop: 40 },
    row: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      backgroundColor: c.panel,
      borderRadius: 14,
      paddingVertical: 12,
      paddingHorizontal: 14,
      borderWidth: 1,
      borderColor: c.line,
    },
    text: { color: c.text, fontSize: 15, lineHeight: 21 },
    meta: { color: c.faint, fontSize: 12, marginTop: 3 },
    icon: { padding: 4 },
    run: {
      width: 30,
      height: 30,
      borderRadius: 15,
      backgroundColor: c.text,
      alignItems: 'center',
      justifyContent: 'center',
    },
  });
