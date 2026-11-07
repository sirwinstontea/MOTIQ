import { useMemo, useState } from 'react';
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
import { isRequired, limitFor } from '../robot/profiles';
import {
  deviceOf,
  Device,
  EnvObject,
  FAILURE_POLICIES,
  isLookStep,
  normalizeSkill,
  Primitive,
  PrimitiveCall,
  primitivesOf,
  PUSHABLE_OBJECTS,
  pushSimSequence,
  realArmConnected,
  realArmProblems,
  SkillDoc,
  skillIcons,
  SkillStep,
  STEP_ACTIONS,
  validateSkill,
} from '../skills';
import { dryRun } from '../skills/dryrun';
import { checkHardware, HardwareReport } from '../skills/hardware';
import { generateSkill } from '../skills/generate';
import { Colors, fonts, radius, shadowFloat, type, useStyles, useTheme } from '../theme';
import SkillPreview from './SkillPreview';
import { Button, IconButton, Pill, Segmented } from './ui';

type Props = {
  initial: SkillDoc;
  startInJson?: boolean; // import: open on an empty JSON box
  onClose: () => void;
  onSave: (s: SkillDoc) => void;
};

type Field = {
  key: string;
  type: 'number' | 'string' | 'array';
  options?: string[];
  min?: number;
  max?: number;
  required: boolean;
  hint?: string;
};

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

function fieldsFor(p: Primitive, device: Device, objects: string[]): Field[] {
  return Object.entries(p.parameters).map(([key, spec]) => {
    const lim = device === 'arm' ? limitFor(key) : {};
    const min = Math.max(spec.min ?? -Infinity, lim.min ?? -Infinity);
    const max = Math.min(spec.max ?? Infinity, lim.max ?? Infinity);
    return {
      key,
      type: spec.type === 'number' ? 'number' : spec.type === 'array' ? 'array' : 'string',
      options: spec.type === 'enum' ? spec.values : spec.type === 'object_id' ? objects : undefined,
      min: Number.isFinite(min) ? min : undefined,
      max: Number.isFinite(max) ? max : undefined,
      required: isRequired(spec),
      hint: spec.default !== undefined ? `default ${JSON.stringify(spec.default)}` : spec.note,
    };
  });
}

// text inputs hand back strings: turn them into numbers / arrays and drop empties
function cleanCall(c: PrimitiveCall, prims: Primitive[]): PrimitiveCall {
  const p = prims.find((x) => x.id === c.primitive);
  if (!p || !c.parameters) return c;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(c.parameters)) {
    if (v === '' || v == null) continue;
    const spec = p.parameters[k];
    if (spec?.type === 'number' && typeof v === 'string' && Number.isFinite(Number(v))) out[k] = Number(v);
    else if (spec?.type === 'array' && typeof v === 'string')
      out[k] = v
        .split(/[\s,]+/)
        .filter(Boolean)
        .map(Number);
    else out[k] = v;
  }
  return Object.keys(out).length ? { ...c, parameters: out } : { primitive: c.primitive };
}

function clean(doc: SkillDoc): SkillDoc {
  const prims = primitivesOf(doc);
  return {
    ...doc,
    skill: { ...doc.skill, name: doc.skill.name.trim(), description: doc.skill.description.trim() },
    intent: {
      ...doc.intent,
      natural_language_instruction: doc.intent.natural_language_instruction || doc.skill.description.trim(),
      success_conditions: doc.intent.success_conditions.map((x) => x.trim()).filter(Boolean),
    },
    environment: {
      ...doc.environment,
      objects: doc.environment.objects.map((o) => {
        const loc = o.initial_state?.location ?? { x: 0, y: 0, z: 0 };
        return {
          ...o,
          initial_state: {
            ...o.initial_state,
            location: { x: Number(loc.x) || 0, y: Number(loc.y) || 0, z: Number(loc.z) || 0 },
          },
        };
      }),
    },
    steps: doc.steps.map((s, i) => ({
      ...s,
      id: s.id || `step_${String(i + 1).padStart(3, '0')}`,
      primitive_sequence: s.primitive_sequence?.map((c) => cleanCall(c, prims)),
    })),
  };
}

// starting points for new steps, following the template's pick / place pattern
function blankStep(action: string, doc: SkillDoc): SkillStep {
  const objs = doc.environment.objects;
  const thing = objs.find((o) => o.category !== 'container')?.id;
  const box = objs.find((o) => o.category === 'container')?.id;
  const id = `step_${Date.now().toString(36)}`;
  switch (action) {
    case 'OVERVIEW':
      return { id, action, failure_policy: 'STOP_AND_REOBSERVE' };
    case 'PUSH': {
      const t = objs.find((o) => PUSHABLE_OBJECTS.includes(o.id))?.id ?? thing ?? '';
      return { id, action, target: t, failure_policy: 'REPORT_FAILURE', primitive_sequence: pushSimSequence(t) };
    }
    case 'DETECT':
      return { id, action, target: thing, failure_policy: 'STOP_AND_REOBSERVE' };
    case 'VERIFY':
      return {
        id,
        action,
        target: thing,
        parameters: { check_containment: true, check_orientation: true },
        failure_policy: 'REPORT_FAILURE',
      };
    case 'PICK':
      return {
        id,
        action,
        target: thing,
        failure_policy: 'RETRY_WITH_REOBSERVATION',
        primitive_sequence: [
          { primitive: 'OPEN_GRIPPER' },
          { primitive: 'MOVE_ABOVE', parameters: { target: thing, height_m: 0.15 } },
          { primitive: 'MOVE_DOWN', parameters: { distance_m: 0.13, speed_mps: 0.05 } },
          { primitive: 'CLOSE_GRIPPER', parameters: { force_pct: 70 } },
          { primitive: 'MOVE_UP', parameters: { distance_m: 0.13 } },
        ],
      };
    case 'VISIT':
      return {
        id,
        action,
        target: thing,
        parameters: { hold_s: 2, come_back: true },
        failure_policy: 'REPORT_FAILURE',
        // what the simulator runs; on the real arm the bridge's ur_visit is used instead (camera-guided)
        primitive_sequence: [
          { primitive: 'MOVE_ABOVE', parameters: { target: thing, height_m: 0.15 } },
          { primitive: 'WAIT', parameters: { seconds: 2 } },
        ],
      };
    case 'PLACE':
      return {
        id,
        action,
        target: box,
        failure_policy: 'STOP_AND_REPLAN',
        primitive_sequence: [
          { primitive: 'MOVE_ABOVE', parameters: { target: box, height_m: 0.15 } },
          { primitive: 'MOVE_DOWN', parameters: { distance_m: 0.08, speed_mps: 0.05 } },
          { primitive: 'OPEN_GRIPPER' },
          { primitive: 'MOVE_UP', parameters: { distance_m: 0.08 } },
        ],
      };
    default:
      return { id, action: 'SEQUENCE', failure_policy: 'REPORT_FAILURE', primitive_sequence: [] };
  }
}

export default function SkillEditor({ initial, startInJson, onClose, onSave }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [doc, setDoc] = useState<SkillDoc>(() => clone(initial));
  const [mode, setMode] = useState<'form' | 'json'>(startInJson ? 'json' : 'form');
  const [json, setJson] = useState(startInJson ? '' : JSON.stringify(initial, null, 2));
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const [ask, setAsk] = useState('');
  const [asking, setAsking] = useState(false);
  const [askNote, setAskNote] = useState<{ ok: boolean; text: string } | null>(null);

  const device = deviceOf(doc);

  // Grok drafts (or refines) the skill from plain language; defaults come from makeSkill, nothing is saved yet
  const generate = async () => {
    const prompt = ask.trim();
    if (!prompt || asking) return;
    setAsking(true);
    setAskNote(null);
    setError(null);
    try {
      const refine = doc.steps.length > 0;
      const out = await generateSkill(prompt, clean(doc));
      setDoc(out.doc);
      setPreview(true);
      setAsk('');
      const unc = out.doc.interpretation?.uncertainties?.length ?? 0;
      setAskNote(
        out.problems.length
          ? { ok: false, text: `Draft has problems Grok couldn't fix: ${out.problems[0]} Edit it below.` }
          : {
              ok: true,
              text: `${refine ? 'Updated' : 'Drafted'} ${out.doc.steps.length} steps${
                unc ? `, ${unc} thing${unc === 1 ? '' : 's'} to check (see Checks)` : ''
              }. Review, watch the preview, then save.`,
            },
      );
    } catch (e: any) {
      setAskNote({ ok: false, text: String(e?.message ?? e) });
    } finally {
      setAsking(false);
    }
  };
  const prims = primitivesOf(doc).filter((p) => p.id !== 'STOP');
  const objectIds = doc.environment.objects.map((o) => o.id);

  const key = JSON.stringify(doc);
  const check = useMemo(() => {
    const d = clean(JSON.parse(key));
    const v = validateSkill(d);
    if (realArmConnected()) v.warnings.unshift(...realArmProblems(d));
    const run = v.errors.length ? null : dryRun(d);
    return { v, run, d };
  }, [key]);

  const patch = (fn: (d: SkillDoc) => void) =>
    setDoc((prev) => {
      const next = clone(prev);
      fn(next);
      return next;
    });

  const switchMode = (m: 'form' | 'json') => {
    if (m === mode) return;
    if (m === 'json') {
      setJson(JSON.stringify(clean(doc), null, 2));
      setJsonError(null);
      setMode('json');
    } else if (applyJson()) setMode('form');
  };

  const applyJson = () => {
    try {
      const parsed = normalizeSkill(JSON.parse(json));
      if (initial.skill.id && !startInJson) parsed.skill.id = initial.skill.id;
      setDoc(parsed);
      setJsonError(null);
      return true;
    } catch (e: any) {
      setJsonError(String(e?.message ?? e));
      return false;
    }
  };

  const save = () => {
    let d = doc;
    if (mode === 'json') {
      try {
        d = normalizeSkill(JSON.parse(json));
        if (initial.skill.id && !startInJson) d.skill.id = initial.skill.id;
      } catch (e: any) {
        return setJsonError(String(e?.message ?? e));
      }
    }
    const out = clean(d);
    const v = validateSkill(out);
    if (v.errors.length) return setError(v.errors[0]);
    onSave(out);
  };

  const updateStep = (i: number, fn: (st: SkillStep) => void) => patch((x) => fn(x.steps[i]));
  const moveIn = <T,>(arr: T[], i: number, by: number) => {
    const j = i + by;
    if (j < 0 || j >= arr.length) return;
    [arr[i], arr[j]] = [arr[j], arr[i]];
  };

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View style={s.backdrop}>
        <SafeAreaView edges={['top', 'bottom']} style={s.wrap}>
          <KeyboardAvoidingView behavior={Platform.OS === 'web' ? undefined : 'padding'} style={s.card}>
            <View style={s.head}>
              <Text style={s.title}>
                {startInJson ? 'Import skill' : initial.skill.name ? 'Edit skill' : 'New skill'}
              </Text>
              <Segmented
                options={[
                  { id: 'form', label: 'Form' },
                  { id: 'json', label: 'JSON' },
                ]}
                value={mode}
                onChange={switchMode}
              />
              <IconButton icon="close" size={32} onPress={onClose} label="Close" />
            </View>

            {mode === 'json' ? (
              <View style={{ flex: 1, minHeight: 300 }}>
                <Text style={s.hint}>
                  The full skill file (schema {doc.schema_version}). Paste a skill here to import it. Anything the form
                  doesn&apos;t show, like safety, constraints or uncertainties, can be edited here.
                </Text>
                <TextInput
                  value={json}
                  onChangeText={setJson}
                  multiline
                  autoCapitalize="none"
                  autoCorrect={false}
                  spellCheck={false}
                  placeholder='{ "schema_version": "1.1.0", "skill": { ... }, "steps": [ ... ] }'
                  placeholderTextColor={c.faint}
                  style={[s.input, s.json]}
                />
                {jsonError && <Text style={s.error}>{jsonError}</Text>}
              </View>
            ) : (
              <ScrollView contentContainerStyle={{ paddingBottom: 12 }} keyboardShouldPersistTaps="handled">
                <View style={s.grok}>
                  <View style={s.grokHead}>
                    <Ionicons name="sparkles-outline" size={15} color={c.accent} />
                    <Text style={s.grokTitle}>
                      {doc.steps.length ? 'Ask Grok to change it' : 'Describe it to Grok'}
                    </Text>
                  </View>
                  <TextInput
                    value={ask}
                    onChangeText={setAsk}
                    placeholder={
                      doc.steps.length
                        ? 'e.g. shake it twice before placing, and go slower'
                        : 'e.g. pick up the orange screw, put it in the delivery zone and go home'
                    }
                    placeholderTextColor={c.faint}
                    style={[s.input, s.multi]}
                    multiline
                    editable={!asking}
                    onSubmitEditing={generate}
                  />
                  <View style={s.grokFoot}>
                    <Text style={[s.hint, { flex: 1, marginBottom: 0 }]}>
                      Grok fills the steps; safety and limits stay on their defaults.
                    </Text>
                    <Button
                      label={asking ? 'Thinking…' : doc.steps.length ? 'Update' : 'Generate'}
                      icon="sparkles"
                      size="sm"
                      variant="primary"
                      disabled={asking || !ask.trim()}
                      onPress={generate}
                    />
                  </View>
                  {askNote && (
                    <Text style={[s.hint, { marginBottom: 0, color: askNote.ok ? c.dim : c.bad }]}>{askNote.text}</Text>
                  )}
                </View>

                <Text style={s.label}>Name</Text>
                <TextInput
                  value={doc.skill.name}
                  onChangeText={(v) => patch((x) => (x.skill.name = v))}
                  placeholder="e.g. Pick orange screw and place in delivery zone"
                  placeholderTextColor={c.faint}
                  style={s.input}
                />

                <Text style={s.label}>Description</Text>
                <TextInput
                  value={doc.skill.description}
                  onChangeText={(v) => patch((x) => (x.skill.description = v))}
                  placeholder="What it does - Motion reads this to pick the right skill"
                  placeholderTextColor={c.faint}
                  style={[s.input, s.multi]}
                  multiline
                />

                <Text style={s.label}>Goal</Text>
                <TextInput
                  value={doc.intent.goal}
                  onChangeText={(v) => patch((x) => (x.intent.goal = v))}
                  placeholder="e.g. Place the orange screw in the delivery zone."
                  placeholderTextColor={c.faint}
                  style={s.input}
                />

                <Text style={s.label}>Success conditions (one per line)</Text>
                <TextInput
                  value={doc.intent.success_conditions.join('\n')}
                  onChangeText={(v) => patch((x) => (x.intent.success_conditions = v.split('\n')))}
                  placeholder={'The orange screw is in the delivery zone.\nThe screw remains upright.'}
                  placeholderTextColor={c.faint}
                  style={[s.input, s.multi]}
                  multiline
                />

                <Text style={s.label}>Icon</Text>
                <View style={s.chips}>
                  {skillIcons.map((ic) => (
                    <Pressable
                      key={ic}
                      onPress={() => patch((x) => (x.app = { ...x.app, icon: ic }))}
                      style={[s.iconOpt, doc.app?.icon === ic && s.iconOptOn]}
                    >
                      <Ionicons name={ic} size={18} color={doc.app?.icon === ic ? c.accent : c.dim} />
                    </Pressable>
                  ))}
                </View>

                {device === 'arm' && (
                  <>
                    <Text style={s.label}>Objects (metres, base frame)</Text>
                    {doc.environment.objects.map((o, i) => (
                      <ObjectRow
                        key={i}
                        obj={o}
                        onChange={(next) => patch((x) => (x.environment.objects[i] = next))}
                        onRemove={() => patch((x) => x.environment.objects.splice(i, 1))}
                      />
                    ))}
                    <Pressable
                      style={s.addChip}
                      onPress={() =>
                        patch((x) =>
                          x.environment.objects.push({
                            id: `object_${x.environment.objects.length + 1}`,
                            name: '',
                            category: 'object',
                            attributes: {},
                            initial_state: { location: { x: 0.4, y: 0, z: 0.02 }, orientation: { type: 'upright' } },
                            detection: { required: true, reidentify_before_pick: true },
                          }),
                        )
                      }
                    >
                      <Ionicons name="add" size={14} color={c.dim} />
                      <Text style={s.addText}>object</Text>
                    </Pressable>
                  </>
                )}

                <Text style={s.label}>Steps</Text>
                {doc.steps.length === 0 && (
                  <Text style={s.hint}>Add the steps in the order the robot should do them.</Text>
                )}
                {doc.steps.map((st, i) => (
                  <View key={st.id + i} style={s.step}>
                    <View style={s.stepHead}>
                      <Text style={s.stepNum}>{i + 1}</Text>
                      <View style={[s.chips, { flex: 1 }]}>
                        {STEP_ACTIONS.filter((a) => device === 'arm' || a === 'SEQUENCE').map((a) => (
                          <Chip
                            key={a}
                            label={a.toLowerCase()}
                            on={st.action === a}
                            small
                            onPress={() =>
                              updateStep(i, (x) => {
                                x.action = a;
                                if (!isLookStep(x) && !x.primitive_sequence) x.primitive_sequence = [];
                              })
                            }
                          />
                        ))}
                      </View>
                      <IconButton
                        icon="chevron-up"
                        plain
                        size={28}
                        color={c.dim}
                        disabled={i === 0}
                        onPress={() => patch((x) => moveIn(x.steps, i, -1))}
                        label="Move up"
                      />
                      <IconButton
                        icon="chevron-down"
                        plain
                        size={28}
                        color={c.dim}
                        disabled={i === doc.steps.length - 1}
                        onPress={() => patch((x) => moveIn(x.steps, i, 1))}
                        label="Move down"
                      />
                      <IconButton
                        icon="trash-outline"
                        plain
                        size={28}
                        color={c.faint}
                        onPress={() => patch((x) => x.steps.splice(i, 1))}
                        label="Remove step"
                      />
                    </View>

                    {device === 'arm' && objectIds.length > 0 && (
                      <View style={s.arg}>
                        <Text style={s.argKey}>target{['DETECT', 'VISIT', 'TOUCH', 'PUSH'].includes(st.action) ? '' : ' (optional)'}</Text>
                        <View style={s.chips}>
                          {objectIds.map((id) => (
                            <Chip
                              key={id}
                              label={doc.environment.objects.find((o) => o.id === id)?.name || id}
                              on={st.target === id}
                              small
                              onPress={() => updateStep(i, (x) => (x.target = x.target === id ? undefined : id))}
                            />
                          ))}
                        </View>
                      </View>
                    )}

                    <View style={s.arg}>
                      <Text style={s.argKey}>if it fails</Text>
                      <View style={s.chips}>
                        {FAILURE_POLICIES.map((f) => (
                          <Chip
                            key={f}
                            label={f.toLowerCase().replace(/_/g, ' ')}
                            on={(st.failure_policy ?? 'REPORT_FAILURE') === f}
                            small
                            onPress={() => updateStep(i, (x) => (x.failure_policy = f))}
                          />
                        ))}
                      </View>
                    </View>

                    {st.action === 'PUSH' && (
                      <Text style={s.hint}>
                        Arm connected: one checked controller task in the PyBullet sim (closed gripper, down beside the
                        object, push 5 cm, lift) on its taught push path. Pushable: {PUSHABLE_OBJECTS.join(', ')}. The
                        moves below are what the app simulator shows.
                      </Text>
                    )}
                    {st.action === 'TOUCH' && (
                      <Text style={s.hint}>
                        Real arm: the camera guides the arm over the target, it comes down at 10 mm/s until the wrist
                        force sensor feels contact, lifts and goes back. Needs the touch offset taught once. The moves
                        below are what the simulator runs.
                      </Text>
                    )}
                    {st.action === 'VISIT' && (
                      <Text style={s.hint}>
                        Real arm: the arm camera finds the target and guides the arm over it (sideways only, same
                        height, slow), waits, then goes back to where it started. The moves below are what the
                        simulator runs.
                      </Text>
                    )}
                    {isLookStep(st) ? (
                      <Text style={s.hint}>
                        {st.action === 'OVERVIEW'
                          ? 'The arm camera looks at the table from where the arm is now and lists the objects it sees. No motion.'
                          : st.action === 'DETECT'
                          ? 'Looks for the target with the vision server or the camera before moving to it.'
                          : 'Checks the success conditions with the camera (in the simulator: against the predicted scene).'}
                      </Text>
                    ) : (
                      <>
                        {(st.primitive_sequence ?? []).map((cl, j) => {
                          const p = prims.find((x) => x.id === cl.primitive);
                          return (
                            <View key={j} style={s.prim}>
                              <View style={s.stepHead}>
                                <Text style={s.primName}>{cl.primitive.toLowerCase().replace(/_/g, ' ')}</Text>
                                <IconButton
                                  icon="chevron-up"
                                  plain
                                  size={26}
                                  color={c.dim}
                                  disabled={j === 0}
                                  onPress={() => updateStep(i, (x) => moveIn(x.primitive_sequence!, j, -1))}
                                  label="Move up"
                                />
                                <IconButton
                                  icon="chevron-down"
                                  plain
                                  size={26}
                                  color={c.dim}
                                  disabled={j === (st.primitive_sequence?.length ?? 0) - 1}
                                  onPress={() => updateStep(i, (x) => moveIn(x.primitive_sequence!, j, 1))}
                                  label="Move down"
                                />
                                <IconButton
                                  icon="close"
                                  plain
                                  size={26}
                                  color={c.faint}
                                  onPress={() => updateStep(i, (x) => x.primitive_sequence!.splice(j, 1))}
                                  label="Remove primitive"
                                />
                              </View>
                              {p &&
                                fieldsFor(p, device, objectIds).map((f) => (
                                  <ArgField
                                    key={f.key}
                                    field={f}
                                    value={cl.parameters?.[f.key]}
                                    onChange={(v) =>
                                      updateStep(i, (x) => {
                                        const call = x.primitive_sequence![j];
                                        call.parameters = { ...(call.parameters ?? {}), [f.key]: v };
                                      })
                                    }
                                  />
                                ))}
                            </View>
                          );
                        })}
                        <View style={s.chips}>
                          {prims.map((p) => (
                            <Pressable
                              key={p.id}
                              onPress={() =>
                                updateStep(
                                  i,
                                  (x) =>
                                    (x.primitive_sequence = [...(x.primitive_sequence ?? []), { primitive: p.id }]),
                                )
                              }
                              style={s.addChip}
                            >
                              <Ionicons name="add" size={12} color={c.dim} />
                              <Text style={s.addText}>{p.id.toLowerCase().replace(/_/g, ' ')}</Text>
                            </Pressable>
                          ))}
                        </View>
                      </>
                    )}
                  </View>
                ))}

                <Text style={[s.hint, { marginTop: 10 }]}>Add step</Text>
                <View style={s.chips}>
                  {STEP_ACTIONS.filter((a) => device === 'arm' || a === 'SEQUENCE').map((a) => (
                    <Pressable key={a} onPress={() => patch((x) => x.steps.push(blankStep(a, x)))} style={s.addChip}>
                      <Ionicons name="add" size={14} color={c.dim} />
                      <Text style={s.addText}>{a.toLowerCase()}</Text>
                    </Pressable>
                  ))}
                </View>

                <Checks check={check} />
                {device === 'arm' && <HardwareCheck check={check} stamp={key} />}

                {doc.steps.length > 0 && (
                  <>
                    <Pressable onPress={() => setPreview((v) => !v)} style={s.previewHead}>
                      <Text style={[s.label, { marginBottom: 0 }]}>Preview</Text>
                      <Ionicons
                        name={preview ? 'chevron-up' : 'chevron-down'}
                        size={14}
                        color={c.faint}
                        style={{ marginTop: 20 }}
                      />
                    </Pressable>
                    {preview ? (
                      <View style={{ marginTop: 8 }}>
                        <SkillPreview skill={clean(doc)} />
                      </View>
                    ) : (
                      <Text style={s.hint}>Watch the dry run before saving.</Text>
                    )}
                  </>
                )}
              </ScrollView>
            )}

            {error && <Text style={s.error}>{error}</Text>}
            <View style={s.actions}>
              {mode === 'json' && <Button label="Check JSON" variant="ghost" onPress={applyJson} />}
              <View style={{ flex: 1 }} />
              <Button label="Cancel" variant="ghost" onPress={onClose} />
              <Button label="Save skill" variant="primary" onPress={save} />
            </View>
          </KeyboardAvoidingView>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

// validation + dry run summary
function Checks({
  check,
}: {
  check: { v: { errors: string[]; warnings: string[] }; run: ReturnType<typeof dryRun> | null };
}) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const { v, run } = check;
  const rows: { tone: 'bad' | 'warn' | 'ok'; text: string }[] = [
    ...v.errors.map((t) => ({ tone: 'bad' as const, text: t })),
    ...(run?.error ? [{ tone: 'bad' as const, text: `Won't run as written: ${run.error}` }] : []),
    ...(run?.warnings ?? []).map((t) => ({ tone: 'warn' as const, text: t })),
    ...v.warnings.map((t) => ({ tone: 'warn' as const, text: t })),
  ];
  if (!rows.length && run)
    rows.push({ tone: 'ok', text: 'Dry run passes: every move is within the limits and reachable.' });
  if (!rows.length) return null;
  return (
    <View style={{ marginTop: 16, gap: 6 }}>
      <Text style={[s.label, { marginTop: 0 }]}>Checks</Text>
      {rows.map((r, i) => (
        <View key={i} style={s.checkRow}>
          <Ionicons
            name={r.tone === 'bad' ? 'close-circle' : r.tone === 'warn' ? 'alert-circle' : 'checkmark-circle'}
            size={14}
            color={r.tone === 'bad' ? c.bad : r.tone === 'warn' ? c.warn : c.ok}
            style={{ marginTop: 1 }}
          />
          <Text style={s.checkText}>{r.text}</Text>
        </View>
      ))}
    </View>
  );
}

// can the real cell (UR5e + its tool + camera) physically run this skill? See src/skills/hardware.ts
function HardwareCheck({
  check,
  stamp,
}: {
  check: { v: { errors: string[] }; run: ReturnType<typeof dryRun> | null; d: SkillDoc };
  stamp: string;
}) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [res, setRes] = useState<{ stamp: string; report: HardwareReport | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  const stale = !!res && res.stamp !== stamp;
  const r = res?.report;

  const runCheck = () => {
    setBusy(true);
    // let the spinner paint before the IK scan blocks the JS thread
    setTimeout(() => {
      try {
        setRes({ stamp, report: checkHardware(check.d, check.run) });
      } finally {
        setBusy(false);
      }
    }, 30);
  };

  const row = (tone: 'bad' | 'warn' | 'ok' | 'dim', text: string, k: string | number) => (
    <View key={k} style={s.checkRow}>
      <Ionicons
        name={
          tone === 'bad'
            ? 'close-circle'
            : tone === 'warn'
              ? 'alert-circle'
              : tone === 'ok'
                ? 'checkmark-circle'
                : 'ellipse-outline'
        }
        size={14}
        color={tone === 'bad' ? c.bad : tone === 'warn' ? c.warn : tone === 'ok' ? c.ok : c.dim}
        style={{ marginTop: 1 }}
      />
      <Text style={s.checkText}>{text}</Text>
    </View>
  );
  const where = (step?: number) => (step === undefined ? '' : `Step ${step + 1}: `);

  return (
    <View style={{ marginTop: 16, gap: 6 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Text style={[s.label, { marginTop: 0, flex: 1 }]}>Hardware check</Text>
        {r && !stale && (
          <Pill label={r.verdict === 'yes' ? 'YES' : 'NO'} tone={r.verdict === 'yes' ? 'ok' : 'bad'} dot />
        )}
        {stale && <Pill label="Outdated" tone="warn" />}
        <Button
          label={res ? 'Re-check' : 'Check hardware'}
          size="sm"
          icon="hardware-chip-outline"
          loading={busy}
          disabled={check.v.errors.length > 0}
          onPress={runCheck}
        />
      </View>
      {!res && (
        <Text style={s.hint}>
          Can the real cell (UR5e, its tool and the vision camera) physically run this skill? Checks reach, joint
          limits, tool, payload, camera and the robot claims in the skill.
        </Text>
      )}
      {r && (
        <View style={{ gap: 6, opacity: stale ? 0.5 : 1 }}>
          <Text style={s.checkText}>{r.summary}</Text>
          {r.blockers.map((b, i) => row('bad', `${where(b.step)}${b.message}`, `b${i}`))}
          {r.needs.length > 0 && <Text style={[s.label, { marginTop: 4 }]}>Information needed</Text>}
          {r.needs.map((n, i) => row('warn', n, `n${i}`))}
          <Pressable onPress={() => setMore((m) => !m)} hitSlop={6}>
            <Text style={[s.hint, { color: c.accent }]}>
              {more
                ? 'Hide details'
                : `Show conditions, numbers and per-step results (${r.conditions.length} conditions)`}
            </Text>
          </Pressable>
          {more && (
            <>
              <Text style={[s.label, { marginTop: 4 }]}>Holds only if</Text>
              {r.conditions.map((t, i) => row('dim', t, `c${i}`))}
              <Text style={[s.label, { marginTop: 4 }]}>Steps</Text>
              {r.steps.map((st) =>
                st.findings
                  .filter((f) => f.status !== 'fail')
                  .map((f, i) =>
                    row(
                      f.status === 'pass' ? 'ok' : f.status === 'info' ? 'dim' : 'warn',
                      `Step ${st.index + 1} · ${f.check.replace(/_/g, ' ')}: ${f.message}`,
                      `s${st.index}-${i}`,
                    ),
                  ),
              )}
              {r.global
                .filter((f) => f.status !== 'fail')
                .map((f, i) =>
                  row(
                    f.status === 'pass' ? 'ok' : f.status === 'info' ? 'dim' : 'warn',
                    `${f.check.replace(/_/g, ' ')}: ${f.message}`,
                    `g${i}`,
                  ),
                )}
              <Text style={[s.label, { marginTop: 4 }]}>Key numbers</Text>
              {Object.entries(r.metrics).map(([k, v]) => row('dim', `${k}: ${v}`, `m${k}`))}
              <Text style={[s.label, { marginTop: 4 }]}>Assumed</Text>
              {r.assumptions.map((t, i) => row('dim', t, `a${i}`))}
              <Text style={s.hint}>
                {r.robot} · {r.tool} · {r.camera}. Hardware capability only, not a safety approval.
              </Text>
            </>
          )}
        </View>
      )}
    </View>
  );
}

function ObjectRow({
  obj,
  onChange,
  onRemove,
}: {
  obj: EnvObject;
  onChange: (o: EnvObject) => void;
  onRemove: () => void;
}) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const loc = obj.initial_state?.location ?? { x: 0, y: 0, z: 0 };
  const setLoc = (k: 'x' | 'y' | 'z', v: string) =>
    onChange({ ...obj, initial_state: { ...obj.initial_state, location: { ...loc, [k]: v as unknown as number } } });
  const container = obj.category === 'container';
  return (
    <View style={s.step}>
      <View style={s.stepHead}>
        <TextInput
          value={obj.name}
          onChangeText={(name) => onChange({ ...obj, name })}
          placeholder="name, e.g. orange screw"
          placeholderTextColor={c.faint}
          style={[s.input, s.argInput, { flex: 1 }]}
        />
        <IconButton icon="trash-outline" plain size={28} color={c.faint} onPress={onRemove} label="Remove object" />
      </View>
      <View style={s.chips}>
        <Chip
          label="object"
          on={!container}
          small
          onPress={() =>
            onChange({ ...obj, category: 'object', detection: { required: true, reidentify_before_pick: true } })
          }
        />
        <Chip
          label="container"
          on={container}
          small
          onPress={() =>
            onChange({ ...obj, category: 'container', detection: { required: true, reidentify_before_place: true } })
          }
        />
        <TextInput
          value={String(obj.attributes?.color ?? '')}
          onChangeText={(color) => onChange({ ...obj, attributes: { ...obj.attributes, color } })}
          placeholder="colour"
          placeholderTextColor={c.faint}
          style={[s.input, s.argInput, { width: 90 }]}
        />
      </View>
      <View style={s.xyz}>
        {(['x', 'y', 'z'] as const).map((k) => (
          <View key={k} style={{ flex: 1 }}>
            <Text style={s.argKey}>{k}</Text>
            <TextInput
              value={String(loc[k] ?? '')}
              onChangeText={(v) => setLoc(k, v)}
              keyboardType="numbers-and-punctuation"
              style={[s.input, s.argInput]}
            />
          </View>
        ))}
      </View>
      <Text style={s.hint}>id: {obj.id}</Text>
    </View>
  );
}

function ArgField({ field, value, onChange }: { field: Field; value: unknown; onChange: (v: unknown) => void }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const range = field.min != null && field.max != null ? ` (${field.min} to ${field.max})` : '';
  const shown = Array.isArray(value) ? value.join(', ') : value === undefined ? '' : String(value);

  return (
    <View style={s.arg}>
      <Text style={s.argKey}>
        {field.key}
        {field.required ? '' : ' (optional)'}
      </Text>
      {field.options ? (
        <View style={s.chips}>
          {field.options.map((o) => (
            <Chip key={o} label={o.replace(/^object_/, '')} on={value === o} onPress={() => onChange(o)} small />
          ))}
        </View>
      ) : (
        <TextInput
          value={shown}
          onChangeText={onChange}
          placeholder={(field.hint ?? (field.type === 'array' ? '6 numbers, comma separated' : field.type)) + range}
          placeholderTextColor={c.faint}
          keyboardType={field.type === 'string' ? 'default' : 'numbers-and-punctuation'}
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
      maxWidth: 620,
      maxHeight: '100%',
      flexShrink: 1,
      backgroundColor: c.panel,
      borderRadius: radius.lg,
      padding: 20,
      boxShadow: shadowFloat,
    },
    head: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 4 },
    grok: { backgroundColor: c.panelHi, borderRadius: radius.md, padding: 12, gap: 8, marginTop: 12 },
    grokHead: { flexDirection: 'row', alignItems: 'center', gap: 6 },
    grokTitle: { ...type.label, color: c.text },
    grokFoot: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    title: { ...type.h2, color: c.text, flex: 1 },
    label: { ...type.overline, color: c.faint, marginTop: 20, marginBottom: 8 },
    hint: { ...type.caption, fontSize: 12, color: c.faint, marginBottom: 6 },
    input: {
      backgroundColor: c.panel,
      borderRadius: radius.md,
      borderWidth: 1,
      borderColor: c.lineStrong,
      paddingHorizontal: 14,
      height: 44,
      ...type.body,
      color: c.text,
    },
    multi: { height: 72, textAlignVertical: 'top', paddingTop: 10 },
    json: {
      flex: 1,
      minHeight: 280,
      height: undefined,
      textAlignVertical: 'top',
      paddingTop: 10,
      fontFamily: fonts.mono,
      fontSize: 11,
    },
    chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, alignItems: 'center' },
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
    prim: { backgroundColor: c.panel, borderRadius: radius.sm, padding: 10, gap: 6 },
    primName: { flex: 1, fontFamily: fonts.mono, fontSize: 12, color: c.text },
    arg: { gap: 6 },
    argKey: { ...type.caption, color: c.faint },
    argInput: { height: 36, backgroundColor: c.panel, borderColor: c.line, fontSize: 13 },
    xyz: { flexDirection: 'row', gap: 8 },
    addChip: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 4,
      paddingHorizontal: 10,
      height: 28,
      borderRadius: radius.pill,
      borderWidth: 1,
      borderColor: c.lineStrong,
      borderStyle: 'dashed',
      alignSelf: 'flex-start',
    },
    addText: { fontFamily: fonts.mono, fontSize: 11, color: c.dim },
    checkRow: { flexDirection: 'row', gap: 8, alignItems: 'flex-start' },
    checkText: { ...type.caption, fontSize: 12, color: c.dim, flex: 1 },
    previewHead: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 8 },
    error: { ...type.caption, fontSize: 12, color: c.bad, marginTop: 8 },
    actions: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 14 },
  });
