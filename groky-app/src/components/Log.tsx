import { useRef, useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type { Line } from '../voice/types';
import { Colors, radius, type, useStyles, useTheme } from '../theme';

type Props = { lines: Line[]; canSend: boolean; onSend: (t: string) => void };

export default function Log({ lines, canSend, onSend }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [draft, setDraft] = useState('');
  const list = useRef<FlatList<Line>>(null);

  const tools = lines.filter((l): l is Extract<Line, { kind: 'tool' }> => l.kind === 'tool' && l.ok !== undefined);
  const okCount = tools.filter((t) => t.ok).length;
  const avg = tools.length ? Math.round(tools.reduce((n, t) => n + (t.ms ?? 0), 0) / tools.length) : 0;

  const send = () => {
    const t = draft.trim();
    if (!t || !canSend) return;
    onSend(t);
    setDraft('');
  };

  return (
    <View style={s.wrap}>
      <View style={s.stats}>
        <Stat label="actions" value={`${okCount}/${tools.length}`} />
        <Stat label="avg ms" value={tools.length ? String(avg) : '-'} />
        <Stat label="turns" value={String(lines.filter((l) => l.kind === 'user').length)} />
      </View>

      <FlatList
        ref={list}
        data={lines}
        keyExtractor={(l) => l.id}
        renderItem={({ item }) => <Row line={item} />}
        onContentSizeChange={() => list.current?.scrollToEnd({ animated: true })}
        contentContainerStyle={{ paddingVertical: 8, gap: 8 }}
        ListEmptyComponent={<Text style={s.empty}>Nothing yet. Say hi.</Text>}
      />

      <View style={s.input}>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          onSubmitEditing={send}
          placeholder={canSend ? 'Type instead of talking' : 'Start a session first'}
          placeholderTextColor={c.faint}
          editable={canSend}
          style={s.field}
          returnKeyType="send"
        />
        <Pressable onPress={send} hitSlop={8}>
          <View style={[s.send, { backgroundColor: draft.trim() && canSend ? c.accent : c.panelHi }]}>
            <Ionicons name="arrow-up" size={16} color={draft.trim() && canSend ? c.accentInk : c.faint} />
          </View>
        </Pressable>
      </View>
    </View>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  const s = useStyles(makeStyles);
  return (
    <View style={s.stat}>
      <Text style={s.statValue}>{value}</Text>
      <Text style={s.statLabel}>{label}</Text>
    </View>
  );
}

function Row({ line }: { line: Line }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  if (line.kind === 'tool') {
    const color = line.ok === undefined ? c.accent : line.ok ? c.ok : c.bad;
    return (
      <View style={s.tool}>
        <View style={[s.toolDot, { backgroundColor: color }]} />
        <Text style={s.toolText} numberOfLines={1}>
          {line.name}
          <Text style={{ color: c.faint }}> {line.args !== '{}' ? line.args : ''}</Text>
        </Text>
        <Text style={s.toolMs}>{line.ms != null ? `${line.ms}ms` : '...'}</Text>
      </View>
    );
  }
  if (line.kind === 'error') return <Text style={s.err}>{line.text}</Text>;

  const me = line.kind === 'user';
  return (
    <View style={[s.bubble, me ? s.me : s.them]}>
      <Text style={s.bubbleText}>{line.text}</Text>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    wrap: { flex: 1 },
    stats: { flexDirection: 'row', gap: 8, marginBottom: 6 },
    stat: { flex: 1, backgroundColor: c.panelHi, borderRadius: radius.md, padding: 10, alignItems: 'center' },
    statValue: { ...type.stat, color: c.text },
    statLabel: { fontFamily: type.caption.fontFamily, fontSize: 10, lineHeight: 13, color: c.faint, marginTop: 2 },
    empty: { ...type.caption, fontSize: 12, color: c.faint, textAlign: 'center', marginTop: 40 },
    bubble: { maxWidth: '85%', paddingHorizontal: 14, paddingVertical: 9, borderRadius: 18 },
    me: { alignSelf: 'flex-end', backgroundColor: c.accentSoft, borderBottomRightRadius: 6 },
    them: { alignSelf: 'flex-start', backgroundColor: c.panel, borderBottomLeftRadius: 6 },
    bubbleText: { ...type.body, color: c.text },
    tool: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 6 },
    toolDot: { width: 6, height: 6, borderRadius: 3 },
    toolText: { flex: 1, ...type.mono, color: c.dim },
    toolMs: { ...type.mono, fontSize: 10, color: c.faint },
    err: { ...type.caption, fontSize: 12, color: c.bad, paddingHorizontal: 6 },
    input: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      backgroundColor: c.panel,
      borderRadius: radius.md,
      borderWidth: 1,
      borderColor: c.lineStrong,
      paddingLeft: 14,
      paddingRight: 6,
      height: 44,
      marginVertical: 10,
    },
    field: { flex: 1, ...type.body, color: c.text, outlineStyle: 'none' } as any,
    send: { width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  });
