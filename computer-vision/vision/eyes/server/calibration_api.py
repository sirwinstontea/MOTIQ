"""HTTP API for Skills -> Calibration and the single-shot locate. All routes are NON-ACTUATING.

Auth: when `BRIDGE_KEY` (same key the MOTIQ bridge and the app use, `EXPO_PUBLIC_BRIDGE_KEY`) or
`MOTIQ_CALIBRATION_KEY` is set, every route here needs the `X-Bridge-Key` header. Without a key, read-only GETs are
open and state-changing requests are accepted from loopback only.

    GET  /calibration/status                     readiness, robot/camera identity, active calibration, card state
    GET  /calibration/setup                      validated setup JSON + problems to show
    PUT  /calibration/setup                      validate and save (invalidates the active calibration on identity change)
    POST /calibration/sessions                   new session (board geometry, camera and robot identity frozen)
    GET  /calibration/sessions/{id}              samples, rejections, coverage, provisional result
    POST /calibration/sessions/{id}/capture      one new frame + verified stationary flange pose
    DELETE /calibration/sessions/{id}/samples/{n}
    POST /calibration/sessions/{id}/solve        provisional T_flange_camera + diagnostics (never activates)
    POST /calibration/sessions/{id}/validate     independent view of the fixed board, not used in the fit
    POST /calibration/sessions/{id}/activate     {"confirm": true} after quality gates
    POST /localization/snapshot                  one RGB-D frame -> object position in UR5e base (motion_allowed: false)
    POST /localization/snapshot_all              one RGB-D frame -> every detection, each with a base point or a reason
                                                 (used by the read-only digital twin; motion_allowed: false)
"""
from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..calibration.service import CalibrationService, WorkflowError

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def configured_key() -> str:
    return os.environ.get("MOTIQ_CALIBRATION_KEY") or os.environ.get("BRIDGE_KEY") or ""


def require_access(request: Request) -> None:
    key = configured_key()
    if key:
        got = request.headers.get("x-bridge-key", "")
        if not hmac.compare_digest(got.encode(), key.encode()):
            raise WorkflowError("unauthorized", "bad or missing X-Bridge-Key", 401)
        return
    if request.method != "GET":
        host = request.client.host if request.client else ""
        if host not in LOOPBACK:
            raise WorkflowError("unauthorized", "calibration changes from the network need BRIDGE_KEY set on the "
                                "vision server and X-Bridge-Key in the request", 401)


class ActivateRequest(BaseModel):
    confirm: bool = False
    operator_note: str = Field(default="", max_length=500)


class SnapshotRequest(BaseModel):
    source: str = "arm"
    object_label: str | None = Field(default=None, max_length=100)
    object_id: int | None = None
    single_frame: bool = True


def build_router(get_service) -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_access)])

    def svc() -> CalibrationService:
        return get_service()

    @r.get("/calibration/status")
    def status() -> dict:
        return svc().status()

    @r.get("/calibration/setup")
    def get_setup() -> dict:
        return svc().get_setup()

    @r.put("/calibration/setup")
    def put_setup(body: Any = Body(...)) -> dict:
        return svc().put_setup(body)

    @r.post("/calibration/sessions")
    def create_session() -> dict:
        return svc().create_session()

    @r.get("/calibration/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        return svc().get_session(session_id)

    @r.post("/calibration/sessions/{session_id}/capture")
    def capture(session_id: str) -> dict:
        return svc().capture(session_id)

    @r.delete("/calibration/sessions/{session_id}/samples/{index}")
    def delete_sample(session_id: str, index: int) -> dict:
        return svc().delete_sample(session_id, index)

    @r.post("/calibration/sessions/{session_id}/solve")
    def solve(session_id: str) -> dict:
        return svc().solve(session_id)

    @r.post("/calibration/sessions/{session_id}/validate")
    def validate(session_id: str) -> dict:
        return svc().validate(session_id)

    @r.post("/calibration/sessions/{session_id}/activate")
    def activate(session_id: str, req: ActivateRequest) -> dict:
        return svc().activate(session_id, req.confirm, req.operator_note)

    @r.post("/localization/snapshot")
    def snapshot(req: SnapshotRequest) -> dict:
        s = svc()
        if req.source != s.source:
            raise WorkflowError("bad_request", f"only source '{s.source}' has an eye-in-hand calibration", 400)
        if not req.single_frame:
            raise WorkflowError("bad_request", "only single_frame=true is supported here; use GET /locate for the "
                                "multi-frame median", 400)
        return s.snapshot(req.object_label, req.object_id)

    @r.post("/localization/snapshot_all")
    def snapshot_all() -> dict:
        return svc().snapshot_all()

    return r


async def workflow_error_handler(_request: Request, exc: WorkflowError) -> JSONResponse:
    return JSONResponse(exc.payload(), status_code=exc.http)
