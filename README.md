# MOTIQ

**Tell a robot what to do — or show it once — and it does it, checks its own work, and remembers the task as a reusable skill.**

MOTIQ is an end-to-end stack for natural-language and demonstration-driven robot control. A tablet app (Expo) talks to a Grok voice agent (**Motion**). Motion calls robot tools over HTTP. A small **bridge** turns those tools into typed, safety-gated controller tasks. A **computer-vision** server detects and tracks objects. A **teach** path turns a short demonstration video into a `skill.json`. Everything is designed so you can run it in simulation first, then point the same contracts at different arms, cameras, and LLM backends.

Built for the **SpaceX AI (xAI) Grok Hackathon**, Ctrl+Alt Museum, Pavia, 4–5 November 2026.

---

## Table of contents

1. [What you get](#1-what-you-get)
2. [Repository layout](#2-repository-layout)
3. [Architecture](#3-architecture)
4. [End-to-end workflows](#4-end-to-end-workflows)
5. [Modularity and scalability](#5-modularity-and-scalability-any-model-any-cell)
6. [Prerequisites](#6-prerequisites)
7. [Quick start (app only)](#7-quick-start-app-only)
8. [Full stack runbook](#8-full-stack-runbook)
9. [Ports, URLs, and environment](#9-ports-urls-and-environment)
10. [Skills format and safety](#10-skills-format-and-safety)
11. [Approval model](#11-approval-model)
12. [robot-chooser (any arm / any hardware)](#12-robot-chooser-any-arm--any-hardware)
13. [Testing](#13-testing)
14. [Troubleshooting](#14-troubleshooting)
15. [Where to read next](#15-where-to-read-next)
16. [Status and limits](#16-status-and-limits)

---

## 1. What you get

| Capability | What happens |
|---|---|
| **Talk to robots** | Say *"put the orange screw in the delivery zone"* or *"dog, walk forward and sit"*. Motion picks tools and drives the arm and/or dog. |
| **See before and after** | Cameras + YOLO tracking number objects on screen. Picks search with the eye-in-hand camera. VERIFY checks the result. |
| **Learn by watching** | **Show Motion** (15 s video + voice) or **Upload video** / **Learn from camera** → planner tokens → Grok writes a MOTIQ `skill.json`. |
| **Remember skills** | Voice tasks and demos become skills. Edit, dry-run, preview, approve, run again. Learned skills also sync to the PC. |
| **Stay gated** | Controller allowlists actions, checks reach/workspace/speed, latches faults. Voice waits for your **yes/no** before arm motion. Skills need Skills-tab approval for real hardware (unless full access). |
| **Simulate first** | Same task JSON on `mock`, RoboDK, or URSim+RoboDK sync. Physical backends are refused in the simulation-oriented build. |

---

## 2. Repository layout

```
MOTIQ/
├── groky-app/                  # Expo / React Native app (tablet, phone, web)
├── bridge/                     # App tool protocol → robot_controller tasks
├── robot_controller/           # Typed, safety-gated controller + backends
├── computer-vision/            # Detection, tracking, Grok vision tools, video planner
├── computer-vision-to-app/     # One-command vision + teach wiring into the app
├── robot-chooser/              # Pick any arm + describe any cell; regenerate controller config
└── README.md                   # This file
```

| Folder | Role | Default ports |
|---|---|---|
| [`groky-app/`](groky-app) | UI + Grok Voice session + skills runner + cameras | Expo Metro (e.g. 8081), web preview |
| [`bridge/`](bridge) | `POST /tools/*` → controller; vision state → pick targets; proxies arm camera | **8080** |
| [`robot_controller/`](robot_controller) | Task FSM, safety, mock / RoboDK / URSim backends | **8765** (loopback API) |
| [`computer-vision/`](computer-vision) | `vision/eyes` server, `vision/grok` tools, `vision/planner` (video → tokens/skills) | **8000** |
| [`computer-vision-to-app/`](computer-vision-to-app) | `run.py` (setup/serve/env) + `teach_server.py` (Show Motion → skill) | teach **8001** |
| [`robot-chooser/`](robot-chooser) | Catalog + hardware components → `robot_controller/config/*.yaml` | UI/API **8770** |

Each folder has its own README with deeper detail. This document is the **operator map** for the whole system.

---

## 3. Architecture

### 3.1 Big picture

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  Human (tablet / browser)                                                │
 │    voice / typed chat / Show Motion / Skills UI                          │
 └───────────────────────────────┬──────────────────────────────────────────┘
                                 │
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  groky-app (Expo)                                                        │
 │    Motion = Grok realtime voice                                          │
 │    tools: arm_*, dog_*, camera_look, run_skill, propose_plan, …        │
 │    skills: validate → dry-run → run (DETECT / PICK / PLACE / VERIFY)     │
 └───────────────┬───────────────────────────────┬──────────────────────────┘
                 │ EXPO_PUBLIC_ARM_URL             │ EXPO_PUBLIC_VISION_SERVER
                 │ EXPO_PUBLIC_BRIDGE_KEY           │ EXPO_PUBLIC_TEACH_SERVER
                 ▼                                 ▼
 ┌───────────────────────────┐     ┌────────────────────────────────────────┐
 │  bridge :8080             │     │  computer-vision :8000                 │
 │  motiq_bridge.py          │◄────│  /state/arm  /frame/arm.jpg            │
 │  app tools → controller   │     │  YOLO + track + Grok voice tools       │
 │  writes vision_bridge.json│     └─────────────────┬──────────────────────┘
 └─────────────┬─────────────┘                       │
               │ localhost:8765                        │ tokenize / teach
               ▼                                       ▼
 ┌───────────────────────────┐     ┌────────────────────────────────────────┐
 │  robot_controller :8765   │     │  teach_server :8001                    │
 │  allowlisted AI actions   │     │  video/frames → vision/planner         │
 │  mock | robodk_sim |      │     │  → tokens → Grok → skill.json          │
 │  ursim_sync               │     │  stores learned skills on PC           │
 └─────────────┬─────────────┘     └────────────────────────────────────────┘
               ▼
      mock  ·  RoboDK UR5e twin  ·  URSim (Docker, loopback)
```

### 3.2 Contracts (why the pieces stay swappable)

| Boundary | Contract | Meaning |
|---|---|---|
| App ↔ robots | HTTP bridge: `GET /health`, `POST /tools/<name>`, `POST /tools/stop`, optional `GET /camera.jpg` | Any robot laptop can implement this; empty URL = in-app simulator. |
| App ↔ vision | [`computer-vision/API_APP.md`](computer-vision/API_APP.md): `/health`, `/frame/{source}.jpg`, `/state/{source}`, `/voice/*` | App does not embed YOLO. |
| App ↔ teach | `POST /teach`, `POST /teach_frames`, `GET/POST/DELETE /skills` | Demo video in → `skill.json` out. |
| Bridge ↔ controller | Task JSON ([`AI_ACTION_CONTRACT.md`](robot_controller/docs/AI_ACTION_CONTRACT.md)) | High-level actions only — no raw joints/scripts from the LLM path. |
| Planner / skills ↔ arm | Closed primitive catalog + MOTIQ skill schema 1.1.0 | Skills are data, not generated code. |
| Cell setup ↔ controller | YAML from **robot-chooser** (`robot.yaml`, `scene.yaml`, `safety.yaml`, `selection.yaml`) | New arm/device = YAML, not a rewrite of the app. |

### 3.3 Data that moves

- **Voice turn:** audio ↔ Grok realtime → tool calls → bridge → controller → result → Motion speaks.
- **Vision frame:** camera → eyes pipeline → annotated JPEG + `FramePayload` (ids, labels, colours, optional millimetre coords after calibration).
- **Pick:** bridge reads `/state/arm`, maps tracks → scene object IDs via `class_labels`, writes `robot_controller/runs/vision_bridge.json`, submits `identify` / `pick` / `place` tasks.
- **Show Motion:** 15 s WebM/MP4 (+ audio) or JPEG sequence → teach server remuxes to MP4 → `vision/planner` tokenize → Grok skill draft → Skills tab + optional Motion `propose_plan` + your yes.

---

## 4. End-to-end workflows

### 4.1 Voice command (arm)

1. Open **Talk**, start listening.
2. Say e.g. *"Put the orange screw in the blue bucket."*
3. Motion plans, calls `propose_plan`, asks you out loud.
4. You say **yes** or **no** (typed works too). No separate Approve card — spoken/typed yes/no is enough.
5. On yes, Motion calls `arm_pick_and_place` (or `run_skill`). Bridge expands to controller actions. Vision confirms object visibility when needed.
6. Afterward Motion can `camera_look` / verify and report.

### 4.2 Show Motion → skill → run

```
Show Motion (15 s)
    → groky-app records video (+ mic)
    → POST teach_server :8001 (/teach or /teach_frames)
    → ffmpeg normalise to MP4
    → vision/planner tokenize (keyframes + Grok vision + STT)
    → Grok writes MOTIQ skill.json (draft, human_review_required)
    → saved in app Skills + computer-vision/data/skills/learned/
    → Motion summarises and propose_plan
    → your yes → run_skill
```

### 4.3 Learn from camera (track motion, no full video planner)

With `EXPO_PUBLIC_VISION_SERVER` set: **Skills → Learn from camera** polls `/state/arm`, infers what moved, asks Grok for a skill draft. Opens in the editor as draft.

### 4.4 Skills tab run

Tap **Run** on a skill card (that tap is intentional). Real hardware still requires Skills-tab **Approve + real robot** unless **full access** is on. Dry-run / preview never move hardware.

### 4.5 Dog

If `EXPO_PUBLIC_DOG_URL` points at a dog bridge implementing the same tool protocol (`dog_walk`, `dog_turn`, `dog_pose`, …), Motion can drive it in the same conversation. Empty URL = simulated dog in the app.

---

## 5. Modularity and scalability (any model, any cell)

MOTIQ is built as **replaceable layers** behind stable contracts. You should not need to rewrite the tablet app to change the arm, the LLM host, or the detector weights.

### 5.1 Any arm / robot model (robot-chooser)

[`robot-chooser/`](robot-chooser) is the scalability surface for hardware:

| You want… | You do… | Code change? |
|---|---|---|
| Another catalog arm (UR3e, xArm, Elite, …) | Add/select in `catalog.yaml` / UI; fill verified limits + own scene + workspace | No (until READY) |
| Brand-new arm brand | `add-robot` + limits from datasheet/RoboDK + scene with `dof` joint targets | No app rewrite |
| New gripper / camera / FT sensor / dog / conveyor | One `components:` entry; or new `hardware_types/<type>.yaml` for a new *kind* | No |
| Different sim backend | `backend: mock` or `robodk_sim` in `hardware.yaml` | No |

On every change, chooser regenerates `robot_controller/config/{robot,safety,scene,selection}.yaml`. If status is **BLOCKED**, the controller **refuses to start** (fail closed). The app still speaks the same tool names; the bridge reports the live `model` / `dof` from controller health.

> **Note:** the groky-app **Robots** screen does not call the chooser API yet (chooser is CLI/UI on `:8770`). Restart controller + bridge after applying a selection. Live hot-swap of a running controller is not implemented.

### 5.2 Any controller backend (same task JSON)

| Backend | Use when |
|---|---|
| `mock` | No RoboDK; CI and laptop demos |
| `robodk_sim` | Digital twin GUI, collision checks in simulate mode |
| `ursim_sync` | RoboDK plans + URSim executes (loopback only) |
| `physical_disabled` | Explicit refusal stub for a future hardware adapter |

The AI never gets `moveJ` / raw XYZ tools — only allowlisted actions in the action contract.

### 5.3 Any OpenAI-compatible LLM / vision host

Computer-vision Grok client (`vision/grok`) is configured by env:

| Variable | Role |
|---|---|
| `XAI_API_KEY` | API key (xAI, OpenRouter, or compatible) |
| `XAI_BASE_URL` | Default `https://api.x.ai/v1`; OpenRouter: `https://openrouter.ai/api/v1` |
| `XAI_MODEL` | Chat/vision model id (e.g. `grok-4.7` or `x-ai/grok-4.7`) |
| `GROK_MOCK=1` | Deterministic offline responses for plumbing tests |

The **app** voice path uses xAI realtime (`EXPO_PUBLIC_XAI_API_KEY` or `EXPO_PUBLIC_TOKEN_URL`) and `EXPO_PUBLIC_GROK_VISION_MODEL` for `camera_look`. Point keys/models at whatever your deployment allows; keep secrets out of git.

### 5.4 Any detector weights / camera

- Eyes pipeline loads YOLO weights under `computer-vision/data/models/` (override via CLI flags / config).
- Camera modes: RealSense, webcam index, synthetic scene, optional second `dog` source.
- Open-vocab path: Grok can name novel objects; YOLO-World can follow those classes live.
- Extra app cameras: `EXPO_PUBLIC_CAMERAS=Name=http://…/shot.jpg` (e.g. phone IP Webcam).

### 5.5 Any skill content (not hardcoded demos)

Skills are JSON (schema 1.1.0): intent, robot profile, primitives, objects, steps, safety, review. Import/export from the app. Built-ins ship as templates; learned skills are ordinary files. The runner only executes known primitives through the bridge — **no eval of model-generated code**.

### 5.6 Any machine that speaks the bridge protocol

A third-party arm or mobile base integrates by implementing the small HTTP bridge API the app already uses. Until then, leave `EXPO_PUBLIC_*_URL` empty and use the in-app simulator.

---

## 6. Prerequisites

| Piece | Need |
|---|---|
| App | Node **20+**, npm, Expo Go (device) or a browser for web |
| Vision / teach / controller | Python **3.10–3.12**, `venv`, ffmpeg (system or imageio-ffmpeg) |
| Optional RoboDK stack | RoboDK installed; for URSim sync: Docker URSim + `ur_rtde` |
| Network | Tablet/phone on same LAN as the robot PC (or USB reverse tunnels) |
| Keys | xAI (or OpenRouter) API key for live voice/vision/teach; without keys, mock modes only exercise plumbing |

---

## 7. Quick start (app only)

Enough to talk to Motion with **simulated** robots (no bridge, no camera server):

```bash
cd groky-app
npm install
cp .env.example .env
# set EXPO_PUBLIC_XAI_API_KEY=...   (or EXPO_PUBLIC_TOKEN_URL)
npx expo start --web          # or: npx expo start  → Expo Go QR
```

Leave `EXPO_PUBLIC_ARM_URL` / `EXPO_PUBLIC_DOG_URL` empty. Motion still plans; arm tools run in the app simulator. After `.env` changes: `npx expo start -c`.

---

## 8. Full stack runbook

Run these on the **robot PC** (or a single powerful laptop). Then point the app `.env` at that machine’s LAN IP.

### 8.A One-shot stack (vision + teach + controller + bridge)

```bash
cd MOTIQ
# Controller venv once:
cd robot_controller && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && cd ..

# Vision venv once (or let run.py setup do it):
python computer-vision-to-app/run.py setup

BRIDGE_KEY='pick-a-long-secret' ./bridge/start_stack.sh
# Options:
#   BACKEND=mock VISION=0 BRIDGE_KEY=... ./bridge/start_stack.sh
#   VISION_MODE=synthetic BRIDGE_KEY=... ./bridge/start_stack.sh
```

`start_stack.sh` starts:

1. Vision eyes on **:8000** (if `computer-vision/.venv` exists and `VISION≠0`)
2. Teach server on **:8001**
3. Controller on **127.0.0.1:8765**
4. Bridge on **:8080**

### 8.B Vision + teach only (Cameras tab / Show Motion)

```bash
python computer-vision-to-app/run.py all
# or synthetic:  python computer-vision-to-app/run.py all --mode synthetic
# teach alone:   python computer-vision-to-app/run.py teach
```

This writes `EXPO_PUBLIC_VISION_SERVER` and `EXPO_PUBLIC_TEACH_SERVER` into `groky-app/.env`.

### 8.C Controller alone

```bash
cd robot_controller
source .venv/bin/activate
cp config/robot.example.yaml config/robot.yaml   # if not using robot-chooser output
python -m robot_control.cli serve --backend mock
# RoboDK: setup-station, then --backend robodk_sim (RoboDK GUI running)
python -m robot_control.cli demo --backend mock --task examples/pick_place.json
```

### 8.D robot-chooser (optional, before serving the controller)

```bash
cd robot_controller && source .venv/bin/activate && cd ../robot-chooser
python -m robot_chooser serve          # http://127.0.0.1:8770/
python -m robot_chooser status         # READY vs BLOCKED
python -m robot_chooser select ur_ur5e
python -m robot_chooser apply
# then restart controller + bridge
```

### 8.E App `.env` for the full stack

```bash
# groky-app/.env  (restart Expo with -c after edits)
EXPO_PUBLIC_XAI_API_KEY=xai-...
EXPO_PUBLIC_ARM_URL=http://<robot-pc-ip>:8080
EXPO_PUBLIC_BRIDGE_KEY=pick-a-long-secret
EXPO_PUBLIC_VISION_SERVER=http://<robot-pc-ip>:8000
EXPO_PUBLIC_TEACH_SERVER=http://<robot-pc-ip>:8001
# optional:
# EXPO_PUBLIC_DOG_URL=http://<dog-pc>:...
# EXPO_PUBLIC_CAMERAS=Overhead=http://<phone>:8080/shot.jpg
# EXPO_PUBLIC_VISION_SOURCES=arm,dog
```

Same machine browser demo: use `http://127.0.0.1:...` for those URLs.

### 8.F After emergency stop / fault

Controller latches CANCELLED/FAULT. Reset **only on the robot PC** (loopback):

```bash
curl -X POST localhost:8765/operator/reset \
  -H 'content-type: application/json' \
  -d '{"operator":"your-name"}'
```

The bridge never forwards this — the voice agent cannot clear faults alone.

---

## 9. Ports, URLs, and environment

### 9.1 Ports

| Port | Service |
|---|---|
| 8000 | computer-vision (`vision.eyes`) |
| 8001 | teach server (Show Motion / upload → skill) |
| 8080 | arm bridge (`motiq_bridge.py`) |
| 8765 | robot_controller HTTP API + dashboard (loopback) |
| 8770 | robot-chooser UI/API (loopback) |
| 20500 | RoboDK API (when used) |
| Expo | Metro bundler / web (dev) |

### 9.2 App env (`groky-app/.env`)

See [`groky-app/.env.example`](groky-app/.env.example).

| Variable | Purpose |
|---|---|
| `EXPO_PUBLIC_XAI_API_KEY` | Dev key for Grok voice/vision (bundled into JS — own devices only) |
| `EXPO_PUBLIC_TOKEN_URL` | Preferred: backend that mints short-lived realtime tokens |
| `EXPO_PUBLIC_ARM_URL` / `DOG_URL` | Bridge bases; empty = simulate |
| `EXPO_PUBLIC_BRIDGE_KEY` | Sent as `X-Bridge-Key` |
| `EXPO_PUBLIC_VISION_SERVER` | Eyes HTTP API |
| `EXPO_PUBLIC_TEACH_SERVER` | Teach HTTP API |
| `EXPO_PUBLIC_VISION_SOURCES` | e.g. `arm,dog` |
| `EXPO_PUBLIC_CAMERAS` | Extra JPEG cameras `Name=url,...` |
| `EXPO_PUBLIC_GROK_VOICE` / `_MODEL` / `_VISION_MODEL` | Voice + look models |

### 9.3 Vision / teach env (`computer-vision/.env`)

| Variable | Purpose |
|---|---|
| `XAI_API_KEY` | Planner + Grok vision tools |
| `XAI_BASE_URL` / `XAI_MODEL` | OpenAI-compatible host + model |
| `GROK_MOCK` | Offline mock |

Teach server reuses `EXPO_PUBLIC_XAI_API_KEY` from the app `.env` if the vision `.env` has no key.

---

## 10. Skills format and safety

- **Schema:** MOTIQ skill format **1.1.0** (see `groky-app/src/skills/` and templates such as `pick_place_ur5e`).
- **Steps:** DETECT / PICK / PLACE / VERIFY / SEQUENCE with `primitive_sequence` and `failure_policy`.
- **Checks:** UR kinematics/limits in the app (`src/robot/arm.ts`); controller re-checks reach, workspace, speed, allowlisted IDs.
- **Physical gates:** `review.approved` + `safety.physical_execution_allowed` (Skills tab). Edits revoke approval.
- **Unknown poses:** default `unknown_parameters_policy: NEEDS_VALIDATION` refuses real moves to unmeasured objects.
- **Controller docs:** [`SAFETY_BOUNDARIES.md`](robot_controller/docs/SAFETY_BOUNDARIES.md), [`STATES_AND_FEEDBACK.md`](robot_controller/docs/STATES_AND_FEEDBACK.md).

Simulation success **does not** prove physical safety.

---

## 11. Approval model

| Path | What waits for you |
|---|---|
| Voice / Show Motion arm plans | Motion calls `propose_plan`, asks out loud; **say or type yes/no**. Arm tools and `run_skill` stay locked until yes (simulator included). |
| Skills tab **Run** | Your tap runs it (subject to physical gates below). |
| Real robot skill execution | Skills-tab Approve (+ real robot) unless **full access** is on. Full access still requires the voice yes for Motion-driven moves. |
| Stop | Never locked — stop button / `stop` tool always works. |

Grok cannot approve its own plan. A new request, “no”, stop, or timeout ends approval.

---

## 12. robot-chooser (any arm / any hardware)

Quick operator view (full guide: [`robot-chooser/README.md`](robot-chooser/README.md)):

```bash
python -m robot_chooser list
python -m robot_chooser select ur_ur5e
python -m robot_chooser components
python -m robot_chooser add-component --id wrist_cam --type camera ...
python -m robot_chooser status    # exit 0 READY, 2 BLOCKED
python -m robot_chooser apply
```

**Design goal:** new arm = catalog entry; new device = component; new *kind* of device = one `hardware_types/*.yaml`. Payload/TCP/attachment rules are derived automatically. Physical actuators are never enabled by the chooser (simulation-oriented).

Reference cell today: **`ur_ur5e` READY** (placeholders for measured tool mass/TCP). Other catalog robots may be **Config incomplete** until limits/scene/workspace/`.robot` files exist.

---

## 13. Testing

```bash
# App
cd groky-app && npm run typecheck && npm run lint

# Controller (offline mock)
cd robot_controller && .venv/bin/python -m pytest -q

# robot-chooser
cd robot-chooser && ../robot_controller/.venv/bin/python -m pytest -q

# Vision plumbing without camera
python computer-vision-to-app/run.py check
```

Opt-in integration: `RUN_ROBODK_TESTS=1`, `RUN_URSIM_TESTS=1` (see controller README).

---

## 14. Troubleshooting

| Symptom | What to try |
|---|---|
| App “No signal” on Arm vision | `run.py check`; confirm LAN IP + firewall; restart Expo after `.env` |
| Show Motion / teach “video unreadable” | Teach remux needs ffmpeg; ensure teach server log shows MP4 normalisation; recording must be non-empty |
| Bridge refuses pick | Object not in view / not mapped; check vision `/state/arm` and scene `class_labels` |
| Tasks refused after Stop | `POST localhost:8765/operator/reset` on the robot PC |
| Controller won’t start after chooser | `robot_chooser status` — fix BLOCKED requirements; or pass explicit `--config` |
| Voice works but arm never moves | Pending plan — say **yes**; or Skills approval / full access; or empty `ARM_URL` = sim only |
| Camera busy | Only one process per device — close `run_demo` / other apps |
| Install wheels fail | Use Python 3.10–3.12 |

---

## 15. Where to read next

| Doc | Contents |
|---|---|
| [`groky-app/README.md`](groky-app/README.md) | App tabs, skills, bridge client, env |
| [`bridge/README.md`](bridge/README.md) | Tool → task map, eye-in-hand search, stack script |
| [`robot_controller/README.md`](robot_controller/README.md) | Backends, CLI, demo objects, layout |
| [`robot_controller/docs/`](robot_controller/docs/) | Action contract, safety, vision integration, RoboDK Linux |
| [`computer-vision-to-app/README.md`](computer-vision-to-app/README.md) | `run.py`, teach, Show Motion path |
| [`computer-vision/API_APP.md`](computer-vision/API_APP.md) | HTTP/WS API for the app |
| [`computer-vision/vision/eyes/README.md`](computer-vision/vision/eyes/README.md) | Detection / tracking pipeline |
| [`computer-vision/vision/grok/README.md`](computer-vision/vision/grok/README.md) | Grok vision toolkit + OpenAI-compatible config |
| [`computer-vision/vision/planner/README.md`](computer-vision/vision/planner/README.md) | Video → tokens → skills |
| [`robot-chooser/README.md`](robot-chooser/README.md) | Scalable hardware model |

Interactive vision docs when the server is up: `http://<pc>:8000/docs`.

---

## 16. Status and limits

- Hackathon build: voice, vision, skills, teach→skill, and **simulated** execution (mock / RoboDK / URSim) are the supported paths.
- **Physical motion** depends on your deployment and safety commissioning; the controller’s simulation-oriented revision documents physical backends as disabled/refused — read [`SAFETY_BOUNDARIES.md`](robot_controller/docs/SAFETY_BOUNDARIES.md).
- robot-chooser is integrated into **config generation + controller/bridge health**, not yet into the app Robots UI.
- Gripper and many grasp targets are symbolic / taught placeholders; camera XYZ is unused for picks until hand-eye calibration is valid — picks use registered scene targets.
- Mic is ignored while Motion speaks; use the stop button to interrupt.

---

**MOTIQ** — voice in, skills out, robots (simulated or bridged) in between, with contracts you can extend to **any arm model, any cell device, and any OpenAI-compatible brain**.
