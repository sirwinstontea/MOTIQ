# MOTIQ

**Tell a robot what to do, or show it once, and it does it, checks its own work and remembers it as a skill.**

Built for the **SpaceX AI (xAI) Grok Hackathon, Pavia, 4–5 November 2026**, and powered by Grok end to end: Grok
Voice runs the conversation, Grok vision checks the cameras, and Grok writes the robot skills.

## What it can do

- **Talk to robots.** Pick up the tablet and say *"put the red cube in the box"* or *"dog, come here and sit"*.
  Motion, the Grok voice agent, picks the right robot (a UR5e arm or a robot dog) and calls its actions as tools.
- **See before and after acting.** The camera finds and tracks objects, numbers them on screen and measures where
  they are. Before a pick the robot looks for the object first. After the pick it checks that the object actually
  arrived, and offers to retry if it didn't.
- **Learn by watching.** Do a task by hand in front of the camera (*Skills → Learn from camera*), or upload a
  demonstration video. MOTIQ works out what moved where, and Grok turns that into a reusable skill.
- **Remember skills.** Anything you ask by voice is saved as a skill, and Grok gives it a short name. Tap it to run
  it again, edit it by voice or in the editor, or import and export it as JSON.
- **Stay safe.** Each skill is validated, dry-run and checked against the arm's real reach, payload and joint
  limits before it can run. It needs your approval, and it runs in simulation (RoboDK / URSim digital twin) first.
  "Stop!" halts everything.
- **Show its thinking.** Every step appears live: what it heard, which tool it picked, what the camera saw and how
  it answered.

## How it fits together

```
 you ──voice──▶ groky-app (Expo / React Native, Grok Voice)
                   │  tool calls                      │  vision tools, camera frames
                   ▼                                  ▼
               bridge :8080  ◀── object positions ── computer-vision :8000
                   │                                  (YOLO detect + track, Grok naming,
                   ▼                                   video → skill planner)
           robot_controller :8765
           (typed, safety-gated actions)
                   │
                   ▼
     mock  ·  RoboDK UR5e twin  ·  URSim     (physical hardware is disabled in this build)
```

| Folder | What it is |
|---|---|
| [`groky-app/`](groky-app) | The MOTIQ app (tablet, phone, web): voice agent, cameras, skills, history |
| [`computer-vision/`](computer-vision) | Vision server: detection, tracking, calibration, Grok vision tools, and `vision/planner` (demonstration video → skill JSON → RoboDK) |
| [`computer-vision-to-app/`](computer-vision-to-app) | One command that installs and starts the vision server and connects it to the app's Cameras tab and *Learn from camera* |
| [`bridge/`](bridge) | Connects the app's robot tools to the controller and passes it what the camera sees |
| [`robot_controller/`](robot_controller) | Robot controller with interchangeable backends: the same task JSON runs on mock, RoboDK or URSim |
| [`robot-chooser/`](robot-chooser) | Pick the arm model (UR5e, UR3e, xArm5, CS63, CS66), adjust tool/mounting/cameras; regenerates the hardware requirements YAML and the controller config (simulation only) |

## Quick start

```bash
# 1. the app
cd groky-app && npm install
cp .env.example .env              # add EXPO_PUBLIC_XAI_API_KEY
npx expo start --web              # or scan the QR code with Expo Go

# 2. computer vision in the app (optional, needs a webcam or RealSense)
python computer-vision-to-app/run.py all

# 3. robot simulation (optional)
cd robot_controller && pip install -e '.[dev]'
python -m robot_control.cli serve --backend mock
```

More detail is in each folder's README, and the original project brief is in
[`README whole project.md`](README%20whole%20project.md).

## Status

This is a hackathon build. Voice control, the vision server, skills and simulated execution (mock / RoboDK / URSim)
work. **No physical robot moves in this revision.** Simulation results don't prove that real operation is safe:
see [`robot_controller/docs/SAFETY_BOUNDARIES.md`](robot_controller/docs/SAFETY_BOUNDARIES.md).
