"""Server FastAPI per Visione A ("Occhi"):
- Stream video MJPEG con Set-of-Mark (`/stream/{source}`) e raw (`/stream/{source}/raw`)
- WebSocket con il JSON degli oggetti e telemetria (`/ws/objects`, `/ws/objects/{source}`)
- Endpoint REST per stato istantaneo, target del cane, verifica locale e bridge Grok Voice.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from vision.grok.voice_tools import TOOLS as VOICE_TOOLS
from vision.grok.voice_tools import dispatch as voice_dispatch

from ..calibration.hand_eye import (
    DEFAULT_MAX_SPREAD_M, FIXED_POSE, FixedTcp, HandEye, aggregate, board_pose, is_stationary, look_pose_and_save,
    solve_and_save,
)
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

    # --- calibrazione dall'app (skill "Calibra camera braccio") ------------------------------------------------
    # Il foglio ArUco resta fermo sul tavolo. La skill muove il braccio a piccoli passi (bridge) e dopo ogni posa
    # chiama /sample; poi /solve (hand-eye -> markers_mm.json, niente da toccare); poi, dalla posa di osservazione,
    # /look_pose (-> fixed_pose.json, caricata subito). La visione non muove mai il robot.
    calib: dict[str, list[tuple[list[float], Any]]] = {}

    async def _fresh_frame(node, after: float, timeout_s: float = 3.0):
        """Colore (+ profondità) di un frame preso dopo `after`: il braccio si è appena fermato."""
        deadline = time.monotonic() + timeout_s
        while True:
            if not pipeline._running:
                node.reader.read_once()
            if hasattr(node.reader, "get_frames"):
                ok, color, depth, _fid, ts = node.reader.get_frames()
            else:
                (ok, color, _fid, ts), depth = node.reader.get_latest(), None
            if ok and ts >= after:
                return color, depth
            if time.monotonic() > deadline:
                return None, None
            await asyncio.sleep(0.05)

    @app.post("/calibration/{source}/reset")
    def calibration_reset(source: str) -> dict:
        calib[source] = []
        return {"ok": True, "n": 0}

    @app.get("/calibration/{source}")
    def calibration_status(source: str) -> dict:
        node = pipeline.nodes.get(source)
        return {"ok": node is not None, "n": len(calib.get(source, [])),
                "robot_pose_source": getattr(getattr(node, "robot_pose_fn", None), "__class__", type(None)).__name__,
                "calibration_id": getattr(node, "calibration_id", None)}

    @app.post("/calibration/{source}/sample")
    async def calibration_sample(source: str, request: Request) -> dict:
        """Un campione: posa del foglio vista ora + posa del braccio ora. Corpo opzionale `{"tcp_pose": [x, y, z, rx,
        ry, rz]}` (m, rad, frame Base); senza, la posa si legge dalla fonte del server (`run --robot-ip`)."""
        node = pipeline.nodes.get(source)
        if node is None:
            return {"ok": False, "reason": f"sorgente '{source}' non registrata"}
        intr = getattr(node.reader, "intrinsics", None)
        if intr is None:
            return {"ok": False, "reason": "serve la camera con intrinseci (RealSense o RoboDK)"}
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - corpo vuoto
            body = {}
        if isinstance(body, dict) and body.get("tcp_pose") is not None:
            pose = [float(v) for v in body["tcp_pose"]]
            if len(pose) != 6:
                return {"ok": False, "reason": "tcp_pose: servono 6 numeri [x, y, z, rx, ry, rz] in m e rad"}
        else:
            fn = getattr(node, "robot_pose_fn", None)
            reading = fn() if fn is not None else None
            if reading is None:
                return {"ok": False, "reason": "posa del braccio non disponibile: avviate il server con --robot-ip "
                        "<IP dell'UR> oppure mandate tcp_pose" + (f" ({fn.last_error})" if getattr(fn, "last_error", None) else "")}
            if reading.moving:
                return {"ok": False, "reason": "il braccio si sta muovendo: aspettate che sia fermo"}
            pose = list(reading.pose)
        color, _depth = await _fresh_frame(node, time.time())
        if color is None:
            return {"ok": False, "reason": "nessun frame dalla camera"}
        found = board_pose(color, intr)
        if found is None:
            return {"ok": False, "reason": "foglio non visto: servono almeno 2 marker interi nell'inquadratura",
                    "n": len(calib.get(source, []))}
        samples = calib.setdefault(source, [])
        if samples and is_stationary(samples[-1][0], pose, tol_m=0.005, tol_deg=2.0):
            return {"ok": False, "reason": "posa uguale alla precedente: il braccio non si è mosso?", "n": len(samples)}
        samples.append((pose, found[0]))
        return {"ok": True, "n": len(samples), "markers_seen": found[1], "reproj_px": round(found[2], 2),
                "advice": "bastano 12-15 pose, ruotate attorno ad assi diversi" if len(samples) < 12 else "abbastanza pose: /solve"}

    @app.post("/calibration/{source}/solve")
    def calibration_solve(source: str, hand_eye_file: str = Query(default="data/calibration/hand_eye.json"),
                          markers_file: str = Query(default="data/calibration/markers_mm.json")) -> dict:
        """Hand-eye dai campioni -> hand_eye.json + centri dei marker nel base in markers_mm.json (senza toccarli)."""
        node = pipeline.nodes.get(source)
        samples = calib.get(source, [])
        if len(samples) < 3:
            return {"ok": False, "reason": f"servono almeno 3 campioni (meglio 12-15), ce ne sono {len(samples)}"}
        src = type(getattr(node, "robot_pose_fn", None)).__name__ if getattr(node, "robot_pose_fn", None) else "app"
        res = solve_and_save([p for p, _ in samples], [b for _, b in samples],
                             getattr(node.reader, "intrinsics", None), src, hand_eye_file, markers_file)
        if not res["reliable"]:
            res.update(ok=False, reason=f"dispersione {res['spread_mm']} mm (> 5): più pose e rotazioni più ampie")
        return res

    @app.post("/calibration/{source}/look_pose")
    async def calibration_look_pose(source: str, table_z_mm: float | None = Query(default=None),
                                    markers_file: str = Query(default="data/calibration/markers_mm.json"),
                                    out: str = Query(default="data/calibration/fixed_pose.json")) -> dict:
        """Dalla posa di osservazione, con il foglio ancora sul tavolo: posa fissa -> fixed_pose.json, usata subito."""
        node = pipeline.nodes.get(source)
        intr = getattr(getattr(node, "reader", None), "intrinsics", None)
        if node is None or intr is None:
            return {"ok": False, "reason": "serve la camera con intrinseci (RealSense o RoboDK)"}
        raw = json.loads(Path(markers_file).read_text(encoding="utf-8"))
        markers = {int(k): (float(v[0]), float(v[1])) for k, v in raw.items() if str(k).isdigit()}
        z = table_z_mm if table_z_mm is not None else float(raw.get("_table_z_mm") or 0.0)
        frames = []
        for _ in range(8):
            color, depth = await _fresh_frame(node, time.time())
            if color is not None:
                frames.append((color, depth))
        res = look_pose_and_save(frames, intr, markers, z / 1000.0, out)
        if Path(out).exists() and res.get("file"):
            node.hand_eye, node.tcp_fn = HandEye.load(out), FixedTcp(FIXED_POSE)  # attiva subito, senza riavvio
            node.calibrator, node.calibration_id = None, Path(out).stem + time.strftime("_%Y%m%d-%H%M")
            node.tcp_readings.clear()
        return res

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
