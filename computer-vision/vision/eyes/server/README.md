# eyes/server

FastAPI server for the eyes, on port 8000 by default. `app.py` exposes `create_app`.

| Group | Routes |
|---|---|
| Health and state | `GET /health`, `GET /state/{source}`, `GET /models/status`, `POST /frame/process` |
| Video | `GET /frame/{source}.jpg`, `GET /stream/{source}` (with marks), `GET /stream/{source}/raw` |
| WebSocket | `/ws/objects`, `/ws/objects/{source}`, `/ws/telemetry` |
| Actions | `POST /action/{source}/start`, `POST /action/{source}/verify_local |
| Calibration | `GET /calibration/{source}`, `POST /calibration/{source}/reset`, `/sample`, `/solve`, `/look_pose`, `POST /empty_table/{source}` |
| Location | `GET /locate/{source}` |
| Voice | `GET /voice/tools`, `POST /voice/dispatch` |
| Grok | `POST /grok/detect`, `/grok/describe`, `/grok/name`, `/grok/changes/reference`, `/grok/changes/diff`, `/grok/reset` |

Usage is covered in [`vision/eyes`](..).
