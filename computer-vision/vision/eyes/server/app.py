"""Server FastAPI per Visione A ("Occhi"):
- Stream video MJPEG con Set-of-Mark (`/stream/{source}`) e raw (`/stream/{source}/raw`)
- WebSocket con il JSON degli oggetti e telemetria (`/ws/objects`, `/ws/objects/{source}`)
- Endpoint REST per stato istantaneo, target del cane, verifica locale e bridge Grok Voice.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from vision.grok.voice_tools import TOOLS as VOICE_TOOLS
from vision.grok.voice_tools import dispatch as voice_dispatch

from ..calibration.hand_eye import DEFAULT_MAX_SPREAD_M, aggregate
from ..pipeline import EyePipeline
from ..verification.verifier import verify_action_local


class DogTargetRequest(BaseModel):
    target_id: int | None = None
    target_label: str | None = None


class ActionStartRequest(BaseModel):
    action: str = "pick"
    target_id: int
    destination_id: int | None = None


class VoiceToolRequest(BaseModel):
    name: str
    arguments: dict[str, Any] | str = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def encode_jpeg(frame_bgr, quality: int = 80) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return b""
    return buf.tobytes()


def create_app(pipeline: EyePipeline | None = None) -> FastAPI:
    """Crea e configura l'applicazione FastAPI collegata alla `EyePipeline`."""
    if pipeline is None:
        pipeline = EyePipeline()
        pipeline.add_source("arm", mode="synthetic")
        pipeline.add_source("dog", mode="synthetic")

    app = FastAPI(
        title="Visione A - Occhi (Grok Hackathon)",
        version="0.1.0",
        description="Percezione locale: MJPEG Set-of-Mark, WebSocket JSON oggetti, calibrazione ArUco e telemetria cane.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.pipeline = pipeline
    app.state.vision_state = pipeline.state

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "sources": list(pipeline.nodes.keys()),
        }

    @app.get("/state/{source}")
    def get_source_state(source: str) -> dict:
        if source in pipeline.nodes and not pipeline._running:
            pipeline.step(source)
        payload = pipeline.state.get_payload(source)
        return payload.model_dump()

    @app.get("/frame/{source}.jpg")
    def get_marked_jpeg(source: str, raw: bool = Query(default=False)) -> Response:
        if source in pipeline.nodes and not pipeline._running:
            pipeline.step(source)
        img = pipeline.state.get_frame(source) if raw else pipeline.state.get_marked_frame(source)
        return Response(content=encode_jpeg(img), media_type="image/jpeg")

    async def _mjpeg_generator(source: str, raw: bool = False, fps: float = 20.0):
        delay = 1.0 / max(1.0, fps)
        while True:
            if source in pipeline.nodes and not pipeline._running:
                pipeline.step(source)
            img = pipeline.state.get_frame(source) if raw else pipeline.state.get_marked_frame(source)
            jpg = encode_jpeg(img)
            if jpg:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                )
            await asyncio.sleep(delay)

    @app.get("/stream/{source}")
    async def stream_marked(source: str, fps: float = Query(default=20.0, ge=1.0, le=60.0)):
        """Stream MJPEG con riquadri e numeri Set-of-Mark per l'app web."""
        return StreamingResponse(
            _mjpeg_generator(source, raw=False, fps=fps),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/stream/{source}/raw")
    async def stream_raw(source: str, fps: float = Query(default=20.0, ge=1.0, le=60.0)):
        """Stream MJPEG del frame originale pulito."""
        return StreamingResponse(
            _mjpeg_generator(source, raw=True, fps=fps),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.post("/dog/target")
    def set_dog_target(req: DogTargetRequest) -> dict:
        node = pipeline.nodes.get("dog")
        if node and node.dog_navigator:
            node.dog_navigator.set_target(target_id=req.target_id, target_label=req.target_label)
        return {"ok": True, "target_id": req.target_id, "target_label": req.target_label}

    @app.post("/action/{source}/start")
    def start_action(source: str, req: ActionStartRequest) -> dict:
        if source in pipeline.nodes and not pipeline._running:
            pipeline.step(source)
        rec = pipeline.state.record_action_start(
            source=source,
            action=req.action,
            target_id=req.target_id,
            destination_id=req.destination_id,
        )
        return {
            "ok": True,
            "action": rec["action"],
            "target_id": rec["target_id"],
            "destination_id": rec["destination_id"],
            "objects_before_count": len(rec["objects_before"]),
        }

    @app.post("/action/{source}/verify_local")
    def verify_action_endpoint(source: str) -> dict:
        if source in pipeline.nodes and not pipeline._running:
            pipeline.step(source)
        last = pipeline.state.get_last_action(source)
        if last is None:
            return {"ok": False, "error": "Nessuna azione registrata per questa sorgente."}
        res = verify_action_local(
            objects_before=last["objects_before"],
            objects_after=pipeline.state.get_objects(source),
            action=last["action"],
            target_id=last["target_id"],
            destination_id=last.get("destination_id"),
        )
        return {"ok": True} | res.model_dump()

    @app.post("/empty_table/{source}")
    async def record_empty_table(source: str, timeout_s: float = Query(default=5.0, gt=0, le=30)) -> dict:
        """Registra il tavolo vuoto (sfondo per le maschere dalla profondità). Va fatto con il braccio nella posa di
        osservazione e **niente** sul tavolo: vale solo per quella posa. Ripetere se cambia la luce o la posa."""
        node = pipeline.nodes.get(source)
        if node is None:
            return {"ok": False, "reason": f"sorgente '{source}' non registrata"}
        node.record_empty_table()
        deadline = time.monotonic() + timeout_s
        while node.recording_bg and time.monotonic() < deadline:
            if not pipeline._running:
                pipeline.step(source)
            await asyncio.sleep(0.05)
        ready = bool(node.bg.ready)
        return {"ok": ready, "has_depth": bool(node.bg.has_depth) if ready else False,
                "reason": None if ready else f"tavolo vuoto non registrato in {timeout_s} s (camera collegata?)"}

    @app.get("/locate/{source}")
    async def locate(source: str, label: str | None = Query(default=None), id: int | None = Query(default=None),
                     n: int = Query(default=10, ge=1, le=60), timeout_s: float = Query(default=3.0, gt=0, le=15),
                     max_spread_m: float = Query(default=DEFAULT_MAX_SPREAD_M, gt=0)) -> dict:
        """Posizione di un oggetto nel frame base del robot: mediana di `n` frame nuovi presi a robot fermo.

        Pensato per la look pose: il braccio si ferma sopra il tavolo, si chiama `/locate`, poi si scende.
        Con `label` (classe o nome) prende l'oggetto più sicuro e lo segue per id nei frame successivi.
        """
        def fail(status: str, reason: str, **extra) -> dict:
            return {"ok": False, "status": status, "reason": reason, "position_base_m": None, "xyz_base_m": None,
                    "timestamp_utc": _utc_now(), **extra}

        node = pipeline.nodes.get(source)
        if node is None:
            return fail("unknown_source", f"sorgente '{source}' non registrata")
        if node.hand_eye is None:
            return fail("missing_calibration", "camera sul braccio non calibrata (calibrate-look-pose o calibrate-hand-eye)")
        if label is None and id is None:
            return fail("bad_request", "serve label o id")
        want = (label or "").strip().lower()
        samples, cams, quality, last_fid, track, cls = [], [], [], None, id, None
        seen = no_depth = unsynced = partial = 0
        deadline = time.monotonic() + timeout_s
        while len(samples) < n and time.monotonic() < deadline:
            if not pipeline._running:
                pipeline.step(source)
            payload = pipeline.state.get_payload(source)
            if payload.frame_id != last_fid:
                last_fid = payload.frame_id
                if track is not None:
                    cands = [o for o in payload.objects if o.id == track]
                else:
                    cands = sorted((o for o in payload.objects
                                    if want in (o.label.lower(), (o.name or "").lower())), key=lambda o: -o.conf)
                if cands:
                    seen += 1
                    o = cands[0]
                    x, y, w, h = o.bbox
                    if min(x, y) <= 0.005 or x + w >= 0.995 or y + h >= 0.995:
                        partial += 1  # tagliato dal bordo: il centroide visibile non è il centro dell'oggetto
                    elif o.xyz_cam_m is None:
                        no_depth += 1
                    elif o.xyz_base_m is None:
                        unsynced += 1
                    else:
                        track, cls = o.id, o.label
                        samples.append((o.xyz_base_m, o.yaw_base_deg))
                        cams.append(o.xyz_cam_m)
                        quality.append((o.depth_points or 0, o.depth_valid_ratio or 0.0))
            await asyncio.sleep(0.01)
        if not samples:
            if not seen:
                return fail("object_not_detected", "oggetto non visibile")
            if partial >= max(no_depth, unsynced):
                return fail("object_partially_visible", "oggetto tagliato dal bordo dell'immagine: spostatelo verso il "
                            "centro dell'inquadratura o cambiate la posa di osservazione")
            if no_depth >= unsynced:
                return fail("invalid_depth", "oggetto visto ma senza profondità valida sulla maschera")
            err = getattr(node.tcp_fn, "last_error", None)
            return fail("unsynced_robot_pose", "oggetto visto ma il robot era in moto o la sua posa non è disponibile"
                        + (f" ({err})" if err else ""))
        res = aggregate(samples, max_spread_m)
        status = "ok" if res["ok"] else "unstable_measurement"
        if res["ok"] and len(samples) < n:
            res = {**res, "ok": False, "reason": f"solo {len(samples)}/{n} frame validi in {timeout_s} s"}
            status = "insufficient_frames"
        tcp = node.robot_still
        pts, ratio = np.median([q[0] for q in quality]), float(np.median([q[1] for q in quality]))
        return {**res, "status": status, "timestamp_utc": _utc_now(), "ts": time.time(), "frame_id": "base",
                "source": source, "camera": node.camera_name, "object_class": cls, "id": track, "label": label,
                "point_semantics": "visible_surface_centroid_3d",
                "position_camera_m": [round(float(v), 4) for v in np.median(np.array(cams), axis=0)],
                "position_base_m": res.get("xyz_base_m") if res["ok"] else None,
                "valid_depth_ratio": round(ratio, 3), "num_valid_points": int(pts),
                "calibration_id": node.calibration_id,
                "tcp_pose": [round(v, 4) for v in tcp.pose] if tcp else None}

    @app.get("/voice/tools")
    def voice_tools() -> list[dict]:
        """Strumenti da mettere in session.tools di Grok Voice (così l'app non li copia a mano)."""
        return VOICE_TOOLS

    @app.post("/voice/dispatch")
    def dispatch_voice_tool(req: VoiceToolRequest) -> dict:
        """Esegue uno strumento di Grok Voice (`vision.grok.voice_tools.dispatch`) sullo stato live."""
        return voice_dispatch(req.name, req.arguments, pipeline.state)

    @app.websocket("/ws/objects")
    @app.websocket("/ws/objects/{source}")
    @app.websocket("/ws/telemetry")
    async def ws_objects(websocket: WebSocket, source: str = "arm", hz: float = 15.0):
        """WebSocket che invia il JSON continuo degli oggetti rilevati e telemetria."""
        await websocket.accept()
        delay = 1.0 / max(1.0, min(60.0, hz))
        try:
            while True:
                if source in pipeline.nodes and not pipeline._running:
                    pipeline.step(source)
                payload = pipeline.state.get_payload(source)
                await websocket.send_json(payload.model_dump())
                await asyncio.sleep(delay)
        except WebSocketDisconnect:
            return

    return app
