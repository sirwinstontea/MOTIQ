// Desktop (Electron) injects loopback URLs + bridge key at runtime via window.__MOTIQ_DESKTOP__.
// Expo Go / web without Electron ignore this and keep EXPO_PUBLIC_* build-time values.

type DesktopCfg = Partial<{
  EXPO_PUBLIC_ARM_URL: string;
  EXPO_PUBLIC_BRIDGE_KEY: string;
  EXPO_PUBLIC_VISION_SERVER: string;
  EXPO_PUBLIC_TEACH_SERVER: string;
  EXPO_PUBLIC_CHOOSER_URL: string;
  EXPO_PUBLIC_XAI_API_KEY: string;
  EXPO_PUBLIC_VISION_SOURCES: string;
  MOTIQ_DESKTOP: string;
}>;

declare global {
  // eslint-disable-next-line no-var
  var __MOTIQ_DESKTOP__: DesktopCfg | undefined;
  interface Window {
    __MOTIQ_DESKTOP__?: DesktopCfg;
    motiqDesktop?: {
      getConfig: () => Promise<DesktopCfg & { firstRunComplete?: boolean; backend?: string; visionMode?: string; robodkHint?: string }>;
      saveFirstRun: (p: { xaiApiKey?: string; backend?: string; visionMode?: string }) => Promise<DesktopCfg>;
      pickRobotFile: () => Promise<string | null>;
      openExternal: (url: string) => Promise<void>;
    };
  }
}

export function isMotiqDesktop(): boolean {
  try {
    if (typeof window !== 'undefined' && window.__MOTIQ_DESKTOP__?.MOTIQ_DESKTOP === '1') return true;
    if (typeof window !== 'undefined' && window.motiqDesktop) return true;
  } catch {
    /* ignore */
  }
  return false;
}

export function desktopEnv(key: keyof DesktopCfg): string {
  try {
    const cfg = (typeof window !== 'undefined' && (window.__MOTIQ_DESKTOP__ || globalThis.__MOTIQ_DESKTOP__)) || {};
    const v = cfg[key];
    return typeof v === 'string' ? v : '';
  } catch {
    return '';
  }
}
