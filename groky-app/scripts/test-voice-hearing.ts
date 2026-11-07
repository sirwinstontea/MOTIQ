// Lightweight checks for multilingual hearing / approval (no Expo runtime).
// Run: npm run test:voice
import assert from 'node:assert/strict';
import {
  __resetForTests,
  currentPlan,
  isNo,
  isYes,
  normalizeUtterance,
  propose,
  userSaid,
} from '../src/lib/armApproval';
import { inputTranscription, TRANSCRIBE_KEYTERMS } from '../src/voice/transcription';
import { needsToolNudge, shouldSpeakText, textFromDone } from '../src/voice/turn';

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

check('a look request with no tool is nudged once', () => {
  assert.equal(needsToolNudge('what do you see on the table?', 'On it.', false), true);
  assert.equal(needsToolNudge('what do you see on the table?', 'On it.', true), false);
  assert.equal(needsToolNudge('guarda il tavolo', 'Va bene.', false), true);
});

check('a yes with no tool is nudged, a no is not', () => {
  assert.equal(needsToolNudge('yes', 'On it.', false), true);
  assert.equal(needsToolNudge('no', 'Okay.', false), false);
});

check('who are you is not a tool turn', () => {
  assert.equal(needsToolNudge('who are you?', "I'm Motion.", false), false);
});

check('a written answer with no audio is spoken, unless we are about to nudge', () => {
  assert.equal(shouldSpeakText("I'm Motion, I move the arm.", false, false), true);
  assert.equal(shouldSpeakText("I'm Motion.", true, false), false);
  assert.equal(shouldSpeakText('On it.', false, true), false);
  assert.equal(shouldSpeakText('  ', false, false), false);
});

check('response.done text is recovered when no delta arrived', () => {
  assert.equal(
    textFromDone({ response: { output: [{ content: [{ transcript: 'I see the orange screw.' }] }] } }),
    'I see the orange screw.',
  );
  assert.equal(textFromDone({}), '');
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
