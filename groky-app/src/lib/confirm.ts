// Yes/no confirmation that works everywhere. React Native's Alert.alert shows no buttons on web (react-native-web),
// so a destructive action confirmed only through it silently does nothing in the browser.
import { Alert, Platform } from 'react-native';

export function confirmAction(title: string, message: string, okLabel: string, onOk: () => void): void {
  if (Platform.OS === 'web') {
    const ask = (globalThis as { confirm?: (m: string) => boolean }).confirm;
    if (ask?.(`${title}\n\n${message}`)) onOk();
    return;
  }
  Alert.alert(title, message, [
    { text: 'Cancel', style: 'cancel' },
    { text: okLabel, style: 'destructive', onPress: onOk },
  ]);
}
