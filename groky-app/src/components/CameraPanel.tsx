import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { ActivityIndicator, Image, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { CameraView, useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import { AudioModule, RecordingPresets, useAudioRecorder } from 'expo-audio';
import { Ionicons } from '@expo/vector-icons';
import env from '../lib/config';
import { Camera, cameras, frameUrl, headersFor, snapshot } from '../lib/cameras';
import { blobToBase64, FRAME_FPS, grabFrames, Recording, recordWeb, wait } from '../lib/recorder';
import { Colors, radius, type, useStyles, useTheme } from '../theme';
import { Button, IconButton, Pill, Segmented } from './ui';

export type CameraHandle = {
  capture: () => Promise<{ source: string; b64: string }>;
  // record the selected camera (video + audio) for `seconds`, see lib/recorder.ts
  record: (seconds: number, signal?: AbortSignal) => Promise<{ source: string; rec: Recording }>;
};

type Props = {
  onShow: () => void;
  busy: boolean;
  busyLabel?: string;
  recording?: boolean; // Show Motion is recording: the button stops it
  enabled: boolean;
  onToggle: (on: boolean) => void;
  watching: boolean;
  onWatch: (on: boolean) => void;
  height?: number;
};

type ModelStatus = {
  ok: boolean;
  custom_model: string;
  yolo_world_model: string;
  yolo_world_active: boolean;
  yolo_classes_count: number;
  yolo_classes: string[];
  grok_active: boolean;
  reference_frame_set: boolean;
};

const CameraPanel = forwardRef<CameraHandle, Props>(function CameraPanel(
  { onShow, busy, busyLabel, recording, enabled, onToggle, watching, onWatch, height = 240 },
  ref,
) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [cam, setCam] = useState<Camera>(cameras[0]);
  // Su PC / browser web la webcam è SEMPRE frontale ('front' / 'user'). Su tablet/mobile 'back'.
  const [facing, setFacing] = useState<'back' | 'front'>(Platform.OS === 'web' ? 'front' : 'back');
  const [perm, requestPerm] = useCameraPermissions();
  const [, requestMic] = useMicrophonePermissions();
  const [mode, setMode] = useState<'picture' | 'video'>('picture');
  const view = useRef<CameraView>(null);
  const webVideoRef = useRef<HTMLVideoElement | null>(null);
  const mic = useAudioRecorder(RecordingPresets.HIGH_QUALITY);

  // Grok Vision state
  const [models, setModels] = useState<ModelStatus | null>(null);
  const [grokBusy, setGrokBusy] = useState<string | null>(null);
  const [grokResult, setGrokResult] = useState<{ title: string; text: string; tone?: 'neutral' | 'ok' | 'warn' | 'bad' } | null>(null);

  const fetchModelStatus = async () => {
    if (!env.visionServer) return;
    try {
      const r = await fetch(`${env.visionServer}/models/status`);
      if (r.ok) {
        const data = await r.json();
        setModels(data);
      }
    } catch {
      // vision server non raggiungibile
    }
  };

  useEffect(() => {
    fetchModelStatus();
    const interval = setInterval(fetchModelStatus, 8000);
    return () => clearInterval(interval);
  }, []);

  const runGrokAction = async (endpoint: string, title: string, successFn: (res: any) => string) => {
    if (!env.visionServer) return;
    setGrokBusy(title);
    setGrokResult(null);
    try {
      const r = await fetch(`${env.visionServer}${endpoint}`, { method: 'POST' });
      const data = await r.json();
      if (!data.ok) {
        setGrokResult({ title, text: data.error || 'Errore durante l\'elaborazione', tone: 'bad' });
      } else {
        const msg = successFn(data);
        setGrokResult({ title, text: msg, tone: 'ok' });
        fetchModelStatus();
      }
    } catch (e: any) {
      setGrokResult({ title, text: `Connessione fallita: ${e.message}`, tone: 'bad' });
    } finally {
      setGrokBusy(null);
    }
  };

  const handleGrokDetect = () => {
    runGrokAction('/grok/detect?source=arm', 'Grok Detect', (d) => {
      const names = d.objects?.map((o: any) => o.name || o.label).join(', ') || 'nessuno';
      return `Rilevati ${d.count} oggetti: ${names}. YOLO-World ora li traccia in tempo reale!`;
    });
  };

  const handleGrokDescribe = () => {
    runGrokAction('/grok/describe?source=arm', 'Descrizione Scena', (d) => d.description || 'Nessuna descrizione.');
  };

  const handleGrokName = () => {
    runGrokAction('/grok/name?source=arm', 'Rinomina Oggetti', (d) => {
      const list = d.named?.map((n: any) => `ID ${n.id}: "${n.name}"`).join(', ') || 'Nessun oggetto nominato.';
      return `Oggetti riconosciuti e nominati: ${list}`;
    });
  };

  const handleSaveRef = () => {
    runGrokAction('/grok/changes/reference?source=arm', 'Salva Riferimento', () => 'Scena di riferimento memorizzata! Sposta o rimuovi oggetti per calcolare le differenze.');
  };

  const handleDiff = () => {
    runGrokAction('/grok/changes/diff?source=arm', 'Analisi Differenze', (d) => {
      const exp = d.explanation || 'Nessun cambiamento rilevato.';
      return `${exp} (${d.changes?.length || 0} modifiche evidenziate a video)`;
    });
  };

  const handleReset = () => {
    runGrokAction('/grok/reset?source=arm', 'Reset Memoria', () => 'Memoria resettata e YOLO-World ripristinato alle classi base.');
  };

  // microphone alongside a url camera: browser MediaRecorder, or expo-audio on a phone / tablet
  const recordMic = async (seconds: number, signal?: AbortSignal) => {
    if (Platform.OS === 'web') {
      const { blob, mime } = await recordWeb(false, seconds, signal);
      return { audio: await blobToBase64(blob), audioMime: mime };
    }
    const p = await AudioModule.requestRecordingPermissionsAsync();
    if (!p.granted) return {};
    await mic.prepareToRecordAsync();
    mic.record();
    await Promise.race([wait(seconds * 1000), new Promise((r) => signal?.addEventListener('abort', r))]);
    await mic.stop();
    if (!mic.uri) return {};
    return { audio: await blobToBase64(await (await fetch(mic.uri)).blob()), audioMime: 'audio/mp4' };
  };

  useImperativeHandle(ref, () => ({
    record: async (seconds, signal) => {
      if (!enabled) throw new Error('the camera is turned off');
      if (cam.url) {
        const url = cam.url;
        const t0 = Date.now();
        const [frames, sound] = await Promise.all([
          grabFrames(() => snapshot(url, cam.open), seconds, FRAME_FPS, signal),
          recordMic(seconds, signal).catch(() => ({})),
        ]);
        if (frames.length < 2) throw new Error(`no frames from ${cam.label}`);
        // real rate, so the video lasts as long as the recording did
        const fps = frames.length / Math.max(1, (Date.now() - t0) / 1000);
        return { source: cam.label.toLowerCase(), rec: { kind: 'frames', frames, fps, ...sound } };
      }
      if (Platform.OS === 'web') {
        const { blob, mime } = await recordWeb(true, seconds, signal);
        return { source: 'tablet', rec: { kind: 'video', blob, mime } };
      }
      if (!view.current) throw new Error('tablet camera is not open');
      const m = await requestMic();
      if (!m.granted) throw new Error('microphone access is needed to record the demonstration');
      setMode('video');
      await wait(400); // let the camera switch to video mode
      try {
        const stopper = () => view.current?.stopRecording();
        signal?.addEventListener('abort', stopper);
        const out = await view.current?.recordAsync({ maxDuration: seconds });
        signal?.removeEventListener('abort', stopper);
        if (!out?.uri) throw new Error('could not record video');
        return { source: 'tablet', rec: { kind: 'video', uri: out.uri, mime: Platform.OS === 'ios' ? 'video/quicktime' : 'video/mp4' } };
      } finally {
        setMode('picture');
      }
    },
    capture: async () => {
      if (!enabled) throw new Error('the camera is turned off');
      if (!cam.url) {
        if (Platform.OS === 'web') {
          const video = webVideoRef.current;
          if (video && video.videoWidth > 0) {
            const canvas = document.createElement('canvas');
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            const ctx = canvas.getContext('2d');
            if (ctx) {
              if (facing === 'front') {
                ctx.translate(canvas.width, 0);
                ctx.scale(-1, 1);
              }
              ctx.drawImage(video, 0, 0);
              const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
              return { source: 'tablet', b64: dataUrl.split(',')[1] };
            }
          }
        }
        if (!view.current) throw new Error('tablet camera is not open');
        const pic = await view.current.takePictureAsync({ base64: true, quality: 0.35, shutterSound: false });
        if (!pic?.base64) throw new Error('could not take a picture');
        return { source: 'tablet', b64: pic.base64 };
      }
      return { source: cam.label.toLowerCase(), b64: await snapshot(cam.url, cam.open) };
    },
  }));

  const isVisionCam = Boolean(cam.url && cam.id.startsWith('vision-'));

  return (
    <View style={s.card}>
      <View style={s.head}>
        {enabled && cameras.length < 2 ? (
          <View style={{ flex: 1 }} />
        ) : enabled ? (
          <ScrollView horizontal style={{ flex: 1 }} showsHorizontalScrollIndicator={false}>
            <Segmented
              options={cameras.map((k) => ({ id: k.id, label: k.label }))}
              value={cam.id}
              onChange={(id) => setCam(cameras.find((k) => k.id === id) ?? cameras[0])}
            />
          </ScrollView>
        ) : (
          <View style={s.offLabel}>
            <Ionicons name="videocam-off-outline" size={16} color={c.faint} />
            <Text style={s.muted}>Camera off</Text>
          </View>
        )}
        <IconButton
          icon="power"
          size={32}
          onPress={() => onToggle(!enabled)}
          color={enabled ? c.dim : c.accent}
          label={enabled ? 'Turn camera off' : 'Turn camera on'}
        />
      </View>

      {/* Badges stato modelli (quando abilitato e connesso al vision server) */}
      {enabled && isVisionCam && (
        <ScrollView horizontal style={s.modelsRow} showsHorizontalScrollIndicator={false} contentContainerStyle={s.modelsContent}>
          <Pill label="Custom ONNX (Pezzi & Mani)" tone="ok" dot />
          <Pill
            label={`YOLO-World (${models?.yolo_classes_count ?? 88} classi)`}
            tone={models?.yolo_world_active ? 'ok' : 'neutral'}
            dot
          />
          <Pill
            label={models?.grok_active ? 'Grok Multimodale' : 'Grok Vision'}
            tone={models?.grok_active ? 'accent' : 'neutral'}
            dot
          />
          {models?.reference_frame_set && <Pill label="Rif. Salvato" tone="warn" dot />}
        </ScrollView>
      )}

      {enabled && (
        <>
          <View style={[s.feed, { height }]}>
            {cam.url ? (
              <Snapshots key={cam.url} url={cam.url} open={cam.open} />
            ) : Platform.OS === 'web' ? (
              <WebCamera
                facing={facing}
                onVideoRef={(el) => {
                  webVideoRef.current = el;
                }}
                onFacingChange={setFacing}
              />
            ) : !perm ? null : !perm.granted ? (
              <Pressable onPress={requestPerm} style={s.center}>
                <Ionicons name="camera-outline" size={24} color={c.faint} />
                <Text style={s.muted}>Allow camera access</Text>
              </Pressable>
            ) : (
              <CameraView
                ref={view}
                style={StyleSheet.absoluteFill}
                facing={facing}
                mode={mode}
                animateShutter={false}
              />
            )}

            {watching && <Pill label="Motion is watching" tone="bad" dot style={s.live} />}

            {!cam.url && (
              <IconButton
                icon="camera-reverse-outline"
                size={32}
                onPress={() => setFacing((f) => (f === 'back' ? 'front' : 'back'))}
                style={s.flip}
                label="Flip camera"
              />
            )}
          </View>

          {/* Barra Strumenti Grok Vision */}
          {isVisionCam && (
            <View style={s.grokSection}>
              <View style={s.grokHeader}>
                <Ionicons name="sparkles" size={14} color={c.accent} />
                <Text style={s.grokTitle}>Grok Multimodale & YOLO</Text>
                {grokBusy && <ActivityIndicator size="small" color={c.accent} style={{ marginLeft: 6 }} />}
              </View>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.grokActions}>
                <Button
                  size="sm"
                  variant="secondary"
                  icon="scan-outline"
                  label="Grok Detect"
                  loading={grokBusy === 'Grok Detect'}
                  onPress={handleGrokDetect}
                />
                <Button
                  size="sm"
                  variant="secondary"
                  icon="chatbubble-ellipses-outline"
                  label="Descrivi Scena"
                  loading={grokBusy === 'Descrizione Scena'}
                  onPress={handleGrokDescribe}
                />
                <Button
                  size="sm"
                  variant="secondary"
                  icon="pricetag-outline"
                  label="Rinomina Oggetti"
                  loading={grokBusy === 'Rinomina Oggetti'}
                  onPress={handleGrokName}
                />
                <Button
                  size="sm"
                  variant="secondary"
                  icon="camera-outline"
                  label="Salva Rif."
                  loading={grokBusy === 'Salva Riferimento'}
                  onPress={handleSaveRef}
                />
                <Button
                  size="sm"
                  variant="secondary"
                  icon="git-compare-outline"
                  label="Differenze"
                  loading={grokBusy === 'Analisi Differenze'}
                  onPress={handleDiff}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  icon="refresh-outline"
                  label="Reset"
                  loading={grokBusy === 'Reset Memoria'}
                  onPress={handleReset}
                />
              </ScrollView>
            </View>
          )}

          {/* Box Risultato Grok */}
          {grokResult && (
            <View style={[s.resultBox, { borderColor: grokResult.tone === 'bad' ? c.bad : c.accent }]}>
              <View style={s.resultHeader}>
                <Text style={[s.resultTitle, { color: grokResult.tone === 'bad' ? c.bad : c.accent }]}>
                  {grokResult.title}
                </Text>
                <Pressable onPress={() => setGrokResult(null)} hitSlop={8}>
                  <Ionicons name="close-circle-outline" size={16} color={c.dim} />
                </Pressable>
              </View>
              <Text style={s.resultText}>{grokResult.text}</Text>
            </View>
          )}

          <View style={s.actions}>
            <View style={{ flex: 1 }}>
              <Button
                label={busy ? busyLabel || 'Motion is looking...' : 'Show Motion'}
                icon={recording ? 'stop-circle-outline' : 'videocam-outline'}
                loading={busy && !recording}
                variant={recording ? 'danger' : undefined}
                onPress={onShow}
                block
              />
            </View>
            <Pressable
              onPress={() => onWatch(!watching)}
              style={[s.watch, watching && { backgroundColor: c.accent, borderColor: c.accent }]}
              accessibilityRole="switch"
              accessibilityState={{ checked: watching }}
              accessibilityLabel="Live view: let Motion keep watching"
            >
              <Ionicons name={watching ? 'radio' : 'radio-outline'} size={16} color={watching ? c.accentInk : c.dim} />
              <Text style={[s.watchText, { color: watching ? c.accentInk : c.dim }]}>Live</Text>
            </Pressable>
          </View>
        </>
      )}
    </View>
  );
});

export default CameraPanel;

// Componente Webcam per Browser Web con fallbacks automatici per evitare NotReadableError
function WebCamera({
  facing,
  onVideoRef,
  onFacingChange,
}: {
  facing: 'front' | 'back';
  onVideoRef: (el: HTMLVideoElement | null) => void;
  onFacingChange?: (f: 'front' | 'back') => void;
}) {
  const s = useStyles(makeStyles);
  const { c } = useTheme();
  const videoEl = useRef<HTMLVideoElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeFacing, setActiveFacing] = useState<'front' | 'back'>(facing);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let cancelled = false;

    async function start() {
      setError(null);
      setReady(false);
      if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
        setError('Webcam non supportata dal browser.');
        return;
      }

      // Tentativi ordinati:
      // 1. facingMode desiderato
      // 2. se 'back' fallisce (es. RealSense occupata da Python), tenta automaticamente 'user' (webcam laptop)
      // 3. qualsiasi video disponibile
      const modesToTry = facing === 'back' ? ['environment', 'user'] : ['user', 'environment'];

      for (const mode of modesToTry) {
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: mode },
            audio: false,
          });
          setActiveFacing(mode === 'user' ? 'front' : 'back');
          if (mode === 'user' && facing === 'back') {
            onFacingChange?.('front');
          }
          break;
        } catch (err: any) {
          console.warn(`getUserMedia (${mode}) non riuscito:`, err.message || err.name);
        }
      }

      if (!stream) {
        try {
          stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
          setActiveFacing('front');
        } catch (err: any) {
          if (!cancelled) {
            setError(
              err.name === 'NotReadableError'
                ? 'La fotocamera è attualmente occupata da un altro programma.'
                : `Impossibile avviare la webcam (${err.name || err.message}).`
            );
          }
          return;
        }
      }

      if (cancelled) {
        stream?.getTracks().forEach((t) => t.stop());
        return;
      }

      if (videoEl.current && stream) {
        videoEl.current.srcObject = stream;
        videoEl.current.play().catch(() => {});
        onVideoRef(videoEl.current);
        setReady(true);
      }
    }

    start();

    return () => {
      cancelled = true;
      if (stream) {
        stream.getTracks().forEach((t) => t.stop());
      }
      onVideoRef(null);
    };
  }, [facing]);

  if (error) {
    return (
      <View style={s.center}>
        <Ionicons name="videocam-off-outline" size={28} color={c.dim} />
        <Text style={[s.muted, { textAlign: 'center', paddingHorizontal: 20 }]}>{error}</Text>
        <Button
          size="sm"
          variant="secondary"
          label="Prova Webcam Frontale"
          onPress={() => onFacingChange?.('front')}
          style={{ marginTop: 8 }}
        />
      </View>
    );
  }

  return (
    <View style={StyleSheet.absoluteFill}>
      <video
        ref={(el) => {
          videoEl.current = el;
          if (el) onVideoRef(el);
        }}
        autoPlay
        playsInline
        muted
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          backgroundColor: '#000',
          transform: activeFacing === 'front' ? 'scaleX(-1)' : 'none',
        }}
      />
      {!ready && (
        <View style={s.center}>
          <ActivityIndicator color={c.dim} />
        </View>
      )}
    </View>
  );
}

// Polls a jpeg url. Two stacked images so the old frame stays up until the new one has loaded (no flicker).
function Snapshots({ url, open }: { url: string; open?: boolean }) {
  const camHeaders = headersFor({ open });
  const s = useStyles(makeStyles);
  const { c } = useTheme();
  const [n, setN] = useState(0);
  const [shown, setShown] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const next = frameUrl(url, n);

  return (
    <>
      {shown && (
        <Image source={{ uri: shown, headers: camHeaders }} style={StyleSheet.absoluteFill} resizeMode="cover" />
      )}
      <Image
        key={next}
        source={{ uri: next, headers: camHeaders }}
        style={[StyleSheet.absoluteFill, { opacity: 0 }]}
        onLoad={() => {
          setShown(next);
          setFailed(false);
          setTimeout(() => setN((x) => x + 1), 200);
        }}
        onError={() => {
          setFailed(true);
          setTimeout(() => setN((x) => x + 1), 2000);
        }}
      />
      {!shown && (
        <View style={s.center}>
          {failed ? (
            <>
              <Ionicons name="videocam-off-outline" size={26} color={c.dim} />
              <Text style={s.muted}>No signal</Text>
            </>
          ) : (
            <ActivityIndicator color={c.dim} />
          )}
        </View>
      )}
    </>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    card: { backgroundColor: c.panel, borderRadius: radius.lg, padding: 16, gap: 12 },
    head: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    offLabel: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8 },
    feed: { borderRadius: radius.md, overflow: 'hidden', backgroundColor: c.page },
    center: {
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8,
    },
    muted: { ...type.caption, fontSize: 12, color: c.faint },
    live: { position: 'absolute', top: 10, left: 10 },
    flip: { position: 'absolute', top: 8, right: 8 },
    actions: { flexDirection: 'row', gap: 8, alignItems: 'stretch' },
    watch: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      paddingHorizontal: 14,
      borderRadius: radius.md,
      borderWidth: 1,
      borderColor: c.lineStrong,
    },
    watchText: { ...type.caption, fontSize: 13 },
    modelsRow: { maxHeight: 30 },
    modelsContent: { flexDirection: 'row', gap: 8, alignItems: 'center' },
    grokSection: {
      backgroundColor: c.panelHi,
      borderRadius: radius.md,
      padding: 10,
      gap: 8,
    },
    grokHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
    grokTitle: { ...type.caption, fontSize: 12, fontWeight: '600', color: c.text },
    grokActions: { flexDirection: 'row', gap: 8, alignItems: 'center' },
    resultBox: {
      backgroundColor: c.panelHi,
      borderRadius: radius.md,
      padding: 12,
      borderWidth: 1,
      gap: 6,
    },
    resultHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
    resultTitle: { ...type.label, fontSize: 12, fontWeight: '700' },
    resultText: { ...type.body, fontSize: 13, color: c.text, lineHeight: 18 },
  });
