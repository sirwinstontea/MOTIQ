import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';
import env from '../lib/config';
import {
  fetchChooserState,
  getChooserUrl,
  patchChooserHardware,
  selectChooserRobot,
  setChooserUrl,
  type ChooserRobot,
  type ChooserState,
} from '../lib/chooser';
import { setFullAccess, useFullAccess } from '../lib/fullAccess';
import { devices } from '../devices';
import { Colors, Mode, radius, shadowFloat, type, useStyles, useTheme } from '../theme';
import { Button, IconButton, Segmented } from './ui';

const modes: { id: Mode; label: string }[] = [
  { id: 'system', label: 'System' },
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
];

const backends: { id: string; label: string }[] = [{ id: 'pybullet', label: 'Run sim' }];

type Props = { open: boolean; onClose: () => void };

// modal on a 60% scrim (desktop), full-height sheet (mobile)
export default function Settings({ open, onClose }: Props) {
  const { c, mode, setMode } = useTheme();
  const s = useStyles(makeStyles);
  const mobile = useWindowDimensions().width < 641;
  const auth = env.tokenUrl ? 'token server' : env.xaiKey ? 'api key (dev)' : 'not set';
  const full = useFullAccess();

  const [chooserUrl, setChooserUrlState] = useState('');
  const [state, setState] = useState<ChooserState | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const url = await getChooserUrl();
      setChooserUrlState(url);
      if (!url) {
        setState(null);
        return;
      }
      const st = await fetchChooserState();
      setState(st);
    } catch (e: any) {
      setState(null);
      setErr(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const saveUrl = async () => {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      await setChooserUrl(chooserUrl);
      setMsg(chooserUrl.trim() ? 'Chooser URL saved on this device.' : 'Chooser URL cleared.');
      await load();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const pickRobot = async (id: string) => {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const st = await selectChooserRobot(id);
      setState(st);
      const label = st.report?.label || st.report?.status || 'updated';
      setMsg(`Active arm: ${id} · ${label}. Restart the controller + bridge on the PC to apply.`);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const setBackend = async (backend: string) => {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const st = await patchChooserHardware({ backend });
      setState(st);
      setMsg(`Backend: ${backend}. Restart the controller + bridge on the PC to apply.`);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const robots: ChooserRobot[] = state?.catalog ?? [];
  const active = state?.hardware?.active_robot;
  const backend = state?.hardware?.backend || 'mock';
  const reportLabel = state?.report?.label || state?.report?.status;
  const blockers = state?.report?.blockers ?? [];

  return (
    <Modal visible={open} transparent animationType={mobile ? 'slide' : 'fade'} onRequestClose={onClose}>
      <Pressable style={[s.backdrop, mobile && s.backdropMobile]} onPress={onClose}>
        <Pressable style={[s.card, mobile && s.cardMobile]} onPress={() => {}}>
          <View style={s.head}>
            <Text style={s.title}>Settings</Text>
            <IconButton icon="close" size={32} onPress={onClose} label="Close" />
          </View>

          <ScrollView style={{ flexGrow: 0 }} contentContainerStyle={{ paddingBottom: 8 }} keyboardShouldPersistTaps="handled">
            <Text style={[s.overline, { marginTop: 0 }]}>Appearance</Text>
            <Segmented options={modes} value={mode} onChange={setMode} stretch />

            <Text style={s.overline}>Connections</Text>
            <Row label="Grok voice" value={`${env.voiceModel} · ${env.voice}`} />
            <Row label="Auth" value={auth} color={auth === 'not set' ? c.warn : undefined} />
            {Object.values(devices).map((d, i, all) => (
              <Row key={d.label} label={d.label} value={d.url || 'simulated'} last={i === all.length - 1} />
            ))}
            <Text style={s.hint}>Edit .env and restart Expo to change bridge / vision URLs.</Text>

            <Text style={s.overline}>Hardware</Text>
            <Text style={s.sectionHint}>
              Pick the arm and sim backend. This calls robot-chooser on the robot PC and rewrites its controller config.
            </Text>
            <Text style={s.fieldLabel}>Chooser URL</Text>
            <TextInput
              value={chooserUrl}
              onChangeText={setChooserUrlState}
              placeholder="http://192.168.1.50:8770"
              placeholderTextColor={c.faint}
              autoCapitalize="none"
              autoCorrect={false}
              style={s.input}
            />
            <View style={s.rowActions}>
              <Button label="Save URL" size="sm" onPress={saveUrl} disabled={busy} />
              <Button label="Refresh" size="sm" onPress={load} disabled={busy || loading} />
            </View>

            {loading && (
              <View style={s.centerRow}>
                <ActivityIndicator color={c.accent} />
              </View>
            )}

            {!loading && state && (
              <>
                <Text style={[s.fieldLabel, { marginTop: 14 }]}>Arm model</Text>
                <View style={s.robotList}>
                  {robots.map((r) => {
                    const on = r.id === active || r.active;
                    return (
                      <Pressable
                        key={r.id}
                        onPress={() => pickRobot(r.id)}
                        disabled={busy}
                        style={[s.robotRow, on && { borderColor: c.accent, backgroundColor: c.panelHi }]}
                      >
                        <View style={{ flex: 1, minWidth: 0 }}>
                          <Text style={s.robotTitle}>
                            {r.brand} {r.model}
                          </Text>
                          <Text style={s.robotMeta}>
                            {r.id} · {r.dof} DOF · {r.status}
                            {r.nominal_reach_m != null ? ` · ${r.nominal_reach_m} m` : ''}
                          </Text>
                        </View>
                        {on && <Text style={[s.robotMeta, { color: c.accent }]}>active</Text>}
                      </Pressable>
                    );
                  })}
                </View>

                <Text style={[s.fieldLabel, { marginTop: 14 }]}>Simulation backend</Text>
                <Segmented
                  options={backends}
                  value="pybullet"
                  onChange={setBackend}
                  stretch
                />

                <Text style={[s.hint, { marginTop: 12 }]}>
                  Status: {reportLabel || '—'}
                  {blockers.length ? ` · ${blockers.length} blocker(s)` : ''}
                </Text>
                {blockers.slice(0, 3).map((b, i) => (
                  <Text key={i} style={[s.hint, { color: c.warn, marginTop: 4 }]}>
                    {b.message || b.code}
                  </Text>
                ))}
              </>
            )}

            {!loading && !state && !err && (
              <Text style={s.hint}>
                Start robot-chooser on the PC:{'\n'}
                python -m robot_chooser serve --host 0.0.0.0 --port 8770
              </Text>
            )}

            {msg && <Text style={[s.hint, { color: c.ok, marginTop: 10 }]}>{msg}</Text>}
            {err && <Text style={[s.hint, { color: c.bad, marginTop: 10 }]}>{err}</Text>}

            <Text style={s.overline}>Real arm</Text>
            <View style={[s.row, { borderBottomWidth: 0 }]}>
              <View style={{ flexShrink: 1 }}>
                <Text style={s.rowLabel}>Full access</Text>
                <Text style={[s.rowValue, full && { color: c.warn }]}>
                  {full
                    ? 'ON: Motion plans and runs skills and tasks; it tells you the plan and waits for your yes before the real arm moves'
                    : 'Off: skills also need approval in the Skills tab first'}
                </Text>
              </View>
              <Switch value={full} onValueChange={setFullAccess} />
            </View>
            <Text style={s.hint}>On at every app start. Say "safe mode" / "full access off" to turn it off.</Text>
          </ScrollView>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

function Row({ label, value, color, last }: { label: string; value: string; color?: string; last?: boolean }) {
  const s = useStyles(makeStyles);
  return (
    <View style={[s.row, last && { borderBottomWidth: 0 }]}>
      <Text style={s.rowLabel}>{label}</Text>
      <Text style={[s.rowValue, color && { color }]} numberOfLines={1}>
        {value}
      </Text>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    backdrop: {
      flex: 1,
      backgroundColor: c.scrim,
      alignItems: 'center',
      justifyContent: 'center',
      padding: 20,
    },
    backdropMobile: { justifyContent: 'flex-end', padding: 0 },
    card: {
      width: '100%',
      maxWidth: 420,
      maxHeight: '90%',
      backgroundColor: c.panel,
      borderRadius: radius.lg,
      padding: 24,
      boxShadow: shadowFloat,
    },
    cardMobile: {
      maxWidth: undefined,
      maxHeight: '92%',
      minHeight: '85%',
      borderBottomLeftRadius: 0,
      borderBottomRightRadius: 0,
      paddingBottom: 40,
    },
    head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 },
    title: { ...type.h2, color: c.text },
    overline: { ...type.overline, color: c.faint, marginTop: 20, marginBottom: 8 },
    sectionHint: { ...type.caption, color: c.faint, marginBottom: 10 },
    fieldLabel: { ...type.label, fontSize: 12, color: c.dim, marginBottom: 6 },
    input: {
      ...type.body,
      fontSize: 13,
      color: c.text,
      backgroundColor: c.bg,
      borderWidth: 1,
      borderColor: c.lineStrong,
      borderRadius: radius.md,
      paddingHorizontal: 12,
      paddingVertical: 10,
      outlineStyle: 'none',
    } as any,
    rowActions: { flexDirection: 'row', gap: 8, marginTop: 10 },
    centerRow: { paddingVertical: 16, alignItems: 'center' },
    robotList: { gap: 8 },
    robotRow: {
      borderWidth: 1,
      borderColor: c.line,
      borderRadius: radius.md,
      paddingHorizontal: 12,
      paddingVertical: 10,
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
    },
    robotTitle: { ...type.label, color: c.text },
    robotMeta: { ...type.caption, color: c.faint, marginTop: 2 },
    row: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      paddingVertical: 10,
      borderBottomWidth: 1,
      borderBottomColor: c.line,
      gap: 12,
    },
    rowLabel: { ...type.label, fontSize: 12, color: c.text },
    rowValue: { ...type.caption, fontSize: 12, color: c.faint, flexShrink: 1 },
    hint: { ...type.caption, color: c.faint, marginTop: 14 },
  });
