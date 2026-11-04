import { forwardRef, useImperativeHandle, useRef, useState } from 'react';
import { ActivityIndicator, Image, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { Ionicons } from '@expo/vector-icons';
import { Camera, camHeaders, cameras, frameUrl, snapshot } from '../lib/cameras';
import { Colors, radius, type, useStyles, useTheme } from '../theme';
import { Button, IconButton, Pill, Segmented } from './ui';

export type CameraHandle = { capture: () => Promise<{ source: string; b64: string }> };

type Props = {
  onShow: () => void;
  busy: boolean;
  enabled: boolean;
  onToggle: (on: boolean) => void;
  height?: number;
};

const CameraPanel = forwardRef<CameraHandle, Props>(function CameraPanel(
  { onShow, busy, enabled, onToggle, height = 240 },
  ref,
) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const [cam, setCam] = useState<Camera>(cameras[0]);
  const [facing, setFacing] = useState<'back' | 'front'>('back');
  const [perm, requestPerm] = useCameraPermissions();
  const view = useRef<CameraView>(null);

  useImperativeHandle(ref, () => ({
    capture: async () => {
      if (!enabled) throw new Error('the camera is turned off');
      if (!cam.url) {
        if (!view.current) throw new Error('tablet camera is not open');
        const pic = await view.current.takePictureAsync({ base64: true, quality: 0.35, shutterSound: false });
        if (!pic?.base64) throw new Error('could not take a picture');
        return { source: 'tablet', b64: pic.base64 };
      }
      return { source: cam.label.toLowerCase(), b64: await snapshot(cam.url) };
    },
  }));

  return (
    <View style={s.card}>
      <View style={s.head}>
        {enabled ? (
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
              <Snapshots key={cam.url} url={cam.url} />
            ) : !perm ? null : !perm.granted ? (
              <Pressable onPress={requestPerm} style={s.center}>
                <Ionicons name="camera-outline" size={24} color={c.faint} />
                <Text style={s.muted}>Allow camera access</Text>
              </Pressable>
            ) : (
              <CameraView ref={view} style={StyleSheet.absoluteFill} facing={facing} animateShutter={false} />
            )}

            <Pill label={cam.label} tone="bad" dot style={s.live} />

            {!cam.url && perm?.granted && (
              <IconButton
                icon="camera-reverse-outline"
                size={32}
                onPress={() => setFacing((f) => (f === 'back' ? 'front' : 'back'))}
                style={s.flip}
                label="Flip camera"
              />
            )}
          </View>

          <Button
            label={busy ? 'Groky is looking...' : 'Show Groky'}
            icon="eye-outline"
            loading={busy}
            onPress={onShow}
            block
          />
        </>
      )}
    </View>
  );
});

export default CameraPanel;

// Polls a jpeg url. Two stacked images so the old frame stays up until the new one has loaded (no flicker).
function Snapshots({ url }: { url: string }) {
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
  });
