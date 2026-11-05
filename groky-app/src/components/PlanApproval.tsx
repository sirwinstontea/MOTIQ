import * as Haptics from 'expo-haptics';
import { StyleSheet, Text, View } from 'react-native';
import { usePlan } from '../lib/armApproval';
import type { Target } from '../lib/armApproval';
import { Colors, radius, shadowFloat, type, useStyles, useTheme } from '../theme';
import { Button } from './ui';

// Motion's plan for the arm, waiting for a yes. Saying "yes" / "no" out loud (or typing it) works the same.
// With the PC stack on SIM=1 and the physical arm reachable, the user picks where: Simulation or Real arm
// (saying "simulation" / "real arm" works the same).
export default function PlanApproval({ onDecide }: { onDecide: (ok: boolean, target?: Target) => void }) {
  const plan = usePlan();
  const s = useStyles(makeStyles);
  const { c } = useTheme();
  if (!plan || plan.state !== 'pending') return null;

  const decide = (ok: boolean, target?: Target) => {
    Haptics.notificationAsync(ok ? Haptics.NotificationFeedbackType.Success : Haptics.NotificationFeedbackType.Warning);
    onDecide(ok, target);
  };
  const overline = plan.choice
    ? 'Waiting for your approval · simulation or real arm?'
    : plan.target === 'sim'
      ? 'Simulation (URSim + RoboDK) · waiting for your approval'
      : 'Real arm · waiting for your approval';
  const hint = plan.choice ? 'Say "simulation" or "real arm" (or "no"), or tap.' : 'Say "yes" or "no", or tap.';

  return (
    <View style={s.wrap} pointerEvents="box-none">
      <View style={s.card}>
        <Text style={[s.overline, { color: c.warn }]}>{overline}</Text>
        <Text style={s.say}>{plan.say}</Text>
        {plan.steps.map((step, i) => (
          <Text key={i} style={s.step}>
            {i + 1}. {step}
          </Text>
        ))}
        <Text style={s.hint}>{hint}</Text>
        <View style={s.row}>
          <Button label="Cancel" icon="close" onPress={() => decide(false)} />
          {plan.choice ? (
            <>
              <Button label="Simulation" icon="cube-outline" variant="primary" onPress={() => decide(true, 'sim')} />
              <Button label="Real arm" icon="warning" variant="danger" onPress={() => decide(true, 'real')} />
            </>
          ) : (
            <Button label="Approve" icon="checkmark" variant="primary" onPress={() => decide(true)} />
          )}
        </View>
      </View>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    wrap: { position: 'absolute', left: 0, right: 0, bottom: 84, alignItems: 'center', paddingHorizontal: 16 },
    card: {
      width: '100%',
      maxWidth: 560,
      backgroundColor: c.panel,
      borderRadius: radius.lg,
      borderWidth: 1,
      borderColor: c.warn,
      padding: 16,
      gap: 6,
      boxShadow: shadowFloat,
    },
    overline: { ...type.overline },
    say: { ...type.body, color: c.text },
    step: { ...type.caption, color: c.dim },
    hint: { ...type.caption, color: c.faint, marginTop: 4 },
    row: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'flex-end', gap: 8, marginTop: 8 },
  });
