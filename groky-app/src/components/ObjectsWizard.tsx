import { useEffect, useState } from 'react';
import { StyleSheet, Text, TextInput, View } from 'react-native';
import { robotCatalog, robotCatalogReady, type UserObject } from '../lib/robotCatalog';
import { Colors, fonts, radius, type, useStyles, useTheme } from '../theme';
import { Button } from './ui';

const emptyRow = (): UserObject => ({ id: '', name: '', aliases: [], role: 'pickable' });

/** Register custom object names for YOLO-World + scene class_labels (no full model training). */
export default function ObjectsWizard() {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [rows, setRows] = useState<UserObject[]>([emptyRow()]);
  const [msg, setMsg] = useState<{ text: string; bad?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!robotCatalogReady()) return;
    robotCatalog.listObjects().then((r) => {
      if (r.ok && r.objects?.length) setRows(r.objects);
    });
  }, []);

  if (!robotCatalogReady()) {
    return (
      <View style={s.box}>
        <Text style={s.title}>Your objects</Text>
        <Text style={s.hint}>Connect the arm bridge to register custom object names for vision and skills.</Text>
      </View>
    );
  }

  const setRow = (i: number, patch: Partial<UserObject>) =>
    setRows((all) => all.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const save = async () => {
    const cleaned = rows
      .map((r) => ({
        ...r,
        name: r.name.trim(),
        id: (r.id || r.name).trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, ''),
        aliases: (r.aliases || [])
          .flatMap((a) => String(a).split(','))
          .map((a) => a.trim())
          .filter(Boolean),
        role: r.role || 'pickable',
      }))
      .filter((r) => r.name && r.id);
    if (!cleaned.length) {
      setMsg({ text: 'Add at least one named object.', bad: true });
      return;
    }
    setBusy(true);
    const r = await robotCatalog.registerObjects(cleaned);
    setBusy(false);
    if (r.ok) {
      setRows(r.objects.length ? r.objects : cleaned);
      setMsg({
        text: `Saved ${r.objects.length} object(s). YOLO-World vocab updated${
          r.scenes_updated?.length ? `; scene labels merged (${r.scenes_updated.length} file(s))` : ''
        }. Restart vision to load open-vocab classes.`,
      });
    } else setMsg({ text: r.message, bad: true });
  };

  return (
    <View style={s.box}>
      <Text style={s.title}>Your objects</Text>
      <Text style={s.hint}>
        Fast path: name what Motiq should recognise. Uses YOLO-World (no training). Optional fine-tuned ONNX stays in
        Colab — Settings → Improve detection.
      </Text>
      {rows.map((r, i) => (
        <View key={i} style={s.row}>
          <TextInput
            style={s.input}
            placeholder="name (e.g. red mug)"
            placeholderTextColor={c.faint}
            value={r.name}
            onChangeText={(v) => setRow(i, { name: v })}
            autoCapitalize="none"
          />
          <TextInput
            style={s.input}
            placeholder="aliases, comma-separated"
            placeholderTextColor={c.faint}
            value={(r.aliases || []).join(', ')}
            onChangeText={(v) => setRow(i, { aliases: v.split(',').map((x) => x.trim()) })}
            autoCapitalize="none"
          />
        </View>
      ))}
      <View style={s.actions}>
        <Button label="Add object" icon="add" size="sm" variant="secondary" onPress={() => setRows((a) => [...a, emptyRow()])} />
        <Button label="Save" icon="checkmark" size="sm" variant="primary" loading={busy} onPress={save} />
      </View>
      {msg && <Text style={[s.hint, { color: msg.bad ? c.bad : c.ok }]}>{msg.text}</Text>}
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    box: { gap: 10, marginTop: 8 },
    title: { ...type.title, color: c.text, fontFamily: fonts.semibold },
    hint: { ...type.caption, color: c.dim, lineHeight: 18 },
    row: { gap: 6 },
    input: {
      borderWidth: 1,
      borderColor: c.line,
      borderRadius: radius.md,
      paddingHorizontal: 12,
      paddingVertical: 10,
      color: c.text,
      fontFamily: fonts.regular,
    },
    actions: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },
  });
