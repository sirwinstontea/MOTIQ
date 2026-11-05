# robot-chooser

Choose the MOTIQ arm model, adjust the cell hardware (tool, mounting, cameras, speed, workspace), and get
the **hardware requirements document** (`hardware.yaml`) plus the matching `robot_controller` config files,
regenerated and validated on every change. Simulation only: the chooser can pick the `mock` or `robodk_sim`
backend, never a physical one.

```
robot-chooser/
  catalog.yaml     robot FACTS: any number of arms (5 to start), DOF, limits (+source, verified), RoboDK file, scene, workspace
  hardware.yaml    CELL SETUP: active robot, backend, speed, tool, mounting, cameras
                   + generated `requirements:` block (READY/BLOCKED, blockers, warnings, payload budget)
  robot_chooser/   core.py (checks + generation), cli.py, server.py + static/index.html (local UI)
  tests/           pytest (27 tests, offline)
```

## Use it

Uses the controller's venv (pydantic + pyyaml; the generated files are validated with `robot_control`):

```bash
cd robot_controller && python -m venv .venv && .venv/bin/pip install -e '.[dev]' && cd ..
cd robot-chooser
../robot_controller/.venv/bin/python -m robot_chooser serve        # UI on http://127.0.0.1:8770/
../robot_controller/.venv/bin/python -m robot_chooser list --brand Elite
../robot_controller/.venv/bin/python -m robot_chooser select ur_ur3e
../robot_controller/.venv/bin/python -m robot_chooser set tool.mass_kg 0.35
../robot_controller/.venv/bin/python -m robot_chooser set tool.mass_status measured
../robot_controller/.venv/bin/python -m robot_chooser set-robot ur_ur3e scene_file ur_ur3e.yaml
../robot_controller/.venv/bin/python -m robot_chooser apply        # after editing the YAML by hand
../robot_controller/.venv/bin/python -m pytest -q                  # tests
```
(Windows: `..\robot_controller\.venv\Scripts\python.exe`.) Exit code: 0 = READY, 2 = BLOCKED, 1 = bad input.

## What one change does

1. Validates the input (finite numbers, 0 < speed <= 100, known robot id, `.robot` file name only, no physical backend).
2. Checks the active robot against the setup: model file present (required for `robodk_sim`), limits filled in
   **and verified**, a scene of its own, a workspace of its own, tool mass vs payload, placeholder tool data,
   workspace/targets vs nominal reach (+TCP, orientative only), joint targets with the wrong number of joints,
   uncalibrated cameras, DOF < 6 notes.
3. If nothing blocks: builds `robot_controller/config/robot.yaml`, `safety.yaml` (workspace + speed on top of
   `safety.example.yaml`), `scene.yaml`, validates them with the controller's `RobotConfig` / `SafetyConfig` /
   `SceneConfig` / `cross_validate`, and writes them (a hand-written file is moved to `*.bak` once).
   Every robot with verified limits also gets `config/robots/<id>.yaml`.
4. If something blocks: removes the previously generated active files. `config/selection.yaml` records
   `status: BLOCKED` and `robot_control.cli` then **refuses to start** (no silent fallback to the UR5e examples).
5. Rewrites `hardware.yaml` with the `requirements:` block.

The controller reads the generated files through its normal defaults (`config/robot.yaml` etc.), and the
bridge loads `config/scene.yaml` when present. Restart the controller/bridge after a change; there is no
live switching inside a running controller yet.

## Adding any other robot

The catalog is not limited to five arms: any serial arm with 1-12 axes can be added (UI "Add a robot", or):

```bash
python -m robot_chooser add-robot --id kuka_kr3_r540 --brand KUKA --model "KR 3 R540" --dof 6 \
    --reach 0.541 --payload 3 --url https://robodk.com/robot/KUKA/KR-3-R540 --robot-file "KR 3 R540.robot"
python -m robot_chooser remove-robot kuka_kr3_r540        # not allowed for the active robot
```
A new robot starts **Config incomplete**. To make it selectable as READY give it, like any robot: verified
limits with a source (`set-robot <id> limits ...` or the UI's limits box), its own scene in
`robot_controller/config/scenes/<id>.yaml`, a workspace, and for `robodk_sim` the `.robot` file from its
RoboDK library page in `robot_controller/stations/`. `library_url` can be a RoboDK or manufacturer `https://` page.

## Status of the five starting robots

| id | model | state today | what is missing |
|---|---|---|---|
| `ur_ur5e` | UR5e | Ready (repo reference cell) | real tool mass / TCP (placeholders) |
| `ur_ur3e` | UR3e | Config incomplete | its own scene (`config/scenes/ur_ur3e.yaml`), workspace, `stations/UR3e.robot` for robodk_sim |
| `ufactory_xarm5` | xArm5 (5 DOF) | Config incomplete | limits (datasheet / RoboDK model), scene, workspace, `.robot` file |
| `elite_cs63` | CS63 | Config incomplete | same as xArm5 |
| `elite_cs66` | CS66 | Config incomplete | same as xArm5 |

Joint limits and speeds for xArm5 / CS63 / CS66 are deliberately **not** filled in: copy them from the
manufacturer datasheet or the RoboDK model (`robot.JointLimits()`), add the source, then tick *verified*.
Catalogue reach/payload are for filtering only; the controller's IK, path and payload checks decide feasibility.

## API (UI uses it; loopback, no auth)

`GET /api/state` · `POST /api/select {"robot_id"}` · `POST /api/hardware {partial hardware.yaml}` ·
`POST /api/robots/<id> {library_file|scene_file|default_workspace|limits|station_name|notes}` · `POST /api/apply`.
Aliases following the multi-robot contract: `GET /robots/catalog`, `GET /robots/active`, `POST /robots/select`.
Errors: `{"ok": false, "error": {"code": "UNKNOWN_ROBOT|VALIDATION_ERROR|...", "message"}}` with 404/422.

## Not done here (next steps of the multi-robot plan)

Live `RobotSelectionManager` inside the controller (select without restart, 409 while busy), per-skill
preflight with RoboDK IK, and the app's Robots screen calling these endpoints through the bridge.
