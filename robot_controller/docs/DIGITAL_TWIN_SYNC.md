# Sync Digital Twin — UR5e + RealSense → RoboDK (MIRROR ONLY)

**MIRROR ONLY — NO PHYSICAL MOTION.** Real world → RoboDK simulation, one direction. The twin reads the UR5e with
`rtde_receive` only, reads object positions from the computer-vision server, and writes only to a RoboDK robot that is
in simulate mode with no driver connected. It has no channel that can move, stop, power or configure the robot.
**Stop Sync** stops the mirroring loop; it is *not* the robot stop.

Spec: `../../MOTIQ_Digital_Twin_Sync_Codex_Spec_EN.md`.

## What it does

| | Source | RoboDK write |
|---|---|---|
| Joints | `RTDEReceiveInterface.getActualQ()` (rad), URSim or the physical UR5e; or a scripted fake | `robot.setJoints(q_deg)`, ~10 Hz |
| Objects | vision `POST /localization/snapshot_all`: one stationary-verified RGB-D frame, flange eye-in-hand calibration, UR5e base, visible-surface centroids. Or a replay fixture (simulation only) | magenta sphere `MOTIQ_Detected_<id>` at the visible point; grey = last known, not current |

CAD objects (`Blue_Bucket_01`, …) are **not moved**: one visible-surface centroid does not give a CAD origin or
orientation. An explicit, validated `tabletop_contact_preserve_orientation` policy exists (it puts the CAD bottom face on
a measured table height and keeps the current orientation, flagged approximate), off by default.

Frames: `p_R = T_R_B · p_B`, then each marker pose is computed in its real parent frame:
`T_P_M = inv(T_S_P) · T_S_R · T_R_M` (`T_S_R` = robot `PoseAbs()`, the robot base). `Objects_Frame`'s 90° demo rotation
is handled; sending base XYZ as a local pose would be wrong.

## Modules (`robot_control/digital_twin/`)

| File | Role |
|---|---|
| `config.py` | Typed config, `${ENV}` substitution, target rules (simulation / physical), alignment sign-off |
| `frames.py` | The only rad→deg and m↔mm conversions; frame algebra; nominal UR5e DH FK |
| `telemetry.py` | `RTDEJointSource` (receive only, reconnect with backoff), `FakeJointSource`, stale detection |
| `vision_client.py` | Typed adapter for `snapshot_all` + replay fixture; never invents missing fields |
| `scene_mapper.py` | Frame gates (calibration, identity, sim/physical, stationary, age, flange cross-check) and per-object gates; label → registered id; duplicates → ambiguous |
| `robodk_writer.py` | Guard before every batch; markers created once, re-rendered only on change |
| `service.py` | State machine, joint + object loops, single writer (`runs/digital_twin.lock`) |
| `api.py`, `cli.py` | Local API on 127.0.0.1:8770 and CLI |

Reused: `scripts/mirror_ursim_to_robodk.py` is now a thin wrapper over this module (its old flange-vs-active-TCP
"error" was replaced by a like-for-like flange comparison). Vision: `CalibrationService` (flange calibration) gained
`snapshot_all()` sharing the existing `snapshot()` capture path.

## States and reasons

`DISCONNECTED` → `READY` (after Check) → `RUNNING` ⇄ `PAUSED_STALE` (telemetry frozen: scene frozen, markers grey) →
`STOPPED`. `ERROR` = a guard refused mid-run (run mode left simulate, driver connected, program running on the robot
item, RoboDK gone): loops stop, nothing more is written, the operator must Start again.

Object reasons shown in the app: `invalid_calibration`, `calibration_changed`, `robot_identity_mismatch`,
`simulation_mismatch`, `moving`, `frame_mismatch`, `stale_frame`, `no_depth`, `truncated`, `low_confidence`,
`ambiguous`, `duplicate_detection`, `not_visible`, `out_of_range`, `vision_offline`, `robot_offline`.

## Step 1 — simulate (no robot)

```bash
cd robot_controller
cp config/digital_twin.sim.example.json config/digital_twin.sim.json        # fake joints + replay objects
python -m robot_control.digital_twin.cli check    --config config/digital_twin.sim.json
python -m robot_control.digital_twin.cli snapshot --config config/digital_twin.sim.json
python -m robot_control.digital_twin.cli run      --config config/digital_twin.sim.json   # Ctrl+C = stop mirroring
```

The fake script swings the arm for 6 s then holds it for 6 s; markers update only during the holds. With the demo
station, the 3 accepted replay markers land on the top faces of the bucket/ring/screw; the lid (confidence 0.4) and
the unknown `tazza` are skipped.

**Use a RoboDK instance the MOTIQ controller is not driving.** The `robodk_sim`/`ursim_sync` controller backends move
the same `UR5e` item; the guard refuses while a program runs on it, but two writers would still fight between moves.
Either stop the controller, or open a second RoboDK (it takes port 20501), load the station into it and set
`robodk.port: 20501` (and `robodk.station_name`).

From the app: start the service, then **Skills → Sync Digital Twin → Open**:

```bash
python -m robot_control.digital_twin.cli serve --config config/digital_twin.sim.json
# or with the whole stack:
DIGITAL_TWIN_CONFIG=config/digital_twin.sim.json BRIDGE_KEY=... ./bridge/start_stack.sh
```

## Step 2 — simulate with URSim (real RTDE, still no robot)

`cp config/digital_twin.ursim.example.json config/digital_twin.ursim.json`; start the URSim container
(`ursim/README.md`), jog it from URSim's PolyScope, run `check` / `run` as above. Same receive-only code path as the
real robot.

## Step 3 — the physical UR5e (operator present, robot moved only from the pendant)

1. Calibration: **Skills → Calibration** must show *Calibrated* (flange eye-in-hand, validated, activated). Start the
   vision server with read-only robot telemetry: `VISION_ROBOT_IP=<ur ip> ./bridge/start_stack.sh`.
2. Config (git-ignored):
   ```bash
   cp config/digital_twin.example.json config/digital_twin.local.json
   export MOTIQ_UR_HOST=<ur ip>          # never committed
   ```
   Optional pins: `vision.expected_calibration_id` (from the Calibration card), `vision.expected_robot_serial`.
3. Robot only first: `check`, then `snapshot --robot-only`, then `run --robot-only`.
   **HIL 1:** move one joint at a time from the pendant; RoboDK must follow the same joint, sign and angle. Repeat in a
   few poses.
4. Base alignment `T_R_B`. Evidence from the simulation run: RoboDK's UR5e flange FK equals the nominal UR5e DH FK to
   0.05 mm / 0.0° for the same joints, i.e. RoboDK's robot base has the same axes as the UR controller's base, so
   identity is the expected value. It still needs an operator check on the real cell: jog the TCP along base +X and +Y
   from the pendant (Base frame) and confirm the RoboDK flange moves along the same RoboDK robot-base axes. Then set
   `"validated": true, "validated_for": "physical", "validated_by": "<name, date, how>"`. A simulation sign-off is
   never accepted for the physical target.
5. Objects: `check` must show `can_start_with_objects: true`. With the arm still, `snapshot`, then `run`.
   **HIL 3:** look at one fixed object from several still viewpoints; log the marker positions (status/diagnostics) and
   their spread. **HIL 4:** move the object, hold the arm still, confirm the marker moves. **HIL 5:** unplug camera /
   RTDE: status goes stale/offline. **HIL 6:** switch RoboDK out of simulate (in a controlled, non-motion setup): writes
   refused, state `ERROR`. **HIL 7:** Stop Sync: no UR command (there is no command channel; verify on the pendant log).

No accuracy is claimed: the twin reports measured values (sample age, Hz, FK consistency, flange cross-check, spread
from your logs). A good visual match is not a validation of object pose.

## Local API / bridge

Service (127.0.0.1 only; X-Bridge-Key required when `BRIDGE_KEY` is set):

```
GET  /digital-twin/status
POST /digital-twin/check
POST /digital-twin/snapshot  {"objects": true|false}
POST /digital-twin/start     {"objects": true|false}     idempotent
POST /digital-twin/stop                                   mirroring only, {"robot_command_sent": false}
```

The bridge exposes the same five routes (authenticated) and forwards only to the twin service (`--digital-twin`,
default `http://127.0.0.1:8770`); the proxy has no reference to the controller or `PhysicalArm`.

Example status (simulation, live):

```json
{"ok": true, "mode": "mirror_only", "label": "MIRROR ONLY — NO PHYSICAL MOTION", "target": "simulation",
 "state": "RUNNING", "physical_motion_enabled": false, "objects_enabled": true,
 "robot": {"source": "fake", "connected": true, "last_joint_sample_age_ms": 31.0, "hz": 9.9, "moving": false,
           "fk_consistency": {"robodk_flange_vs_ur_nominal_dh_flange_mm": 0.05, "deg": 0.0}},
 "camera": {"source": "replay", "calibration_id": "replay:digital_twin_replay.example.json", "last_reason": null},
 "robodk": {"connected": true, "station": "UR5e Controller Demo", "run_mode": 1, "driver_state": -2, "safe": true},
 "objects": {"updated": 3, "stale": 0, "not_visible": 0, "skipped": 2, "unknown": 1, "per_object": {"...": "..."}},
 "last_error": null}
```

Diagnostics: `runs/digital_twin/diagnostics.jsonl` (checks, snapshots, object rounds, rejections; rotated at 5 MB; no
images).

## Measured on this PC (2026-11-05, simulation)

- Joints: 9.3–10 Hz through the full service → bridge path. RoboDK re-renders on every `setJoints`/`setPose`/`setColor`
  (30–90 ms, single-threaded API), so that is the ceiling; identical joints are not re-sent while still (refreshed
  every 1 s), markers are re-sent only when they move > 0.5 mm or change colour.
- Guard cost per batch: `RunMode` 0.1 ms, `Busy` 3.5 ms, `ConnectedState` ~30 ms; station name re-read at most 1/s.

## Not implemented (by design)

- No real-robot movement of any kind, no automatic pick/place, no gripper.
- No full 6D CAD pose from one RGB-D centroid; markers show a visible-surface point with display-only orientation.
- No moving-camera localization: objects update only from stationary, pose-synchronized frames.

## Open items (operator / team)

- Robot IP (`MOTIQ_UR_HOST`), exact RoboDK station name, and which RoboDK instance the twin owns.
- Physical `T_R_B` sign-off (step 3.4).
- The active flange calibration id and robot serial to pin.
- Per-object orientation / table height before enabling any CAD policy.
- Hardware-in-the-loop checks 1–7 above have **not** been run (they need an operator at the robot).
