"""Optional FastAPI transport.

By default it binds to 127.0.0.1 only, with no authentication. Binding to another
address (e.g. 0.0.0.0 in a container) is refused by the CLI unless
ROBOT_API_PASSWORD is set. Every route except /health then requires HTTP Basic
auth (the browser prompts once for the dashboard) or ``Authorization: Bearer <password>``.
Health means "the process is up", not "safe to move".
"""
from __future__ import annotations

import base64
import binascii
import hmac

from fastapi import FastAPI, HTTPException, Request
from pathlib import Path

from fastapi.responses import HTMLResponse, JSONResponse

from ..action_registry import tool_specs
from ..models import ErrorCode, RobotControlError
from ..task_executor import TaskExecutor

_HTTP = {ErrorCode.BUSY: 409, ErrorCode.FAULT_LATCHED: 409, ErrorCode.DUPLICATE_TASK: 409,
         ErrorCode.UNKNOWN_ID: 404, ErrorCode.VALIDATION_ERROR: 422, ErrorCode.UNKNOWN_ACTION: 422}


def _authorized(header: str, user: str, password: str) -> bool:
    scheme, _, value = header.partition(" ")
    scheme = scheme.lower()
    if scheme == "bearer":
        return hmac.compare_digest(value.strip().encode(), password.encode())
    if scheme == "basic":
        try:
            u, sep, p = base64.b64decode(value.strip(), validate=True).decode("utf-8").partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return False
        ok_user = hmac.compare_digest(u.encode(), user.encode())
        ok_pass = hmac.compare_digest(p.encode(), password.encode())
        return bool(sep) and ok_user and ok_pass
    return False


def create_app(ex: TaskExecutor, *, auth: tuple[str, str] | None = None) -> FastAPI:
    """``auth=(user, password)`` protects every route except /health."""
    app = FastAPI(title="robot_control (simulation only)", version="0.1.0")

    if auth:
        user, password = auth

        @app.middleware("http")
        async def _require_auth(request: Request, call_next):
            if request.url.path == "/health" or _authorized(request.headers.get("authorization", ""), user, password):
                return await call_next(request)
            return JSONResponse(status_code=401,
                                content={"error": {"code": "UNAUTHORIZED", "message": "credentials required",
                                                   "recoverable": True}},
                                headers={"WWW-Authenticate": 'Basic realm="robot_control", charset="UTF-8"'})

    @app.exception_handler(RobotControlError)
    async def _err(_req: Request, e: RobotControlError):
        return JSONResponse(status_code=_HTTP.get(e.code, 400),
                            content={"error": {"code": e.code.value, "message": e.message, "recoverable": e.recoverable}})

    @app.get("/health")
    def health():
        return {"process": "ok", "backend": ex.c.backend_name, "executor_state": ex.state.value,
                "note": "health is not a claim of physical motion readiness"}

    @app.get("/", response_class=HTMLResponse)
    def dashboard():
        return (Path(__file__).with_name("dashboard.html")).read_text(encoding="utf-8")

    @app.get("/status")
    def status():
        """Live: what the robot is doing now, what is next, where it is."""
        return ex.live_status()

    @app.get("/robot/state")
    def robot_state():
        return ex.robot_state()

    @app.get("/scene")
    def scene():
        return ex.scene.snapshot()

    @app.get("/tools")
    def tools():
        return tool_specs()

    @app.post("/tasks", status_code=202)
    async def submit(request: Request):
        try:
            body = await request.body()
        except Exception:
            raise HTTPException(400, "unreadable body")
        st = ex.submit(body.decode("utf-8"))
        return st.model_dump(mode="json", exclude={"events"})

    @app.get("/tasks/{task_id}")
    def task(task_id: str):
        return ex.get_task(task_id).model_dump(mode="json")

    @app.post("/tasks/{task_id}/cancel")
    def cancel(task_id: str):
        return ex.cancel(task_id).model_dump(mode="json", exclude={"events"})

    @app.get("/robot/speed")
    def get_speed():
        return ex.speed_info()

    @app.post("/robot/speed")
    async def set_speed(request: Request):
        """Body: {"percent": 25}. Speed/accel as % of the model maximums; refused (409) while a task runs."""
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict) or "percent" not in body:
            raise RobotControlError(ErrorCode.VALIDATION_ERROR, 'body must be {"percent": <number>}')
        return ex.set_speed_percent(body["percent"])

    @app.post("/robot/stop")
    def stop():
        ex.stop_robot()
        return {"requested": "controlled_stop", "note": "software stop request; NOT an emergency stop"}

    @app.post("/operator/reset")
    async def operator_reset(request: Request):
        """Operator clears a latched FAULT/CANCELLED state. Same-PC only; not an AI tool,
        and the LAN bridge never forwards it."""
        if not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403, "operator reset is only accepted from the robot PC itself")
        try:
            operator = str((await request.json()).get("operator", ""))
        except Exception:
            operator = ""
        ex.reset_fault(operator=operator)
        return {"reset": True, "operator": operator, "executor_state": ex.state.value}

    return app
