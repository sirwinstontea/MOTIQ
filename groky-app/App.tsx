import { useCallback, useEffect, useRef, useState } from 'react';
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
import { useKeyboardVisible } from './src/hooks/useKeyboard';
import { NotifyProvider, useNotify } from './src/hooks/useNotify';
import type { SkillDoc } from './src/skills';
import { useSkills } from './src/hooks/useSkills';
import type { VoiceCommand } from './src/skills/fromVoice';
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
  const keyboard = useKeyboardVisible();

  const [level] = useState(() => new Animated.Value(0));
  const onLevel = useCallback((v: number) => level.setValue(v), [level]);
  const history = useHistory();
  // robot requests given by voice are saved as skills (useSkills is created after useVoice, hence the ref)
  const saveVoiceRef = useRef<((cmd: VoiceCommand) => void) | null>(null);
  const onCommand = useCallback((cmd: VoiceCommand) => saveVoiceRef.current?.(cmd), []);
  const { notify } = useNotify();
  // a robot action Motion ran by voice failed
  const onToolError = useCallback(
    (what: string, error: string) => notify({ kind: 'bad', title: `Couldn't ${what}`, body: error }),
    [notify],
  );
  const voice = useVoice({ onLevel, onAsked: history.add, onCommand, onToolError });
  // Show Motion recordings become skills: saved to the Skills tab as drafts that still need approving
  const saveRef = useRef<((doc: SkillDoc) => SkillDoc | undefined) | null>(null);
  const onSkill = useCallback(
    (doc: SkillDoc, problems: string[]) => {
      const dup = saveRef.current?.(doc);
      if (dup) return dup.skill.id; // save() already said it's a copy of an existing skill: use that one
      notify({
        kind: problems.length ? 'warn' : 'ok',
        title: `Learned "${doc.skill.name}"`,
        body: problems.length
          ? `Saved to Skills, but it has a problem: ${problems[0]}`
          : 'Saved to Skills. It runs in simulation now; approve it in Skills before it moves the real robot.',
      });
      return doc.skill.id;
    },
    [notify],
  );
  const cam = useCamera(voice, onSkill);
  const skills = useSkills();
  saveVoiceRef.current = skills.saveVoice;
  useEffect(() => {
    saveRef.current = skills.save;
  }, [skills.save]);
  useAlerts(voice.lines);

  // let Grok run skills by voice, and tell it when the list changes
  const { setSkills, refreshTools } = voice;
  const { getSkills, start: startSkill, edit: editSkill } = skills;
  useEffect(() => {
    setSkills({ list: getSkills, run: (id) => startSkill(id, 'voice'), edit: editSkill });
    return () => setSkills(null);
  }, [setSkills, getSkills, startSkill, editSkill]);
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
      busyLabel={cam.phase}
      recording={cam.recording}
      enabled={cam.enabled}
      onToggle={cam.setEnabled}
      watching={cam.watching}
      onWatch={cam.setWatching}
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

          <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'web' ? undefined : 'padding'}>
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

      {mobile && !keyboard && (
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
