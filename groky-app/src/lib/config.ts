// Expo inlines EXPO_PUBLIC_* at build time, so these must be read with literal keys.
const env = {
  tokenUrl: process.env.EXPO_PUBLIC_TOKEN_URL ?? '',
  xaiKey: process.env.EXPO_PUBLIC_XAI_API_KEY ?? '',
  voiceModel: process.env.EXPO_PUBLIC_GROK_VOICE_MODEL || 'grok-voice-latest',
  voice: process.env.EXPO_PUBLIC_GROK_VOICE || 'eve',
  // Optional BCP-47 hint for ASR (e.g. "it", "ja", "es-MX"). Empty = auto-detect all Grok Voice languages.
  // Spanish/Portuguese need a regional tag; bare "es" / "pt" are ignored by the API.
  languageHint: (process.env.EXPO_PUBLIC_GROK_LANGUAGE_HINT ?? '').trim(),
  dogUrl: (process.env.EXPO_PUBLIC_DOG_URL ?? '').replace(/\/$/, ''),
  armUrl: (process.env.EXPO_PUBLIC_ARM_URL ?? '').replace(/\/$/, ''),
  bridgeKey: process.env.EXPO_PUBLIC_BRIDGE_KEY ?? '',
  visionModel: process.env.EXPO_PUBLIC_GROK_VISION_MODEL || 'grok-4.20-0309-non-reasoning',
  visionUrl: process.env.EXPO_PUBLIC_VISION_URL ?? '',
  // computer-vision server (../computer-vision/API_APP.md), used by DETECT for measured object positions
  visionServer: (process.env.EXPO_PUBLIC_VISION_SERVER ?? '').replace(/\/$/, ''),
  // which vision sources to list as cameras (the server publishes 'arm', and 'dog' if started with a dog camera)
  visionSources: (process.env.EXPO_PUBLIC_VISION_SOURCES || 'arm,webcam') as string,
  // teach server (../computer-vision-to-app/teach_server.py): demonstration video -> action tokens via vision/planner
  teachServer: (process.env.EXPO_PUBLIC_TEACH_SERVER ?? '').replace(/\/$/, ''),
  // robot-chooser (../robot-chooser): Settings picks arm / backend; LAN IP for Expo Go, e.g. http://192.168.1.50:8770
  chooserUrl: (process.env.EXPO_PUBLIC_CHOOSER_URL ?? '').replace(/\/$/, ''),
  // "Name=url,Name=url" - any url that returns a jpeg (bridge /camera.jpg, IP Webcam /shot.jpg, ...)
  cameras: process.env.EXPO_PUBLIC_CAMERAS ?? '',
};

export default env;

export const SAMPLE_RATE = 24000;
