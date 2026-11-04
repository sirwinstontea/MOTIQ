# MOTIQ

Talk to Motion, the voice agent for a robot dog and a robot arm, and watch them do it. Motion (running on Grok)
listens, decides, moves the robots, looks through the cameras to check its own work, and tells you how it went.

Built at the Grok Hackathon, Ctrl+Alt Museum, Pavia (4-5 Nov 2026).

## The idea

Controlling a robot usually means a laptop, a terminal and someone who knows the API. We wanted anyone walking into
the museum to pick up a tablet and say _"put the red cube in the box"_ or _"dog, come here and sit"_, and have it
just work.

Three things make that possible:

- **Voice agent.** Grok Voice runs the conversation in real time and calls robot actions as tools. One agent drives
  both robots, so a single sentence can involve the dog and the arm.
- **Vision in the loop.** Grok can take a picture through any camera (tablet, robot, overhead) and reason about it.
  It looks before acting on something it hasn't seen, and after a pick it checks that the object actually landed
  where it should. If not, it says so and offers to retry.
- **You can see what it's thinking.** Every step shows up live: what it heard, which tool it chose and with what
  arguments, how long the robot took, what the camera saw, what it answered. No black box.

## Demo

1. "What do you see?" - Grok looks through the tablet camera and describes the table.
2. "Put the red cube in the box." - arm picks it up, places it, then Grok checks the camera and confirms.
3. "Dog, walk forward a meter and wave." - two tool calls in a row, dog card shows it running.
4. "Stop!" - everything halts, Grok talks after.
5. Open History, tap an earlier command to run it again.

## How it works

```
   you ──voice──▶  Grok Voice (realtime)  ──tool call──▶  robot bridge (HTTP)  ──▶  dog / arm
    ▲                    │      ▲                                 │
    │                    │      └──────── result ◀────────────────┘
    │                    ▼
    │              camera_look ──frame──▶ Grok vision ──▶ "the cube is in the box"
    │                    │
    └────── speech ◀─────┘
```

The app holds the Grok session, plays the audio and runs the tools. Each robot sits behind a small HTTP bridge on
its own laptop, so the app doesn't care what SDK the hardware uses.

Grok sees one flat tool list: `dog_walk`, `dog_turn`, `dog_pose`, `arm_goto`, `arm_grip`, `arm_pick_place`,
`arm_check`, `camera_look` and `stop`.

## The app

| tab | what it's for |
| --- | --- |
| Talk | the orb, live transcript, camera and the agent's activity timeline side by side. Open the transcript to type: it works without the mic too |
| Cameras | big camera view plus "Show Motion", next to the activity timeline |
| Robots | status of each robot: online / simulated / offline, battery, latency, success rate, recent actions, stop |
| History | everything you've asked, searchable, pin favourites, tap to run again |

Light and dark theme in Settings. The camera can be switched off from the camera panel; Grok is told it's off
instead of guessing.

## Skills

Skills use the MOTIQ skill format (schema 1.1.0). `src/skills/templates/pick_place_ur5e.json` is the reference: intent
and success conditions, the robot profile, `action_primitives`, environment objects, constraints, steps
(DETECT / PICK / PLACE / VERIFY / SEQUENCE, each with a `primitive_sequence` and a `failure_policy`), interpretation
and safety. The arm's tools and limits are read straight from that template (`src/robot/profiles.ts`). Two app
extensions: `robot.device` (`arm` | `dog`) and an `app` block (icon, builtin).

- **Editing:** the Skills tab has a form for steps, primitives, objects and success conditions, and a JSON tab for
  the whole file. Import pastes a skill in; the share button exports a self-contained file.
- **Checks:** every arm primitive goes through a UR5e model (`src/robot/arm.ts`: DH kinematics, operational limits,
  joint ranges, reach, the singularity cylinder around the base, the table plane). The editor and the preview show a
  dry run of the whole skill. Nothing outside the limits is ever sent to the bridge, from skills or from voice.
- **Running:** `src/skills/runner.ts`. DETECT uses the vision server (`EXPO_PUBLIC_VISION_SERVER`) if set, otherwise
  Grok vision on the selected camera. VERIFY asks the camera to confirm the success conditions. In the simulator both
  use the predicted scene. `RETRY_WITH_REOBSERVATION` retries a step once after looking again; the others stop.
- **Safety gates:** a skill only moves real hardware when `review.approved` and `safety.physical_execution_allowed`
  are both true (Review & approve on the card). Saving an edit drops the approval. With
  `unknown_parameters_policy: NEEDS_VALIDATION` (the default), a real run refuses to move to an object whose position
  wasn't measured by a calibrated camera; set it to `USE_SKILL_VALUES` to trust the positions in the file.
- **Not modelled:** collisions with anything but the table, gripper fingers, payload. The UR safety configuration
  stays the hard limit on the real cell.

MOVE_ABOVE lifts straight up and then travels in joint space: from HOME, a straight line to the cube passes about
1 cm from the base axis, which would trip a protective stop on the real arm.

## Skill preview

The film icon on a skill card (or Preview in the editor) replays the dry run as a 2D side view for the arm and a
top-down floor for the dog, and flags steps that would be refused, grasps that would close on nothing and checks
that would fail. The arm drawing is a 2-link sketch of the real 6 joints. Logic in `src/preview/scene.ts`.

## Robot bridge

Each robot laptop runs a tiny HTTP server:

- `GET /health` - 200 and some JSON (battery, pose...) when the robot is connected
- `POST /tools/<name>` - JSON args in, JSON result out, ideally `{ "ok": true, ... }`
- `POST /tools/stop` - must always work and return fast
- `GET /camera.jpg` - latest frame (optional, shows up as a camera automatically)

Requests carry `X-Bridge-Key` if one is set. With no bridge URL the robot is simulated, so the whole app can be
demoed without hardware. Tool names and arguments are in `src/devices/tools.ts`.

Arm tools are the UR5e primitives in lower case: `POST /tools/move_left`, `/tools/close_gripper`,
`/tools/move_above`... The body has the resolved parameters (defaults filled in, already checked against the limits)
plus `urscript`, the URScript the app would run for that step, so the bridge can either execute the primitive itself
or forward the script to the controller. Reply with `{ "ok": true }` and, ideally, `tcp_pose` (`[x, y, z, rx, ry, rz]`),
`joints_deg` (6 numbers) and, for `close_gripper`, `object_detected`, so the app tracks the real arm instead of its
prediction.

## Run it

Needs Node 20+ and Expo Go on the tablet.

```sh
npm install
cp .env.example .env   # add the xAI key
npx expo start
```

Scan the QR code with Expo Go. On venue wifi use `npm run tunnel`, or plug the tablet in over USB and run
`../tablet.sh` from the repo's parent folder (see `COMMANDS.md` there).

### .env

| var | what |
| --- | --- |
| `EXPO_PUBLIC_XAI_API_KEY` | xAI key. Gets bundled into the app, so only on our own devices |
| `EXPO_PUBLIC_TOKEN_URL` | optional backend that mints short-lived Grok Voice tokens, used instead of the key |
| `EXPO_PUBLIC_DOG_URL`, `EXPO_PUBLIC_ARM_URL` | robot bridges, e.g. `http://192.168.1.20:8000`. Empty = simulated |
| `EXPO_PUBLIC_BRIDGE_KEY` | sent as `X-Bridge-Key` |
| `EXPO_PUBLIC_CAMERAS` | extra cameras, `Name=url,Name=url`, each url returns a JPEG |
| `EXPO_PUBLIC_VISION_SERVER` | computer-vision server (`../computer-vision/API_APP.md`), used by skill DETECT steps |
| `EXPO_PUBLIC_GROK_VOICE` | voice, default `eve` |
| `EXPO_PUBLIC_GROK_VISION_MODEL` | model for camera questions, default `grok-4.20-0309-non-reasoning` (~1s; `grok-4.7` ~4s) |

Restart with `npx expo start -c` after changing `.env`.

A phone running the "IP Webcam" Android app works as an extra camera: `Overhead=http://<phone-ip>:8080/shot.jpg`.

## Code

```
App.tsx              shell: sidebar, top bar, tabs
src/screens/         Talk, Cameras, Robots, History
src/components/      orb, camera panel, activity timeline, log, sidebar...
src/hooks/           useVoice, useCamera, useDevices, useHistory
src/voice/           Grok realtime session, prompt, audio
src/devices/         robot tools, bridge client, simulator
src/robot/           UR5e kinematics, arm model and limits, live arm state, detect/verify
src/skills/          skill format, built-ins, validation, dry run, runner
src/lib/             config, vision, cameras
src/preview/         2D skill preview timeline
```

`npm run lint`, `npm run typecheck`, `npm run format` before pushing.

## Known limits

- Replies play once fully received, not streamed. Fine for short answers.
- The mic is ignored while Motion talks, so you can't interrupt by voice. Use the stop button.
- The token endpoint's response format isn't documented; `src/voice/token.ts` accepts the likely shapes.
- Tool list is a first guess until we see the real hardware.
