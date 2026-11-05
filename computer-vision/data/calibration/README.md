# data/calibration

Calibration inputs and outputs for the camera.

| File | Purpose |
|---|---|
| `aruco_board.png` | Printable sheet with ArUco markers 0-3 (`DICT_4X4_50`) |
| `markers_mm.json` | Marker centres in the robot base frame, in mm |

The values in `markers_mm.json` are **placeholders**: they are still the positions on the printed sheet, not base coordinates, and the code detects this case (`markers_still_on_sheet` in `vision/eyes/calibration/hand_eye.py`). Do not measure them by hand. `calibra_senza_toccare.bat` (`calibrate-hand-eye`) writes them from 12-15 arm poses. See [`vision/eyes`](../../vision/eyes) and [`calibration_skill`](../../calibration_skill).

These results are generated here and depend on the camera mount, so calibrate again if the camera moves:

| File | Written by |
|---|---|
| `calibration_arm.json` | `calibrate-arm` |
| `hand_eye.json` | `calibrate-hand-eye` |
| `fixed_pose.json` | `calibrate-look-pose` |
