# tests

Offline tests for the vision stack. No camera, robot, RoboDK or network is needed.

| File | Covers |
|---|---|
| `test_eyes.py` | Capture, tracking, Set-of-Mark, ArUco, dog telemetry, local verification, FastAPI and WebSocket, zoom |
| `test_segmentation.py` | Masks on synthetic scenes: depth, colour, GrabCut, geometry and measures |
| `test_hand_eye.py` | Deprojection, board pose, hand-eye and fixed-pose calibration, camera to base |
| `test_robodk.py` | A fake RoboDK renders the scene, checking the chain from reader to mask to `/locate` |
| `test_planner.py` | `vision/planner` with a mock Grok and mock executor |
| `test_calibration_skill.py` | The calibration skill follows the app's validation rules and the bridge limits |
| `fixtures/planner/` | `tokens_unscrew.json`, recorded video tokens for planner tests |

Run from `computer-vision/`:

```bash
GROK_MOCK=1 python -m pytest tests vision/grok/tests
```
