import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { ActivityIndicator, Image, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { CameraView, useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import { AudioModule, RecordingPresets, useAudioRecorder } from 'expo-audio';
import { Ionicons } from '@expo/vector-icons';
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

const CameraPanel = forwardRef<CameraHandle, Props>(function CameraPanel(
  { onShow, busy, busyLabel, recording, enabled, onToggle, watching, onWatch, height = 240 },
  ref,
) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [cam, setCam] = useState<Camera>(cameras[0]);
  const [facing, setFacing] = useState<'back' | 'front'>('back');
  const [perm, requestPerm] = useCameraPermissions();
  const [, requestMic] = useMicrophonePermissions();
  const [mode, setMode] = useState<'picture' | 'video'>('picture');
  // web: unmount CameraView before MediaRecorder so getUserMedia is not blocked by the preview
  const [previewOn, setPreviewOn] = useState(true);
  const view = useRef<CameraView>(null);
  const mic = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const releaseWaiter = useRef<(() => void) | null>(null);
  const readyWaiter = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!previewOn && releaseWaiter.current) {
      const done = releaseWaiter.current;
      releaseWaiter.current = null;
      // give the browser a tick to stop the preview tracks after unmount
      wait(300).then(done);
    }
  }, [previewOn]);

  const releasePreview = async () => {
    if (!previewOn) {
      await wait(50);
      return;
    }
    await new Promise<void>((resolve) => {
      releaseWaiter.current = resolve;
      setPreviewOn(false);
    });
  };

  const waitCameraReady = (ms = 2500) =>
    new Promise<void>((resolve) => {
      const t = setTimeout(() => {
        readyWaiter.current = null;
        resolve(); // don't hang forever; recordAsync will error clearly if still unready
      }, ms);
      readyWaiter.current = () => {
        clearTimeout(t);
        resolve();
      };
    });

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
    await Promise.race([wait(seconds * 1000), new Promise<void>((r) => signal?.addEventListener('abort', () => r(), { once: true }))]);
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
        if (frames.length < 2) {
          throw new Error(
            `no frames from ${cam.label} — is the vision / bridge camera reachable? Got ${frames.length} frame(s).`,
          );
        }
        // real rate, so the video lasts as long as the recording did
        const fps = frames.length / Math.max(1, (Date.now() - t0) / 1000);
        return { source: cam.label.toLowerCase(), rec: { kind: 'frames', frames, fps, ...sound } };
      }
      if (Platform.OS === 'web') {
        // Expo CameraView holds getUserMedia on web; a second open often yields an empty / unreadable webm.
        await releasePreview();
        try {
          const { blob, mime } = await recordWeb(true, seconds, signal);
          return { source: 'tablet', rec: { kind: 'video', blob, mime } };
        } finally {
          setPreviewOn(true);
        }
      }
      const m = await requestMic();
      if (!m.granted) throw new Error('microphone access is needed to record the demonstration');
      // switch to video mode and wait for the camera to come back before recordAsync
      const ready = waitCameraReady();
      setMode('video');
      await ready;
      await wait(150);
      try {
        const camView = view.current;
        if (!camView) throw new Error('tablet camera is not open');
        const stopper = () => {
          try {
            camView.stopRecording();
          } catch {
            // already stopped
          }
        };
        signal?.addEventListener('abort', stopper);
        let out: { uri: string } | undefined;
        try {
          out = await camView.recordAsync({ maxDuration: seconds });
        } finally {
          signal?.removeEventListener('abort', stopper);
        }
        if (!out?.uri) throw new Error('could not record video');
        return {
          source: 'tablet',
          rec: { kind: 'video', uri: out.uri, mime: Platform.OS === 'ios' ? 'video/quicktime' : 'video/mp4' },
        };
      } finally {
        setMode('picture');
      }
    },
    capture: async () => {
      if (!enabled) throw new Error('the camera is turned off');
      if (!cam.url) {
        if (!previewOn || !view.current) throw new Error('tablet camera is not open');
        const pic = await view.current.takePictureAsync({ base64: true, quality: 0.35, shutterSound: false });
        if (!pic?.base64) throw new Error('could not take a picture');
        return { source: 'tablet', b64: pic.base64 };
      }
      return { source: cam.label.toLowerCase(), b64: await snapshot(cam.url, cam.open) };
    },
  }));

  const showLocal = enabled && !cam.url && !!perm?.granted && previewOn;

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

      {enabled && (
        <>
          <View style={[s.feed, { height }]}>
            {cam.url ? (
              <Snapshots key={cam.url} url={cam.url} open={cam.open} />
            ) : !perm ? null : !perm.granted ? (
              <Pressable onPress={requestPerm} style={s.center}>
                <Ionicons name="camera-outline" size={24} color={c.faint} />
                <Text style={s.muted}>Allow camera access</Text>
              </Pressable>
            ) : previewOn ? (
              <CameraView
                ref={view}
                style={StyleSheet.absoluteFill}
                facing={facing}
                mode={mode}
                animateShutter={false}
                onCameraReady={() => {
                  readyWaiter.current?.();
                  readyWaiter.current = null;
                }}
              />
            ) : (
              <View style={s.center}>
                <ActivityIndicator color={c.dim} />
                <Text style={s.muted}>{recording || busy ? 'Recording…' : 'Starting recorder…'}</Text>
              </View>
            )}

            {watching && <Pill label="Motion is watching" tone="bad" dot style={s.live} />}

            {showLocal && (
              <IconButton
                icon="camera-reverse-outline"
                size={32}
                onPress={() => setFacing((f) => (f === 'back' ? 'front' : 'back'))}
                style={s.flip}
                label="Flip camera"
              />
            )}
          </View>

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
  });
