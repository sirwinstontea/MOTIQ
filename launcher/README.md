# launcher

The MOTIQ setup wizard, settings screen and service manager. It's the same code whether you run it from a checkout
(`MOTIQ.bat`, `./motiq.sh`, `python launcher/motiq.py`) or as `MOTIQ.exe` from the Windows installer.

```bash
python launcher/motiq.py            # first run: wizard; afterwards: start MOTIQ and open the app
python launcher/motiq.py --setup    # open Settings
python launcher/motiq.py --stop     # stop every MOTIQ service
python launcher/motiq.py --no-window   # don't open windows (prints the address)
```

The launcher itself needs only the Python standard library (3.10+). It serves its UI on `http://127.0.0.1:8790`, which
is loopback only because it holds API keys and starts processes. It opens the UI and the app as Edge or Chrome app
windows, or as browser tabs when neither is installed.

## What the wizard does

1. **Asks**, one question per screen, with Back and Next and a step list in the sidebar. The questions are: robot arm
   (from `robot-chooser/catalog.yaml`), how it runs (just the app / simulation / RoboDK / real UR arm), robot IP,
   driver file and safety check (real arm only), tool type and size, camera and its mount, AI key, language, device
   (this PC / phone / both) and speed limit. **I don't know yet** saves a demo config: the app alone, with its
   simulated arm.
2. **Installs** what that answer set needs and skips anything already done (`.motiq/provisioned.json`):
   - uv, then Python 3.12, `robot_controller/.venv` (`-e .[dev,robodk]`) and `computer-vision/.venv` (the three
     `vision/*/requirements.txt` files, retried without RealSense if that wheel is missing)
   - Node.js 20+ (a portable copy in `.motiq/tools/node` if none is on PATH), then `npm install` in `groky-app`
3. **Configures** the files MOTIQ already reads: `groky-app/.env`, `computer-vision/.env` and, through the
   `robot_chooser` API, the arm, backend, speed and tool (`robot-chooser/hardware.yaml` and the generated controller
   configs).
4. **Starts** robot-chooser, the vision server, the teach server, the controller, the arm bridge and Expo, then opens
   the app. This is `bridge/start_stack.sh` (non-SIM path) ported to Python so it runs on Windows. With RoboDK it
   starts RoboDK first and builds the station if it's missing.

In every simulation mode the bridge gets `MOTIQ_UR_JOG` pointing at a file that doesn't exist, so it never loads the
physical UR driver, even when `../ur_manual/ur_jog.py` is present. Only *Real robot arm* passes your `ur_jog.py` and
`MOTIQ_UR_HOST`.

## Files

| Path | Purpose |
|---|---|
| `motiq.py` | Entry point: single instance, auto-start when configured, exits when idle |
| `motiq_launcher/config.py` | Answers (`.motiq/config.json`), defaults, the demo preset, `plan()` (services, backend, bind address) |
| `motiq_launcher/detect.py` | Catalog robots, cameras (Windows PnP), RoboDK, Node, uv |
| `motiq_launcher/provision.py` | Install steps with progress, each skipped when its inputs haven't changed |
| `motiq_launcher/configure.py` | Writes the `.env` files and drives robot-chooser |
| `motiq_launcher/stack.py` | Starts, health-checks and stops the services; logs in `.motiq/logs/` |
| `motiq_launcher/server.py` | HTTP API for the UI |
| `motiq_launcher/window.py` | App windows (Edge/Chrome `--app`) |
| `ui/` | Wizard UI. `groky.css` is the Groky design system copied verbatim, `launcher.css` the layout on top, `questions.js` the questions |

`.motiq/` holds the answers, including API keys, plus logs and downloaded tools. It's gitignored.
