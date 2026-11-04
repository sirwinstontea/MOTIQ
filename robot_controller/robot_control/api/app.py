"""Optional LOCAL FastAPI transport. Bind to 127.0.0.1 only (enforced by the CLI).

No authentication is implemented, so this must never be exposed to a LAN or
the Internet. Health means "the process is up", not "safe to move".
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from pathlib import Path

from fastapi.responses import HTMLResponse, JSONResponse

from ..action_registry import tool_specs
from ..models import ErrorCode, RobotControlError
from ..task_executor import TaskExecutor

_HTTP = {ErrorCode.BUSY: 409, ErrorCode.FAULT_LATCHED: 409, ErrorCode.DUPLICATE_TASK: 409,
         ErrorCode.UNKNOWN_ID: 404, ErrorCode.VALIDATION_ERROR: 422, ErrorCode.UNKNOWN_ACTION: 422}


def create_app(ex: TaskExecutor) -> FastAPI:
    app = FastAPI(title="robot_control (simulation only)", version="0.1.0")

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
