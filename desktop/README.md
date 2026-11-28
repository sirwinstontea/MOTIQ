# MOTIQ Desktop

Installable workstation for Windows, macOS and Linux. Runs the Motiq UI and the local stack (bridge, vision **inference**, teach, controller, robot-chooser) on **your PC**. Default motion backend: **RoboDK simulation**.

Unsigned builds for now (SmartScreen / Gatekeeper may warn).

## What you get

1. **Install Motiq** — `.exe` / `.dmg` / AppImage via `electron-builder` (see below).
2. **Your objects** — Settings → Objects: name items for YOLO-World (no training required). Optional Colab ONNX later.
3. **Any RoboDK arm** — Robots → **Import RoboDK**: pick a `.robot` / `.rdk`; Motiq fits the demo cell and marks the arm READY for `robodk_sim`.
4. **Voice / video → sim** — Talk or Show Motion; tasks execute as named moves in RoboDK.

CV **training** is **not** installed (use Colab notebooks under `computer-vision/training/` if you want a fine-tuned detector).

## You must provide

| Dependency | Required? | Notes |
|---|---|---|
| **RoboDK** | For default sim | Install from [robodk.com](https://robodk.com/); leave GUI running (API `:20500`). Linux: `robot_controller/docs/ROBODK_LINUX_SETUP.md` |
| **xAI / OpenRouter API key** | For voice / teach LLM | First-run wizard |
| **Python 3.10–3.12 + venvs** | Dev / unpackaged | `robot_controller/.venv`, `computer-vision/.venv` (packaged builds ship a portable runtime when bundled) |
| **RoboDK `.robot` / library station** | To import “any” arm | Must already be on disk / in RoboDK |
| **Webcam / RealSense** | Optional | First-run camera mode; synthetic works without a camera |
| **Labeled dataset + Colab** | Optional | Only to replace open-vocab with a custom ONNX |

Physical arms need a separate manufacturer driver/adapter (out of scope for this desktop path).

## Develop

```bash
# 1. Create venvs (once)
cd robot_controller && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && cd ..
python3 computer-vision-to-app/run.py setup   # or computer-vision/setup.sh

# 2. Desktop shell
cd desktop && npm install
npm start                    # starts stack + Electron
# or: MOTIQ_UI_URL=http://127.0.0.1:8081 npm start   with Expo web running
```

Cross-platform stack without Electron:

```bash
BRIDGE_KEY=dev-secret-key BACKEND=robodk_sim VISION_MODE=synthetic \
  python3 desktop/scripts/start_stack.py
```

## Build installers (unsigned)

```bash
cd desktop
npm install
npm run build:web    # Expo web → renderer/ (placeholder if export unavailable)
npm run dist:win     # MotiqSetup-*.exe
npm run dist:mac     # .dmg
npm run dist:linux   # AppImage
```

Installer extracts runtimes into the app directory and does not leave training notebooks or Metro on the machine. Packaged trees **exclude** `.env` / `.env.*` so local API keys are never baked into the installer. The desktop stack binds vision, teach, chooser, controller and bridge to **127.0.0.1** only.

## Import any RoboDK arm (CLI)

```bash
# RoboDK must be running
cd robot-chooser
../robot_controller/.venv/bin/python -m robot_chooser import-robot \
  --source ../UR3e.rdk --brand "Universal Robots" --model UR3e --select
```

Or from the app: **Robots → Import RoboDK**.

## Limits (honest)

- Simulation only for general “any robot” claims.
- Auto-layout may fail for odd kinematics; Motiq reports `LAYOUT_FAILED` instead of silently using UR5e.
- App 2D preview remains a UR-shaped sketch; trust RoboDK for reach.
- Picks still use taught targets (demo cell), not live camera XYZ.
