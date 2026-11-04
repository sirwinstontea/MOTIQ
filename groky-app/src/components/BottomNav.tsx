import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Colors, fonts, type, useStyles, useTheme } from '../theme';
import { navItems, Tab, useNavBadges } from './Sidebar';

type Props = { tab: Tab; onTab: (t: Tab) => void; historyCount: number; skillRunning?: boolean };

// mobile tab bar: active tab = accent icon + label
export default function BottomNav({ tab, onTab, historyCount, skillRunning }: Props) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const badge = useNavBadges(historyCount, skillRunning);
  const insets = useSafeAreaInsets();

  return (
    <View style={[s.nav, { paddingBottom: 8 + insets.bottom }]}>
      {navItems.map((it) => {
        const on = tab === it.id;
        const b = badge(it.id);
        const color = on ? c.accent : c.faint;
        return (
          <Pressable
            key={it.id}
            onPress={() => {
              Haptics.selectionAsync();
              onTab(it.id);
            }}
            style={s.item}
            accessibilityLabel={it.label}
            accessibilityState={{ selected: on }}
          >
            <View>
              <Ionicons name={it.icon} size={20} color={color} />
              {b.bad && !!b.count && <View style={[s.dot, { backgroundColor: c.bad }]} />}
              {b.live && <View style={[s.dot, { backgroundColor: c.accent }]} />}
            </View>
            <Text style={[s.label, { color }]}>{it.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    nav: {
      flexDirection: 'row',
      justifyContent: 'space-around',
      backgroundColor: c.panel,
      borderTopWidth: 1,
      borderTopColor: c.line,
      paddingTop: 10,
      paddingBottom: 6,
    },
    item: { flex: 1, alignItems: 'center', gap: 4 },
    label: { ...type.caption, fontFamily: fonts.medium, fontSize: 10 },
    dot: {
      position: 'absolute',
      top: -2,
      right: -4,
      width: 8,
      height: 8,
      borderRadius: 4,
      borderWidth: 1.5,
      borderColor: c.panel,
    },
  });
