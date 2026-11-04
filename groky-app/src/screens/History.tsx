import { useMemo, useState } from 'react';
import { Alert, FlatList, Pressable, StyleSheet, Text, TextInput, useWindowDimensions, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type { Ask } from '../hooks/useHistory';
import { Button, IconButton, IconChip } from '../components/ui';
import { Colors, radius, type, useStyles, useTheme } from '../theme';
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
  const [focused, setFocused] = useState(false);
  const mobile = useWindowDimensions().width < 641;

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
    <View style={[s.page, mobile && { paddingHorizontal: 16 }]}>
      <View style={s.head}>
        <View style={{ flex: 1 }}>
          <Text style={s.title}>History</Text>
          <Text style={s.sub}>Everything you asked. Tap one to send it again.</Text>
        </View>
        {items.length > 0 && <Button label="Clear" variant="ghost" size="sm" onPress={confirmClear} />}
      </View>

      <View style={[s.search, focused && { borderColor: c.accent }]}>
        <Ionicons name="search" size={16} color={c.faint} />
        <TextInput
          value={q}
          onChangeText={setQ}
          placeholder="Search"
          placeholderTextColor={c.faint}
          style={s.searchInput}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
        />
      </View>

      <FlatList
        data={list}
        keyExtractor={(i) => i.id}
        style={{ flexGrow: 0 }}
        contentContainerStyle={list.length ? s.list : undefined}
        ListEmptyComponent={<Text style={s.empty}>{q ? 'No matches.' : 'Nothing yet.'}</Text>}
        renderItem={({ item, index }) => (
          <Pressable
            onPress={() => onRun(item.text)}
            style={({ pressed }) => [
              s.row,
              index === list.length - 1 && { borderBottomWidth: 0 },
              pressed && { opacity: 0.7 },
            ]}
          >
            <IconChip icon={item.via === 'voice' ? 'mic-outline' : 'chatbox-outline'} color={c.dim} />
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={s.text} numberOfLines={2}>
                {item.text}
              </Text>
              <Text style={s.meta}>
                {ago(item.at)}
                {item.count > 1 && `  ·  asked ${item.count}x`}
                {item.via === 'voice' ? '  ·  voice' : '  ·  typed'}
              </Text>
            </View>
            <IconButton
              icon={item.pinned ? 'star' : 'star-outline'}
              plain
              size={32}
              color={item.pinned ? c.accent : c.faint}
              onPress={() => onPin(item.id)}
              label={item.pinned ? 'Unpin' : 'Pin'}
            />
            {!mobile && (
              <IconButton
                icon="trash-outline"
                plain
                size={32}
                color={c.faint}
                onPress={() => onRemove(item.id)}
                label="Delete"
              />
            )}
            <Button label={mobile ? 'Send' : 'Send again'} size="sm" onPress={() => onRun(item.text)} />
          </Pressable>
        )}
      />
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    page: { flex: 1, paddingHorizontal: 24, paddingTop: 8, paddingBottom: 16 },
    head: { flexDirection: 'row', alignItems: 'flex-start' },
    title: { ...type.title, color: c.text },
    sub: { ...type.caption, fontSize: 12, color: c.faint, marginTop: 6 },
    search: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      backgroundColor: c.panel,
      borderRadius: radius.md,
      borderWidth: 1,
      borderColor: c.lineStrong,
      paddingHorizontal: 14,
      height: 44,
      marginTop: 18,
      marginBottom: 18,
    },
    searchInput: { flex: 1, ...type.body, color: c.text, outlineStyle: 'none' } as any,
    empty: { ...type.caption, fontSize: 12, color: c.faint, marginTop: 8 },
    list: { backgroundColor: c.panel, borderRadius: radius.lg, paddingHorizontal: 16 },
    row: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: c.line,
    },
    text: { ...type.label, color: c.text },
    meta: { ...type.caption, color: c.faint, marginTop: 2 },
  });
