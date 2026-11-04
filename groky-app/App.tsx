import { useCallback, useState } from 'react';
import {
  Animated,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  useWindowDimensions,
  View,
} from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import CameraPanel from './src/components/CameraPanel';
import DeviceChips from './src/components/DeviceChips';
import Settings from './src/components/Settings';
import Sidebar, { Tab } from './src/components/Sidebar';
import StopButton from './src/components/StopButton';
import { useCamera } from './src/hooks/useCamera';
import { DevicesProvider } from './src/hooks/useDevices';
import { useHistory } from './src/hooks/useHistory';
import { useVoice } from './src/hooks/useVoice';
import Cameras from './src/screens/Cameras';
import History from './src/screens/History';
import Robots from './src/screens/Robots';
import Talk from './src/screens/Talk';
import { Colors, ThemeProvider, useStyles, useTheme } from './src/theme';

export default function App() {
  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <DevicesProvider>
          <Main />
        </DevicesProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}

function Main() {
  const { c, isDark } = useTheme();
  const s = useStyles(makeStyles);
  const wide = useWindowDimensions().width >= 720;

  const [level] = useState(() => new Animated.Value(0));
  const onLevel = useCallback((v: number) => level.setValue(v), [level]);
  const history = useHistory();
  const voice = useVoice({ onLevel, onAsked: history.add });
  const cam = useCamera(voice);

  const [tab, setTab] = useState<Tab>('talk');
  const [menuOpen, setMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const live = voice.status !== 'off';

  const openTab = (t: Tab) => {
    setTab(t);
    setMenuOpen(false);
  };

  const rerun = (text: string) => {
    Haptics.selectionAsync();
    setTab('talk');
    if (!live) voice.start();
    voice.say(text);
  };

  const camera = (height: number) => (
    <CameraPanel
      ref={cam.ref}
      onShow={cam.show}
      busy={cam.looking}
      enabled={cam.enabled}
      onToggle={cam.setEnabled}
      height={height}
    />
  );
  const stopButton = live && <StopButton onPress={voice.emergencyStop} />;

  const sidebar = (
    <Sidebar
      tab={tab}
      onTab={openTab}
      historyCount={history.items.length}
      onSettings={() => {
        setMenuOpen(false);
        setSettingsOpen(true);
      }}
    />
  );

  return (
    <SafeAreaView style={s.root} edges={['top', 'bottom', 'left']}>
      <StatusBar style={isDark ? 'light' : 'dark'} />
      <View style={s.shell}>
        {wide && sidebar}

        <View style={{ flex: 1 }}>
          <View style={s.top}>
            {!wide && (
              <Pressable onPress={() => setMenuOpen(true)} style={s.topBtn} hitSlop={8}>
                <Ionicons name="menu" size={22} color={c.text} />
              </Pressable>
            )}
            <DeviceChips />
            <View style={{ flex: 1 }} />
            {tab === 'talk' && (
              <Pressable onPress={voice.clear} style={s.topBtn} hitSlop={8}>
                <Ionicons name="add" size={22} color={c.text} />
              </Pressable>
            )}
          </View>

          <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
            {tab === 'talk' && <Talk voice={voice} level={level} wide={wide} camera={camera} stopButton={stopButton} />}
            {tab === 'cameras' && <Cameras voice={voice} wide={wide} camera={camera} />}
            {tab === 'robots' && <Robots lines={voice.lines} />}
            {tab === 'history' && (
              <History
                items={history.items}
                onRun={rerun}
                onPin={history.togglePin}
                onRemove={history.remove}
                onClear={history.clear}
              />
            )}
          </KeyboardAvoidingView>

          {tab !== 'talk' && stopButton}
        </View>
      </View>

      {!wide && (
        <Modal visible={menuOpen} transparent animationType="fade" onRequestClose={() => setMenuOpen(false)}>
          <View style={s.drawerWrap}>
            <SafeAreaView edges={['top', 'bottom', 'left']} style={s.drawer}>
              {sidebar}
            </SafeAreaView>
            <Pressable style={{ flex: 1 }} onPress={() => setMenuOpen(false)} />
          </View>
        </Modal>
      )}

      <Settings open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </SafeAreaView>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    root: { flex: 1, backgroundColor: c.bg },
    shell: { flex: 1, flexDirection: 'row' },
    top: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 16, paddingTop: 8, minHeight: 48 },
    topBtn: {
      width: 40,
      height: 40,
      borderRadius: 20,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: c.panel,
      borderWidth: 1,
      borderColor: c.line,
    },
    drawerWrap: { flex: 1, flexDirection: 'row', backgroundColor: 'rgba(0,0,0,0.4)' },
    drawer: { backgroundColor: c.bg },
  });
