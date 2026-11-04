import { forwardRef, useImperativeHandle, useRef, useState } from 'react';
import { ActivityIndicator, Image, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { Ionicons } from '@expo/vector-icons';
import { Camera, camHeaders, cameras, frameUrl, snapshot } from '../lib/cameras';
import { Colors, useStyles, useTheme } from '../theme';

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
          <ScrollView
            horizontal
            style={{ flex: 1 }}
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={s.chips}
          >
            {cameras.map((k) => {
              const on = k.id === cam.id;
              return (
                <Pressable key={k.id} onPress={() => setCam(k)} style={[s.chip, on && s.chipOn]}>
                  <Ionicons
                    name={k.url ? 'videocam-outline' : 'tablet-portrait-outline'}
                    size={14}
                    color={on ? c.bg : c.dim}
                  />
                  <Text style={[s.chipText, on && { color: c.bg }]}>{k.label}</Text>
                </Pressable>
              );
            })}
          </ScrollView>
        ) : (
          <View style={s.offLabel}>
            <Ionicons name="videocam-off-outline" size={16} color={c.dim} />
            <Text style={s.muted}>Camera off</Text>
          </View>
        )}
        <Pressable
          onPress={() => onToggle(!enabled)}
          style={[s.power, !enabled && s.powerOff]}
          hitSlop={8}
          accessibilityLabel={enabled ? 'Turn camera off' : 'Turn camera on'}
        >
          <Ionicons name="power" size={15} color={enabled ? c.dim : c.bg} />
          {!enabled && <Text style={s.powerText}>Turn on</Text>}
        </Pressable>
      </View>

      {enabled && (
        <>
          <View style={[s.feed, { height }]}>
            {cam.url ? (
              <Snapshots key={cam.url} url={cam.url} />
            ) : !perm ? null : !perm.granted ? (
              <Pressable onPress={requestPerm} style={s.center}>
                <Ionicons name="camera-outline" size={28} color={c.dim} />
                <Text style={s.muted}>Allow camera access</Text>
              </Pressable>
            ) : (
              <CameraView ref={view} style={StyleSheet.absoluteFill} facing={facing} animateShutter={false} />
            )}

            <View style={s.live}>
              <View style={s.liveDot} />
              <Text style={s.liveText}>{cam.label}</Text>
            </View>

            {!cam.url && perm?.granted && (
              <Pressable onPress={() => setFacing((f) => (f === 'back' ? 'front' : 'back'))} style={s.flip} hitSlop={8}>
                <Ionicons name="camera-reverse-outline" size={18} color="#fff" />
              </Pressable>
            )}
          </View>

          <Pressable
            onPress={onShow}
            disabled={busy}
            style={({ pressed }) => [s.show, (pressed || busy) && { opacity: 0.7 }]}
          >
            {busy ? (
              <ActivityIndicator size="small" color={c.bg} />
            ) : (
              <Ionicons name="eye-outline" size={18} color={c.bg} />
            )}
            <Text style={s.showText}>{busy ? 'Grok is looking...' : 'Show Grok'}</Text>
          </Pressable>
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
    card: { backgroundColor: c.panel, borderRadius: 18, padding: 10, borderWidth: 1, borderColor: c.line, gap: 10 },
    head: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    chips: { gap: 6 },
    offLabel: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6, paddingLeft: 4 },
    power: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 5,
      height: 28,
      minWidth: 28,
      paddingHorizontal: 7,
      borderRadius: 14,
      justifyContent: 'center',
      backgroundColor: c.bg,
    },
    powerOff: { backgroundColor: c.text, paddingHorizontal: 11 },
    powerText: { color: c.bg, fontSize: 12, fontWeight: '700' },
    chip: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 10, height: 28, borderRadius: 14 },
    chipOn: { backgroundColor: c.text },
    chipText: { color: c.dim, fontSize: 12, fontWeight: '600' },
    feed: { borderRadius: 12, overflow: 'hidden', backgroundColor: '#000' },
    center: {
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
    },
    muted: { color: c.dim, fontSize: 13 },
    live: {
      position: 'absolute',
      top: 8,
      left: 8,
      flexDirection: 'row',
      alignItems: 'center',
      gap: 5,
      backgroundColor: 'rgba(0,0,0,0.5)',
      paddingHorizontal: 8,
      height: 22,
      borderRadius: 11,
    },
    liveDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#ff3b3b' },
    liveText: { color: '#fff', fontSize: 11, fontWeight: '700' },
    flip: {
      position: 'absolute',
      top: 8,
      right: 8,
      width: 32,
      height: 32,
      borderRadius: 16,
      backgroundColor: 'rgba(0,0,0,0.5)',
      alignItems: 'center',
      justifyContent: 'center',
    },
    show: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8,
      height: 40,
      borderRadius: 12,
      backgroundColor: c.text,
    },
    showText: { color: c.bg, fontSize: 14, fontWeight: '700' },
  });
