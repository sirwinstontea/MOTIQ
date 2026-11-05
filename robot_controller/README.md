# robot_control: a robot controller you can swap backends under (simulation-only build)

A typed, safety-gated controller that the team's Grok planner and vision service call with **high-level
actions** (`identify_object`, `pick_object`, `shake_object`, `place_object`, …). The **same task JSON** runs on:

| Backend | What it is | Motion |
|---|---|---|
| `mock` | Pure-Python, deterministic simulator with fault injection. Doesn't need RoboDK. | simulated |
| `robodk_sim` | The RoboDK digital twin: a UR5e in a demo station, forced to `RUNMODE_SIMULATE` | simulated |
| `ursim_sync` | RoboDK plans and collision-checks each segment, **URSim** (the UR controller software in Docker, localhost only) executes it over RTDE, and RoboDK mirrors URSim's live joints | simulated (2 simulators in sync) |
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
python -m robot_control.cli demo --backend mock --task examples/pick_place.json

# RoboDK UR5e demo (RoboDK GUI must be running: ~/RoboDK/RoboDK-Start.sh)
cp config/robot.example.yaml config/robot.yaml        # then set backend: robodk_sim
python -m robot_control.cli setup-station --config config/robot.yaml   # builds the "UR5e Controller Demo" station tab
python -m robot_control.cli demo --backend robodk_sim --task examples/pick_place.json --config config/robot.yaml

# Opt-in RoboDK integration tests (simulator only)
RUN_ROBODK_TESTS=1 python -m pytest -m integration -q

# Other commands
python -m robot_control.cli validate --task examples/invalid_action.json   # -> UNKNOWN_ACTION
python -m robot_control.cli tools                                          # AI tool allowlist (JSON)
python -m robot_control.cli serve --backend mock                           # local API on 127.0.0.1:8765
```

### Live status (what it's doing, what's next, where it is)

Before any motion, each task is expanded into a step plan (e.g. for `pick_place.json`). You can
follow it in four places:
* **Terminal** (`demo`): a `NOW` / `NEXT` block with the target position for each step, plus a live line with
  TCP xyz, joints, gripper and held object.
* **RoboDK status bar**: `[3/14] LIFTING: lift orange_screw_03`.
* **Live JSON**: `runs/live_status.json` is rewritten atomically about 4× per second. Other apps can poll it.
* **Dashboard**: add `--dashboard` and open http://127.0.0.1:8765/. `GET /status` returns the same JSON.

### Speed

`config/safety.example.yaml` sets `speed_percent_of_model_max: 10` (the startup default). The four motion limits are
10% of the model's maximums in `robot.*.yaml` (UR5e: TCP 0.10 m/s, 0.2 m/s²; joints 0.314 rad/s = 18°/s, 0.4 rad/s²).
Joint speed is scaled from the slowest joint's maximum. Override at startup with `--speed-percent 25`, or change it
live (between tasks) with the dashboard's Speed card, the app's arm card, or
`curl -X POST localhost:8765/robot/speed -H 'content-type: application/json' -d '{"percent": 25}'`
(`GET /robot/speed` reads it; a change is refused with 409 while a task runs). Every task records the speed it ran
at (`speed_percent` in `GET /tasks/{id}`). Values outside
0 < p ≤ 100, or setting both a percentage and absolute values, are rejected as `CONFIG_ERROR`.
RoboDK plays simulations at 5× real time by default (Tools → Options → Motion → Simulation speed), so set
it to 1× to see real-time motion.

Run `setup-station` again to reset the objects to their slots. If you re-run the demo while the screw is
still on the delivery pad, the controller **correctly fails** with `GRIP_NOT_CONFIRMED` and latches FAULT.

### Synchronized runs: URSim + RoboDK (`ursim_sync`)

Every task can run on URSim and RoboDK in lock-step. For each segment:

1. RoboDK resolves the target, solves IK and runs the collision test.
2. URSim executes the move (`moveJ`, or `moveL_FK` for linear moves) at the configured speed.
3. While URSim moves, its *actual* joints are streamed into RoboDK at 50 Hz, so held objects follow.

Before each move, URSim must be RUNNING with safety NORMAL, and both robots must agree to within 0.5°. After
each move, URSim must have reached the planned joints and RoboDK must match URSim. On connect, RoboDK is
re-synced from URSim. The TCP is set to the same 150 mm offset on both, so their TCP readouts match.

`ursim_host` must be a loopback address, so **this backend cannot drive a physical arm**.

```bash
# URSim running (see ursim/README.md), powered on + brakes released; RoboDK running
python -m pip install ur_rtde
python -m robot_control.cli setup-station --config config/robot.yaml          # station + targets + 4 demo objects
python -m robot_control.cli demo --backend ursim_sync --config config/robot.yaml --task examples/objects/all_objects_pick_place.json
RUN_URSIM_TESTS=1 python -m pytest -m integration tests/test_ursim_sync_smoke.py -q
python scripts/mirror_ursim_to_robodk.py        # read-only live mirror (e.g. while jogging in PolyScope)
```

Read-only digital twin (joints + calibrated object markers, app-controlled, simulate first then the real UR5e):
`python -m robot_control.digital_twin.cli check|snapshot|run|serve --config config/digital_twin.sim.json`, see
[docs/DIGITAL_TWIN_SYNC.md](docs/DIGITAL_TWIN_SYNC.md).

### Tasks for the demo objects (`examples/objects/`)

| Task | Actions |
|---|---|
| `<object>_pick_place.json` (one per object) | identify → pick from its slot → place back in its slot → home |
| `all_objects_pick_place.json` | the same for all four in turn (13 actions), then home |

The objects are `blue_bucket_01`, `green_ring_02`, `orange_screw_03` and `white_bucket_lid_04`. Placing each
object back in its own slot makes the tasks repeatable without a reset. Vision labels map to the objects:
`secchio_blu`, `anello_verde`, `vite_arancione` and `tappo_bianco`. There is no red cube in the scene any more.
Grasp targets are **simulation placeholders**: tool-down on the object axis at mid-height, which is
what the symbolic gripper needs. They were not taught on a real cell and use no real jaw geometry. The masses are
unknown, so `shake_object` is refused for these objects until a measured `mass_kg` is added to the scene.

### Demo objects (blue bucket, green ring, orange screw, white lid)

Four coloured, watertight STL models (in mm) for the RoboDK station. See [objects/README.md](objects/README.md).

```bash
python -m pip install -e '.[objects,robodk]'
python scripts/generate_all.py && python scripts/validate_all.py -q
python scripts/import_all.py --dry-run      # no RoboDK contact
python scripts/import_all.py                # adds them to the open station under Objects_Frame (skips duplicates)
```

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
examples/              pick_place.json, move_to_target.json, invalid_action.json
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
  `scene.example.yaml`, and box geometry (table, delivery pad); the 4 demo objects are imported as meshes. The geometry stands in for the real cell.
* **Orientation:** this RoboDK UR5e model faces −X at home `[0,-90,90,-90,-90,0]°`, so the demo cell sits at
  negative X. The Windows prototype reported home `[0,-90,-90,0,90,0]`; that posture was not reused because it
  could not be checked against a station here.
* **Gripper:** symbolic only. In RoboDK, closing attaches an object to the tool only if it is within 15 mm of
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
* RoboDK demo: `demo_001` **SUCCEEDED** (measured with the old red cube; re-run with `examples/pick_place.json` to re-check with the orange screw).
  Robot `ConnectedState` = Disconnected, meaning no driver was used.

## Not done / blockers

* No real gripper model, payload/TCP calibration, or camera calibration.
* No authentication on the HTTP API, so it is loopback only and must not be exposed.
* A physical adapter (e.g. UR RTDE) is deliberately not implemented. See the commissioning plan in
  [docs/SAFETY_BOUNDARIES.md](docs/SAFETY_BOUNDARIES.md).
