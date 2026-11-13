# Table placement, contact and generated paths (RoboDK simulation)

These extend the existing task executor, safety policy, Bridge and Voice approval flow. No separate motion
controller or physical robot path is introduced. All new motion requests include optional task `speed_percent`.

## Destinations across the table

The legacy `delivery_zone` remains a named taught point, not a workspace boundary. `place_on_table` accepts
either absolute base-frame `x_m`, `y_m`, or `direction`, `distance_m` relative to the held object's current station
position. Bridge/Voice `move_object_on_table` identifies, picks and places in one task with one approval.
Example: "Move the blue bucket ten centimetres right at twenty percent speed."

The full configured horizontal, axis-aligned table is available, subject to reachability and path validation.
The configured box footprint must fit fully on the table (RoboDK uses the actual upright object's yaw).
Release preserves the actual TCP/object offset and leaves a 1 mm
simulated surface clearance. Rotated tables are explicitly unsupported. Grasp/release are symbolic, not physics.
Simulation `check_placed` verifies named destinations using live geometry, not arbitrary table XY placement or
camera-confirmed insertion. See the recovery guide for the explicit simulated-camera recognition mode.

## Contact policy and tags

Robot-chooser hardware settings:

```yaml
workspace_mode: kinematic
collision_policy: table_and_tagged
```

The default `all` preserves the previous collision map. `table_and_tagged` temporarily ignores active collision
pairs between registered untagged scene objects/geometry and the selected robot/tool or each other during tests and
execution. Table, self-collisions, other robots, unknown geometry and tagged objects remain protected. Active pairs
are restored on completion, cancellation or failure. Missing table geometry or absent active table/robot pairs
refuse execution. This is not a certification that the station collision map is complete.

In kinematic/contact mode, the TCP Z floor is the configured table surface rather than an extra 15 mm clearance;
whole-link/tool clearance still depends on RoboDK geometry and collision pairs. There is no lateral table-footprint
restriction on free TCP movements, but placed/pushed objects must stay supported on the table.

Add `avoid_collision: true` to an object's definition in the active robot's source scene, specified by `scene_file`
in [catalog.yaml](../../robot-chooser/catalog.yaml), then apply robot-chooser and restart. Do not edit generated
scene files: regeneration replaces them. Tagged objects cannot be pushed. Tags currently have a configuration
field, not an in-app editor. `arm_scene` reports policy, tags and table geometry without moving.
The same `avoid_collision` field is available under `station_geometry` entries. The table is always protected,
regardless of its tag. Untagged registered visual markers such as `delivery_pad` are not obstacles in this policy;
unknown objects added manually to RoboDK remain protected until explicitly configured.

For example, preserve the other fields and add the tag to the desired registered object's YAML entry:

```yaml
objects:
  orange_screw_03:
    avoid_collision: true
    # Keep the existing dimensions, station_item and taught target fields here.
```

`push_object` accepts explicit `direction` and `distance_m` together. One task executes that stroke rather than
repeating a fixed 5 cm action. It derives XY from the current station object pose and uses the object's taught
push Z/orientation. Start/end footprints must fit on the table. All four approach/stroke/retreat segments are checked
before the first move and rechecked individually. Omit both parameters only for the legacy taught path.
Objects need a configured push posture. They ride symbolically with the tool during the stroke: no friction,
contact-force or gravity simulation is claimed. Contact permission is never a physical robot safety authorization.

## Paths

For batched collision-map updates, task timing feedback and optional circle geometric tolerance, see
[Motion performance](MOTION_PERFORMANCE.md). Default paths and collision-check resolutions remain unchanged.

`follow_path` accepts 1-256 `{x,y,z}` base-frame waypoints in metres and preserves current tool orientation.
All straight segments, including entry, are chained through IK and validated before moving; each is revalidated
before execution. State drift rejects execution rather than silently changing the planned path.

`circle_object` draws a closed polygon around the object's current XY at explicit `radius_m` and absolute
base-frame `z_m`, with optional `clockwise`. Voice asks for missing radius/height. It uses at least 24 and at most
256 segments, targeting roughly 1 cm maximum arc length per segment. Requests exceeding the computational bound
are refused. This is a stopped-segment polygon, not a blended spline or exact MoveC. The straight entry is checked.
Neither tool provides automatic detours/global obstacle planning. Existing RoboDK path singularity/limit checks,
collision policy, cancellation, speed limits and faults remain authoritative. No universal Jacobian guarantee is claimed.

Controller action examples, inside the existing task contract:

```json
{"action_id":"p","action":"push_object","parameters":{"object_id":"blue_bucket_01","direction":"right","distance_m":0.10}}
{"action_id":"t","action":"place_on_table","parameters":{"x_m":-0.4,"y_m":-0.1}}
{"action_id":"c","action":"circle_object","parameters":{"object_id":"blue_bucket_01","radius_m":0.15,"z_m":0.25,"clockwise":true}}
```

Top-level task `speed_percent` overrides the task only, not the default. These examples are not certified reachable
for any robot. Compatibility requires the selected model's matching scene, limits, frames, geometry and feasible IK.

## Restart and manual validation

See [pick/place recovery](PICK_PLACE_RECOVERY.md) for live object-relative destinations, `location: center`,
simulated-camera recognition and repair of an already invalid station/collision map.

Stop the MOTIQ stack terminal with Ctrl+C. Leave RoboDK and the separate existing token-server terminal open.
From the repository root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bridge\start_robodk.ps1
```

Open http://localhost:8081, refresh and start a new Voice session to load the tools. Query status and scene first;
confirm model, simulation mode, policy, table frame and default speed. Then explicitly request/approve a 1 cm motion
at 10% in a clear reachable direction. Inspect actual feedback before larger requests. Test path requests and pushes
separately, with small distances and clear table space. On failure stop the sequence; explicitly request/approve
"Reset simulation fault", correct the cause and request a new movement. Reset never retries the failed action.

For another compatible robot, select it through robot-chooser, confirm READY with its own source scene, apply and
restart controller/Bridge. Recheck geometry, tags, TCP and limits before repeating the small simulation test.
Automated unit tests use mocks; they do not establish live RoboDK/model compatibility. Moving integration tests
remain opt-in and were not run as part of this change.

## Verification recorded on 2026-11-12

- Final focused controller/Bridge run: **61 passed** (paths/table, contact policy, push, generic motion, Bridge routing).
- Contract-inclusive earlier run: **70 passed**; later additions are covered by the final focused run.
- Robot-chooser: **50 passed, 4 failed**. New workspace/contact policy cases pass. Remaining tests assume an
  unconfigured five-entry xArm5 model and unconfigured Elite limits, whereas the current catalog records six RoboDK
  joint slots for xArm5 (including a constrained axis) and configured Elite limits. The catalog was not changed to
  satisfy these old assumptions. Test fixtures now isolate policy defaults from the operator's local selection.
- Broad controller regression before the last small additions: **248 passed, 2 failed, 8 integration tests excluded**.
  Failures: `test_touch_limits_and_sim_refusal`, `test_teach_offset_round_trip`; these exercise the unavailable
  external physical UR driver, not the new RoboDK path. The Linux-only digital-twin test module could not collect on
  Windows because it imports `fcntl`; it was excluded from that regression run, not treated as passed.
- `npm run test:voice` passed, including required approval for new path/contact tools and simulation fault reset.
- `npm run typecheck`, focused ESLint and `git diff --check` passed.
- No new robot movement or moving RoboDK integration test was executed. No hardware was connected. The active
  disk selection remains UR5e and is READY; loading the changed policy requires a stack restart/new Voice session.

## Files for this extension

Controller: [models](../robot_control/models.py), [action registry](../robot_control/action_registry.py),
[config](../robot_control/config.py), [controller interface](../robot_control/controller.py),
[geometry planning](../robot_control/planning.py), [executor](../robot_control/task_executor.py),
[RoboDK backend](../robot_control/backends/robodk_sim.py), [mock backend](../robot_control/backends/mock.py),
[scene metadata](../robot_control/scene_registry.py).

Bridge/app: [Bridge routing](../../bridge/motiq_bridge.py), [tool schemas](../../groky-app/src/devices/tools.ts),
[app routing](../../groky-app/src/devices/bridge.ts), [Voice prompt](../../groky-app/src/voice/prompt.ts),
[Voice session](../../groky-app/src/voice/session.ts), [approval predicate](../../groky-app/src/lib/armApproval.ts).

Configuration: [chooser](../../robot-chooser/robot_chooser/core.py), [hardware policy](../../robot-chooser/hardware.yaml).
Generated active scene/safety configuration was refreshed through robot-chooser, preserving robot selection.

Tests: [paths/table](../tests/test_paths_and_table.py), [collision/contact](../tests/test_contact_policy.py),
[Bridge](../tests/test_bridge_generic_motion.py), [allowlist](../tests/test_contracts.py),
[chooser policy](../../robot-chooser/tests/test_workspace_mode.py),
[chooser fixture isolation](../../robot-chooser/tests/conftest.py),
[Voice approval](../../groky-app/scripts/test-voice-hearing.ts).

Documentation: this guide, [motion](GENERIC_ROBODK_MOTION.md), [action contract](AI_ACTION_CONTRACT.md),
[Bridge README](../../bridge/README.md), [Windows startup](../../bridge/WINDOWS_ROBODK_START.md).
Earlier generic-motion/reset/startup edits and unrelated local files were preserved; nothing was committed or pushed.
