"""Skills -> Calibration workflow backend: setup, sessions, capture, solve, validate, activate, single-shot locate.

NON-ACTUATING. Nothing here sends a command to any robot: robot data comes only from a read-only `FlangeProvider`
(`capture/ur_flange.py`). The operator moves the UR5e by hand from the teach pendant between captures.

Sessions live in `data/calibration/sessions/<session_id>/` (git-ignored): `session.json`, `samples/NNNN.json` +
`NNNN.png`, `result.json` (provisional). Session ids are generated here and validated by regex: clients never
choose a filesystem path.

Capture protocol (one sample = one NEW colour frame + one verified stationary flange pose):
    r0 = flange()  -> wait `stationary_dwell_s` -> r1 = flange(), r0 ~ r1 (robot already still)
    frame with frame_id newer than any seen and ts >= r1.wall_s
    r2 = flange(), r0 ~ r2 within tolerance, r2 fresh   -> the frame was taken with the robot still in that pose
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from ..capture.realsense import UnsupportedDistortion, opencv_distortion
from ..capture.ur_flange import FlangeReading, pose_delta, stable_pair
from .boards import BoardRejected, observe_board
from .flange_calibration import (
    ACTIVE_FILE, Sample, activate, activation_problems, build_result, load_active, predict_board_error_mm, solve,
    summarize_record, utc_now,
)
from .hand_eye import camera_matrix
from .setup_config import (
    CALIB_DIR, CalibrationSetup, atomic_write_json, board_problems, board_warnings, flange_problems, identity_sha256,
    load_setup, mount_estimate_in_flange_m, mount_estimate_notes, parse_setup, save_setup, setup_sha256,
)

SESSION_RE = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")
FRAME_TIMEOUT_S = 3.0
DUPLICATE_POSE_MM, DUPLICATE_POSE_DEG = 5.0, 2.0


class WorkflowError(Exception):
    """Typed failure returned to the app as `{ok: false, status, reason}`."""

    def __init__(self, status: str, reason: str, http: int = 409, **extra: Any):
        super().__init__(reason)
        self.status, self.reason, self.http, self.extra = status, reason, http, extra

    def payload(self) -> dict:
        return {"ok": False, "status": self.status, "reason": self.reason, **self.extra}


class CalibrationService:
    def __init__(self, pipeline: Any, source: str = "arm", calib_dir: str | Path = CALIB_DIR,
                 flange_provider: Any = None, frame_timeout_s: float = FRAME_TIMEOUT_S,
                 sleep: Callable[[float], None] = time.sleep):
        self.pipeline, self.source = pipeline, source
        self.calib_dir = Path(calib_dir)
        self.sessions_dir = self.calib_dir / "sessions"
        self.flange = flange_provider
        self.frame_timeout_s = frame_timeout_s
        self.sleep = sleep
        self._lock = threading.RLock()       # one capture/solve/activate at a time
        self._last_frame_id = -1

    # --- setup ------------------------------------------------------------------------------------------------

    def setup(self) -> CalibrationSetup | None:
        return load_setup(self.calib_dir)[0]

    def get_setup(self) -> dict:
        cfg, errors, src = load_setup(self.calib_dir)
        return {"ok": cfg is not None, "status": "ok" if cfg else "invalid_setup", "source_file": src,
                "setup": cfg.model_dump(mode="json") if cfg else None, "errors": errors,
                "setup_sha256": setup_sha256(cfg) if cfg else None, **self._setup_notes(cfg)}

    def _setup_notes(self, cfg: CalibrationSetup | None) -> dict:
        if cfg is None:
            return {"board_problems": [], "board_warnings": [], "flange_problems": [], "mount_notes": []}
        return {"board_problems": board_problems(cfg), "board_warnings": board_warnings(cfg),
                "flange_problems": flange_problems(cfg), "mount_notes": mount_estimate_notes(cfg)}

    def put_setup(self, data: Any) -> dict:
        cfg, errors = parse_setup(data)
        if cfg is None:
            raise WorkflowError("invalid_setup", "setup not saved: fix the highlighted fields", 422, errors=errors)
        with self._lock:
            old = self.setup()
            save_setup(cfg, self.calib_dir)
        invalidated = old is not None and identity_sha256(old) != identity_sha256(cfg) and self._active_record() is not None
        return {**self.get_setup(), "calibration_requires_revalidation": invalidated}

    # --- camera / robot ---------------------------------------------------------------------------------------

    @property
    def node(self) -> Any:
        node = self.pipeline.nodes.get(self.source)
        if node is None:
            raise WorkflowError("camera_offline", f"vision source '{self.source}' not registered", 503)
        return node

    def camera_info(self) -> dict | None:
        node = self.pipeline.nodes.get(self.source)
        if node is None:
            return None
        r = node.reader
        intr = getattr(r, "intrinsics", None)
        connected = bool(getattr(r, "is_connected", True))
        if intr is None:
            return None
        info = {"connected": connected, "model": getattr(r, "device_name", "") or getattr(r, "mode", ""),
                "serial": getattr(r, "device_serial", "") or None, "firmware": getattr(r, "firmware", "") or None,
                "mode": getattr(r, "mode", "unknown"), "simulation": getattr(r, "mode", "") != "realsense",
                **(intr.to_dict() if hasattr(intr, "to_dict") else {
                    "fx": intr.fx, "fy": intr.fy, "cx": intr.cx, "cy": intr.cy, "width": intr.width,
                    "height": intr.height, "distortion_model": "none", "coeffs": [0.0] * 5})}
        try:
            opencv_distortion(intr)
            info["distortion_supported"] = True
        except UnsupportedDistortion as exc:
            info["distortion_supported"], info["distortion_error"] = False, str(exc)
        return info

    def _require_camera(self, cfg: CalibrationSetup) -> dict:
        cam = self.camera_info()
        if cam is None or not cam["connected"]:
            raise WorkflowError("camera_offline", "D435 not connected or without intrinsics", 503)
        if not cam.get("distortion_supported", True):
            raise WorkflowError("camera_mismatch", cam.get("distortion_error", "unsupported distortion"))
        if cfg.robot.pose_mode == "physical_read_only":
            if cam["simulation"]:
                raise WorkflowError("camera_mismatch", "setup is in physical mode but the camera is simulated")
            if not cam["serial"]:
                raise WorkflowError("camera_mismatch", "camera serial unknown: cannot tie the calibration to it")
            if cfg.camera.serial and cam["serial"] != cfg.camera.serial:
                raise WorkflowError("camera_mismatch", f"connected camera {cam['serial']} != setup camera.serial "
                                    f"{cfg.camera.serial}")
            if "D435" not in (cam["model"] or "") and "D435" in cfg.camera.model:
                raise WorkflowError("camera_mismatch", f"connected camera is '{cam['model']}', setup expects "
                                    f"'{cfg.camera.model}'")
        return cam

    def _read_flange(self) -> FlangeReading:
        if self.flange is None:
            raise WorkflowError("robot_offline", "no read-only robot pose source: start the vision server with "
                                "--robot-ip <UR IP> (or --simulate-robot for a labelled demo)", 503)
        return self.flange.read()

    def _stationary_flange(self, cfg: CalibrationSetup) -> tuple[FlangeReading, FlangeReading]:
        """r0, r1 a dwell apart, both valid, still and equal: the robot has been still for at least the dwell."""
        g = cfg.quality_gates
        if self.flange is not None and cfg.robot.pose_mode == "physical_read_only" and getattr(self.flange, "simulation", False):
            raise WorkflowError("invalid_flange_pose", "simulation robot provider in physical mode: refused")
        if flange_problems(cfg) and cfg.robot.pose_mode == "physical_read_only":
            raise WorkflowError("invalid_flange_pose", flange_problems(cfg)[0])
        r0 = self._read_flange()
        if not r0.valid:
            raise WorkflowError(r0.status, r0.reason or "flange pose unavailable")
        self.sleep(g.stationary_dwell_s)
        r1 = self._read_flange()
        ok, status, reason = stable_pair(r0, r1, g.stationary_tol_mm, g.stationary_tol_deg, g.max_pose_age_ms)
        if not ok:
            raise WorkflowError(status, reason or status)
        return r0, r1

    def _close_flange(self, cfg: CalibrationSetup, r0: FlangeReading) -> FlangeReading:
        g = cfg.quality_gates
        r2 = self._read_flange()
        ok, status, reason = stable_pair(r0, r2, g.stationary_tol_mm, g.stationary_tol_deg, g.max_pose_age_ms)
        if not ok:
            raise WorkflowError("unsynced_robot_pose" if status == "robot_moving" else status,
                                (reason or status) + " (robot pose not stable around the camera frame)")
        return r2

    def _new_frame(self, after_wall: float) -> tuple[np.ndarray, np.ndarray | None, int, float]:
        """A colour (+depth) frame newer than every frame used before and taken after `after_wall`."""
        node, deadline = self.node, time.monotonic() + self.frame_timeout_s
        reader = node.reader
        while True:
            if not self.pipeline._running and hasattr(reader, "read_once"):
                reader.read_once()
            if hasattr(reader, "get_frames"):
                ok, color, depth, fid, ts = reader.get_frames()
            else:
                (ok, color, fid, ts), depth = reader.get_latest(), None
            if ok and fid > self._last_frame_id and ts >= after_wall:
                self._last_frame_id = fid
                return color, depth, fid, ts
            if time.monotonic() > deadline:
                raise WorkflowError("camera_offline" if not ok else "stale_frame",
                                    "no new camera frame after the robot pose was read", 503)
            self.sleep(0.02)

    # --- sessions ---------------------------------------------------------------------------------------------

    def _session_dir(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not SESSION_RE.match(session_id):
            raise WorkflowError("bad_request", "invalid session id", 400)
        d = self.sessions_dir / session_id
        if not (d / "session.json").exists():
            raise WorkflowError("not_found", f"session {session_id} not found", 404)
        return d

    def _load_session(self, session_id: str) -> tuple[Path, dict]:
        d = self._session_dir(session_id)
        return d, json.loads((d / "session.json").read_text(encoding="utf-8"))

    def _samples(self, d: Path, kind: str = "samples") -> list[dict]:
        sd = d / kind
        if not sd.exists():
            return []
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(sd.glob("[0-9][0-9][0-9][0-9].json"))]

    def create_session(self) -> dict:
        cfg = self.setup()
        if cfg is None:
            raise WorkflowError("invalid_setup", "calibration_setup.json is invalid: fix it in Board Setup", 422)
        probs = board_problems(cfg)
        if probs:
            raise WorkflowError("board_geometry_incomplete", probs[0], 422)
        cam = self._require_camera(cfg)
        with self._lock:
            sid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
            d = self.sessions_dir / sid
            (d / "samples").mkdir(parents=True)
            flange_status = self.flange.status() if self.flange is not None else None
            sess = {
                "session_id": sid, "created_at_utc": utc_now(), "state": "collecting",
                "setup_sha256": setup_sha256(cfg), "identity_sha256": identity_sha256(cfg),
                "board": cfg.board.model_dump(mode="json"),
                "camera": {"model": cam["model"], "serial": cam["serial"], "firmware": cam.get("firmware"),
                           "intrinsics": {k: cam[k] for k in ("fx", "fy", "cx", "cy", "width", "height",
                                                              "distortion_model", "coeffs")}},
                "robot_model": cfg.robot.model, "robot_serial": cfg.robot.robot_serial,
                "robot_pose_source": None, "simulation": bool(cam["simulation"] or cfg.robot.pose_mode == "simulation"),
                "robot_provider": flange_status, "rejected": [], "n_rejected": 0,
            }
            atomic_write_json(d / "session.json", sess)
        return self.get_session(sid)

    def get_session(self, session_id: str) -> dict:
        d, sess = self._load_session(session_id)
        samples = self._samples(d)
        cfg = self.setup()
        result = json.loads((d / "result.json").read_text(encoding="utf-8")) if (d / "result.json").exists() else None
        from .flange_calibration import rotation_diversity
        div = rotation_diversity([np.asarray(s["T_base_flange"]) for s in samples]) if len(samples) > 1 else None
        return {"ok": True, "status": "ok", "session": {k: v for k, v in sess.items() if k != "rejected"},
                "samples": [{k: s[k] for k in ("index", "frame_id", "acquired_at_utc", "reprojection_rmse_px",
                                                "n_points", "verdict", "orientation_canonicalized")} for s in samples],
                "n_valid": len(samples), "n_rejected": sess.get("n_rejected", 0),
                "recent_rejections": sess.get("rejected", [])[-10:], "pose_diversity": div,
                "recommended_samples": cfg.capture.recommended_samples if cfg else None,
                "min_samples_to_solve": cfg.capture.min_samples_to_solve if cfg else None,
                "result": summarize_record(result)}

    def _reject(self, d: Path, sess: dict, status: str, reason: str) -> None:
        sess.setdefault("rejected", []).append({"at_utc": utc_now(), "status": status, "reason": reason})
        sess["rejected"] = sess["rejected"][-50:]
        sess["n_rejected"] = int(sess.get("n_rejected", 0)) + 1
        atomic_write_json(d / "session.json", sess)

    def _observe(self, d: Path, sess: dict, cfg: CalibrationSetup) -> dict:
        """One synchronized (frame, stationary flange) observation of the board. Raises WorkflowError."""
        if setup_sha256(cfg) != sess["setup_sha256"]:
            raise WorkflowError("setup_changed", "the setup changed after this session started: start a new session")
        cam = self._require_camera(cfg)
        if cam["serial"] != sess["camera"]["serial"] or [cam["width"], cam["height"]] != [
                sess["camera"]["intrinsics"]["width"], sess["camera"]["intrinsics"]["height"]]:
            raise WorkflowError("camera_mismatch", "camera or resolution changed during the session")
        r0, r1 = self._stationary_flange(cfg)
        color, _depth, fid, ts = self._new_frame(r1.wall_s)
        r2 = self._close_flange(cfg, r0)
        from ..capture.realsense import Intrinsics
        i = sess["camera"]["intrinsics"]
        intr = Intrinsics(i["fx"], i["fy"], i["cx"], i["cy"], i["width"], i["height"], i["distortion_model"],
                          tuple(i["coeffs"]))
        board = cfg.board
        try:
            obs = observe_board(color, intr, board)
        except BoardRejected as exc:
            raise WorkflowError(exc.status, exc.reason) from exc
        if obs.rmse_px > cfg.quality_gates.max_sample_reprojection_px:
            raise WorkflowError("high_reprojection_error", f"board reprojection {obs.rmse_px:.2f} px > "
                                f"{cfg.quality_gates.max_sample_reprojection_px} px: blur, glare or a bent board")
        if sess.get("robot_pose_source") not in (None, r2.source):
            raise WorkflowError("invalid_flange_pose", f"robot pose source changed ({sess['robot_pose_source']} -> "
                                f"{r2.source}) during the session")
        return {"color": color, "frame_id": fid, "frame_ts": ts, "r0": r0, "r1": r1, "r2": r2, "obs": obs}

    def _record(self, o: dict, index: int, cfg: CalibrationSetup) -> dict:
        r0, r2, obs = o["r0"], o["r2"], o["obs"]
        return {
            "index": index, "frame_id": int(o["frame_id"]),
            "acquired_at_utc": utc_now(), "frame_wall_s": o["frame_ts"],
            "robot_reading_wall_s": [r0.wall_s, o["r1"].wall_s, r2.wall_s],
            "robot_reading_monotonic_s": [r0.received_monotonic_s, o["r1"].received_monotonic_s, r2.received_monotonic_s],
            "robot_pose_source": r2.source, "robot_serial": r2.robot_serial, "simulation": r2.simulation,
            "flange_diagnostics": r2.diagnostics,
            "T_base_flange": np.round(r2.T_base_flange, 9).tolist(),
            "T_camera_board": np.round(obs.T_camera_board, 9).tolist(),
            "board": cfg.board.model_dump(mode="json"), "board_type": obs.board_type, "n_points": len(obs.image_points),
            "image_points": np.round(obs.image_points, 4).tolist(), "object_points": obs.object_points.tolist(),
            "orientation_ambiguous": obs.orientation_ambiguous, "orientation_canonicalized": obs.canonicalized,
            "reprojection_rmse_px": round(obs.rmse_px, 4), "verdict": "valid",
        }

    def capture(self, session_id: str) -> dict:
        cfg = self.setup()
        if cfg is None:
            raise WorkflowError("invalid_setup", "calibration_setup.json is invalid", 422)
        with self._lock:
            d, sess = self._load_session(session_id)
            if sess.get("state") not in ("collecting", "solved"):
                raise WorkflowError("session_closed", f"session is {sess.get('state')}")
            try:
                o = self._observe(d, sess, cfg)
                samples = self._samples(d)
                if samples and samples[-1]["frame_id"] == o["frame_id"]:
                    raise WorkflowError("duplicate_frame", "same camera frame as the previous sample")
                for s in samples:
                    dt, dr = pose_delta(np.asarray(s["T_base_flange"]), o["r2"].T_base_flange)
                    if dt < DUPLICATE_POSE_MM and dr < DUPLICATE_POSE_DEG:
                        raise WorkflowError("duplicate_pose", f"pose too close to sample {s['index']} ({dt:.1f} mm, "
                                            f"{dr:.1f}°): move the robot to a new, different pose")
            except WorkflowError as exc:
                self._reject(d, sess, exc.status, exc.reason)
                raise
            index = (samples[-1]["index"] + 1) if samples else 1
            rec = self._record(o, index, cfg)
            cv2.imwrite(str(d / "samples" / f"{index:04d}.png"), o["color"])
            atomic_write_json(d / "samples" / f"{index:04d}.json", rec)
            sess["robot_pose_source"] = rec["robot_pose_source"]
            sess["robot_serial"] = sess.get("robot_serial") or rec["robot_serial"]
            sess["simulation"] = bool(sess.get("simulation") or rec["simulation"])
            if sess.get("state") == "solved":           # new data: the provisional result is stale
                sess["state"] = "collecting"
                (d / "result.json").unlink(missing_ok=True)
            atomic_write_json(d / "session.json", sess)
        out = self.get_session(session_id)
        out["sample"] = {k: rec[k] for k in ("index", "frame_id", "reprojection_rmse_px", "n_points",
                                              "orientation_canonicalized", "robot_pose_source")}
        return out

    def delete_sample(self, session_id: str, index: int) -> dict:
        with self._lock:
            d, sess = self._load_session(session_id)
            for ext in ("json", "png"):
                (d / "samples" / f"{int(index):04d}.{ext}").unlink(missing_ok=True)
            (d / "result.json").unlink(missing_ok=True)
            sess["state"] = "collecting"
            atomic_write_json(d / "session.json", sess)
        return self.get_session(session_id)

    # --- solve / validate / activate --------------------------------------------------------------------------

    def _K_dist(self, sess: dict) -> tuple[np.ndarray, np.ndarray]:
        from ..capture.realsense import Intrinsics
        i = sess["camera"]["intrinsics"]
        intr = Intrinsics(i["fx"], i["fy"], i["cx"], i["cy"], i["width"], i["height"], i["distortion_model"],
                          tuple(i["coeffs"]))
        return camera_matrix(intr), opencv_distortion(intr)

    def solve(self, session_id: str) -> dict:
        cfg = self.setup()
        if cfg is None:
            raise WorkflowError("invalid_setup", "calibration_setup.json is invalid", 422)
        with self._lock:
            d, sess = self._load_session(session_id)
            if setup_sha256(cfg) != sess["setup_sha256"]:
                raise WorkflowError("setup_changed", "the setup changed after this session started: start a new session")
            samples = [Sample.from_record(r) for r in self._samples(d)]
            K, dist = self._K_dist(sess)
            rep = solve(samples, cfg.quality_gates, cfg.capture.min_samples_to_solve, K, dist)
            if "T_flange_camera" not in rep:
                raise WorkflowError(rep["status"], rep["reason"], 422,
                                    **{k: v for k, v in rep.items() if k not in ("ok", "status", "reason")})
            rec = build_result(rep, sess, cfg, setup_sha256(cfg))
            est = mount_estimate_in_flange_m(cfg)
            warnings = []
            if est is not None:
                diff = float(np.linalg.norm(np.asarray(rec["T_flange_camera"])[:3, 3] - np.asarray(est))) * 1000
                rec["mount_estimate_check_mm"] = round(diff, 1)
                if diff > 30.0:
                    warnings.append(f"solved camera position is {diff:.0f} mm from the rough mount estimate: check the "
                                    "estimate's reference/signs (the estimate is never used as the calibration)")
            rec["warnings"] = warnings
            atomic_write_json(d / "result.json", rec)
            sess["state"] = "solved"
            atomic_write_json(d / "session.json", sess)
        return {"ok": rec["passes_quality_gates"], "status": "ok" if rec["passes_quality_gates"] else "high_calibration_error",
                "reason": None if rec["passes_quality_gates"] else "; ".join(rec["failures"]),
                "result": summarize_record(rec), "warnings": warnings,
                "activation_problems": activation_problems(rec)}

    def validate(self, session_id: str) -> dict:
        """Independent view of the (still unmoved) board from a NEW pose, not used for the fit."""
        cfg = self.setup()
        if cfg is None:
            raise WorkflowError("invalid_setup", "calibration_setup.json is invalid", 422)
        with self._lock:
            d, sess = self._load_session(session_id)
            if not (d / "result.json").exists():
                raise WorkflowError("missing_calibration", "solve first")
            rec = json.loads((d / "result.json").read_text(encoding="utf-8"))
            o = self._observe(d, sess, cfg)
            X, T_bb = np.asarray(rec["T_flange_camera"]), np.asarray(rec["T_base_board_during_calibration"])
            s = Sample(0, o["r2"].T_base_flange, o["obs"].T_camera_board, o["obs"].image_points,
                       o["obs"].object_points, o["obs"].rmse_px)
            err = predict_board_error_mm(s, X, T_bb)
            ok = err <= cfg.quality_gates.max_holdout_error_mm
            v = {"at_utc": utc_now(), "frame_id": int(o["frame_id"]), "error_mm": round(err, 3), "ok": ok,
                 "reprojection_rmse_px": round(o["obs"].rmse_px, 3),
                 "T_base_flange": np.round(o["r2"].T_base_flange, 9).tolist()}
            rec.setdefault("validation", []).append(v)
            atomic_write_json(d / "result.json", rec)
        return {"ok": ok, "status": "ok" if ok else "high_calibration_error",
                "reason": None if ok else f"validation error {err:.1f} mm > {cfg.quality_gates.max_holdout_error_mm} mm",
                "validation": v, "result": summarize_record(rec), "activation_problems": activation_problems(rec)}

    def activate(self, session_id: str, confirm: bool, operator_note: str = "") -> dict:
        if confirm is not True:
            raise WorkflowError("confirmation_required", "explicit operator confirmation is required", 400)
        cfg = self.setup()
        with self._lock:
            d, sess = self._load_session(session_id)
            if not (d / "result.json").exists():
                raise WorkflowError("missing_calibration", "solve first")
            rec = json.loads((d / "result.json").read_text(encoding="utf-8"))
            probs = activation_problems(rec)
            if cfg is None or rec.get("identity_sha256") != identity_sha256(cfg):
                probs.append("setup identity changed since the solve")
            if rec.get("simulation") and cfg is not None and cfg.robot.pose_mode == "physical_read_only":
                probs.append("simulated calibration cannot be activated in physical mode")
            if probs:
                raise WorkflowError("high_calibration_error", "cannot activate: " + "; ".join(probs), 409,
                                    activation_problems=probs)
            out = activate(rec, self.calib_dir, operator_note)
            sess["state"] = "activated"
            atomic_write_json(d / "session.json", sess)
            atomic_write_json(d / "result.json", out)
        self.apply_to_pipeline()
        return {"ok": True, "status": "ok", "calibration_id": out["calibration_id"], "result": summarize_record(out)}

    def _active_record(self) -> dict | None:
        p = self.calib_dir / ACTIVE_FILE
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except json.JSONDecodeError:
            return None

    def active(self):
        cam = self.camera_info()
        cam_id = None if cam is None or not cam["connected"] else cam
        return load_active(self.calib_dir, self.setup(), cam_id)

    def apply_to_pipeline(self) -> bool:
        """Use the active flange calibration for the live `xyz_base_m` of the vision pipeline (and `/locate`).

        Explicit adapter: `HandEye(T_tool_cam=T_flange_camera, tcp_source="ur_flange")` fed with *flange* poses from
        the read-only provider. Replaces any fixed-pose / legacy TCP hand-eye on this node; there is no fallback."""
        from .hand_eye import HandEye, T_to_pose, TcpReading
        node = self.pipeline.nodes.get(self.source)
        cal, _status, _reason = self.active()
        if node is None:
            return False
        rec = self._active_record()
        if (cal is None or self.flange is None) and rec is not None and rec.get("approved_for_localization"):
            # eye-in-hand flange mode intended but not usable now: no silent fallback to a fixed pose / TCP hand-eye
            if node.hand_eye is not None:
                print(f"[Calibration] flange calibration present but unusable ({_reason or 'no robot pose source'}): "
                      "base coordinates disabled instead of falling back to the fixed pose")
            node.hand_eye, node.tcp_fn, node.calibration_id = None, None, None
            return False
        if cal is None or self.flange is None:
            return False
        provider = self.flange

        def flange_tcp() -> TcpReading | None:
            r = provider.read()
            if not r.valid or r.T_base_flange is None:
                return None
            return TcpReading(pose=tuple(T_to_pose(r.T_base_flange)), t=r.wall_s, moving=r.moving,
                              age_ms=r.age_ms())

        node.hand_eye = HandEye(T_tool_cam=cal.T_flange_camera, method="flange_park_martin", tcp_source="ur_flange",
                                image_size=tuple(cal.record.get("image_size") or ()) or None,
                                extra={"calibration_id": cal.calibration_id})
        node.tcp_fn = flange_tcp
        node.calibrator = None
        node.calibration_id = cal.calibration_id
        node.tcp_readings.clear()
        return True

    # --- status -----------------------------------------------------------------------------------------------

    def status(self) -> dict:
        cfg, errors, src = load_setup(self.calib_dir)
        cam = self.camera_info()
        reading = None
        if self.flange is not None:
            try:
                reading = self.flange.read().summary()
            except Exception as exc:  # noqa: BLE001 - status never raises
                reading = {"valid": False, "status": "robot_offline", "reason": f"{type(exc).__name__}: {exc}"}
        cal, cal_status, cal_reason = self.active()
        rec = self._active_record()
        sessions = sorted((p.name for p in self.sessions_dir.glob("*") if SESSION_RE.match(p.name)), reverse=True) \
            if self.sessions_dir.exists() else []
        latest = None
        if sessions:
            try:
                latest = self.get_session(sessions[0])
            except Exception:  # noqa: BLE001
                latest = None
        if cal is not None:
            card = "Calibrated"
        elif rec is not None and cal_status != "ok":
            card = "Error"
        elif latest and latest["session"].get("state") == "solved":
            card = "Pending validation"
        elif latest and latest["session"].get("state") == "collecting" and latest["n_valid"] > 0:
            card = "Collecting samples"
        else:
            card = "Not calibrated"
        simulation = bool((cfg and cfg.robot.pose_mode == "simulation") or (cam and cam.get("simulation"))
                          or (self.flange is not None and getattr(self.flange, "simulation", False)))
        return {
            "ok": True, "status": "ok", "card_state": card, "simulation": simulation,
            "mode_label": "SIMULATION" if simulation else "REAL ROBOT",
            "setup": {"valid": cfg is not None, "source_file": src, "errors": errors, **self._setup_notes(cfg)},
            "camera": cam,
            "robot": {"provider": self.flange.status() if self.flange is not None else None, "reading": reading,
                      "model": cfg.robot.model if cfg else None, "serial": cfg.robot.robot_serial if cfg else None},
            "active_calibration": {"valid": cal is not None, "status": cal_status, "reason": cal_reason,
                                   "record": summarize_record(rec)},
            "latest_session": latest["session"] if latest else None,
            "latest_session_counts": {"valid": latest["n_valid"], "rejected": latest["n_rejected"]} if latest else None,
            "motion_allowed": False,
        }

    # --- Phase D: one RGB-D shot -> object in robot base ------------------------------------------------------

    def _still_frame(self):
        """One new aligned RGB-D frame bracketed by stationary flange reads. Raises `WorkflowError` otherwise.

        Returns `(cfg, cam, cal, payload, r2)`: the frame was taken after `r1` and the robot was still (same pose)
        from `r0` to `r2`, so `r2.T_base_flange` is the camera pose for this frame."""
        cfg = self.setup()
        if cfg is None:
            raise WorkflowError("invalid_setup", "calibration_setup.json is invalid", 422)
        cam = self._require_camera(cfg)
        cal, status, reason = self.active()
        if cal is None:
            raise WorkflowError(status, reason or status)
        with self._lock:
            r0, r1 = self._stationary_flange(cfg)
            before_fid = self.pipeline.state.get_payload(self.source).frame_id
            deadline = time.monotonic() + self.frame_timeout_s
            while True:
                if not self.pipeline._running:
                    self.pipeline.step(self.source)
                payload = self.pipeline.state.get_payload(self.source)
                if payload.frame_id != before_fid and payload.frame_id > self._last_frame_id and payload.ts >= r1.wall_s:
                    break
                if time.monotonic() > deadline:
                    raise WorkflowError("camera_offline", "no new RGB-D frame from the camera", 503)
                self.sleep(0.02)
            self._last_frame_id = payload.frame_id
            if self.node.last_depth is None:
                raise WorkflowError("invalid_depth", "no aligned depth frame (RealSense depth stream missing)")
            r2 = self._close_flange(cfg, r0)
        return cfg, cam, cal, payload, r2

    @staticmethod
    def _object_problem(o, loc) -> tuple[str, str] | None:
        """(status, reason) why this detection cannot give a base-frame point, or None if it can."""
        x, y, w, h = o.bbox
        m = loc.edge_margin
        if min(x, y) <= m or x + w >= 1 - m or y + h >= 1 - m:
            return ("object_partially_visible", "object cut by the image border: its visible centroid is "
                    "not reliable. Move the robot so the whole object is in view.")
        if o.xyz_cam_m is None or o.mask is None:
            return "invalid_depth", "object seen but without a valid mask/depth"
        if (o.depth_valid_ratio or 0) < loc.min_depth_valid_ratio or (o.depth_points or 0) < loc.min_depth_points:
            return "invalid_depth", (f"too little valid depth on the mask ({o.depth_points or 0} points, "
                                     f"{(o.depth_valid_ratio or 0) * 100:.0f}% valid)")
        return None

    @staticmethod
    def _frame_meta(cfg, cam, cal, payload, r2) -> dict:
        return {
            "source_frame_id": f"camera_color_{payload.frame_id}",
            "timestamp_utc": datetime.fromtimestamp(payload.ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "ts": payload.ts,
            "coordinate_frame": "ur5e_base",
            "point_semantics": cfg.localization.point_semantics,
            "robot_pose_source": r2.source, "simulation": bool(r2.simulation or cam.get("simulation")),
            "calibration_id": cal.calibration_id, "camera_serial": cam.get("serial"),
            "calibration_reference_frame": cal.record.get("reference_frame"),
            "robot_model": cal.record.get("robot_model"), "robot_serial": cal.record.get("robot_serial"),
            "T_base_flange": np.round(r2.T_base_flange, 6).tolist(),
            # both true by construction: _still_frame raised otherwise
            "robot_stationary": True, "pose_frame_synchronized": True,
            "motion_allowed": False,
        }

    def snapshot(self, object_label: str | None = None, object_id: int | None = None) -> dict:
        """Locate one object from ONE new, aligned RGB-D frame taken with the robot verified still."""
        if not object_label and object_id is None:
            if self.setup() is None:
                raise WorkflowError("invalid_setup", "calibration_setup.json is invalid", 422)
            raise WorkflowError("bad_request", "object_label or object_id required", 400)
        cfg, cam, cal, payload, r2 = self._still_frame()
        want = (object_label or "").strip().lower()
        if object_id is not None:
            cands = [o for o in payload.objects if o.id == object_id]
        else:
            cands = sorted((o for o in payload.objects if want in (o.label.lower(), (o.name or "").lower())),
                           key=lambda o: -o.conf)
        if not cands:
            raise WorkflowError("object_not_detected", f"'{object_label or object_id}' not detected in this frame",
                                frame_id=f"camera_color_{payload.frame_id}")
        o = cands[0]
        prob = self._object_problem(o, cfg.localization)
        if prob:
            raise WorkflowError(*prob)
        p_base = r2.T_base_flange @ cal.T_flange_camera @ np.array([*o.xyz_cam_m, 1.0])
        meta = self._frame_meta(cfg, cam, cal, payload, r2)
        return {
            "ok": True, "status": "ok", "source": self.source, "object_label": o.name or o.label,
            "object_class": o.label, "object_id": o.id, "confidence": round(float(o.conf), 3),
            "timestamp_utc": meta["timestamp_utc"],
            "frame_id": meta["source_frame_id"], "coordinate_frame": "ur5e_base",
            "position_camera_m": [round(float(v), 4) for v in o.xyz_cam_m],
            "position_base_m": [round(float(v), 4) for v in p_base[:3]],
            "point_semantics": meta["point_semantics"],
            "depth_valid_ratio": round(float(o.depth_valid_ratio or 0), 3), "depth_points": int(o.depth_points or 0),
            "robot_pose_source": r2.source, "simulation": meta["simulation"],
            "calibration_id": cal.calibration_id, "camera_serial": cam.get("serial"),
            "T_base_flange": meta["T_base_flange"],
            "quality_flags": [],
            "motion_allowed": False,
        }

    def snapshot_all(self) -> dict:
        """Every detection in ONE new stationary RGB-D frame, each with a base-frame point or the reason it has none.

        Same gates as `snapshot`, applied per object, so a caller (the digital twin) gets one coherent set of
        observations from a single frame + flange pose instead of N separate captures. Read-only."""
        cfg, cam, cal, payload, r2 = self._still_frame()
        objs = []
        for o in payload.objects:
            prob = self._object_problem(o, cfg.localization)
            entry = {
                "track_id": o.id, "object_class": o.label, "object_label": o.name or o.label,
                "confidence": round(float(o.conf), 3), "bbox": list(o.bbox),
                "depth_valid_ratio": None if o.depth_valid_ratio is None else round(float(o.depth_valid_ratio), 3),
                "depth_points": None if o.depth_points is None else int(o.depth_points),
                "position_camera_m": None if o.xyz_cam_m is None else [round(float(v), 4) for v in o.xyz_cam_m],
                "position_base_m": None, "status": "ok", "reason": None,
            }
            if prob:
                entry["status"], entry["reason"] = prob
            else:
                p_base = r2.T_base_flange @ cal.T_flange_camera @ np.array([*o.xyz_cam_m, 1.0])
                entry["position_base_m"] = [round(float(v), 4) for v in p_base[:3]]
            objs.append(entry)
        return {"ok": True, "status": "ok", "source": self.source,
                **self._frame_meta(cfg, cam, cal, payload, r2), "objects": objs}
