// Expo inlines EXPO_PUBLIC_* at build time, so these must be read with literal keys.
// On Motiq Desktop (Electron), window.__MOTIQ_DESKTOP__ overrides loopback URLs at runtime.
// Fields are getters so a late inject (did-finish-load / first-run save) is visible to callers.
import { desktopEnv } from './desktopConfig';

const d = (key: Parameters<typeof desktopEnv>[0], fallback: string) => {
  const v = desktopEnv(key);
  return v || fallback;
};

const env = {
  get tokenUrl() {
    return process.env.EXPO_PUBLIC_TOKEN_URL ?? '';
  },
  get xaiKey() {
    return d('EXPO_PUBLIC_XAI_API_KEY', process.env.EXPO_PUBLIC_XAI_API_KEY ?? '');
  },
  get voiceModel() {
    return process.env.EXPO_PUBLIC_GROK_VOICE_MODEL || 'grok-voice-latest';
  },
  get voice() {
    return process.env.EXPO_PUBLIC_GROK_VOICE || 'eve';
  },
  // Optional BCP-47 hint for ASR (e.g. "it", "ja", "es-MX"). Empty = auto-detect all Grok Voice languages.
  get languageHint() {
    return (process.env.EXPO_PUBLIC_GROK_LANGUAGE_HINT ?? '').trim();
  },
  get dogUrl() {
    return (process.env.EXPO_PUBLIC_DOG_URL ?? '').replace(/\/$/, '');
  },
  get armUrl() {
    return d('EXPO_PUBLIC_ARM_URL', process.env.EXPO_PUBLIC_ARM_URL ?? '').replace(/\/$/, '');
  },
  get bridgeKey() {
    return d('EXPO_PUBLIC_BRIDGE_KEY', process.env.EXPO_PUBLIC_BRIDGE_KEY ?? '');
  },
  get visionModel() {
    return process.env.EXPO_PUBLIC_GROK_VISION_MODEL || 'grok-4.20-0309-non-reasoning';
  },
  get visionUrl() {
    return process.env.EXPO_PUBLIC_VISION_URL ?? '';
  },
  get visionServer() {
    return d('EXPO_PUBLIC_VISION_SERVER', process.env.EXPO_PUBLIC_VISION_SERVER ?? '').replace(/\/$/, '');
  },
  get visionSources() {
    return (d('EXPO_PUBLIC_VISION_SOURCES', process.env.EXPO_PUBLIC_VISION_SOURCES || 'arm,webcam') ||
      'arm,webcam') as string;
  },
  get teachServer() {
    return d('EXPO_PUBLIC_TEACH_SERVER', process.env.EXPO_PUBLIC_TEACH_SERVER ?? '').replace(/\/$/, '');
  },
  get chooserUrl() {
    return d('EXPO_PUBLIC_CHOOSER_URL', process.env.EXPO_PUBLIC_CHOOSER_URL ?? '').replace(/\/$/, '');
  },
  get cameras() {
    return process.env.EXPO_PUBLIC_CAMERAS ?? '';
  },
};

export default env;

export const SAMPLE_RATE = 24000;
