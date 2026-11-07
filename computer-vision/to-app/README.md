# computer-vision/to-app

Connects the vision stack to the MOTIQ app with one command, and hosts the **teach server** that turns demonstration videos into action tokens. Works on Windows, macOS and Linux. Starting it only needs Python 3.10+. The rest is installed for you.

```text
camera ──▶ computer-vision :8000 ──▶ app Cameras tab, Grok vision tools
                  /frame/arm.jpg

Show Motion ──▶ teach server :8001 ──▶ vision/planner tokens ──▶ app + Grok ──▶ skill.json ──▶ Skills tab
```

## Quick start

From the repo root:

```bash
python computer-vision/to-app/run.py all
python computer-vision/to-app/run.py all --mode synthetic     # no camera
```

This installs packages into `computer-vision/.venv` the first time, writes the server addresses into `groky-app/.env`, then starts the vision and teach servers. Restart the app with `npx expo start -c` and open **Cameras**.

## Commands

| Command | What it does |
|---|---|
| `run.py setup` | Create `computer-vision/.venv` and install packages. Falls back to no RealSense driver if it can't install. |
| `run.py serve` | Start the vision server. See the options below. |
| `run.py teach` | Start the teach server. `--teach-port 8001` |
| `run.py env` | Write `EXPO_PUBLIC_VISION_SERVER` and `EXPO_PUBLIC_TEACH_SERVER` to `groky-app/.env`. Options: `--url`, `--port`, `--teach-port`, `--sources arm,webcam`. No other setting is touched. |
| `run.py check` | Test a running server: health, one annotated frame per source, tracked objects |
| `run.py all` | Setup if needed, then `env`, the teach server and `serve` |

Options for `serve` and `all`: `--mode auto|realsense|pybullet|webcam|synthetic|replay|stream`, `--device`, `--port 8000`, `--host`, plus extra arguments passed to the vision server.

## In the app

- `EXPO_PUBLIC_VISION_SERVER` adds the vision cameras, filtered by `EXPO_PUBLIC_VISION_SOURCES` (default `arm,webcam`). The `webcam` source appears only in RealSense mode.
- If `EXPO_PUBLIC_ARM_URL` is set, the arm camera already shows the same annotated frame through the bridge.
- Show Motion and live view send the annotated frame to Grok, so it can refer to objects by number.

## Teach server

[`teach_server.py`](teach_server.py) reads video and calls Grok. It never runs a robot.

| Route | Body | Result |
|---|---|---|
| `GET /health` | | `{ok, mock}` |
| `POST /teach?goal=<text>` | Video file of up to 200 MB | `{ok, summary, tokens}` |
| `POST /teach_frames` | `{frames: [base64 jpeg], fps, audio?, audio_mime?, goal?}` | Same as `/teach` |
| `GET /skills` | | Learned skills on this PC |
| `POST /skills` | Skill document | Saved to `computer-vision/data/skills/learned/<id>.json` |
| `DELETE /skills/<id>` | | Removes the PC copy |

One video is processed at a time. There is no authentication, so run it on a trusted network.

`XAI_API_KEY` comes from `computer-vision/.env`, falling back to `EXPO_PUBLIC_XAI_API_KEY` in `groky-app/.env`. With no key it runs Grok in mock mode, which is only useful for testing.

## Ways to teach a skill

| Method | Recording | Path |
|---|---|---|
| **Show Motion**, tablet camera | Real video with sound via `expo-camera` | `POST /teach` |
| **Show Motion**, JPEG camera | Frames at about 5 fps + tablet mic, joined with ffmpeg | `POST /teach_frames` |
| **Upload video** | An existing recording | `POST /teach` |
| **Learn from camera** | The app polls `/state/arm` and works out what moved | No teach server. See `groky-app/src/skills/fromVision.ts`. |

Each path ends the same way: the app has Grok write a skill using only the arm's objects and destinations, saves it as a draft and shows it in Skills. Motion then explains the plan and waits for your yes. On the arm, DETECT moves the head camera until it sees the object, PICK and PLACE are one checked controller task each, and VERIFY looks at the destination.

Learned skills are kept on the PC as well, and the app loads any it doesn't have when it starts. Deleting a skill in the app deletes the PC copy.

## Using a phone or tablet

Use the network address that `run.py env` writes, such as `http://192.168.1.50:8000`, on the same Wi-Fi. If the app can't reach it, allow Python through the firewall for private networks.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No signal" in the app | Run `run.py check`. If it passes, check the URL from the device and restart Expo after changing `.env`. |
| Camera busy | Only one program can use a camera. Close `run_demo.bat` and other apps. |
| Install fails | Use Python 3.10–3.12. |
| Nothing tracked | Check the model weights in `computer-vision/data/models` and the light. |
| Port 8001 in use | Use `--teach-port`, and update `EXPO_PUBLIC_TEACH_SERVER`. |

## Files

| File | Purpose |
|---|---|
| [`run.py`](run.py) | Setup, env wiring, serve, teach and check, standard library only |
| [`teach_server.py`](teach_server.py) | Video and frames to tokens, plus the learned skill store |
