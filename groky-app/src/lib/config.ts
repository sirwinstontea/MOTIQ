// Expo inlines EXPO_PUBLIC_* at build time, so these must be read with literal keys.
const env = {
  tokenUrl: process.env.EXPO_PUBLIC_TOKEN_URL ?? '',
  xaiKey: process.env.EXPO_PUBLIC_XAI_API_KEY ?? '',
  voiceModel: process.env.EXPO_PUBLIC_GROK_VOICE_MODEL || 'grok-voice-latest',
  voice: process.env.EXPO_PUBLIC_GROK_VOICE || 'eve',
  dogUrl: (process.env.EXPO_PUBLIC_DOG_URL ?? '').replace(/\/$/, ''),
  armUrl: (process.env.EXPO_PUBLIC_ARM_URL ?? '').replace(/\/$/, ''),
  bridgeKey: process.env.EXPO_PUBLIC_BRIDGE_KEY ?? '',
  visionModel: process.env.EXPO_PUBLIC_GROK_VISION_MODEL || 'grok-4.7',
  visionUrl: process.env.EXPO_PUBLIC_VISION_URL ?? '',
  // "Name=url,Name=url" - any url that returns a jpeg (bridge /camera.jpg, IP Webcam /shot.jpg, ...)
  cameras: process.env.EXPO_PUBLIC_CAMERAS ?? '',
};

export default env;

export const SAMPLE_RATE = 24000;
