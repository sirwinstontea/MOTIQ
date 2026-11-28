/**
 * MOTIQ desktop shell: starts the local Python stack, then loads the Expo web UI.
 * Unsigned Electron build — SmartScreen / Gatekeeper may warn.
 */
const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { spawn } = require('child_process');
const Store = require('electron-store');

const store = new Store({ name: 'motiq-config' });
let mainWindow = null;
let stackProc = null;

function motiqRoot() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'motiq');
  }
  return path.resolve(__dirname, '..', '..');
}

function ensureBridgeKey() {
  let key = store.get('bridgeKey');
  if (!key || String(key).length < 8) {
    key = crypto.randomBytes(16).toString('hex');
    store.set('bridgeKey', key);
  }
  return key;
}

function pythonCandidates() {
  const root = motiqRoot();
  const win = process.platform === 'win32';
  const venvRc = win
    ? path.join(root, 'robot_controller', '.venv', 'Scripts', 'python.exe')
    : path.join(root, 'robot_controller', '.venv', 'bin', 'python');
  const venvCv = win
    ? path.join(root, 'computer-vision', '.venv', 'Scripts', 'python.exe')
    : path.join(root, 'computer-vision', '.venv', 'bin', 'python');
  return [venvRc, venvCv, process.env.MOTIQ_PYTHON, 'python3', 'python'].filter(Boolean);
}

function findPython() {
  for (const c of pythonCandidates()) {
    if (c === 'python3' || c === 'python') return c;
    if (fs.existsSync(c)) return c;
  }
  return 'python3';
}

function startStack() {
  if (stackProc) return;
  const root = motiqRoot();
  const key = ensureBridgeKey();
  const script = app.isPackaged
    ? path.join(root, 'start_stack.py')
    : path.join(__dirname, '..', 'scripts', 'start_stack.py');
  const py = findPython();
  const backend = store.get('backend', 'robodk_sim');
  const visionMode = store.get('visionMode', 'synthetic');
  const env = {
    ...process.env,
    MOTIQ_ROOT: root,
    BRIDGE_KEY: key,
    BACKEND: backend === 'mock' ? 'mock' : 'robodk_sim',
    VISION_MODE: visionMode,
    PYTHONUNBUFFERED: '1',
  };
  const xai = store.get('xaiApiKey', '');
  if (xai) {
    env.XAI_API_KEY = xai;
    env.EXPO_PUBLIC_XAI_API_KEY = xai;
  }

  stackProc = spawn(py, [script], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  stackProc.stdout.on('data', (d) => console.log(String(d).trimEnd()));
  stackProc.stderr.on('data', (d) => console.error(String(d).trimEnd()));
  stackProc.on('exit', (code) => {
    console.log('[stack] exited', code);
    stackProc = null;
  });
}

function stopStack() {
  if (!stackProc) return;
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(stackProc.pid), '/T', '/F']);
    } else {
      process.kill(-stackProc.pid, 'SIGTERM');
    }
  } catch (_) {
    try {
      stackProc.kill('SIGTERM');
    } catch (__) {}
  }
  stackProc = null;
}

function uiUrl() {
  // Packaged: load exported Expo web. Dev: prefer built renderer, else Expo web if running.
  const renderer = path.join(__dirname, '..', 'renderer', 'index.html');
  if (fs.existsSync(renderer)) {
    return { type: 'file', path: renderer };
  }
  return { type: 'url', path: process.env.MOTIQ_UI_URL || 'http://127.0.0.1:8081' };
}

function injectDesktopConfig() {
  const key = ensureBridgeKey();
  const xai = store.get('xaiApiKey', '') || '';
  return {
    EXPO_PUBLIC_ARM_URL: 'http://127.0.0.1:8080',
    EXPO_PUBLIC_BRIDGE_KEY: key,
    EXPO_PUBLIC_VISION_SERVER: 'http://127.0.0.1:8000',
    EXPO_PUBLIC_TEACH_SERVER: 'http://127.0.0.1:8001',
    EXPO_PUBLIC_CHOOSER_URL: 'http://127.0.0.1:8771',
    EXPO_PUBLIC_XAI_API_KEY: xai,
    EXPO_PUBLIC_VISION_SOURCES: 'arm,webcam',
    MOTIQ_DESKTOP: '1',
  };
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 640,
    title: 'MOTIQ',
    backgroundColor: '#04112b',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  const target = uiUrl();
  if (target.type === 'file') {
    await mainWindow.loadFile(target.path);
  } else {
    try {
      await mainWindow.loadURL(target.path);
    } catch (e) {
      const html = `<html><body style="font-family:system-ui;padding:2rem;background:#04112b;color:#e8eefc">
        <h1>MOTIQ</h1>
        <p>UI bundle not built yet. Run <code>npm run build:web</code> in desktop/, or start Expo web on :8081.</p>
        <p>${String(e.message || e)}</p>
        <p>Local stack is starting with loopback backends. Configure your API key in the first-run wizard.</p>
      </body></html>`;
      await mainWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));
    }
  }

  mainWindow.webContents.on('did-finish-load', () => {
    const cfg = injectDesktopConfig();
    mainWindow.webContents.executeJavaScript(
      `window.__MOTIQ_DESKTOP__ = ${JSON.stringify(cfg)};
       window.dispatchEvent(new Event('motiq-desktop-config'));`,
    );
  });
}

ipcMain.handle('motiq:get-config', () => ({
  ...injectDesktopConfig(),
  firstRunComplete: !!store.get('firstRunComplete'),
  backend: store.get('backend', 'robodk_sim'),
  visionMode: store.get('visionMode', 'synthetic'),
  robodkHint: 'Install RoboDK separately and leave it running (API port 20500) for robodk_sim.',
}));

ipcMain.handle('motiq:save-first-run', (_e, payload) => {
  if (payload?.xaiApiKey) store.set('xaiApiKey', String(payload.xaiApiKey).trim());
  if (payload?.backend) store.set('backend', payload.backend === 'mock' ? 'mock' : 'robodk_sim');
  if (payload?.visionMode) store.set('visionMode', payload.visionMode);
  store.set('firstRunComplete', true);
  // restart stack with new env
  stopStack();
  startStack();
  return injectDesktopConfig();
});

ipcMain.handle('motiq:pick-robot-file', async () => {
  const r = await dialog.showOpenDialog(mainWindow, {
    title: 'Import RoboDK robot',
    filters: [{ name: 'RoboDK', extensions: ['robot', 'rdk'] }],
    properties: ['openFile'],
  });
  if (r.canceled || !r.filePaths[0]) return null;
  return r.filePaths[0];
});

ipcMain.handle('motiq:open-external', (_e, url) => {
  if (typeof url === 'string' && /^https?:/i.test(url)) shell.openExternal(url);
});

app.whenReady().then(async () => {
  startStack();
  await createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  stopStack();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => stopStack());
