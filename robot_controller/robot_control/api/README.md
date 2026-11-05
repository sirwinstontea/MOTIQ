# api

Optional FastAPI transport, installed with the `api` or `dev` extra.

| File | Purpose |
|---|---|
| `app.py` | `create_app(executor, auth=None)`: REST routes, error mapping, optional auth |
| `dashboard.html` | Live dashboard served at `/`: state, plan, speed card |

Routes are listed in the [controller README](../../README.md#http-api).

The server binds to `127.0.0.1` by default. With `auth=(user, password)`, every route except `/health` needs HTTP Basic or a Bearer token. `/operator/reset` always accepts loopback callers only. `RobotControlError` maps to `{error: {code, message, recoverable}}`: `409` for busy, latched and duplicate tasks, `404` for unknown IDs, `422` for validation errors.
