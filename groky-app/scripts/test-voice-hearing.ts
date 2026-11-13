// Lightweight checks for multilingual hearing / approval (no Expo runtime).
// Run: npm run test:voice
import assert from 'node:assert/strict';
import {
  __resetForTests,
  currentPlan,
  isNo,
  isYes,
  normalizeUtterance,
  needsArmApproval,
  propose,
  userSaid,
} from '../src/lib/armApproval';
import { inputTranscription, TRANSCRIBE_KEYTERMS } from '../src/voice/transcription';

let failed = 0;
const check = (name: string, fn: () => void) => {
  try {
    fn();
    console.log(`ok  ${name}`);
  } catch (e: any) {
    failed += 1;
    console.error(`FAIL ${name}\n  ${e.message}`);
  }
};

check('simulation motion, paths and reset require approval; scene queries do not', () => {
  for (const tool of ['arm_push', 'arm_follow_path', 'arm_circle_object', 'arm_place_on_table',
    'arm_move_object_on_table', 'arm_reset_simulation_fault', 'arm_move_joint', 'run_skill'])
    assert.equal(needsArmApproval(tool), true, tool);
  for (const tool of ['arm_scene', 'arm_status', 'arm_get_speed', 'arm_set_speed', 'arm_stop'])
    assert.equal(needsArmApproval(tool), false, tool);
});

check('transcription enables grok-transcribe without language lock', () => {
  const t = inputTranscription('');
  assert.equal(t.model, 'grok-transcribe');
  assert.equal('language_hint' in t, false);
  assert.ok(Array.isArray(t.keyterms) && t.keyterms.length > 0);
  assert.ok(t.keyterms.length <= 100);
  assert.ok(TRANSCRIBE_KEYTERMS.every((k) => k.length <= 50));
});

check('optional language_hint is passed through', () => {
  assert.equal(inputTranscription('ja').language_hint, 'ja');
  assert.equal(inputTranscription('  es-MX ').language_hint, 'es-MX');
});

const yesSamples = [
  'yes',
  'Yeah!',
  'go ahead',
  'sì',
  'si',
  'va bene',
  'oui',
  "d'accord",
  'ja',
  'sí',
  'vale',
  'sim',
  'はい',
  '好的',
  '네',
  'हां',
  'نعم',
  'да',
  'evet',
  'ya',
  'vâng',
  'হ্যাঁ',
];

const noSamples = [
  'no',
  'Nope',
  'stop',
  'annulla',
  'non',
  'nein',
  'não',
  'いいえ',
  '不要',
  '아니요',
  'नहीं',
  'لا',
  'нет',
  'hayır',
  'không',
  'tidak',
  'না',
];

for (const s of yesSamples) {
  check(`isYes(${JSON.stringify(s)})`, () => {
    assert.equal(isYes(normalizeUtterance(s)), true, `expected yes: ${s}`);
    assert.equal(isNo(normalizeUtterance(s)), false, `yes should not be no: ${s}`);
  });
}

for (const s of noSamples) {
  check(`isNo(${JSON.stringify(s)})`, () => {
    assert.equal(isNo(normalizeUtterance(s)), true, `expected no: ${s}`);
  });
}

check('long yes-with-change is not an approval', () => {
  assert.equal(isYes(normalizeUtterance('yes, but put it in the green box instead please now')), false);
});

check('userSaid approves multilingual short yes', () => {
  __resetForTests();
  propose('pick the screw', ['pick']);
  assert.equal(userSaid('Oui!', 'voice'), 'approved');
  assert.equal(currentPlan()?.state, 'approved');
});

check('userSaid rejects multilingual no', () => {
  __resetForTests();
  propose('pick the screw', ['pick']);
  assert.equal(userSaid('Nein', 'typed'), 'rejected');
  assert.equal(currentPlan()?.state, 'rejected');
});

check('userSaid treats a new Japanese task as new_request', () => {
  __resetForTests();
  propose('pick the screw', ['pick']);
  assert.equal(userSaid('オレンジのネジを取って', 'voice'), 'new_request');
  assert.equal(currentPlan()?.state, 'expired');
});

if (failed) {
  console.error(`\n${failed} failed`);
  process.exit(1);
}
console.log('\nall voice-hearing checks passed');
