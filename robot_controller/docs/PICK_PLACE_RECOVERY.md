# Pick/place stabilization and recovery

Keep the current local branch. The observed failures have concrete causes; starting a fresh branch would not repair
the already-invalid running RoboDK station. No branch switch, commit, push, hardware connection, object repositioning
or robot execution was performed while implementing these fixes. The attached test snapshot was not edited.

## Read-only station evidence

Inspection in the robot base frame found:

- Table: bottom-center `[-0.45, 0, -0.02]` m, size `[0.50, 0.80, 0.02]` m. Therefore top Z=0, center XY=(-0.45,0),
  X bounds [-0.70,-0.20], Y bounds [-0.40,+0.40]. The table pose matches the controller configuration.
- Bucket bottom: approximately `[-0.45, +0.20, -0.0295]` m: **29.5 mm below the surface**.
- Screw bottom: approximately `[-0.62, -0.24, +0.145]` m: at the old bucket location.
- Bucket/table collision pair: **disabled**. The object import specification explicitly disabled that pair.

The fixed delivery TCP height of 28 mm minus the bucket's 57.5 mm TCP-to-bottom offset explains the -29.5 mm result.
This was not a robot-reach failure. The spoken X range in the example treated a bottom-center pose like a minimum
corner; a center at -0.20 is actually the edge of this table. A footprint cannot fit with its center on the edge.

## Corrections

### Persistent startup repair and center-then-push workflow

A one-off unsaved collision-map edit is lost when RoboDK reopens an old station. The controller now reapplies
and verifies the configured objects' table pairs on **every simulation connection**. This only enables required
protection; it neither disables unrelated pairs, moves geometry nor saves/replaces a station. A missing object,
ambiguous object name, mismatched table, active driver, non-simulation mode or moving robot refuses preparation.
Individual pickup/stroke checks still fail closed if a pair is later disabled. Pushed objects now require the
same table-pair protection as held objects during trajectory checks and execution.

Existing exact table contacts need an explicit, offline geometry edit, not repeated resets/retries. Stop the old
MOTIQ stack first; leave the token-server terminal open. Start RoboDK with the selected station, then from the
repository root run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bridge\start_robodk.ps1 -PrepareSimulationScene
```

The flag runs the existing controller's new `prepare-simulation --repair-resting-contacts` command before its
executor starts. It raises only upright, **unheld** registered objects within 0.5 mm of the table surface to
1 mm above it, matching the clearance used for new table placements. It preserves XY, orientation and parents,
checks static object/table collision afterward, and rolls back on failure. Deeper penetration, attachments to
any robot/tool, invalid footprints or remaining collisions require operator correction; this is not an arbitrary
scene-repair algorithm. No robot motion, driver connection, fault reset, task retry or station save occurs.
The CLI refuses while a controller listens on the default port 8765; stop any controller using a custom port too.

For an existing simulated camera, append `-SimVisionUrl http://127.0.0.1:8000` only if that server is already running
in RoboDK mode. Without that flag the launcher still explicitly uses mock detection, not simulated-camera vision.
Newly created demo object frames now start at Z=1 mm; existing frames are deliberately not repositioned by imports.

After startup, refresh the app/new Voice session, query status and scene, then at 10% request and approve:

1. "Pick the blue bucket and put it in the middle of the table." The request uses `location=center`.
2. After confirmed release, "Push the blue bucket to the left 10 centimeters." The request must contain
   `direction=left, distance_m=0.10`; not two default 5 cm pushes. Direction is +Y in robot base coordinates.
3. Verify the live bucket position. For the supplied table, center XY=(-0.45,0), expected post-push XY=(-0.45,0.10).

Push planning reads the bucket's current simulator pose, not its original slot. Each motion remains approved,
IK/limits/path/collision validated, and may still be refused for a genuinely infeasible path. Table pushing is
symbolic translation with 1 mm numerical clearance, not physics/friction simulation. No live movement is claimed
from the mocked regression tests. Voice now explicitly avoids unchanged retries for configuration/collision errors.

Validation of this persistent-startup change: **309 passed, 10 deselected** in the broader non-moving regression
suite (same exclusions as below), plus **4 newly added CLI tests passed** afterward; focused placement and CLI
run **29 passed**. PowerShell parser, CLI help and `git diff --check` passed; no editor errors were reported in
the changed backend, CLI, placement tests or Voice prompt. The latest live controller request reported an aborted
RoboDK socket, no RoboDK process was found, and port 20500 timed out both inside and outside the sandbox. Thus
the new live station preparation and actual center/push motions remain **unexecuted**, pending an explicit restart.

### Follow-up: collision-map refusal during bucket lift

The subsequent `location=center` request reached the grasp and attached the bucket, then refused
`blue_bucket_01_lift` with `PATH_CHECK_INCONCLUSIVE`: its nested cause was `CONFIG_ERROR`, the disabled
bucket/table pair. Resetting alone cannot repair a collision map. The bucket remains held after this failure.

Pickup prerequisites now check the object's table collision pair during task preflight, again before approach,
and immediately before attachment. Missing pairs therefore fail before a pickup starts, rather than first
being discovered after grasping. The carried-object checks remain in place; this does not bypass collisions.

Read-only follow-up inspection confirmed simulation mode, disconnected driver, idle robot, bucket parent
`Gripper TCP`, and all four registered objects at table-top Z=0. RoboDK's static collision queries count these
exact surface contacts as collisions. Enabling the missing pairs alone does not establish a clear path.

During this follow-up the four registered object/table collision pairs were explicitly enabled in the running
station (64 to 68 active pairs); the entire remaining active-pair set was verified unchanged. No robot movement,
object repositioning, fault reset, task retry, or station save was performed by that repair. The live map is
unsaved. Object clearance correction requires a separate, explicit station edit; do not disable table pairs.

**Do not restart the controller while this bucket is attached.** Symbolic grip tracking is process-local.
First resolve the station clearance, explicitly reset through the approved simulation-reset tool, then request
placement of the **already-held** bucket at `location=center` (not a new pick-and-place). Each motion still
requires normal approval and full trajectory validation. Restart to load the preflight code only after a verified
release; preserve the RoboDK station and token-server terminal. No moving integration test was performed.

Follow-up validation: focused placement/contact/path/reset tests **62 passed**; broader regression **298 passed,
10 deselected**, with the same opt-in/external-driver exclusions and Windows-incompatible service module
excluded as documented below. The first sandboxed regression run had temporary-directory permission errors;
the rerun outside that sandbox passed. Editor diagnostics found no errors in the four changed Python files;
`git diff --check` passed. These results do not constitute a live moving pick/place test.

- Source scenes now declare table-supported destinations and a live object-anchored container destination.
  `support_surface: table` computes release from the held object's actual TCP-to-bottom offset, not a universal
  taught TCP Z. `support_surface: container`, `container_id: blue_bucket_01` follows the current bucket pose.
- Pick/place/push Cartesian retargeting is task-local; taught station targets and source scene target coordinates
  are not overwritten. Original-slot destinations therefore remain original slots.
- Dynamic named placement plans/checks approach, release and retreat before starting that phase, rechecks before
  execution, and verifies the released object's actual position. Every movement still uses the existing executor.
- Release below the modeled table is refused without detaching the object. Carried-object motion requires an
  enabled held-object/table collision pair, checked before trajectory testing and execution.
- The object import configuration no longer disables table collisions. Explicit protected object pairs are enabled
  when importing new objects or checking existing ones; failed configuration is reported, not ignored.
- `/scene` and `get_scene` now include live simulator object positions, provenance and table clearance. Table metadata
  spells out bottom-center semantics, center, top and bounds. A mismatched actual/configured table pose fails closed.
- `place_on_table` and Bridge `move_object_on_table` accept `{"location":"center"}`. Voice uses that for "middle of
  the table", with no guessed XY. Explicit XY and relative forms remain supported, mutually exclusive with center.
- Simulation `check_placed` verifies actual object geometry, not just an empty gripper, and does not move to an old
  camera look target. `camera_verified: false` must never be narrated as camera confirmation.

## Existing simulated camera

The existing `computer-vision/vision/eyes/capture/sim_render.py` renderer updates object poses per frame. It is not
the controller's mock detector. Previous Windows startup selected mock detection and bypassed camera recognition.

To use an already-running vision server configured explicitly with the RoboDK camera, restart the stack with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bridge\start_robodk.ps1 -SimVisionUrl http://127.0.0.1:8000
```

This selects the existing `vision_file` detector, shares the Bridge handoff file, and enables `--sim-vision`. Picks
require a fresh, confident recognition; stale/missing detections or safety-stop frames refuse the request. There is
no silent fallback to mock recognition. Outside this explicit mode, mock detection remains labeled as mock.

Start the existing vision environment separately if it is not running. Its CLI is `vision.eyes.cli run --mode robodk
--robot-pose robodk --port 8000`, with `--robodk-robot` set to the selected station robot name. Do not use `--mode auto`,
which may select a physical webcam/RealSense. No new vision server, environment, detector or credentials were created.
The correct Python command is the one from your existing vision environment; no computer-vision venv path is assumed.

Camera recognition and geometric localization are distinct: motion uses live RoboDK geometry in the configured
base frame, not uncalibrated camera centroids. A visible camera detection alone does not verify an arbitrary
destination. The result may report camera visibility separately, but camera-based placement confirmation is not
claimed without a spatial verification pipeline.

## One-time repair of the current station (operator action)

Code changes do not lift an already-penetrating bucket out of the table or silently change the live collision map.

1. Stop issuing movement commands and save a backup of the current RoboDK station before editing it.
2. Without jogging the robot, correct the bucket pose so its modeled bottom is approximately 1 mm above the table.
   For this inspected table the desired bottom Z is +0.001 m in the **robot base frame**. Preserve the chosen XY/yaw.
   If the object's parent is another frame, transform that base-frame position into its parent frame; do not paste
   base coordinates into an arbitrary local-frame dialog. Check other objects for existing penetration too.
3. In RoboDK's collision map enable relevant robot moving links, tool and carried-object pairs against the table.
   Specifically restore Blue_Bucket_01/table; do not disable table pairs to make a refusal disappear. Verify geometry
   clearance and the cell map before motion. The corrected importer prevents reintroducing the explicit exclusion;
   do not use `--replace` as a recovery shortcut because it replaces object geometry/poses.
4. Apply robot-chooser so generated scenes include the support semantics. Stop/restart the MOTIQ stack terminal;
   leave the token server and RoboDK open. Refresh the app and start a new Voice session. Use `-SimVisionUrl` only
   with the existing simulated-camera server configured and running.
5. Query `arm_scene`: verify center, bounds, live object locations, clearance and perception source.
6. Explicitly request/approve `Reset simulation fault` if latched. Reset does not repair geometry or retry anything.
7. At 10%, request/approve a small feasible test. Then test "pick the bucket and place it at the middle of the table";
   the tool must send `location=center`. Verify its actual position and positive table clearance afterward.
8. Move/place the bucket at the delivery destination, then request the screw-to-bucket operation. Confirm that the
   resolved destination follows the bucket's **new** XY, not the original slot. Inspect geometry feedback; do not
   accept a camera-confirmation claim when `camera_verified` is false.

## Limitations and scope

Container placement currently releases symbolically ABOVE the opening at its actual pose. It does not simulate
gravity, insertion, friction, settled contents or cargo following a subsequently moved container. `inside` remains
a legacy destination ID, not a proof that an object fell inside. Do not claim physically verified insertion.
Upright objects and an axis-aligned horizontal table are required; tilted support is refused. Geometry bounds use
configured dimensions, not a certification of arbitrary CAD meshes. Source scenes and actual meshes must match.
Failures remain latched with explicit approved recovery. No automatic retries, hardware fallback, collision bypass
or motion-result caching was added. Further refactoring should follow these regression tests, not replace the
working controller/Bridge/approval architecture wholesale.

## Verification and files (2026-11-13)

- Regression after placement/import-policy fixes: **295 passed, 10 deselected**. Exclusions: eight opt-in moving
  integration tests and two known external physical-UR-driver tests; the Linux-only `fcntl` module was ignored.
- Final targeted placement/Bridge/path/generic-motion/performance tests: **64 passed**, including the additional
  simulated-camera no-fallback case. Another geometry/import/contact subset passed **87 tests**.
- TypeScript typecheck, focused ESLint, PowerShell launcher syntax parsing and `git diff --check` passed.
- Editor diagnostic collection did not complete; runtime/import tests and TypeScript checks above did complete.
- Source configuration was regenerated through robot-chooser and remains UR5e/robodk_sim/READY. The running
  controller and live station were NOT restarted/repaired. No new moving integration test was executed.

Implementation: [controller interfaces](../robot_control/controller.py), [models](../robot_control/models.py),
[scene configuration](../robot_control/config.py), [table geometry](../robot_control/planning.py),
[scene metadata](../robot_control/scene_registry.py), [RoboDK](../robot_control/backends/robodk_sim.py),
[mock center resolution](../robot_control/backends/mock.py), [executor](../robot_control/task_executor.py),
[read-only API](../robot_control/api/app.py), [object importer](../robot_control/sim_objects/robodk_import.py),
[import policy](../objects/object_specifications.json), [Bridge](../../bridge/motiq_bridge.py),
[launcher](../../bridge/start_robodk.ps1), [Voice schemas](../../groky-app/src/devices/tools.ts),
[Voice instructions](../../groky-app/src/voice/prompt.ts).

Destination source scenes: [UR5e](../config/scene.example.yaml), [xArm5](../config/scenes/ufactory_xarm5.yaml),
[UR3e](../config/scenes/ur_ur3e.yaml), [CS63](../config/scenes/elite_cs63.yaml), [CS66](../config/scenes/elite_cs66.yaml).
Configuration edits do not constitute live compatibility tests of those robots.

Tests: [live placement geometry](../tests/test_live_placement.py), [table paths](../tests/test_paths_and_table.py),
[Bridge](../tests/test_bridge_generic_motion.py), [transient contact targets](../tests/test_contact_policy.py).
Documentation: this guide and [table/contact behavior](TABLE_CONTACT_PATHS.md). Earlier local edits are preserved.
