import * as Haptics from 'expo-haptics';
import { Button } from './ui';

// the only large danger fill in the app
export default function StopButton({ onPress }: { onPress: () => void }) {
  const press = () => {
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
    onPress();
  };

  return (
    <Button
      label="Stop robots"
      icon="hand-left"
      variant="danger"
      onPress={press}
      style={{ alignSelf: 'center', marginVertical: 8, paddingHorizontal: 20 }}
    />
  );
}
