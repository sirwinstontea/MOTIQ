import { useState } from 'react';
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { DeviceId, devices } from '../devices';
import { Field, fieldsFor, Skill, skillIcons, Step, validate, WAIT } from '../skills';
import { Colors, fonts, radius, shadowFloat, type, useStyles, useTheme } from '../theme';
import { Button, IconButton, Segmented } from './ui';

type Props = {
  initial: Skill | null; // null = new; id '' = duplicate
  defaultDevice: DeviceId;
  onClose: () => void;
  onSave: (s: Skill) => void;
};

const newId = () => `custom_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;

// new step with enum args pre-filled with their first option
function blankStep(tool: string): Step {
  const args: Record<string, unknown> = {};
  for (const f of fieldsFor(tool)) {
    if (f.options) args[f.key] = f.options[0];
    else if (tool === WAIT) args[f.key] = 1;
  }
  return { tool, args };
}

export default function SkillEditor({ initial, defaultDevice, onClose, onSave }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [device, setDevice] = useState<DeviceId>(initial?.device ?? defaultDevice);
  const [icon, setIcon] = useState(initial?.icon ?? skillIcons[0]);
  const [steps, setSteps] = useState<Step[]>(initial?.steps ?? []);
  const [error, setError] = useState<string | null>(null);

  const tools = [...devices[device].tools.map((t) => `${device}_${t.name}`), WAIT];

  const changeDevice = (d: DeviceId) => {
    setDevice(d);
    setSteps((prev) => prev.filter((st) => st.tool === WAIT || st.tool.startsWith(`${d}_`)));
  };

  const update = (i: number, key: string, value: unknown) =>
    setSteps((prev) => prev.map((st, j) => (j === i ? { ...st, args: { ...st.args, [key]: value } } : st)));

  const move = (i: number, by: number) =>
    setSteps((prev) => {
      const j = i + by;
      if (j < 0 || j >= prev.length) return prev;
      const next = prev.slice();
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });

  const save = () => {
    // numbers come out of text inputs as strings
    const clean = steps.map((st) => ({
      ...st,
      args: Object.fromEntries(
        fieldsFor(st.tool)
          .filter((f) => st.args[f.key] !== undefined && st.args[f.key] !== '')
          .map((f) => [f.key, f.type === 'number' ? Number(st.args[f.key]) : st.args[f.key]]),
      ),
    }));
    const draft = { name: name.trim(), steps };
    const problem = validate(draft);
    if (problem) return setError(problem);
    onSave({
      id: initial?.id || newId(),
      name: draft.name,
      description: description.trim(),
      device,
      icon,
      steps: clean,
    });
  };

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View style={s.backdrop}>
        <SafeAreaView edges={['top', 'bottom']} style={s.wrap}>
          <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={s.card}>
            <View style={s.head}>
              <Text style={s.title}>{initial?.id ? 'Edit skill' : 'New skill'}</Text>
              <IconButton icon="close" size={32} onPress={onClose} label="Close" />
            </View>

            <ScrollView contentContainerStyle={{ paddingBottom: 12 }} keyboardShouldPersistTaps="handled">
              <Text style={s.label}>Name</Text>
              <TextInput
                value={name}
                onChangeText={setName}
                placeholder="e.g. Tidy the table"
                placeholderTextColor={c.faint}
                style={s.input}
              />

              <Text style={s.label}>Description</Text>
              <TextInput
                value={description}
                onChangeText={setDescription}
                placeholder="What it does - Groky reads this to pick the right skill"
                placeholderTextColor={c.faint}
                style={[s.input, { height: 64, textAlignVertical: 'top', paddingTop: 10 }]}
                multiline
              />

              <Text style={s.label}>Robot</Text>
              <Segmented
                options={(Object.keys(devices) as DeviceId[]).map((d) => ({ id: d, label: devices[d].label }))}
                value={device}
                onChange={changeDevice}
              />

              <Text style={s.label}>Icon</Text>
              <View style={s.chips}>
                {skillIcons.map((ic) => (
                  <Pressable key={ic} onPress={() => setIcon(ic)} style={[s.iconOpt, icon === ic && s.iconOptOn]}>
                    <Ionicons name={ic} size={18} color={icon === ic ? c.accent : c.dim} />
                  </Pressable>
                ))}
              </View>

              <Text style={s.label}>Steps</Text>
              {steps.length === 0 && <Text style={s.hint}>Add the actions in the order the robot should do them.</Text>}
              {steps.map((st, i) => (
                <View key={i} style={s.step}>
                  <View style={s.stepHead}>
                    <Text style={s.stepNum}>{i + 1}</Text>
                    <Text style={s.stepTool}>{st.tool === WAIT ? 'wait' : st.tool.slice(device.length + 1)}</Text>
                    <IconButton
                      icon="chevron-up"
                      plain
                      size={28}
                      color={c.dim}
                      disabled={i === 0}
                      onPress={() => move(i, -1)}
                      label="Move up"
                    />
                    <IconButton
                      icon="chevron-down"
                      plain
                      size={28}
                      color={c.dim}
                      disabled={i === steps.length - 1}
                      onPress={() => move(i, 1)}
                      label="Move down"
                    />
                    <IconButton
                      icon="trash-outline"
                      plain
                      size={28}
                      color={c.faint}
                      onPress={() => setSteps((p) => p.filter((_, j) => j !== i))}
                      label="Remove step"
                    />
                  </View>
                  {fieldsFor(st.tool).map((f) => (
                    <ArgField key={f.key} field={f} value={st.args[f.key]} onChange={(v) => update(i, f.key, v)} />
                  ))}
                </View>
              ))}

              <Text style={[s.hint, { marginTop: 10 }]}>Add step</Text>
              <View style={s.chips}>
                {tools.map((t) => (
                  <Pressable key={t} onPress={() => setSteps((p) => [...p, blankStep(t)])} style={s.addChip}>
                    <Ionicons name="add" size={14} color={c.dim} />
                    <Text style={s.addText}>{t === WAIT ? 'wait' : t.slice(device.length + 1).replace(/_/g, ' ')}</Text>
                  </Pressable>
                ))}
              </View>
            </ScrollView>

            {error && <Text style={s.error}>{error}</Text>}
            <View style={s.actions}>
              <Button label="Cancel" variant="ghost" onPress={onClose} />
              <Button label="Save skill" variant="primary" onPress={save} />
            </View>
          </KeyboardAvoidingView>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

function ArgField({ field, value, onChange }: { field: Field; value: unknown; onChange: (v: unknown) => void }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const range = field.min != null && field.max != null ? ` (${field.min} to ${field.max})` : '';

  return (
    <View style={s.arg}>
      <Text style={s.argKey}>
        {field.key}
        {field.required ? '' : ' (optional)'}
      </Text>
      {field.options ? (
        <View style={s.chips}>
          {field.options.map((o) => (
            <Chip key={o} label={o} on={value === o} onPress={() => onChange(o)} small />
          ))}
        </View>
      ) : (
        <TextInput
          value={value === undefined ? '' : String(value)}
          onChangeText={onChange}
          placeholder={(field.hint ?? (field.type === 'number' ? 'number' : 'text')) + range}
          placeholderTextColor={c.faint}
          keyboardType={field.type === 'number' ? 'numbers-and-punctuation' : 'default'}
          style={[s.input, s.argInput]}
        />
      )}
    </View>
  );
}

function Chip({ label, on, onPress, small }: { label: string; on: boolean; onPress: () => void; small?: boolean }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  return (
    <Pressable onPress={onPress} style={[s.chip, small && s.chipSmall, on && { backgroundColor: c.accentSoft }]}>
      <Text style={[s.chipText, on && { color: c.accent }]}>{label}</Text>
    </Pressable>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    backdrop: { flex: 1, backgroundColor: c.scrim },
    wrap: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 16 },
    card: {
      width: '100%',
      maxWidth: 560,
      maxHeight: '100%',
      backgroundColor: c.panel,
      borderRadius: radius.lg,
      padding: 24,
      boxShadow: shadowFloat,
    },
    head: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 },
    title: { ...type.h2, color: c.text },
    label: { ...type.overline, color: c.faint, marginTop: 20, marginBottom: 8 },
    hint: { ...type.caption, fontSize: 12, color: c.faint, marginBottom: 6 },
    input: {
      backgroundColor: c.panel,
      borderRadius: radius.md,
      borderWidth: 1,
      borderColor: c.lineStrong,
      paddingHorizontal: 16,
      height: 44,
      ...type.body,
      color: c.text,
    },
    chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
    chip: {
      paddingHorizontal: 12,
      height: 30,
      borderRadius: radius.pill,
      justifyContent: 'center',
      backgroundColor: c.hover,
    },
    chipSmall: { paddingHorizontal: 10, height: 26 },
    chipText: { ...type.label, fontSize: 12, color: c.dim },
    iconOpt: {
      width: 36,
      height: 36,
      borderRadius: radius.sm,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: c.panelHi,
    },
    iconOptOn: { backgroundColor: c.accentSoft },
    step: { backgroundColor: c.panelHi, borderRadius: radius.md, padding: 12, marginBottom: 8, gap: 8 },
    stepHead: { flexDirection: 'row', alignItems: 'center', gap: 4 },
    stepNum: { fontFamily: fonts.mono, fontSize: 12, color: c.faint, width: 20 },
    stepTool: { flex: 1, fontFamily: fonts.mono, fontSize: 13, color: c.text },
    arg: { gap: 6 },
    argKey: { ...type.caption, color: c.faint },
    argInput: { height: 38, backgroundColor: c.panel, borderColor: c.line, fontSize: 13 },
    addChip: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 4,
      paddingHorizontal: 12,
      height: 30,
      borderRadius: radius.pill,
      borderWidth: 1,
      borderColor: c.lineStrong,
      borderStyle: 'dashed',
    },
    addText: { fontFamily: fonts.mono, fontSize: 12, color: c.dim },
    error: { ...type.caption, fontSize: 12, color: c.bad, marginTop: 8 },
    actions: { flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 16 },
  });
