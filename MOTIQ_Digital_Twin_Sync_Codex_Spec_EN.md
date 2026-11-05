# MOTIQ — Physical UR5e + RealSense → RoboDK Digital Twin Sync

**Codex implementation brief (English)**  
**Target repository:** https://github.com/sirwinstontea/MOTIQ  
**Repository baseline inspected:** `main` at commit `282e5b7a3c7bf131f654f66cff560c9c0b4a6d14` (2026-11-05).  
**Target environment:** Linux PC, RoboDK running locally, connected physical Universal Robots UR5e, Intel RealSense D435 mounted rigidly near the flange, existing MOTIQ computer-vision and app services.  
**Requested feature:** An app-runnable, **read-only-to-the-physical-robot** skill/function named **Sync Digital Twin**, capable of updating the virtual UR5e joint configuration and detected-object positions in RoboDK from physical telemetry and calibrated vision.

> **Important scope boundary:** This feature is **real world → digital twin only**. It must never send a motion command to the physical UR5e. Writing simulated joint values and object transforms inside RoboDK is allowed only when the selected RoboDK robot is in simulation mode and not connected to a real robot driver. Do not use this work to enable automatic pick, approach, or physical motion. Do not pretend a visible-surface centroid is a verified grasp pose or CAD-model origin.

---

## 1. Product behavior / user story

After the RealSense-to-robot **eye-in-hand calibration has been validated**, the operator opens **MOTIQ → Skills** and selects **Sync Digital Twin**. The app offers:

- **Check connections:** inspect the real UR5e receive-only telemetry, RealSense vision server, loaded calibration, RoboDK station, and object model mapping.
- **Snapshot:** obtain one coherent set of physical joint angles and valid object detections, then update the simulated UR5e and the corresponding virtual markers/CAD models once.
- **Start live sync:** continually mirror the physical arm into the RoboDK robot and update objects as valid observations arrive.
- **Stop sync:** stop the background mirroring loop; **do not stop or move the physical robot**. This is not the existing emergency-stop action.
- **View diagnostics:** status, source age, calibration ID, last frame/measurement time, counts of updated/skipped objects, and relevant errors.

**Success demonstration:** An operator manually moves the physical UR5e using its normal pendant controls; the UR5e model in RoboDK follows its joint motion. When a calibrated RealSense detection of an object is available while the arm is stationary, the corresponding object marker, and optionally its CAD model when a safe pose-estimation policy is available, updates to its estimated physical location in the RoboDK scene. All of this is initiated and controlled from the MOTIQ app without commanding the physical robot.

The app must display an unmistakable **MIRROR ONLY — NO PHYSICAL MOTION** label during this mode.

---

## 2. Repository analysis — reuse before adding code

The repository already includes essential components. Inspect their *current contents* again before editing, because the repository may have changed:

| Existing file/component | Relevant role |
|---|---|
| `robot_controller/scripts/mirror_ursim_to_robodk.py` | One-way `RTDEReceiveInterface.getActualQ()` → `robot.setJoints(...)`, with RoboDK simulation/driver safety guards. **Reuse/refactor**, do not create a competing implementation. The script is currently designed around URSim and restricts non-loopback hosts unless explicitly allowed. |
| `computer-vision/vision/eyes/capture/realsense.py` | Aligned RGB/depth RealSense frames and intrinsics. |
| `computer-vision/vision/eyes/calibration/hand_eye.py` | Hand-eye transforms and base-coordinate computation; currently uses `T_tool_cam` / `tcp_pose` terminology and includes a read-only RTDE TCP source. **Verify the actual reference frame of the saved calibration before using it.** |
| `computer-vision/vision/eyes/pipeline.py` | Adds `xyz_cam_m` and potentially `xyz_base_m` to detections; checks arm-still and timestamps. |
| `computer-vision/vision/eyes/schemas.py` | `EyeObject`: `id`, `label`, `conf`, `xyz_cam_m`, `xyz_base_m`, `depth_valid_ratio`, `depth_points`, `yaw_base_deg`. |
| `computer-vision/vision/eyes/server/app.py` | `GET /state/arm`, `GET /locate/arm?label=...`, and calibration endpoints. `/locate/arm` returns `position_base_m`, `calibration_id`, and `point_semantics: visible_surface_centroid_3d` when valid. |
| `robot_controller/robot_control/sim_objects/robodk_import.py` | Imports configured STL objects into RoboDK; handles an `Objects_Frame`. **Do not repeatedly import meshes in the live loop.** |
| `robot_controller/objects/object_specifications.json` | Object IDs, RoboDK names, display frame, initial poses. Initial poses are demonstration placeholders, not measurements. |
| `robot_controller/objects/<id>/config/<id>.json` | Physical dimensions and CAD-local origins for individual objects. Several CAD origins are at the **center of the lowest flat face**, not the observed top surface. |
| `bridge/motiq_bridge.py` | Authenticated app-to-PC bridge, health endpoints, and existing vision/controller access. |
| `groky-app/src/screens/Skills.tsx`, `src/skills/index.ts`, `src/skills/runner.ts`, `src/skills/schema.ts` | Built-in skills and app skill controls. Current generic skills runner is designed for finite actions and can route robot movements; **do not route a persistent read-only sync through normal arm motion primitives**. |

**Important existing issue to verify:** `robot_controller/docs/VISION_INTEGRATION.md` describes an older detector contract that ignores `coords_mm`. Do **not** read the older planar homography `coords_mm` as a 3D base pose. Use validated `xyz_base_m` / `position_base_m` with explicit calibration and frame metadata. Also, `--robot-ip` may configure the calibration sampling source without necessarily configuring the normal runtime base-coordinate path; confirm that the live vision pipeline is actually producing *physical-UR-frame* coordinates before mirroring objects.

The existing mirror script compares a RoboDK **flange FK** position against UR's **actual TCP** pose. These are not necessarily equal because the active UR TCP can be offset. Replace that comparison with like-for-like frames, or clearly label the offset-related difference; do not treat this raw distance as an error metric.

---

## 3. Architecture: one-way, two independent streams

```text
PHYSICAL UR5e (Ethernet)
      │
      │ RTDE RECEIVE ONLY: actual joints q[6], timestamps, speed/status
      ▼
  Linux MOTIQ PC ────────────────────────────────┐
      │                                         │
      │ RealSense D435 RGB + depth              │
      ▼                                         │
 computer-vision :8000                          │
      │ detect/track, depth, calibrated         │
      │ camera→flange→UR base coordinates       │
      ▼                                         ▼
    validated objects                     actual joint angles
      │                                         │
      └────────────────┬────────────────────────┘
                       ▼
       digital_twin_sync service (new)
       read-only UR input, read-only vision input
       station-frame conversion + quality gates
                       │
                       │ RoboDK API, SIMULATION ONLY
                       ▼
             RoboDK virtual UR5e
          robot.setJoints(q_degrees)
          virtual marker/object.setPose(...)
                       ▲
                       │ local authenticated service control
                       │
   MOTIQ app → authenticated bridge → status/snapshot/start/stop
```

Two update rates should be configurable and handled independently:

- Robot joints: initial target **10 Hz** (configurable, e.g. 5–15 Hz, no fixed accuracy guarantee).
- Objects: initially **1–3 Hz** or when a new, stable calibrated detection appears; do not call `GET /locate/arm` independently for every tracked object in a blocking loop.

The robot pose is useful while moving. With the repository's current **stationary-only** vision localization policy, object world positions should update only when the UR pose and camera frame are valid and synchronized during a stopped observation. A future moving-camera time-synchronization feature may be developed separately; do not silently infer world coordinates from a mismatched pose/frame pair.

---

## 4. Coordinate frames and correctness requirements

Define:

- **B:** base frame of the **physical** UR5e.
- **F:** physical UR5e **flange** reference (not assumed to be the active TCP).
- **C:** aligned RGB camera optical reference used by the depth deprojection.
- **R:** base frame of the **virtual** UR5e in RoboDK.
- **S:** RoboDK station/root frame.
- **P:** RoboDK parent frame of an object, e.g. `Objects_Frame`.
- **O:** model origin of a virtual CAD object.

Physical object point (meters, homogeneous coordinates):

```text
^B p_visible = ^B T_F(q_actual) · ^F T_C · ^C p_visible
```

`^F T_C` must come from the **validated hand-eye artifact**, not be replaced by the approximate camera mounting offsets. Previous camera-mount notes: approximate longitudinal X offset **75 mm**, Z offset **35 mm**, unknown sign/Y depending on the mechanical reference. These are only initial guesses and may refer to the active TCP instead of the flange. The calibration board has **21.8 mm square size**; that is calibration metadata, not something to apply again to object positions after calibration.

When RoboDK's virtual base axes match the physical UR base, configure `^R T_B = I`. However, **identity is permitted only after an operator validates the frame alignment**. Otherwise provide an explicit measured rigid `^R T_B` in JSON. No automatic assumption based on how the models happen to look on screen.

```text
^R p_visible = ^R T_B · ^B p_visible
^S T_O       = ^S T_R · ^R T_O
^P T_O       = inverse(^S T_P) · ^S T_O
```

The `^R T_O` (full CAD model pose) is **not recoverable** from one visible-surface centroid alone. The system must implement two distinct modes:

1. **Recommended first milestone — visible-point marker:** draw or reposition a small dedicated marker at `^R p_visible` and label it `object_id / visible surface point`. This is an honest visualization that requires no guess about hidden object geometry or orientation.
2. **Optional CAD object placement (explicit policies):** for registered objects with known dimensions, CAD local origin, assumed tabletop contact and validated orientation/yaw, estimate a model-origin pose. For example, the repository's blue bucket STL has a 115 mm height and an origin at the center of its bottom face. A centroid of the *visible upper surfaces* is **not** that bottom-face origin. Use a per-class model fitting/plane-contact strategy or keep the full CAD object unchanged and update only the marker. Preserve existing CAD orientation when the pose is underconstrained and indicate that it is an approximation.

**Units:** RealSense/world positions use **meters**; physical joint angles are **radians**; RoboDK joint values and object translations are typically **degrees** and **millimeters**. Put conversions at single, well-tested interfaces. Do not mix UR rotation vectors (radians) with RoboDK's Euler `*_mm_deg` configuration format.

---

## 5. Proposed file/module additions

Prefer the smallest integration that fits existing conventions. Suggested structure:

```text
robot_controller/
  robot_control/
    digital_twin/
      __init__.py
      config.py                  # typed config, checks, units, frame mapping
      ur_receive.py              # RTDEReceiveInterface only
      vision_client.py           # existing vision service client / schemas
      scene_mapper.py            # calibrated detection -> RoboDK item mapping
      robodk_writer.py           # simulation-only setJoints + object/marker poses
      service.py                 # state machine, loop, snapshot, start/stop
      api.py                     # local FastAPI endpoints, if using separate service
  config/
    digital_twin.example.json
  tests/
    test_digital_twin_*.py
  docs/
    DIGITAL_TWIN_SYNC.md
bridge/
  motiq_bridge.py                # authenticated read-only-sync control proxy
  README.md                      # document new routes
 groky-app/
  src/screens/Skills.tsx         # read-only Sync Digital Twin card/entry
  src/skills/...                 # typed read-only capability; no motion primitive
  src/devices/bridge.ts          # authenticated start/stop/snapshot/status client
```

You may implement the service inside the existing bridge instead of as a separate local FastAPI app if that is simpler and robust, but ensure a **single owner of the RoboDK writer and RTDE read connection**. The backend loop must outlive a single HTTP request and stop reliably without blocking the app. Never start multiple concurrent writers to the same station.

**Do not edit unrelated perception, planner, movement, or safety gates unless a minimal adapter is required.** Avoid changing existing physical robot tools.

---

## 6. JSON configuration (example; values are not physical measurements)

Create `robot_controller/config/digital_twin.example.json` and validate its schema. Replace all placeholders through configuration or operator setup; never embed a real robot IP or credentials in Git.

```json
{
  "schema_version": "1.0.0",
  "mode": "mirror_only",
  "physical_robot": {
    "model": "UR5e",
    "host": "<UR5E_IP_FROM_LOCAL_ENV>",
    "telemetry": "rtde_receive_only",
    "require_manual_robot_motion": true
  },
  "vision": {
    "base_url": "http://127.0.0.1:8000",
    "source": "arm",
    "require_validated_eye_in_hand": true,
    "require_pose_reference": "flange",
    "require_matching_robot_identity": true,
    "min_confidence": 0.65,
    "min_depth_valid_ratio": 0.60,
    "max_detection_age_ms": 750,
    "max_robot_pose_age_ms": 250,
    "update_only_when_stationary": true
  },
  "robodk": {
    "host": "127.0.0.1",
    "port": 20500,
    "station_name": "<EXACT_STATION_NAME>",
    "robot_item_name": "UR5e",
    "require_simulation_mode": true,
    "reject_connected_robot_driver": true,
    "base_alignment": {
      "frame_from": "ur_physical_base",
      "frame_to": "robodk_virtual_robot_base",
      "validated": false,
      "T_R_B": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
      "translation_units": "m"
    },
    "object_parent_frame": "Objects_Frame",
    "create_visible_point_markers": true,
    "allow_cad_model_reposition": false
  },
  "sync": {
    "joints_hz": 10,
    "objects_hz": 2,
    "stale_after_ms": 1500,
    "on_stale": "freeze_and_mark_stale",
    "auto_start": false
  },
  "objects": {
    "blue_bucket_01": {
      "robodk_item_name": "Blue_Bucket_01",
      "marker_name": "MOTIQ_Detected_blue_bucket_01",
      "pose_policy": "visible_surface_marker"
    },
    "green_ring_02": {
      "robodk_item_name": "Green_Ring_02",
      "marker_name": "MOTIQ_Detected_green_ring_02",
      "pose_policy": "visible_surface_marker"
    },
    "orange_screw_03": {
      "robodk_item_name": "Orange_Screw_03",
      "marker_name": "MOTIQ_Detected_orange_screw_03",
      "pose_policy": "visible_surface_marker"
    },
    "white_bucket_lid_04": {
      "robodk_item_name": "White_Bucket_Lid_04",
      "marker_name": "MOTIQ_Detected_white_bucket_lid_04",
      "pose_policy": "visible_surface_marker"
    }
  }
}
```

Config keys and paths may be changed to fit the project's standards, but preserve explicit units, frame names, validation flags and one-way mode. The `T_R_B` identity shown is a **placeholder** and **must not be accepted** until the alignment is validated. Do not assume `Objects_Frame` has identity pose; in the current repository's `objects/object_specifications.json` it has a demo display transform (translation + 90° about Z).

---

## 7. Physical UR5e telemetry, safely

Use **only** `rtde_receive.RTDEReceiveInterface`, following `scripts/mirror_ursim_to_robodk.py`:

```python
from rtde_receive import RTDEReceiveInterface
import math

rx = RTDEReceiveInterface(robot_host)  # receive-only
q_rad = rx.getActualQ()                # six current physical joint angles
q_deg = [math.degrees(q) for q in q_rad]
# RoboDK simulated robot: robot.setJoints(q_deg)
```

- There must be **no** `rtde_control` import, `moveJ`, `moveL`, URScript transmission, hardware gripper calls, robot power/enable commands, or RoboDK `Connect()` to the physical UR controller.
- Read timestamps and/or monotonic sample time; track disconnected/stale data.
- Use actual **physical joint positions**, not commanded targets.
- Verify matching robot model, joint order, axis direction/zero conventions, and a small set of manually observed positions in simulation.
- Simulated FK can disagree with physical measurements because of model calibration / different TCP offsets; report this without treating the physical TCP as flange pose.
- Disconnect/reconnect read-only streams with backoff; no busy loops.
- On disconnect, stop advancing the simulation and mark status STALE/OFFLINE rather than silently showing an apparently live scene.

### RoboDK write-side guards

Keep and strengthen checks from the existing mirror script:

```python
if RDK.RunMode() != rl.RUNMODE_SIMULATE:
    raise RuntimeError("Refusing: RoboDK not in simulation mode")
if robot.ConnectedState()[0] == rl.ROBOTCOM_READY:
    raise RuntimeError("Refusing: virtual robot connected to a real driver")
```

Validate **before each update batch**, not only once at startup. If the mode/driver state changes, pause/fail closed. Never programmatically switch RoboDK into a physical driver mode. Use `robot.setJoints(q_deg)` only for the virtual robot. Do not call `MoveJ`, `MoveL`, `RunProgram`, etc. Prefer to use an isolated dedicated digital-twin station or ensure there is no concurrent MOTIQ motion simulation writing to the same robot.

---

## 8. Calibrated object observation contract

Use existing `GET /state/arm` for a continuous lightweight stream and/or `GET /locate/arm?label=...` for user-requested stable snapshots. Design a **typed observation adapter** whose output can be tested independently of the physical hardware:

```json
{
  "object_id": "orange_screw_03",
  "tracker_id": 3,
  "class_label": "vite_arancione",
  "timestamp_utc": "2026-11-05T01:30:00Z",
  "frame_id": 984,
  "calibration_id": "hand_eye_example_validated_id",
  "coordinate_frame": "ur_physical_base",
  "point_semantics": "visible_surface_centroid_3d",
  "position_base_m": [-0.45, -0.12, 0.09],
  "confidence": 0.94,
  "depth_valid_ratio": 0.88,
  "depth_points": 410,
  "robot_stationary": true,
  "pose_frame_synchronized": true,
  "valid": true
}
```

**This is a contract example, not a claim that current `/state/arm` returns all these fields.** Codex should adapt existing fields and add metadata only where necessary. Do not fabricate `calibration_id`, frame timestamps, pose freshness or validation booleans from missing values. A saved calibration artifact alone does not prove it is valid for the current setup. Validate its transform direction and frame name against current runtime telemetry.

**Object identity:** tracker numeric IDs are transient and may change. Map class label/name to registered MOTIQ IDs using the existing scene `class_labels` / object specifications. Handle duplicates by requiring a stable instance association (track+spatial gating) or **mark ambiguous and skip** rather than assigning both identical objects to one RoboDK CAD item. Reject unknown object IDs. Do not let arbitrary class names become unvalidated RoboDK item names.

**Quality gates:** no object update if calibration is invalid, camera depth is unavailable, bbox/visible mask is truncated, pose and frame timestamps are mismatched, robot is moving under stationary-only mode, observation has expired, spread is too high, confidence/depth-valid ratio below configured thresholds, or mapping is ambiguous. Keep last known marker with a clear STALE state; never silently interpret its position as current. The app should show an explanatory reason (`invalid_calibration`, `no_depth`, `moving`, `stale_frame`, `unknown_object`, `frame_mismatch`, etc.).

**One capture:** Once calibrated, a single synchronized RGB-D capture can supply one 3D visible-surface point per object when depth is valid. For safer model/scene update, allow optional multi-frame aggregation, without requiring it for a read-only marker demonstration.

---

## 9. RoboDK scene synchronization

### 9.1 Robot

At each healthy telemetry tick:

1. Verify connection, station, robot item, `RUNMODE_SIMULATE`, and disconnected physical driver.
2. Read physical `q_actual[6]` (rad).
3. Convert radians → degrees once.
4. Update **only** `virtual_robot.setJoints(q_deg)`.
5. Report sample time, q, virtual FK and any comparison metrics with *matching* reference frames.

### 9.2 Virtual object markers

For each accepted calibrated base-frame observation:

1. Compute `p_R = T_R_B * p_B` in meters.
2. Convert the **point** to a pose with a clearly marked *display-only* orientation (e.g. identity in frame R). This is a marker pose, not the object's real 6D orientation.
3. Find the named marker item in RoboDK; create it at startup if explicitly allowed, using `MOTIQ_Detected_...` names and a small clearly identifiable geometry/target. Never overwrite an existing unrelated target/object.
4. Compute the marker pose correctly relative to its actual parent frame. The `Objects_Frame` in MOTIQ has a non-identity transform, so simply sending a physical-base XYZ as a local `item.setPose()` is **wrong**.
5. Update the marker at the measured point; display label, confidence, time, calibration ID and `visible_surface_centroid_3d` semantics if feasible.
6. Optional: update the CAD model only if a configured and validated object-specific transform/model fitting strategy delivers its CAD origin and orientation. If not, leave CAD geometry in place and show just the detection marker.

Illustrative transform flow (not a drop-in module):

```python
# All T matrices below use meters.
p_R = T_R_B @ np.array([*xyz_base_m, 1.0])
T_R_marker = np.eye(4)
T_R_marker[:3, 3] = p_R[:3]

# RoboDK station absolute transforms of the virtual robot BASE and marker PARENT:
T_S_R = mat_to_np(robot.Parent().PoseAbs())
T_S_P = mat_to_np(marker.Parent().PoseAbs())
T_P_marker = np.linalg.inv(T_S_P) @ T_S_R @ T_R_marker
marker.setPose(np_to_mat(T_P_marker))  # meters -> RoboDK millimeters
```

The existing functions `vision/eyes/capture/robodk.py::mat_to_np`, `np_to_mat`, and `item_position_base` may be reused or factored into a common transform module. Validate this conversion with synthetic rotations/translations and the repository's `Objects_Frame` configured rotation.

### 9.3 Visibility/absence semantics

- **Observed and valid:** display/update marker at the new position.
- **Detected but invalid depth/pose:** retain previous marker **STALE** and show why; don't teleport it.
- **Not detected in current camera view:** mark **NOT_VISIBLE**; never infer that the object disappeared from the physical workspace.
- **Unregistered detection:** optionally display a generic, non-registered marker in a separate overlay with safe generated names; never hijack an existing registered CAD item.
- **Physically moving object:** update from successive valid stationary-camera observations only; avoid treating transient tracker IDs as permanent physical identities.

---

## 10. App integration: a real runnable capability, not a motion skill

Implement a **built-in entry/card in MOTIQ → Skills** with a dedicated read-only sync control surface. Suggested title: **Sync Digital Twin (UR5e + RealSense)**. Suggested capabilities:

| App action | Backend meaning | Physical robot |
|---|---|---|
| `Check` | GET health and all validation prerequisites | Read-only |
| `Snapshot` | Refresh q + latest valid objects once | Read-only |
| `Start Live Sync` | Start one background mirror loop | Read-only |
| `Stop Sync` | Stop the mirror loop, release streams | **No stop command sent to the robot** |
| `Show status` | Poll service for sync status, last update and errors | Read-only |

The current `SkillStep` schema permits custom action strings, but `runSkill()` is a **finite task runner that can dispatch robot primitives**, and its stop path may call the existing physical robot stop action. Do not implement persistent sync as a fake `SEQUENCE` with MOVE/WAIT/STOP, and do not reuse `stopDevice('arm')` to end the mirror. Prefer a separate **read-only service capability** linked from Skills, or add a specifically handled `DIGITAL_TWIN_SYNC` action with an explicit non-motion executor and a separate cancel callback. The UI must remain usable while the sync is running. Any custom skill schema extension must preserve import/export compatibility and clear failure semantics.

**Suggested authenticated bridge routes** (names may adapt to conventions):

```text
GET  /digital-twin/status
POST /digital-twin/check
POST /digital-twin/snapshot
POST /digital-twin/start
POST /digital-twin/stop
```

Use the existing bridge's `X-Bridge-Key` authentication mechanism and proxy only to the local backend. Keep the backend service bound to `127.0.0.1` and do not expose unauthenticated robot/camera endpoints on the LAN. Validate all input on the server, including allowed robot names and RoboDK station names. `start` should be idempotent (never create duplicate loops). `stop` must stop only digital-twin mirroring; **not call** `/tools/stop`, `/robot/stop`, URScript or RTDE control methods.

Example status response:

```json
{
  "ok": true,
  "mode": "mirror_only",
  "state": "RUNNING",
  "physical_motion_enabled": false,
  "robot": {"connected": true, "last_joint_sample_age_ms": 42, "hz": 10},
  "camera": {"connected": true, "calibration_valid": true, "calibration_id": "cal_..."},
  "robodk": {"connected": true, "station": "<EXACT_STATION_NAME>", "run_mode": "simulate"},
  "objects": {"updated": 2, "stale": 1, "skipped": 1},
  "last_error": null
}
```

**UI states:** `DISCONNECTED`, `READY`, `RUNNING`, `PAUSED_STALE`, `ERROR`, `STOPPED`. Show clear prerequisites; disallow Start if calibration is invalid, RoboDK run mode is unsafe, or frame alignment is unvalidated. A `Snapshot` may still show a robot-only update with a descriptive object-calibration warning if a partial mode is explicitly selected.

---

## 11. Suggested implementation order for Codex

### Milestone A — robot-only mirroring

- Refactor/reuse `scripts/mirror_ursim_to_robodk.py` as a module with injected receive-only UR and RoboDK adapter interfaces.
- Make real UR5e host configurable (no permanent IP or non-loopback auto-allow without explicit configuration).
- Keep SIMULATE/driver-disconnected guards from the existing script.
- Add `--once` and live/manual validation via CLI; physical robot never receives commands.
- Add unit tests using fake RTDE + fake RoboDK.

### Milestone B — calibrated vision read-only feed

- Verify hand-eye frame and calibration ID; correct any TCP/flange mismatch in the runtime pipeline.
- Add a typed adapter for `GET /state/arm` and `GET /locate/arm` without rebuilding perception.
- Implement freshness, quality and stationary gates.
- Implement label/class → registered object ID mapping with ambiguity handling.
- Persist diagnostic snapshots and useful logs (no unbounded RGB/depth dump).

### Milestone C — RoboDK object positions

- First update **surface-point markers**, not the CAD origins.
- Convert UR physical base → RoboDK virtual base → exact marker parent frame.
- Handle `Objects_Frame` non-identity pose and absent/ambiguous mappings.
- Optional per-object pose policy for CAD STL origins, only with validated geometry/orientation assumptions.
- Provide a replay/dry-run fixture for offline testing.

### Milestone D — app-runnable skill/function

- Add the built-in Skills entry and read-only synchronization controls.
- Add authenticated bridge endpoints and a stateful single-owner service.
- Implement start/stop/snapshot/status, including safe cleanup and no physical stop command.
- Add integration tests and a short operator procedure.

---

## 12. Tests and acceptance criteria (must pass before calling it complete)

**Unit tests (offline):**

1. Joint conversion: 6 actual joints in radians → correct RoboDK degrees and ordering.
2. Frames: identity and non-identity `T_R_B`; correct `T_S_R`/`T_S_P` conversion including the demo `Objects_Frame` 90° rotation.
3. Calibration: reject missing/invalid `T_F_C`, wrong robot identity, wrong flange/TCP reference, stale calibration or mismatched camera configuration. Reject using a fixed-look-pose calibration for a moving arm.
4. Surface-vs-CAD origin: a top-surface centroid at z=115 mm must **not** place `Blue_Bucket_01` CAD bottom at z=115 mm; show a marker unless a proper object model policy exists.
5. Quality: low confidence, missing depth, old timestamps, mismatched robot/camera pose, moving robot, truncated bbox, duplicate detection ID, and unmapped objects **do not** update CAD poses.
6. Safety: `RUNMODE_RUN_ROBOT` or driver-connected RoboDK robot **blocks all writes**; no `rtde_control`, robot motion, `stopDevice('arm')`, or URScript calls in the sync code path.
7. Service lifecycle: multiple `start` requests create one loop; `stop` ends mirroring without stopping physical robot; crash/reconnect leaves clear status; stale inputs freeze updates.
8. App: card shows all states correctly; tap Start and Stop only use digital-twin endpoints; fails gracefully when service unavailable.

**Hardware-in-the-loop checks (operator supervised; no automatic motion):**

1. Connect physical UR5e Ethernet and RealSense to Linux; manually verify telemetry and RGB-D frames.
2. With RoboDK in simulation mode and driver disconnected, operator manually moves **one joint at a time from the pendant**. Verify RoboDK follows the same joint, sign, approximate angle, and configuration; repeat for multiple poses.
3. Confirm valid eye-in-hand calibration and physical-UR-flange frame. Observe a fixed registered object from several stationary robot viewpoints. The marker should remain at approximately one location in the RoboDK scene; log spread and absolute residual against a separately measured reference when available.
4. Move the physical object on the table, keep robot stationary, take a new observation, and verify marker changes position accordingly.
5. Unplug the camera or disconnect robot telemetry: status becomes stale/offline and scene stops claiming fresh updates.
6. Connect the RoboDK robot to a physical driver or switch out of simulation mode (in a controlled non-motion setup): synchronization must refuse writes.
7. Press **Stop Sync** in the app: mirror loop stops and there is **no UR command** issued.

No invented numerical accuracy claims: print measured error and repeatability. A good visual match alone is not sufficient validation of object pose.

---

## 13. Linux setup and smoke test instructions

Codex should inspect the actual Python environment and use the repo's installation conventions. Typical dependencies are already present in parts of the project:

```bash
cd /path/to/MOTIQ
# Use the existing project venv / dependencies instead of blindly reinstalling
python -m pip show ur-rtde robodk numpy fastapi httpx

# Current implementation reference (URSim; not yet the final new feature):
cd robot_controller
python scripts/mirror_ursim_to_robodk.py --help
```

The new feature should expose an operator-friendly CLI resembling (illustrative proposed interface):

```bash
# From robot_controller/; commands are to be implemented by Codex
python -m robot_control.digital_twin.cli check --config config/digital_twin.local.json
python -m robot_control.digital_twin.cli snapshot --config config/digital_twin.local.json
python -m robot_control.digital_twin.cli serve --config config/digital_twin.local.json
```

Do **not** assume example commands exist before Codex implements them. An example local configuration must default to `auto_start: false` and `base_alignment.validated: false`. The operator must explicitly confirm robot identity, calibration reference and base alignment before updating objects. The real IP comes from a local ignored file or environment variable.

**Cross-device setup:** the Linux PC hosts RoboDK and MOTIQ services. The tablet/app communicates to the already authenticated MOTIQ bridge. Do not require the app/tablet to speak RTDE or RoboDK directly.

---

## 14. Codex acceptance deliverables

At completion, return:

1. A short summary of added/modified files and why the existing MOTIQ modules were reused.
2. A functioning **Sync Digital Twin** entry inside the app's Skills screen, with Check/Snapshot/Start/Stop and accurate status.
3. The example JSON config and a documented local override strategy.
4. A Linux CLI and local backend API, with read-only UR and guarded RoboDK writers.
5. Offline tests for frame transforms, mapping, validation, staleness and hard safety restrictions.
6. A short manual validation guide listing exact commands and screenshots/JSON examples.
7. An explicit statement of what is **not** implemented: no real-robot movement, no automatic pick/place, and no claim of full 6D CAD pose from one RGB-D centroid.
8. Any remaining blockers (actual station name, robot IP, calibration artifact reference, robot base alignment, and object orientation policies) listed clearly rather than guessed.

**Please implement the feature rather than only writing a design, but do not run hardware-in-the-loop tests without an operator present. Make changes on a dedicated feature branch; do not push to `main` without team approval.**

---

## 15. Prompt to paste into Codex (on the Linux PC)

> Read `MOTIQ_Digital_Twin_Sync_Codex_Spec_EN.md` completely. Implement an app-runnable, read-only `Sync Digital Twin` capability in the existing MOTIQ repository. Use the physical UR5e's RTDE **receive-only actual joints**, the RealSense D435's existing validated eye-in-hand object coordinates, and RoboDK in **simulation mode**. Reuse `robot_controller/scripts/mirror_ursim_to_robodk.py`, the vision `GET /state/arm` / `GET /locate/arm` interfaces, the STL object registry, and MOTIQ's Skills/bridge architecture. The app must offer Check, Snapshot, Start Live Sync, Stop Sync, and Status. Initially update the virtual UR5e and **visible-surface point markers** for objects; do not move CAD object origins unless explicit verified model-origin policies exist. Require a validated physical-base ↔ RoboDK-base alignment and correctly account for `Objects_Frame`. Reject stale/untrusted observations. The synchronizer must never send commands to the physical robot and must never drive a RoboDK robot connected to a real driver. Add tests, JSON configuration, CLI, and operator documentation. Implement on a feature branch and report the tests and remaining blockers.
