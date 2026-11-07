# robot_controller

`robot_control` is a typed, safety-gated robot controller with swappable backends. The Grok planner, the bridge and the vision service call it with **high-level actions** such as `identify_object`, `pick_object`, `shake_object` and `place_object`. The **same task JSON** runs on every backend.

| Backend | What it is | Motion |
|---|---|---|
| `mock` | Deterministic pure-Python simulator with fault injection. No PyBullet needed. | Simulated |
| `pybullet` | PyBullet cell: UR5e kinematics, the four demo parts, symbolic gripper | Simulated |
| `ursim_sync` | PyBullet plans and collision-checks each segment, URSim in Docker executes it over RTDE, PyBullet mirrors the live joints | Simulated, two simulators in sync |
| `physical_disabled` | The interface a future hardware adapter plugs into | **Refused** with `PHYSICAL_BACKEND_DISABLED` |

> [!WARNING]
> **This controller cannot move robot hardware.** Simulation results, including PyBullet collision checks, do **not** show that physical operation is safe. See [docs/SAFETY_BOUNDARIES.md](docs/SAFETY_BOUNDARIES.md). The direct steps in [`bridge/`](bridge) are a separate path that does not go through this controller.

## Quick start

```bash
cd robot_controller
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
python -m pytest -q                                   # 144 passed, 5 integration tests deselected

python -m robot_control.cli demo --backend mock --task examples/pick_place.json
python -m robot_control.cli validate --task examples/invalid_action.json     # UNKNOWN_ACTION
python -m robot_control.cli tools                                            # AI tool allowlist
python -m robot_control.cli serve --backend mock                             # API on 127.0.0.1:8765
```

**PyBullet.** The GUI must be running.

```bash
cp config/robot.example.yaml config/robot.yaml        # set backend: pybullet
python -m robot_control.cli setup-station --config config/robot.yaml
python -m robot_control.cli demo --backend pybullet --task examples/pick_place.json --config config/robot.yaml
RUN_PYBULLET_TESTS=1 python -m pytest -m integration -q
```

Run `setup-station` again to reset the objects. Re-running the demo while the screw is still on the delivery pad correctly fails with `GRIP_NOT_CONFIRMED` and latches FAULT.

## CLI

| Command | Purpose |
|---|---|
| `demo --task FILE` | Run a task. `--dashboard`, `--dashboard-port`, `--events-out`, `--live-file` |
| `validate --task FILE` | Validate without running |
| `setup-station` | Build the PyBullet demo station. `--no-objects` skips the 4 demo objects. |
| `tools` | Print the AI tool allowlist as JSON |
| `serve` | FastAPI server and dashboard. `--host`, `--port` |

Shared flags: `--config`, `--scene`, `--safety`, `--backend`, `--detector mock|none|vision_file`, `--vision-file`, `--start-pybullet`, `--speed-percent`.

Without explicit paths, config comes from `config/robot.yaml`, or `robot.example.yaml` if it's missing, and likewise for `scene` and `safety`. If [`robot-chooser`](robot-chooser) wrote `config/selection.yaml` with a non-READY status, the CLI refuses to start. Passing `--config`, `--scene` and `--safety` bypasses it.

## HTTP API

| Route | Purpose |
|---|---|
| `GET /health` | Process status, backend, `model`, `dof`, `simulation`. Not a claim of motion readiness. |
| `GET /` | Live dashboard |
| `GET /status` | Live status JSON |
| `GET /robot/state`, `GET /scene`, `GET /tools` | State, allowlisted IDs, tool specs |
| `POST /tasks` | Submit a task. Returns 202. |
| `GET /tasks/{id}`, `POST /tasks/{id}/cancel` | Progress, events, controlled cancellation |
| `GET /robot/speed`, `POST /robot/speed` | Read or set `{"percent": 25}`. Returns 409 during a task. |
| `POST /robot/stop` | Controlled stop, **not** an E-stop |
| `POST /operator/reset` | Clears a latched state. Loopback callers only. |

The server binds to `127.0.0.1` with no authentication. The CLI refuses other addresses unless `ROBOT_API_PASSWORD` is set (12+ characters). Then every route except `/health` needs HTTP Basic (`ROBOT_API_USER`, default `operator`) or a Bearer token.

### Docker

[`Dockerfile`](Dockerfile) and [`docker-compose.yml`](docker-compose.yml) run the API and dashboard on the `mock` backend. PyBullet is a desktop app and doesn't run in the container.

```bash
docker build -t robot-controller .
docker run -p 8765:8765 -e ROBOT_API_PASSWORD=change-me-long-secret robot-controller
```

## Task contract

```json
{
  "schema_version": "1.0",
  "task_id": "demo_001",
  "actions": [
    {"action_id": "a1", "action": "identify_object", "parameters": {"object_id": "orange_screw_03"}},
    {"action_id": "a2", "action": "pick_object", "parameters": {"object_id": "orange_screw_03"}},
    {"action_id": "a3", "action": "place_object", "parameters": {"destination_id": "delivery_zone"}},
    {"action_id": "a4", "action": "perform_motion", "parameters": {"motion_id": "go_home"}}
  ]
}
```

The AI only sees named tools. There is no raw XYZ, joint array, `moveJ`, IP or script tool.

| Docs | Contents |
|---|---|
| [AI_ACTION_CONTRACT.md](docs/AI_ACTION_CONTRACT.md) | Actions, parameters and errors |
| [STATES_AND_FEEDBACK.md](docs/STATES_AND_FEEDBACK.md) | The state machine and event format |
| [SAFETY_BOUNDARIES.md](docs/SAFETY_BOUNDARIES.md) | What the gate does, and the commissioning plan |
| [VISION_INTEGRATION.md](docs/VISION_INTEGRATION.md) | How detections reach the controller |
| [PYBULLET_LINUX_SETUP.md](docs/PYBULLET_LINUX_SETUP.md) | Installing PyBullet on Linux |

## Live status

Each task is expanded into a step plan before any motion. Follow it in four places:

- **Terminal** (`demo`): a `NOW` / `NEXT` block, plus a live line with TCP, joints, gripper and held object
- **PyBullet status bar**: `[3/14] LIFTING: lift orange_screw_03`
- **JSON**: `runs/live_status.json`, rewritten atomically about 4 times per second
- **Dashboard**: `--dashboard`, then http://127.0.0.1:8765/

## Speed

`config/safety.example.yaml` sets `speed_percent_of_model_max: 10`. At 10% a UR5e runs at TCP 0.10 m/s and 0.2 m/s², joints 0.314 rad/s (18°/s) and 0.4 rad/s². Override with `--speed-percent 25`, the dashboard, the app's arm card, or `POST /robot/speed`. Every task records `speed_percent`. PyBullet plays simulations at 5× by default, so set it to 1× under Tools → Options → Motion to see real time.

## URSim + PyBullet (`ursim_sync`)

For each segment, PyBullet resolves the target, solves IK and runs the collision test. URSim then executes the move, and its actual joints stream into PyBullet at 50 Hz. Before each move, URSim must be RUNNING with safety NORMAL and both must agree within 0.5°. `ursim_host` must be a loopback address, so **this backend cannot drive a physical arm**.

```bash
# URSim running (see ursim/), powered on, brakes released; PyBullet running
python -m pip install ur_rtde
python -m robot_control.cli setup-station --config config/robot.yaml
python -m robot_control.cli demo --backend ursim_sync --config config/robot.yaml --task examples/objects/all_objects_pick_place.json
RUN_URSIM_TESTS=1 python -m pytest -m integration tests/test_ursim_sync_smoke.py -q
python scripts/mirror_ursim_to_pybullet.py        # read-only live mirror
```

Read-only digital twin (joints + calibrated object markers, app-controlled, simulate first then the real UR5e):
`python -m robot_control.digital_twin.cli check|snapshot|run|serve --config config/digital_twin.sim.json`, see
[docs/DIGITAL_TWIN_SYNC.md](docs/DIGITAL_TWIN_SYNC.md).

## Demo objects

`blue_bucket_01`, `green_ring_02`, `orange_screw_03` and `white_bucket_lid_04` are watertight STL models. See [objects/](objects). Vision labels map to them: `secchio_blu`, `anello_verde`, `vite_arancione`, `tappo_bianco`.

Grasp targets are **simulation placeholders**, not taught on a real cell. Masses are unknown, so `shake_object` is refused until a measured `mass_kg` is added to the scene.

## Layout

| Path | Purpose |
|---|---|
| [`robot_control/`](robot_control) | The package: models, config, FSM, safety, backends, API, CLI |
| [`config/`](config) | Example robot, scene and safety YAML |
| [`docs/`](docs) | Contract, states, safety, vision, PyBullet setup |
| [`examples/`](examples) | Task JSON files |
| [`objects/`](objects) | Demo object configs and STLs |
| [`scripts/`](scripts) | Object tools and the URSim mirror |
| [`stations/`](stations) | `UR5e.robot` and the generated station |
| [`tests/`](tests) | Offline and opt-in integration tests |
| [`ursim/`](ursim) | URSim in Docker |

## Assumptions and limits

- **Station:** `setup-station` builds a labelled **demo** station. Its geometry stands in for the real cell.
- **Orientation:** this PyBullet UR5e faces −X at home `[0,-90,90,-90,-90,0]°`, so the demo cell sits at negative X.
- **Gripper:** symbolic. In PyBullet, closing attaches an object within 15 mm of the TCP. Feedback is always `simulated`.
- **Vision:** camera XYZ isn't used, and any detection with a pose is rejected. Picks use taught targets. The `mock` detector is disclosed as `is_mock: true`.
- **Limits:** model numbers come from UR e-Series datasheets. Acceleration caps and example speeds are conservative configured values, not certified ones.
- No real gripper model, payload or TCP calibration exists, and a physical adapter is deliberately not implemented.
