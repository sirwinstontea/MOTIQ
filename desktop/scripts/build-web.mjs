/**
 * Export the Expo web app into desktop/renderer for Electron to load offline.
 * Falls back to a minimal placeholder if expo export is unavailable in this environment.
 */
import { spawnSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(__dirname, '..');
const groky = path.resolve(desktop, '..', 'groky-app');
const out = path.join(desktop, 'renderer');

fs.mkdirSync(out, { recursive: true });

const env = {
  ...process.env,
  EXPO_PUBLIC_ARM_URL: 'http://127.0.0.1:8080',
  EXPO_PUBLIC_BRIDGE_KEY: 'desktop-placeholder-key',
  EXPO_PUBLIC_VISION_SERVER: 'http://127.0.0.1:8000',
  EXPO_PUBLIC_TEACH_SERVER: 'http://127.0.0.1:8001',
  EXPO_PUBLIC_CHOOSER_URL: 'http://127.0.0.1:8771',
  EXPO_PUBLIC_VISION_SOURCES: 'arm,webcam',
};

console.log('[build-web] exporting Expo web from', groky);
const r = spawnSync(
  'npx',
  ['expo', 'export', '--platform', 'web', '--output-dir', out],
  { cwd: groky, env, encoding: 'utf8', shell: process.platform === 'win32' },
);

if (r.status !== 0) {
  console.warn('[build-web] expo export failed; writing placeholder UI');
  console.warn(r.stdout || '');
  console.warn(r.stderr || '');
  fs.writeFileSync(
    path.join(out, 'index.html'),
    `<!doctype html><html><head><meta charset="utf-8"/><title>MOTIQ</title>
    <style>body{font-family:system-ui;background:#04112b;color:#e8eefc;padding:2rem}
    code{background:#123;padding:.1rem .3rem;border-radius:4px}</style></head>
    <body><h1>MOTIQ Desktop</h1>
    <p>The Expo web bundle was not built in this environment.</p>
    <p>From a machine with Node 20+: <code>cd groky-app && npx expo export --platform web --output-dir ../desktop/renderer</code></p>
    <p>Local backends still start on loopback (bridge :8080, vision :8000, teach :8001, chooser :8771).</p>
    <script>window.__MOTIQ_DESKTOP__=window.__MOTIQ_DESKTOP__||{};</script>
    </body></html>`,
  );
  process.exit(0);
}

console.log('[build-web] wrote', out);
