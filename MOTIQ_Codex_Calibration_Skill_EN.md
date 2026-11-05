# MOTIQ — In-App Calibration Skill (UR5e + Intel RealSense D435)

**Audience:** Teammate using Codex on a Linux PC with read-only access to a **physical UR5e** over Ethernet and access to synchronized RGB and depth frames from an **Intel RealSense D435**.  
**Repository reviewed:** [sirwinstontea/MOTIQ](https://github.com/sirwinstontea/MOTIQ), `main`, commit `dff199b434c541f4cfe47a94ce31d9f9d4702d3c` (November 5, 2026). **Re-audit the working checkout before editing:** code may have changed.  
**Provided hardware information:** Approximate camera offset: **75 mm along longitudinal X** and **35 mm along Z**, relative to a reference informally described as "TCP/flange"; chessboard square side length: **21.8 mm**.  
**Scope:** **Calibrate and localize objects in the UR5e base frame. No automatic robot motion, no grasp planning, and no robot commands from this workflow.** The camera is rigidly mounted near the end of the robot; calibration should reference the **mechanical flange**, not the active TCP.

> **Important distinction:** "Calibration Skill" in the MOTIQ app means an operator-facing workflow launched from the **Skills** screen. A Codex agent skill (`.agents/skills/motiq-calibration/SKILL.md`) is only a set of development instructions for building that workflow. They are not the same runtime feature.

---

## 0. Required end-to-end behavior

Implement a **Skills → Calibration** workflow in the MOTIQ app. The operator must be able to:

1. Check the physical **UR5e** connection, flange pose source, RealSense D435 connection (model/serial), and live camera preview.
2. Open and edit a validated **JSON setup file**, including the approximate mount offsets and **21.8 mm** chessboard square size. Select a `chessboard` or the existing repository `aruco_4` board. Enter the actual number of **inner chessboard corners** (not yet supplied).
3. Rigidly mount the camera; secure the calibration board so it **does not move**; manually reposition the robot using the **teach pendant** to approximately **15–25 diverse stationary poses**; press **Capture Sample** in the app at each pose.
4. Save one coherent image–robot-pose sample at a time: color image, camera intrinsics and distortion, depth metadata where relevant, verified `T_base_flange`, `T_camera_board` from PnP, timestamps, reprojection error, source IDs, and validation status.
5. Solve for **`T_flange_camera`**, assess calibration quality, save the result, and allow activation only when quality gates and operator confirmation pass.
6. Detect an object in **one synchronized RGB + depth shot**, estimate a visible surface 3D point in the camera optical frame, and calculate its position in the robot base frame.
7. Display the object label, 3D position, point semantics, quality flags, timestamp, and `calibration_id`, with a **Copy Coordinates** action. **Never treat this point as an automatically executable robot target.**

### Coordinate frames and equation

- `B`: actual UR5e **base** frame, not a RoboDK world or arbitrary table frame.
- `F`: actual UR5e mechanical **flange** frame, **not** its active TCP or a configurable tool frame.
- `C`: D435 **color optical** camera frame: X points right, Y down, Z forward; align depth to color before projecting.
- `O`: estimated object point, typically `visible_surface_centroid_3d` rather than the object's hidden geometric center.

Convention: `T_A_B` transforms a point expressed in frame `B` into frame `A`.

```text
p_B = T_B_F @ T_F_C @ [x_C, y_C, z_C, 1]^T
```

Always store translation and XYZ in **meters** internally. UR rotation-vector angles are in **radians**; homogeneous rotation matrices are dimensionless. When using a RoboDK source in tests, explicitly convert any millimeter positions. Do not silently mix flange, TCP, base, table, world, or optical coordinate frames.

---

## 1. Mandatory configuration JSON

Create the operator-editable file:

`computer-vision/data/calibration/calibration_setup.json`

Also commit an example/template:

`computer-vision/data/calibration/calibration_setup.example.json`

The two files must use the **same validated schema**; never hard-code these measurements in Python, React Native components, or the Codex instructions.

### Measurements currently supplied

| Parameter | Initial value | Confidence / interpretation |
|---|---:|---|
| Camera longitudinal X offset | **75.0 mm** | Rough estimate; direction and sign must be confirmed |
| Camera Z offset | **35.0 mm** | Rough estimate; direction and sign must be confirmed |
| Camera Y offset | **null** | Not measured; do not invent a value |
| Mount rotation | **null** | Not measured; do not assume zero |
| Offset reference | **unconfirmed TCP or flange** | Must be clarified by the operator |
| Chessboard square size | **21.8 mm** | Side length of **each square**, not of the complete board |
| Chessboard inner corners | **null × null** | Must be entered after counting columns and rows |

For example, a board with 9 × 6 **squares** has 8 × 5 **inner corners**. This is only an explanation; **do not assume the user's board has this shape**.

### Proposed JSON schema and initial values

```json
{
  "schema_version": "1.0.0",
  "robot": {
    "model": "UR5e",
    "pose_mode": "physical_read_only",
    "calibration_frame": "flange",
    "robot_serial": null
  },
  "camera": {
    "model": "Intel RealSense D435",
    "serial": null,
    "coordinate_frame": "camera_color_optical"
  },
  "camera_mount_estimate": {
    "reference_frame_as_measured": "unconfirmed_tcp_or_flange",
    "x_longitudinal_mm": 75.0,
    "y_mm": null,
    "z_mm": 35.0,
    "x_sign_confirmed": false,
    "z_sign_confirmed": false,
    "rotation_rpy_deg": null,
    "status": "rough_estimate_only",
    "use_as_calibration": false
  },
  "board": {
    "type": "chessboard",
    "square_size_mm": 21.8,
    "inner_corners_columns": null,
    "inner_corners_rows": null,
    "orientation_reference_confirmed": false
  },
  "capture": {
    "recommended_samples": 20,
    "min_samples_to_solve": 12,
    "robot_must_be_stationary": true,
    "allow_automatic_robot_motion": false
  },
  "localization": {
    "frame": "robot_base",
    "point_semantics": "visible_surface_centroid_3d",
    "send_robot_motion_commands": false
  }
}
```

### Binding rules for Codex

1. Load the JSON using a **typed Pydantic model**, validate `square_size_mm > 0`, finite values, supported enum values, serials when required, and `inner_corners_columns >= 3`, `inner_corners_rows >= 3` before attempting chessboard PnP. Show missing fields and validation errors **in the app**; never fabricate inner-corner counts, axes, signs, or rotations.
2. **75 mm and 35 mm are only rough mounting estimates.** They are **not** `T_flange_camera`, and without rotation they cannot define a complete 6-DoF transform. Display them as metadata and potentially use them in plausibility checks **after** their reference/signs are confirmed; never substitute them for the solved transform.
3. If the measurements were taken from the **TCP**, request and verify the actual `T_flange_tcp` before interpreting any camera estimate in flange coordinates:

   ```text
   T_flange_camera_est = T_flange_tcp @ T_tcp_camera_est
   ```

   If measured from the **flange**, explicitly confirm `reference_frame_as_measured = "flange"`. **Never assume `T_base_tcp == T_base_flange`.**
4. Hand-eye calibration itself **does not require** these approximate offsets. Permit solving without confirming these estimates **if a genuinely verified `T_base_flange` is available**. Offset confirmation is useful for diagnostics, not an artificial precondition to solve.
5. The **Skills → Calibration → Setup** UI must expose the offsets, optional Y, sign confirmation, measurement reference (`Unconfirmed / Flange / TCP`), board type, **21.8 mm** square size, and editable inner-corner dimensions. Persist changes through a validated server API; do not let clients select arbitrary filesystem paths. Prevent sample capture if the chosen board geometry is incomplete.
6. Keep **`calibration_setup.json`** (editable input) separate from **`hand_eye_flange.json`** (computed, quality-checked output). When relevant inputs, robot/camera identity, camera mounting, or image intrinsics change, invalidate or require revalidation of the active calibration. Never overwrite a validated result with the rough estimate.
7. Store and verify camera serial, output frame convention, RGB resolution, real intrinsics/distortion, robot identity, and a **config version/hash** with every calibration. Do not claim a fixed transform is valid for a physically remounted camera.

**Chessboard geometry:** `P_board(i, j) = [i * 0.0218, j * 0.0218, 0]` meters for successive **inner corners**. The detected indexing and board-frame orientation must be consistent across all captures. Some symmetric checkerboards can be indexed in reversed orientations; reject ambiguous samples or use an orientation mark / ChArUco board where necessary.

---

## 2. Existing MOTIQ code to reuse, not reimplement

**Inspect these files in the actual checkout before editing.** Findings below are from the reviewed commit.

| Existing file | Existing functionality / required adaptation |
|---|---|
| `computer-vision/vision/eyes/calibration/hand_eye.py` | `board_pose`, `calibrate`, `board_spread_mm`, `HandEye`, `ControllerTcp`, `FixedTcp`, camera-to-base transformation. Currently primarily models `T_tool_cam` with **TCP poses**, not a guaranteed physical flange pose. Extend with explicit frame/source metadata, physical read-only flange adapter, and backward compatibility. |
| `computer-vision/vision/eyes/calibration/aruco_calib.py` | Existing ArUco `DICT_4X4_50`, printable target and 2D planar homography. That homography is **not** a replacement for moving 3D eye-in-hand calibration. |
| `computer-vision/vision/eyes/cli.py` | Existing `calibrate-hand-eye --tcp controller\|manual\|robodk`, `calibrate-look-pose`, and `run`. Add explicit **physical flange** and **chessboard** support to CLI/API without breaking existing commands. |
| `computer-vision/vision/eyes/capture/realsense.py` | `RealSenseReader`; obtains aligned RGB + depth, with depth arrays in **millimeters**. `Intrinsics` currently does not retain the full distortion parameters. Verify and extend for PnP and 3D deprojection if the camera reports nonzero distortion. |
| `computer-vision/vision/eyes/segmentation/measure.py` | Generates `xyz_cam_m` from segmented depth. Preserve the **visible surface centroid** semantics. |
| `computer-vision/vision/eyes/schemas.py` | `EyeObject.xyz_cam_m`, `xyz_base_m`, `depth_valid_ratio`, `depth_points`, `yaw_base_deg` already exist. Reuse/extend rather than creating incompatible object models. |
| `computer-vision/vision/eyes/pipeline.py` | `SourceNode.to_base()` already transforms when the robot is stationary. Existing precedence of `fixed_pose_file` over `hand_eye_file` can be **dangerous** for a moving camera. In physical eye-in-hand mode do **not** silently fall back to a fixed camera-to-base pose. |
| `computer-vision/vision/eyes/server/app.py` | Existing `/state/arm`, `/frame/arm.jpg`, and `GET /locate/arm?label=...&n=...`; `/locate` defaults to aggregating several frames. Add a genuine **single-shot** path without breaking existing semantics. |
| `computer-vision/tests/test_hand_eye.py` | Synthetic geometry and calibration tests already exist. Extend them for flange-vs-TCP, chessboard, timestamps, degeneracy, and hold-out validation. |
| `groky-app/src/screens/Skills.tsx` | Displays normal skill cards. Add a distinct **Calibration** card that opens a guided wizard. |
| `groky-app/src/skills/schema.ts`, `index.ts`, `runner.ts` | The existing `SkillDoc` runner is for detection/motion tasks and primitives. **Do not** introduce `CALIBRATE` as a primitive that could be routed to robot motion. |
| `bridge/motiq_bridge.py` | Authenticated app bridge using `X-Bridge-Key`; app bridge on `:8080`, vision service on `:8000`. Includes a `PhysicalArm` interface that imports an **external** `ur_manual/ur_jog.py`; do **not** depend on that motion module as a verified flange pose source, and do not call its movement methods. |
| `robot_controller/robot_control/backends/physical_disabled.py` | The physical backend is intentionally disabled. Do not enable physical motion for calibration. |
| `robot_controller/docs/VISION_INTEGRATION.md` | Controller currently ignores unverified camera/table coordinates and uses taught targets. Preserve this boundary. |
| `groky-app/src/skills/hardware.ts` | A separate cell profile refers to a **D415**. Do not confuse it with the actual **D435**; read the real model and serial from the connected device. |

---

## 3. Hardware and safety constraints

- The operator moves the physical UR5e **manually at low speed from the teach pendant**, with clear space, secured camera/cables, and supervision. The calibration workflow never commands motion to acquire viewpoints.
- The calibration service has **read-only robot access**. Prefer `rtde_receive` or a dedicated telemetry endpoint. Do not initialize `rtde_control` in this workflow, send URScript, or issue MoveL/MoveJ/Home/gripper/stop commands.
- Never change the active TCP, robot installation, safety configuration, tool offsets, or operating limits just for calibration.
- `getActualTCPPose()` describes the **configured TCP**, which may not coincide with the **flange**. Obtain a verified `T_base_flange` using a validated method, such as actual joint values plus UR5e flange forward kinematics or a known, verified active `T_flange_tcp`:

  ```text
  T_base_flange = T_base_tcp @ inverse(T_flange_tcp)
  ```

  If neither the flange transform nor the current active TCP transform can be verified, report **`invalid_flange_pose`** and **do not solve**.
- Do **not** substitute robot-controller simulation state or a mirrored RoboDK pose for telemetry from the real robot.
- Reject frames captured while the robot moves. Associate a **new** camera frame with a stable robot pose; read the pose before and after capture, record monotonic local timestamps, and check that the pose is stable across the capture interval (initial guidance: <=1 mm and <=0.5°). Reject stale robot data and queued/duplicate camera frames. Document handling of RealSense hardware timestamps versus host clock.
- Fail closed if camera or robot data, board pose, depth, timestamps, frame identity, mounting metadata, or calibration quality are missing. Never replace real data with synthetic values in physical mode.
- Protect calibration endpoints: reuse the authenticated `X-Bridge-Key` proxy or apply dedicated authentication. Bind the internal vision API to loopback when practical. Sanitize session IDs and reject path traversal.
- **No robot actuation in this phase**, including through hidden runtime fallbacks or automatic look/search sequences.

---

## 4. Implementation phases (in order)

### Phase A — Verified physical UR5e flange telemetry (read-only)

Create an adapter such as:

`computer-vision/vision/eyes/capture/ur_flange.py`

Suggested read model:

```python
from dataclasses import dataclass
import numpy as np

@dataclass(frozen=True)
class FlangeReading:
    T_base_flange: np.ndarray  # Shape (4, 4), translation in meters
    captured_monotonic_s: float
    received_monotonic_s: float
    robot_serial: str | None
    source: str  # e.g. "ur_rtde_receive_verified_flange"
    moving: bool
    valid: bool
    reason: str | None = None
```

Requirements:

- Use read-only UR telemetry if available (`rtde_receive`). If it exposes only TCP pose, **convert to flange with a verified active TCP transform**, or derive the flange with real joint values and correctly validated kinematic parameters; do not assume zero TCP offset.
- Record exact source/convention, timestamps, robot identity, state freshness, and errors. Implement bounded reconnect/retry behavior; never invent a pose.
- Offer a read-only health/status endpoint and a mock provider for tests. In physical mode, an accidentally selected simulation provider must be rejected.
- Compare readings at a few operator-selected **stationary** real poses and verify consistency. There must be no need to change the robot tool setup.

**Phase A done when:** the app/CLI shows a fresh and validated `T_base_flange` for the real UR5e. If only an unverified `tcp_pose` exists, **block progress**.

### Phase B — Chessboard observations and persistent sample capture

- Reuse existing ArUco logic; add a chessboard detector in `vision/eyes/calibration/` using `cv2.findChessboardCornersSB()`, board dimensions from JSON, and **`square_size_mm = 21.8`**. For OpenCV, `patternSize = (inner_corners_columns, inner_corners_rows)`; these are **inner corners**, not squares.
- Build the known board 3D points with **0.0218 m spacing**; solve `T_camera_board` using `cv2.solvePnP()` with correct RGB camera matrix and distortion coefficients. Refine and compute per-sample reprojection RMSE.
- Ensure corner numbering has a **consistent orientation** across all samples. A symmetric checkerboard may permit a reversed board frame. Reject ambiguous observations and document adding an identifiable corner/orientation marker or choosing the existing ArUco/ChArUco option.
- Each sample pairs **one new RGB image** with a corresponding **verified stationary `T_base_flange`**. Optionally capture aligned depth, but board PnP itself requires RGB and intrinsics, **not** depth.
- Save sessions outside source control (e.g. `computer-vision/data/calibration/sessions/<session_id>/`) using `.gitignore`. Store `samples/*.json`, image files, and a summary; never leak credentials or internal network tokens into logs.
- Per sample save: image/frame ID; acquisition timestamp; robot reading timestamps; `T_base_flange`; `T_camera_board`; board geometry and detected corner count; image size; actual camera intrinsics/distortion/serial; reprojection RMSE; quality verdict.
- Reject stale telemetry, moving robot, mismatched pose time, repeated frame, poor board visibility, inconsistent orientation, invalid intrinsics or high reprojection error. Show an actionable reason on the wizard screen.
- Capture from **15–25 manually selected, diverse poses** with meaningful rotations about at least two axes while the board remains completely stationary. Show sample count and pose/rotation coverage; do not solve an under-constrained dataset.

**Phase B done when:** the operator can gather and inspect at least ~15 valid samples without an automatic robot motion command. Minimum solver count may be 12, but acceptance must also depend on adequate motion diversity and quality.

### Phase C — Solve, validate, and save flange-to-camera calibration

Reuse `vision/eyes/calibration/hand_eye.py` when possible. Run the solver using paired:

```text
T_base_flange_i
T_camera_board_i
```

Equation for each sample:

```text
T_base_board_i = T_base_flange_i @ T_flange_camera @ T_camera_board_i
```

The board is fixed, so `T_base_board_i` should remain approximately constant across samples. Check OpenCV conventions with synthetic tests: `R_gripper2base` corresponds to `R_flange2base`, `R_target2cam` to `R_board2camera`, and the result to `T_flange_camera`. Support existing Park–Martin and available OpenCV methods as appropriate.

Validation requirements:

- Per-image reprojection residuals and sample validity.
- Translation **and rotation** consistency of reconstructed `T_base_board_i`.
- Sufficient nondegenerate rotational motion (not only robot translations or near-identical orientations).
- A small **hold-out dataset** not used to fit the transformation, if enough samples exist.
- Sanity checks for finite numbers, orthonormal rotation (`R.T @ R ≈ I`), `det(R) ≈ +1`, and plausible translation without assuming the rough 75/35 mm offset is exact.
- Example **initial warning thresholds, not guaranteed accuracy**: reprojection RMSE <=1.5 pixels; board positional spread <=5 mm; hold-out position error <=10 mm. Treat these as configurable quality gates to be refined with real measurements. Also validate object depth against a known physical reference and repeat localization from different viewpoints.

Create a separate solved result, for example:

`computer-vision/data/calibration/hand_eye_flange.json`

Suggested output structure (**values below are placeholders, not real calibration**):

```json
{
  "schema_version": "1.0",
  "calibration_type": "eye_in_hand_3d",
  "reference_frame": "flange",
  "camera_frame": "camera_color_optical",
  "robot_model": "UR5e",
  "robot_serial": null,
  "robot_pose_source": "ur_rtde_receive_verified_flange",
  "camera_model": "Intel RealSense D435",
  "camera_serial": "FROM_DEVICE",
  "image_size": [640, 480],
  "intrinsics": {
    "fx": 0,
    "fy": 0,
    "cx": 0,
    "cy": 0,
    "distortion_model": "FROM_DEVICE",
    "coeffs": []
  },
  "board": {
    "type": "chessboard",
    "inner_corners": null,
    "square_size_mm": 21.8,
    "setup_sha256": "FROM_CONFIG"
  },
  "T_flange_camera": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
  "quality": {
    "n_samples": 0,
    "rms_reprojection_px": null,
    "board_spread_mm": null,
    "holdout_error_mm": null
  },
  "setup_sha256": "FROM_CONFIG",
  "mount_estimate_mm": {
    "x_longitudinal": 75.0,
    "y": null,
    "z": 35.0,
    "used_as_calibration": false
  },
  "approved_for_localization": false,
  "created_at_utc": "FROM_RUNTIME"
}
```

**Never save the dummy identity matrix above as a successful physical calibration.** Use atomic writes, meaningful version/calibration IDs, quality gates, operator approval, and rollback/backup of prior active results. Preserve backward compatibility of `HandEye(T_tool_cam=...)` via an **explicitly typed adapter/migration**; do not reinterpret a legacy TCP-frame transform as flange-frame without proof.

**Phase C done when:** synthetic recovery tests pass, real hold-out validation is reported, and activation fails safely on invalid/missing results.

### Phase D — One RGB-D shot → object position in robot base

- Reuse MOTIQ's existing YOLO/tracking and RGB-D segmentation, especially `vision/eyes/segmentation/measure.py`, `EyeObject.xyz_cam_m`, and quality metadata.
- Capture **one newly acquired, correctly aligned RGB + depth pair** while the robot is stationary. Segment the detected object; use valid depth pixels **inside its mask**, not simply the bounding-box center that might fall on the background.
- Deproject to 3D using **matching color-aligned depth and camera intrinsics** (and appropriate handling of lens distortion). Compute a robust representative **visible surface** 3D point; do **not** claim it is the full geometric centroid or a 6-DoF grasp pose.
- Fetch a **verified stationary flange pose corresponding to that frame** and transform:

  ```python
  p_camera_h = np.array([x_c, y_c, z_c, 1.0])
  p_base_h = T_base_flange @ T_flange_camera @ p_camera_h
  position_base_m = p_base_h[:3]
  ```

- Reject camera/robot disconnects, stale or unsynchronized flange pose, robot moving, duplicate/old RGB-D frame, depth zeros/outliers, partial edge-truncated objects, invalid mask, changed camera serial, camera profile mismatch, unapproved calibration, or unknown mounting configuration.
- MOTIQ already exposes `GET /locate/arm?label=...&n=1`, but its internal pipeline/pose synchronization needs review: do **not** assume `n=1` is an atomic RGB-D+pose acquisition. Add `POST /localization/snapshot` or explicitly strengthen the existing acquisition pipeline for true single-shot behavior, preserving old `/locate` semantics.

**Suggested request:**

```json
{"source":"arm","object_label":"orange_screw_03","single_frame":true}
```

**Suggested successful response** (illustrative coordinates, **not** measured):

```json
{
  "ok": true,
  "source": "arm",
  "object_label": "orange_screw_03",
  "timestamp_utc": "2026-11-05T02:00:00Z",
  "frame_id": "camera_color_12345",
  "coordinate_frame": "ur5e_base",
  "position_camera_m": [0.05, 0.01, 0.44],
  "position_base_m": [-0.42, -0.16, 0.09],
  "point_semantics": "visible_surface_centroid_3d",
  "depth_valid_ratio": 0.92,
  "depth_points": 1250,
  "robot_pose_source": "ur_rtde_receive_verified_flange",
  "calibration_id": "sha256_or_file_version",
  "motion_allowed": false
}
```

Provide actionable typed failure statuses, for example:

`camera_offline`, `robot_offline`, `robot_moving`, `stale_robot_pose`, `invalid_flange_pose`, `board_not_detected`, `insufficient_pose_diversity`, `high_calibration_error`, `missing_calibration`, `camera_mismatch`, `invalid_depth`, `object_not_detected`, `object_partially_visible`, `unsynced_robot_pose`.

**Phase D done when:** a single valid RGB-D pair produces `position_base_m`, correctly labeled as a visible surface point, with traceable pose/calibration metadata; repeated observations from three different **stationary** robot poses are mutually consistent within validated tolerances.

### Phase E — Calibration wizard directly accessible from MOTIQ Skills

- Add a **Calibration** card to `groky-app/src/screens/Skills.tsx`, displaying `Not calibrated`, `Collecting samples`, `Pending validation`, `Calibrated`, or `Error`; button: **Start Calibration**. Do not disrupt existing normal skills.
- Build `groky-app/src/components/CalibrationWizard.tsx` and `groky-app/src/lib/calibration.ts`, or an equivalent coherent module layout.
- Suggested wizard screens:
  1. **Connection:** real UR5e telemetry source and D435 model/serial/status, RGB preview.
  2. **Board Setup:** edit the validated JSON values; select chessboard/ArUco; set inner corners and square size.
  3. **Collect Samples:** clear instruction "Move the robot manually with the teach pendant, wait until stationary, then Capture"; show number of valid samples, rejected samples, reprojection errors and pose diversity.
  4. **Solve:** calculate provisional transform, show matrix and metrics. No automatic activation.
  5. **Validate:** independent observations, diagnostics, and clear pass/fail reasons.
  6. **Activate:** requires validated result and explicit operator confirmation.
  7. **Locate Test:** detect an object, acquire one RGB-D shot, display XYZ in the UR5e base frame with a **Copy Coordinates** button and no motion action.
- Show `X longitudinal = 75 mm`, `Z = 35 mm`, `Y = unknown`, axes/signs/rotation unconfirmed, and `chessboard square = 21.8 mm` in the setup UI. Make fields editable and persistent. Make the camera mount estimate visibly distinct from the computed `T_flange_camera`.
- Clearly label **REAL ROBOT / SIMULATION**, robot identity, flange-pose source, camera serial, active calibration and quality status. Disable capture, solve, activation, or localization as appropriate when dependencies are missing.
- **Runtime integration decision:** A calibration workflow is not a motion skill. Prefer a **special card on the Skills screen opening a dedicated wizard**, rather than a `SkillDoc` with a fabricated `CALIBRATE` motion primitive. If export/import compatibility absolutely requires using a `SkillDoc`, add a typed `app.workflow` with a safe, dedicated dispatcher that **never calls `runSkill()`**, and validate imported data to prevent untrusted JSON from triggering motion.
- Proxy the necessary calibration requests through the existing authenticated bridge (`X-Bridge-Key`) or secure a dedicated API. Do not rely solely on disabled UI buttons for safety.
- At completion show 4×4 matrix, calibration ID, quality figures, camera/robot identities and the location test result. Provide copy/export, **not execute/move**.

**Phase E done when:** from **MOTIQ → Skills**, the user can complete setup → capture → solve → validate → activate → single-shot locate without using a shell, provided the physical hardware is available. In mocked mode, the same UI may be demonstrated but must be visibly labeled **SIMULATION**.

### Phase F — Later controller integration (**out of scope**) 

- `robot_controller/robot_control/vision.py` currently ignores uncalibrated/unverified camera coordinates and relies on taught targets. **Do not change the motion policy now.**
- Define, but do not execute, a future `Detection` contract: `object_id`, `position_base_m`, `coordinate_frame`, `point_semantics`, `timestamp`, `confidence`, `calibration_id`, `uncertainty`, and validity status.
- Do not write a visible surface centroid directly into a pick target or named scene target. A later phase must address grasp-point selection, 3D object geometry, orientation, robot/tool offsets, reachability, collision planning, safety interlocks, and operator authorization.

---

## 5. Suggested API surface

Prefer an **authenticated bridge proxy** used by the app, delegating actual camera and read-only robot-pose operations to the local vision service. The API endpoints below may be adjusted if a compatible route already exists; do not create unnecessary duplicates. **All endpoints must remain non-actuating with respect to physical hardware.**

| Proposed route | Purpose |
|---|---|
| `GET /calibration/status` | D435 and real UR5e readiness, verified flange source, session progress and active calibration (without secrets). |
| `GET /calibration/setup` | Return validated operator JSON configuration. |
| `PUT /calibration/setup` | Validate and save editable setup; invalidate/revalidate calibration when appropriate. |
| `POST /calibration/sessions` | Create a session tied to board geometry, robot and camera identity. No motion. |
| `GET /calibration/sessions/{id}` | List samples, errors, metrics, and progress. |
| `POST /calibration/sessions/{id}/capture` | Acquire one new image + verified synchronized stationary flange pose. |
| `POST /calibration/sessions/{id}/solve` | Compute provisional extrinsics and diagnostics; do not automatically activate. |
| `POST /calibration/sessions/{id}/activate` | Activate only after quality gates and explicit human confirmation. No motion. |
| `POST /localization/snapshot` | One synchronized RGB-D pair → detected object's position in robot base frame. |

Use Pydantic input/output models, appropriate HTTP error codes, consistent `{ok, status, reason}` fields, access control, bounded timeouts and sanitized session IDs. Never accept arbitrary local paths or include secrets in diagnostics.

---

## 6. Mandatory tests

Extend `computer-vision/tests/test_hand_eye.py` and add backend/API/UI tests where practical.

1. **Frame algebra:** Known synthetic rotations/translations; `T_base_flange @ T_flange_camera @ p_camera` yields the expected base point. Units conversions between mm and m are explicitly tested.
2. **Flange is not TCP:** Simulate a nonzero active TCP translation **and rotation**. Using `T_base_tcp` incorrectly must either be rejected or corrected using verified `T_flange_tcp`. Never accept an unverified active `tcp_pose` as flange.
3. **Mount estimate JSON:** X=75 mm, Z=35 mm, Y=null, unconfirmed signs and rotation. Values must persist, must **not** become final extrinsics, and must **not** silently overwrite a solved calibration. Test an offset described in TCP coordinates with nonidentity `T_flange_tcp`.
4. **Chessboard scale:** Verify exactly **0.0218 m** between neighboring board points, reject absent inner-corner counts, and test nonzero distortion, 180° orientation ambiguity, and incorrect board indexing.
5. **Synthetic hand-eye:** Generate 15–25 diverse `T_base_flange` poses, a fixed board, and corresponding ideal `T_camera_board`; recover a known `T_flange_camera` within tight numerical tolerance. Reject nearly degenerate rotations.
6. **Board detector/PnP:** Valid dimension pattern, correct solved target pose, rejection of ambiguous/occluded patterns, high reprojection RMSE, or wrong dimensions.
7. **Time and pose synchronization:** Reject moving robot, stale telemetry, duplicate/delayed frame, incompatible clock data, insufficient stationary dwell, and mismatched before/after pose.
8. **Depth/camera validation:** Invalid depth, poor mask, edge-truncated target, changed serial/intrinsics/resolution, and invalid calibration must prevent successful physical localization.
9. **Persistence:** Test versioned calibration records, frame names, method, serial, extrinsics, metrics and explicit approval. Reject dummy identity and ambiguous old TCP-frame files.
10. **API and app workflow:** Mock happy path + every important error path. Errors must not be shown as success. The calibration card must **not** route into `runSkill()` or any motion endpoint.
11. **Regression:** Run existing `test_hand_eye.py`, `test_eyes.py`, and robot-controller contract tests if any adapters are touched.

### Suggested Linux commands (adapt to existing environment)

```bash
cd MOTIQ
python3 -m venv computer-vision/.venv
source computer-vision/.venv/bin/activate
python -m pip install -r computer-vision/vision/eyes/requirements.txt
# If ArUco is unavailable, use a compatible opencv-contrib-python build;
# do not install incompatible opencv-python and opencv-contrib-python in the same environment.
cd computer-vision
PYTHONPATH=. python -m pytest -q tests/test_hand_eye.py tests/test_eyes.py
PYTHONPATH=. python -m vision.eyes.cli calibrate-hand-eye --help
cd ../groky-app
npm ci
npx tsc --noEmit
```

Do not blindly recreate an existing environment or replace a working OpenCV build. For a real session, first check the real UR5e identity and **read-only flange telemetry**, confirm D435 RGB/depth alignment, and provide a human-operated capture procedure. **No automatic physical movement as a test.**

---

## 7. Definition of done and Codex handoff

Deliver all of the following:

- A **Calibration** wizard launched directly from **MOTIQ → Skills**, implemented as a separate non-actuating workflow and supporting setup, capture, solve, validation, activation, and single-shot locate.
- A verified **physical UR5e read-only flange pose** provider (`T_base_flange`) with timestamps, source identity, freshness/stability checks, and fail-closed behavior; never confuse it with active TCP.
- The real **21.8 mm chessboard** configuration and detector, with editable inner-corner counts, as well as compatibility with the existing ArUco option.
- A versioned, validated `T_flange_camera` result tied to the physical camera/robot, optical intrinsics, and flange reference. The initial mount estimate is recorded separately and is **never itself an approved calibration**.
- An endpoint returning a 3D **visible surface point** in the robot base frame from **one synchronized RGB-D shot**, without robot motion.
- Synthetic/mocked tests, docs in `computer-vision/vision/eyes/README.md`, Linux setup instructions, and a concise hardware test checklist.
- A reviewable Git diff explaining backward compatibility with existing CLI commands, `HandEye`, `/locate`, bridge behavior, and normal MOTIQ runtime skills.

**Do not report "tested on the physical robot"** without actually obtaining physical measurements with an operator, a calibration quality report, a fixed-object check from multiple robot poses, and validated flange telemetry. If hardware is unavailable during development, deliver implemented code with mock tests and a precise pending hardware validation checklist—do not invent measurements.

---

## 8. Ready-to-paste Codex prompt (English)

> I'm working at the root of the MOTIQ repository on Linux. Use the `$motiq-calibration` developer skill and read its `references/IMPLEMENTATION.md`. Implement an operator-facing **Skills → Calibration** wizard for an **Intel RealSense D435** rigidly mounted near the tool on a **physical UR5e**. We can read real robot state and RGB+depth frames. The required JSON setup includes a **rough camera mounting offset: 75 mm along longitudinal X and 35 mm along Z**, with Y, signs, rotation, and TCP-versus-flange measurement reference still unconfirmed. These values are **not** the calibration. The physical chessboard has **21.8 mm square sides**, and the number of **inner corners must be editable**. Calibrate `T_flange_camera` from 15–25 stationary, manually captured image+flange-pose pairs while the board remains fixed, validate and persist the result, then use one RGB-D shot to locate a recognized object in the **UR5e base** frame. **Do not use active TCP as flange, do not command the robot, and do not use the motion skill runner for calibration.** Reuse existing `vision/eyes/calibration/hand_eye.py`, `pipeline.py`, `/locate/arm`, the bridge, and the Skills UI wherever possible. Add unit/mock tests, document Linux commands, and show me the diff and test results. **First audit the current checkout and flag any incompatibilities before modifying code.**
