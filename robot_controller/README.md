# robot_control: a robot controller you can swap backends under (simulation-only build)

A typed, safety-gated controller that the team's Grok planner and vision service call with **high-level
actions** (`identify_object`, `pick_object`, `shake_object`, `place_object`, …). The **same task JSON** runs on:

| Backend | What it is | Motion |
|---|---|---|
| `mock` | Pure-Python, deterministic simulator with fault injection. Doesn't need RoboDK. | simulated |
| `robodk_sim` | The RoboDK digital twin: a UR5e in a demo station, forced to `RUNMODE_SIMULATE` | simulated |
| `physical_disabled` | The interface a future hardware adapter would plug into | **refused** (`PHYSICAL_BACKEND_DISABLED`) |

> **No robot hardware can be moved by this revision.** Simulation results, including RoboDK collision
> checks, do **not** show that physical operation is safe. See [docs/SAFETY_BOUNDARIES.md](docs/SAFETY_BOUNDARIES.md).

## Quick start (Linux)

```bash
cd robot_controller
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q                                   # offline: Mock only (92 tests)

# Mock demo: identify -> pick -> lift -> shake x2 -> place -> release -> retreat -> home
python -m robot_control.cli demo --backend mock --task examples/pick_shake_place.json

# RoboDK UR5e demo (RoboDK GUI must be running: ~/RoboDK/RoboDK-Start.sh)
cp config/robot.example.yaml config/robot.yaml        # then set backend: robodk_sim
python -m robot_control.cli setup-station --config config/robot.yaml   # builds the "UR5e Controller Demo" station tab
python -m robot_control.cli demo --backend robodk_sim --task examples/pick_shake_place.json --config config/robot.yaml

# Opt-in RoboDK integration tests (simulator only)
RUN_ROBODK_TESTS=1 python -m pytest -m integration -q

# Other commands
python -m robot_control.cli validate --task examples/invalid_action.json   # -> UNKNOWN_ACTION
python -m robot_control.cli tools                                          # AI tool allowlist (JSON)
python -m robot_control.cli serve --backend mock                           # local API on 127.0.0.1:8765
```

### Live status (what it's doing, what's next, where it is)

Before any motion, each task is expanded into a step plan (e.g. 18 steps for `pick_shake_place.json`). You can
follow it in four places:
* **Terminal** (`demo`): a `NOW` / `NEXT` block with the target position for each step, plus a live line with
  TCP xyz, joints, gripper and held object.
* **RoboDK status bar**: `[7/18] LIFTING: lift red_cube`.
* **Live JSON**: `runs/live_status.json` is rewritten atomically about 4× per second. Other apps can poll it.
* **Dashboard**: add `--dashboard` and open http://127.0.0.1:8765/. `GET /status` returns the same JSON.

### Speed

`config/safety.example.yaml` sets `speed_percent_of_model_max: 25`. The four motion limits are 25% of the model's
maximums in `robot.*.yaml` (UR5e: TCP 0.25 m/s, 0.5 m/s²; joints 0.785 rad/s = 45°/s, 1.0 rad/s²). Joint speed is
scaled from the slowest joint's maximum. Override a single run with `--speed-percent 10`. Values outside
0 < p ≤ 100, or setting both a percentage and absolute values, are rejected as `CONFIG_ERROR`.
RoboDK plays simulations at 5× real time by default (Tools → Options → Motion → Simulation speed), so set
it to 1× to see real-time motion.

Run `setup-station` again to reset the cube to its pick position. If you re-run the demo while the cube is
still on the delivery pad, the controller **correctly fails** with `GRIP_NOT_CONFIRMED` and latches FAULT.

## Layout

```
robot_control/
  models.py            typed contract: actions, tasks, poses (m/rad), events, error codes
  config.py            robot model / scene / safety configs (fail closed on invalid config)
  controller.py        RobotController ABC (DOF from config; move_* are internal only)
  task_executor.py     FSM with an explicit transition table, preflight, cancel/timeout, fault latch
  action_registry.py   AI tool allowlist + strict validation
  scene_registry.py    allowlisted objects/targets/patterns + detections
  vision.py            MockDetector (labelled), NoDetector, MOTIQ vision/eyes file adapter
  safety/              policy.py, limits.py, trajectory.py, watchdog.py
  backends/            mock.py, robodk_sim.py, robodk_station.py, physical_disabled.py
  api/app.py           optional FastAPI (loopback only)
  cli.py
config/                robot.example.yaml (UR5e), robot.ur3e.example.yaml, scene.example.yaml, safety.example.yaml
examples/              pick_shake_place.json, move_to_target.json, invalid_action.json
tests/                 contracts, safety, executor, mock, api, robodk_smoke (integration, opt-in)
docs/                  AI_ACTION_CONTRACT, STATES_AND_FEEDBACK, SAFETY_BOUNDARIES, ROBODK_LINUX_SETUP, VISION_INTEGRATION
stations/              UR5e.robot (RoboDK library), generated UR5e_demo.rdk
```

## Assumptions (recorded per the brief)

* **No existing controller code** was found on this Linux machine (`UR5e_Hackathon_Starter` and the Windows `.rdk` are
  absent), so this is a fresh package. The vision/Grok repo at `../MOTIQ/computer-vision` was **read, not modified**.
  `docs/AGENT_STATE_ACTION_FEEDBACK.md` and `docs/UR5E_RTDE_API_REFERENCE.md` do not exist there.
* **RoboDK station:** none existed, so `setup-station` builds a clearly labelled **demo** station in a new tab:
  UR5e from the RoboDK library, a symbolic gripper TCP (150 mm placeholder), named targets "taught" from
  `scene.example.yaml`, and box geometry (table, delivery pad, red cube). The geometry stands in for the real cell.
* **Orientation:** this RoboDK UR5e model faces −X at home `[0,-90,90,-90,-90,0]°`, so the demo cell sits at
  negative X. The Windows prototype reported home `[0,-90,-90,0,90,0]`; that posture was not reused because it
  could not be checked against a station here.
* **Gripper:** symbolic only. In RoboDK, closing attaches the cube to the tool only if the cube is within 15 mm of
  the TCP. Feedback is always `simulated`, never `hardware_confirmed`.
* **Vision:** there's no camera→robot calibration, so camera XYZ is never used and picks use taught targets.
  The `mock` detector is disclosed in every result (`is_mock: true`).
* **Limits:** model numbers come from UR e-Series datasheets (see `limits.source` in each config).
  The acceleration caps and the example safety speeds are conservative configured values, not certified ones.

## Verified on this machine (2026-11-04)

* `python -m pytest -q`: **92 passed** (Mock only).
* `RUN_ROBODK_TESTS=1 python -m pytest -m integration -q`: **4 passed** against the live RoboDK GUI. These cover:
  the robot is selected by name in `RUNMODE_SIMULATE`; it moves between named targets with 0 collisions; a segment
  into the table is flagged and the robot pose is restored afterwards; a non-simulate run mode is refused.
* RoboDK demo: `demo_001` **SUCCEEDED**, and the cube ends on the delivery pad at (−450, 200, 3) mm.
  Robot `ConnectedState` = Disconnected, meaning no driver was used.

## Not done / blockers

* No real gripper model, payload/TCP calibration, or camera calibration.
* No authentication on the HTTP API, so it is loopback only and must not be exposed.
* A physical adapter (e.g. UR RTDE) is deliberately not implemented. See the commissioning plan in
  [docs/SAFETY_BOUNDARIES.md](docs/SAFETY_BOUNDARIES.md).
