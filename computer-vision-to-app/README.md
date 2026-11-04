# computer-vision-to-app

Shows what the computer vision sees (boxes + track numbers) in the MOTIQ app's **Cameras** tab.
Works on Windows, macOS and Linux. Only Python 3.10+ is needed to start; the rest is installed for you.

```
camera (RealSense / webcam / none) ─▶ computer-vision server :8000 ─▶ app Cameras tab ("Arm vision")
                                          /frame/arm.jpg  (annotated)    └▶ Grok's "what do you see" + vision tools
```

## Quick start

From the repo root:

```bash
python computer-vision-to-app/run.py all
```

That installs the vision packages into `computer-vision/.venv` (first time only), writes the server address into
`groky-app/.env`, and starts the server. Then (re)start the app (`npx expo start`, Expo reads `.env` only at start)
and open **Cameras → Arm vision**.

No camera? Try it anyway with a fake scene: `python computer-vision-to-app/run.py all --mode synthetic`.

## Commands

| Command | What it does |
|---|---|
| `run.py setup` | create `computer-vision/.venv` and install packages (falls back to no RealSense driver if it can't install) |
| `run.py serve` | start the vision server. `--mode auto` (default: RealSense if present, else webcam), `realsense`, `webcam --device 1`, `synthetic`; `--port 8000`; `--dog-target <index or stream>` adds a second camera `dog` |
| `run.py env` | set `EXPO_PUBLIC_VISION_SERVER` in `groky-app/.env` (this computer's network address, or `--url http://host:8000`); `--sources arm,dog` to list both cameras. Only that setting is touched |
| `run.py check` | test a running server: health, one annotated frame per source, tracked objects |
| `run.py all` | setup if needed, then `env`, then `serve` |

## In the app

- `EXPO_PUBLIC_VISION_SERVER` = the server address. When it is set, the Cameras tab gets **Arm vision** (and
  **Dog vision** if you set `EXPO_PUBLIC_VISION_SOURCES=arm,dog` and started the server with `--dog-target`).
- If a robot's bridge URL is set (`EXPO_PUBLIC_ARM_URL`), its camera already shows the same annotated frame through
  the bridge, so no extra vision entry is added for that robot.
- "Show Motion" and Live view send the annotated frame to Grok, so it can refer to objects by their numbers.

## Learning a skill from the camera

**Skills → Learn from camera** (shown when `EXPO_PUBLIC_VISION_SERVER` is set): press Start, do the task by hand in
front of the arm camera, press Stop. The app polls `/state/arm`, works out which objects moved and where they ended
up (grouped by name + colour, so a hand hiding an object doesn't split it), shows that summary, and Grok writes a
skill from it (`groky-app/src/skills/fromVision.ts`). The draft opens in the skill editor, marked as a draft needing
review, with `skill.source.type = "vision"`. Nothing is saved or run until you save and approve it.

## Using a phone / tablet / another computer

Use the network address (what `run.py env` writes by default, e.g. `http://192.168.1.50:8000`), on the same Wi-Fi.
If the app can't load it, allow Python through the firewall for private networks. In a browser (Expo web) on the same
computer you can use `http://localhost:8000`.

## Troubleshooting

- **"No signal" in the app:** run `python computer-vision-to-app/run.py check`; if that passes, check
  `EXPO_PUBLIC_VISION_SERVER` is reachable from the device and the app was restarted after changing `.env`.
- **Camera busy:** only one program can use a camera. Close other apps (and `computer-vision/run_demo.bat`).
- **Install fails:** use Python 3.10-3.12 (newer versions may lack wheels for some packages).
- **Nothing tracked:** objects need the model weights in `computer-vision` and good light; boxes appear on the frame
  only for detected objects.
