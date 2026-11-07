"""Local HTTP API of the digital twin service. Bound to 127.0.0.1; the app reaches it only through the
authenticated MOTIQ bridge (`/digital-twin/*`). When BRIDGE_KEY (or DIGITAL_TWIN_KEY) is set, X-Bridge-Key is required
here too.

    GET  /digital-twin/status
    POST /digital-twin/check
    POST /digital-twin/snapshot   {"objects": true|false}
    POST /digital-twin/start      {"objects": true|false}   idempotent: never a second loop
    POST /digital-twin/stop                                 stops mirroring only; nothing is sent to the robot
"""
from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError
from starlette.concurrency import run_in_threadpool

from .service import TwinService


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objects: StrictBool = True        # "yes" / 1 are rejected, not coerced


def configured_key() -> str:
    return os.environ.get("DIGITAL_TWIN_KEY") or os.environ.get("BRIDGE_KEY") or ""


def create_app(svc: TwinService, key: str | None = None) -> FastAPI:
    key = configured_key() if key is None else key

    @asynccontextmanager
    async def lifespan(_app):
        yield
        svc.close()                                    # stop mirroring threads; nothing is sent to the robot

    app = FastAPI(title="MOTIQ digital twin (mirror only)", lifespan=lifespan)

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if key and not hmac.compare_digest(request.headers.get("x-bridge-key", "").encode(), key.encode()):
            return JSONResponse({"ok": False, "status": "unauthorized", "reason": "bad or missing X-Bridge-Key"},
                                status_code=401)
        return await call_next(request)

    async def body(request: Request) -> Body:
        raw = await request.body()
        if not raw.strip():
            return Body()
        try:
            return Body.model_validate_json(raw)
        except ValidationError as e:
            raise ValueError(e.errors()[0]["msg"]) from e

    def reply(res: dict) -> JSONResponse:
        code = 200 if res.get("ok") else (409 if res.get("status") not in ("robot_offline", "sim_offline") else 503)
        return JSONResponse(res, status_code=code)

    @app.exception_handler(ValueError)
    async def bad(_req, exc):
        return JSONResponse({"ok": False, "status": "bad_request", "reason": str(exc)}, status_code=400)

    @app.get("/digital-twin/status")
    def status():
        return svc.status()

    @app.post("/digital-twin/check")
    async def check():
        return JSONResponse(await run_in_threadpool(svc.check))

    @app.post("/digital-twin/snapshot")
    async def snapshot(request: Request):
        b = await body(request)
        return reply(await run_in_threadpool(svc.snapshot, b.objects))

    @app.post("/digital-twin/start")
    async def start(request: Request):
        b = await body(request)
        return reply(await run_in_threadpool(svc.start, b.objects))

    @app.post("/digital-twin/stop")
    async def stop():
        return reply(await run_in_threadpool(svc.stop))

    return app
