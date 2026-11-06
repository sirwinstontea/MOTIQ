# eyes/calibration

Maps pixels and camera points to robot coordinates.

| File | Purpose |
|---|---|
| `aruco_calib.py` | `ArmCalibrator`: ArUco detection (`DICT_4X4_50`), table homography, pixel to mm, grasp angle |
| `hand_eye.py` | Camera on the arm: camera to base transform, TCP sources, board pose, multi-frame measurement |

`hand_eye.py` supports two modes:

1. **Fixed pose** (recommended, `calibrate-look-pose`): the arm always looks from the same pose, so one transform from camera to base is constant.
2. **Eye-in-hand** (`calibrate-hand-eye`): the sheet is observed from several arm poses to solve the camera mount, which is then used with the current flange pose.

Pose readers: `ControllerTcp`, `RtdeTcp`, `FixedTcp`. Base coordinates are only filled in while the arm is still (`still_for_frame`). Commands are in [`vision/eyes`](..).
