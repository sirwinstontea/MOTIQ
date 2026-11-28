import { useEffect, useState } from 'react';
import { Modal, StyleSheet, Text, TextInput, View } from 'react-native';
import { isMotiqDesktop } from '../lib/desktopConfig';
import { Colors, fonts, radius, type, useStyles, useTheme } from '../theme';
import { Button, Segmented } from './ui';

/** First-run wizard for the Electron desktop shell (API key + RoboDK/backend + camera mode). */
export default function FirstRunDesktop() {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState('');
  const [backend, setBackend] = useState<'robodk_sim' | 'mock'>('robodk_sim');
  const [visionMode, setVisionMode] = useState('synthetic');
  const [hint, setHint] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!isMotiqDesktop() || typeof window === 'undefined' || !window.motiqDesktop) return;
    window.motiqDesktop.getConfig().then((cfg) => {
      if (!cfg.firstRunComplete) {
        setOpen(true);
        setHint(cfg.robodkHint || '');
        if (cfg.backend === 'mock') setBackend('mock');
        if (cfg.visionMode) setVisionMode(String(cfg.visionMode));
      }
    });
  }, []);

  if (!open) return null;

  const save = async () => {
    setBusy(true);
    const cfg = await window.motiqDesktop?.saveFirstRun({ xaiApiKey: key.trim(), backend, visionMode });
    if (cfg && typeof window !== 'undefined') {
      window.__MOTIQ_DESKTOP__ = { ...window.__MOTIQ_DESKTOP__, ...cfg, MOTIQ_DESKTOP: '1' };
      window.dispatchEvent(new Event('motiq-desktop-config'));
    }
    setBusy(false);
    setOpen(false);
  };

  return (
    <Modal visible animationType="fade" transparent>
      <View style={s.scrim}>
        <View style={s.card}>
          <Text style={s.title}>Welcome to MOTIQ Desktop</Text>
          <Text style={s.body}>
            Full control on this PC: local bridge, vision (inference only — no training), teach, and RoboDK simulation.
            Paste your xAI API key to enable voice. Install RoboDK separately for the default sim backend.
          </Text>
          {!!hint && <Text style={s.hint}>{hint}</Text>}
          <Text style={s.label}>xAI API key</Text>
          <TextInput
            style={s.input}
            value={key}
            onChangeText={setKey}
            placeholder="xai-..."
            placeholderTextColor={c.faint}
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry
          />
          <Text style={s.label}>Simulation backend</Text>
          <Segmented
            value={backend}
            onChange={(v) => setBackend(v as 'robodk_sim' | 'mock')}
            options={[
              { id: 'robodk_sim', label: 'RoboDK' },
              { id: 'mock', label: 'Mock' },
            ]}
          />
          <Text style={s.label}>Camera</Text>
          <Segmented
            value={visionMode}
            onChange={setVisionMode}
            options={[
              { id: 'synthetic', label: 'Synthetic' },
              { id: 'webcam', label: 'Webcam' },
              { id: 'realsense', label: 'RealSense' },
            ]}
          />
          <View style={s.actions}>
            <Button label="Continue" icon="checkmark" variant="primary" loading={busy} onPress={save} />
          </View>
          <Text style={s.hint}>Unsigned build — OS may warn on first launch. Training notebooks are not installed.</Text>
        </View>
      </View>
    </Modal>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    scrim: {
      flex: 1,
      backgroundColor: 'rgba(0,0,0,0.55)',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 24,
    },
    card: {
      width: '100%',
      maxWidth: 480,
      backgroundColor: c.bg,
      borderRadius: radius.lg,
      padding: 20,
      gap: 10,
      borderWidth: 1,
      borderColor: c.line,
    },
    title: { ...type.title, color: c.text, fontFamily: fonts.semibold },
    body: { ...type.body, color: c.dim, lineHeight: 20 },
    hint: { ...type.caption, color: c.faint, lineHeight: 16 },
    label: { ...type.caption, color: c.dim, marginTop: 4, fontFamily: fonts.semibold },
    input: {
      borderWidth: 1,
      borderColor: c.line,
      borderRadius: radius.md,
      paddingHorizontal: 12,
      paddingVertical: 10,
      color: c.text,
      fontFamily: fonts.mono,
    },
    actions: { marginTop: 8 },
  });
