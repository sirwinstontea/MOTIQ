import { useCallback, useEffect, useState } from 'react';
import { Animated, KeyboardAvoidingView, Platform, StyleSheet, useWindowDimensions, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import * as Haptics from 'expo-haptics';
import {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
  useFonts,
} from '@expo-google-fonts/inter';
import { JetBrainsMono_500Medium } from '@expo-google-fonts/jetbrains-mono';
import BottomNav from './src/components/BottomNav';
import CameraPanel from './src/components/CameraPanel';
import DeviceChips from './src/components/DeviceChips';
import Notifications from './src/components/Notifications';
import Settings from './src/components/Settings';
import Sidebar, { Tab } from './src/components/Sidebar';
import StopButton from './src/components/StopButton';
import { IconButton } from './src/components/ui';
import { useAlerts } from './src/hooks/useAlerts';
import { useCamera } from './src/hooks/useCamera';
import { DevicesProvider } from './src/hooks/useDevices';
import { useHistory } from './src/hooks/useHistory';
import { NotifyProvider } from './src/hooks/useNotify';
import { useSkills } from './src/hooks/useSkills';
import { useVoice } from './src/hooks/useVoice';
import Cameras from './src/screens/Cameras';
import History from './src/screens/History';
import Robots from './src/screens/Robots';
import Skills from './src/screens/Skills';
import Talk from './src/screens/Talk';
import { Colors, ThemeProvider, useStyles, useTheme } from './src/theme';

export default function App() {
  const [fontsLoaded, fontError] = useFonts({
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    Inter_700Bold,
    JetBrainsMono_500Medium,
  });
  // fall back to system fonts if loading fails rather than hanging on a blank screen
  if (!fontsLoaded && !fontError) return null;

  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <NotifyProvider>
          <DevicesProvider>
            <Main />
          </DevicesProvider>
        </NotifyProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}

// desktop >= 1024: full sidebar · tablet 641-1023: icon rail · mobile: bottom nav
function useLayout() {
  const { width } = useWindowDimensions();
  const layout = width >= 1024 ? 'desktop' : width >= 641 ? 'tablet' : 'mobile';
  return { layout, wide: width >= 860 } as const;
}

function Main() {
  const { isDark } = useTheme();
  const s = useStyles(makeStyles);
  const { layout, wide } = useLayout();
  const mobile = layout === 'mobile';

  const [level] = useState(() => new Animated.Value(0));
  const onLevel = useCallback((v: number) => level.setValue(v), [level]);
  const history = useHistory();
  const voice = useVoice({ onLevel, onAsked: history.add });
  const cam = useCamera(voice);
  const skills = useSkills();
  useAlerts(voice.lines);

  // let Grok run skills by voice, and tell it when the list changes
  const { setSkills, refreshTools } = voice;
  const { getSkills, start: startSkill } = skills;
  useEffect(() => {
    setSkills({ list: getSkills, run: (id) => startSkill(id, 'voice') });
    return () => setSkills(null);
  }, [setSkills, getSkills, startSkill]);
  useEffect(() => {
    refreshTools();
  }, [skills.skills, refreshTools]);

  const [tab, setTab] = useState<Tab>('talk');
  const [settingsOpen, setSettingsOpen] = useState(false);

  const live = voice.status !== 'off';

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
  const stopAll = () => {
    skills.stop();
    voice.emergencyStop();
  };
  const stopButton = (live || skills.running) && <StopButton onPress={stopAll} />;

  return (
    <SafeAreaView style={s.root} edges={mobile ? ['top', 'left', 'right'] : ['top', 'bottom', 'left', 'right']}>
      <StatusBar style={isDark ? 'light' : 'dark'} />
      <View style={s.shell}>
        {!mobile && (
          <Sidebar
            tab={tab}
            onTab={setTab}
            compact={layout === 'tablet'}
            historyCount={history.items.length}
            skillRunning={skills.running}
            onSettings={() => setSettingsOpen(true)}
          />
        )}

        <View style={{ flex: 1 }}>
          <View style={[s.top, mobile && s.topMobile]}>
            <DeviceChips compact={mobile} />
            <View style={{ flex: 1 }} />
            {tab === 'talk' && !mobile && <IconButton icon="add" onPress={voice.clear} label="New conversation" />}
            <Notifications size={mobile ? 34 : 40} />
            {mobile && (
              <IconButton icon="settings-outline" size={34} onPress={() => setSettingsOpen(true)} label="Settings" />
            )}
          </View>

          <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
            {tab === 'talk' && (
              <Talk voice={voice} level={level} wide={wide} mobile={mobile} camera={camera} stopButton={stopButton} />
            )}
            {tab === 'cameras' && <Cameras voice={voice} wide={wide} camera={camera} />}
            {tab === 'robots' && <Robots lines={voice.lines} />}
            {tab === 'skills' && <Skills skills={skills} />}
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

      {mobile && (
        <BottomNav tab={tab} onTab={setTab} historyCount={history.items.length} skillRunning={skills.running} />
      )}

      <Settings open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </SafeAreaView>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    root: { flex: 1, backgroundColor: c.bg },
    shell: { flex: 1, flexDirection: 'row' },
    top: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 24, paddingTop: 14, minHeight: 54 },
    topMobile: { paddingHorizontal: 16, paddingTop: 8, minHeight: 46 },
  });
