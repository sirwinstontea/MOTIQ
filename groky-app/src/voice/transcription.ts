// Input-audio transcription for Grok Voice (hearing). The realtime voice model understands speech in every
// supported language with automatic detection; this block is what makes the app *see* that transcript so the UI
// and arm yes/no approval can react. Without `model: "grok-transcribe"`, streaming `…transcription.updated`
// events are not emitted (xAI Speech to Speech docs).
// Do not set language_hint by default: an unset hint keeps auto language detection for all supported languages.
// Optional EXPO_PUBLIC_GROK_LANGUAGE_HINT biases ASR when a demo is known to be monolingual.

/** Domain terms that Motiq demos mis-hear without a bias (EN + IT object / robot vocabulary). Max 100, ≤50 chars. */
export const TRANSCRIBE_KEYTERMS = [
  'MOTIQ',
  'Motion',
  'Grok',
  'UR5e',
  'orange screw',
  'blue bucket',
  'green ring',
  'white bucket lid',
  'delivery zone',
  'vite arancione',
  'secchio blu',
  'anello verde',
  'tappo bianco',
  'pinza',
  'robot arm',
  'gripper',
  'pick and place',
  'Show Motion',
  'full access',
  'safe mode',
];

export type TranscriptionConfig = {
  model: 'grok-transcribe';
  keyterms: string[];
  language_hint?: string;
};

/** Build the `audio.input.transcription` object for session.update. */
export function inputTranscription(languageHint?: string): TranscriptionConfig {
  const hint = languageHint?.trim();
  return {
    model: 'grok-transcribe',
    keyterms: TRANSCRIBE_KEYTERMS,
    ...(hint ? { language_hint: hint } : {}),
  };
}
