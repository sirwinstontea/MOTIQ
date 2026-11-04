# Groky

Talk to a robot dog and a robot arm and watch them do it. Grok listens, decides, moves the robots, looks through the
cameras to check its own work, and tells you how it went.

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
| Talk | the orb, live transcript, camera and the agent's activity timeline side by side |
| Cameras | big camera view plus "Show Grok", next to the activity timeline |
| Robots | status of each robot: online / simulated / offline, battery, latency, success rate, recent actions, stop |
| History | everything you've asked, searchable, pin favourites, tap to run again |

Light and dark theme in Settings. The camera can be switched off from the camera panel; Grok is told it's off
instead of guessing.

## Robot bridge

Each robot laptop runs a tiny HTTP server:

- `GET /health` - 200 and some JSON (battery, pose...) when the robot is connected
- `POST /tools/<name>` - JSON args in, JSON result out, ideally `{ "ok": true, ... }`
- `POST /tools/stop` - must always work and return fast
- `GET /camera.jpg` - latest frame (optional, shows up as a camera automatically)

Requests carry `X-Bridge-Key` if one is set. With no bridge URL the robot is simulated, so the whole app can be
demoed without hardware. Tool names and arguments are in `src/devices/tools.ts`.

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
| `EXPO_PUBLIC_GROK_VOICE` | voice, default `eve` |
| `EXPO_PUBLIC_GROK_VISION_MODEL` | model for camera questions, default `grok-4.7` |

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
src/lib/             config, vision, cameras
```

`npm run lint`, `npm run typecheck`, `npm run format` before pushing.

## Known limits

- Replies play once fully received, not streamed. Fine for short answers.
- The mic is ignored while Groky talks, so you can't interrupt by voice. Use the stop button.
- The token endpoint's response format isn't documented; `src/voice/token.ts` accepts the likely shapes.
- Tool list is a first guess until we see the real hardware.
