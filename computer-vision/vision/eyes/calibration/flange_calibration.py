"""Eye-in-hand calibration referenced to the UR5e **flange**: solve, validate, persist, activate (Phase C).

For every sample i (robot still, board fixed on the table):

    T_base_board = T_base_flange_i @ T_flange_camera @ T_camera_board_i

`T_flange_camera` is solved with Park & Martin (`hand_eye._park_martin`: OpenCV's R_gripper2base = flange->base,
R_target2cam = board->camera, result = camera->flange expressed as T_flange_camera). Quality is judged on:
- per-sample PnP reprojection RMSE;
- consistency of the reconstructed fixed board (translation AND rotation spread);
- rotational diversity of the robot poses (largest rotation, and rotation about a second axis);
- a hold-out subset that is not used for the fit (when there are enough samples);
- sanity of the transform (finite, orthonormal, det +1, plausible translation).

Persistence: the provisional result lives in the session; `activate` (quality gates + explicit operator
confirmation) writes `data/calibration/hand_eye_flange.json` atomically, keeping a copy of the previous active file
in `data/calibration/history/`. The rough mount estimate is recorded as metadata only. Legacy `hand_eye.json`
(`T_tool_cam`, TCP frame) is never read as a flange calibration.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

from .hand_eye import _park_martin
from .setup_config import CalibrationSetup, QualityGates, atomic_write_json, identity_sha256

ACTIVE_FILE = "hand_eye_flange.json"
RESULT_SCHEMA = "1.0"
MAX_PLAUSIBLE_OFFSET_M = 0.5   # camera farther than this from the flange = not a wrist mount


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --- rotation helpers ------------------------------------------------------------------------------------------

def rot_angle_deg(R: np.ndarray) -> float:
    c = (np.trace(R) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def nearest_rotation(M: np.ndarray) -> np.ndarray:
    U, _, Vt = np.linalg.svd(M)
    D = np.diag([1.0, 1.0, np.sign(np.linalg.det(U @ Vt))])
    return U @ D @ Vt


def mean_pose(Ts: Sequence[np.ndarray]) -> np.ndarray:
    """Translation mean and chordal (SVD) rotation mean."""
    T = np.eye(4)
    T[:3, :3] = nearest_rotation(sum(t[:3, :3] for t in Ts))
    T[:3, 3] = np.mean([t[:3, 3] for t in Ts], axis=0)
    return T


def rotation_diversity(T_base_flanges: Sequence[np.ndarray]) -> dict:
    """How much the flange rotated between samples.

    `max_rotation_deg`: largest relative rotation between two samples. `second_axis_rotation_deg`: RMS rotation along
    the second principal axis of all pairwise relative rotation vectors: near zero when every rotation is about the
    same axis (degenerate for hand-eye).
    """
    n = len(T_base_flanges)
    rel = []
    for i in range(n):
        for j in range(i + 1, n):
            R = T_base_flanges[i][:3, :3].T @ T_base_flanges[j][:3, :3]
            rel.append(np.degrees(cv2.Rodrigues(np.ascontiguousarray(R))[0].ravel()))
    if not rel:
        return {"max_rotation_deg": 0.0, "second_axis_rotation_deg": 0.0, "pairs": 0}
    A = np.array(rel)
    s = np.linalg.svd(A, compute_uv=False) / math.sqrt(len(rel))
    return {"max_rotation_deg": round(float(np.max(np.linalg.norm(A, axis=1))), 2),
            "second_axis_rotation_deg": round(float(s[1]) if len(s) > 1 else 0.0, 2), "pairs": len(rel)}


def transform_problems(T: np.ndarray) -> list[str]:
    out = []
    if T.shape != (4, 4) or not np.all(np.isfinite(T)):
        return ["transform is not a finite 4x4 matrix"]
    R = T[:3, :3]
    if not np.allclose(R.T @ R, np.eye(3), atol=1e-6):
        out.append("rotation is not orthonormal")
    if abs(np.linalg.det(R) - 1.0) > 1e-6:
        out.append("rotation determinant is not +1")
    if not np.allclose(T[3], [0, 0, 0, 1]):
        out.append("last row is not [0, 0, 0, 1]")
    if np.allclose(T, np.eye(4), atol=1e-9):
        out.append("identity transform (placeholder, not a calibration)")
    if float(np.linalg.norm(T[:3, 3])) > MAX_PLAUSIBLE_OFFSET_M:
        out.append(f"camera more than {MAX_PLAUSIBLE_OFFSET_M * 1000:.0f} mm from the flange: implausible")
    return out


# --- samples and solve -----------------------------------------------------------------------------------------

@dataclass(eq=False)  # identity comparison: fields are numpy arrays
class Sample:
    index: int
    T_base_flange: np.ndarray
    T_camera_board: np.ndarray
    image_points: np.ndarray
    object_points: np.ndarray
    rmse_px: float

    @classmethod
    def from_record(cls, rec: dict) -> Sample:
        return cls(int(rec["index"]), np.asarray(rec["T_base_flange"], float), np.asarray(rec["T_camera_board"], float),
                   np.asarray(rec["image_points"], float), np.asarray(rec["object_points"], float),
                   float(rec["reprojection_rmse_px"]))


def board_in_base(samples: Sequence[Sample], X: np.ndarray) -> list[np.ndarray]:
    return [s.T_base_flange @ X @ s.T_camera_board for s in samples]


def board_consistency(boards: Sequence[np.ndarray]) -> dict:
    M = mean_pose(boards)
    dt = np.array([np.linalg.norm(b[:3, 3] - M[:3, 3]) for b in boards]) * 1000.0
    dr = np.array([rot_angle_deg(M[:3, :3].T @ b[:3, :3]) for b in boards])
    return {"mean": M, "trans_dev_mm": dt, "rot_dev_deg": dr,
            "board_spread_mm": float(np.sqrt(np.mean(dt ** 2))), "board_spread_max_mm": float(dt.max()),
            "board_rotation_spread_deg": float(np.sqrt(np.mean(dr ** 2))), "board_rotation_max_deg": float(dr.max())}


def corner_errors_mm(samples: Sequence[Sample], X: np.ndarray, T_base_board: np.ndarray) -> list[float]:
    """Mean distance (mm) between each sample's board corners reconstructed in base and the fitted fixed board."""
    out = []
    for s in samples:
        P = np.column_stack([s.object_points, np.ones(len(s.object_points))]).T
        obs = (s.T_base_flange @ X @ s.T_camera_board @ P)[:3]
        ref = (T_base_board @ P)[:3]
        out.append(float(np.mean(np.linalg.norm(obs - ref, axis=0)) * 1000.0))
    return out


def handeye_reprojection_px(samples: Sequence[Sample], X: np.ndarray, T_base_board: np.ndarray, K: np.ndarray,
                            dist: np.ndarray) -> list[float]:
    """Reproject the fitted fixed board through each robot pose + X: includes robot and calibration error."""
    out = []
    Xi = np.linalg.inv(X)
    for s in samples:
        T_cb = Xi @ np.linalg.inv(s.T_base_flange) @ T_base_board
        rvec = cv2.Rodrigues(np.ascontiguousarray(T_cb[:3, :3]))[0]
        proj, _ = cv2.projectPoints(s.object_points, rvec, T_cb[:3, 3].astype(np.float64), K, dist)
        out.append(float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - s.image_points) ** 2, axis=1)))))
    return out


def holdout_indices(n: int, min_fit: int) -> list[int]:
    """Every 5th sample (positions 4, 9, ...) when at least 3 can be held out and `min_fit` remain."""
    idx = list(range(4, n, 5))
    return idx if len(idx) >= 3 and n - len(idx) >= min_fit else []


def solve(samples: Sequence[Sample], gates: QualityGates, min_samples: int, K: np.ndarray,
          dist: np.ndarray) -> dict:
    """Solve and assess. Never raises for bad data: returns `{"ok": False, "status", "reason", ...}`."""
    samples = list(samples)
    n = len(samples)
    if n < min_samples:
        return {"ok": False, "status": "insufficient_samples",
                "reason": f"{n} valid samples, at least {min_samples} needed (20 recommended)"}
    div = rotation_diversity([s.T_base_flange for s in samples])
    if div["max_rotation_deg"] < gates.min_max_rotation_deg or div["second_axis_rotation_deg"] < gates.min_second_axis_rotation_deg:
        return {"ok": False, "status": "insufficient_pose_diversity", "pose_diversity": div,
                "reason": f"not enough rotation: largest {div['max_rotation_deg']}° (need {gates.min_max_rotation_deg}°), "
                          f"second axis {div['second_axis_rotation_deg']}° (need {gates.min_second_axis_rotation_deg}°). "
                          "Tilt the camera about two different axes between captures."}
    hold = holdout_indices(n, min_samples)
    fit = [s for k, s in enumerate(samples) if k not in hold]
    held = [s for k, s in enumerate(samples) if k in hold]

    rejected: list[dict] = []
    X = _park_martin([s.T_base_flange for s in fit], [s.T_camera_board for s in fit])
    cons = board_consistency(board_in_base(fit, X))
    flipped = [s for s, d in zip(fit, cons["rot_dev_deg"]) if d > 90.0]
    if flipped:  # 180°-symmetric chessboard indexed the other way round: drop those samples and refit once
        rejected += [{"index": s.index, "status": "inconsistent_orientation",
                      "reason": "board indexed in the opposite orientation to the other samples"} for s in flipped]
        fit = [s for s in fit if s not in flipped]
        if len(fit) < min_samples:
            return {"ok": False, "status": "inconsistent_orientation", "rejected_samples": rejected,
                    "reason": f"{len(flipped)} samples have the board orientation flipped; fewer than {min_samples} "
                              "remain. Use a board with odd x even inner corners or the ArUco board."}
        X = _park_martin([s.T_base_flange for s in fit], [s.T_camera_board for s in fit])
        cons = board_consistency(board_in_base(fit, X))
    X[:3, :3] = nearest_rotation(X[:3, :3])
    T_bb = cons["mean"]

    problems = transform_problems(X)
    pnp = [s.rmse_px for s in fit]
    he_px = handeye_reprojection_px(fit, X, T_bb, K, dist)
    hold_err = corner_errors_mm(held, X, T_bb) if held else []
    quality = {
        "n_samples": len(samples), "n_fit": len(fit), "n_holdout": len(held),
        "rms_reprojection_px": round(float(np.sqrt(np.mean(np.square(pnp)))), 3),
        "max_reprojection_px": round(float(max(pnp)), 3),
        "handeye_reprojection_rms_px": round(float(np.sqrt(np.mean(np.square(he_px)))), 3),
        "board_spread_mm": round(cons["board_spread_mm"], 3), "board_spread_max_mm": round(cons["board_spread_max_mm"], 3),
        "board_rotation_spread_deg": round(cons["board_rotation_spread_deg"], 3),
        "board_rotation_max_deg": round(cons["board_rotation_max_deg"], 3),
        "holdout_error_mm": round(float(np.mean(hold_err)), 3) if hold_err else None,
        "holdout_max_error_mm": round(float(max(hold_err)), 3) if hold_err else None,
        "pose_diversity": div,
    }
    failures = list(problems)
    if quality["max_reprojection_px"] > gates.max_sample_reprojection_px:
        failures.append(f"sample reprojection {quality['max_reprojection_px']} px > {gates.max_sample_reprojection_px} px")
    if quality["board_spread_mm"] > gates.max_board_spread_mm:
        failures.append(f"board position spread {quality['board_spread_mm']} mm > {gates.max_board_spread_mm} mm")
    if quality["board_rotation_spread_deg"] > gates.max_board_rotation_spread_deg:
        failures.append(f"board rotation spread {quality['board_rotation_spread_deg']}° > {gates.max_board_rotation_spread_deg}°")
    if hold_err and quality["holdout_max_error_mm"] > gates.max_holdout_error_mm:
        failures.append(f"hold-out error {quality['holdout_max_error_mm']} mm > {gates.max_holdout_error_mm} mm")
    per_sample = []
    fit_ids = {s.index for s in fit}
    for s in samples:
        role = "fit" if s.index in fit_ids else "holdout" if s in held else "rejected"
        row = {"index": s.index, "role": role, "pnp_rmse_px": round(s.rmse_px, 3)}
        if role == "fit":
            k = [f.index for f in fit].index(s.index)
            row.update(board_dev_mm=round(float(cons["trans_dev_mm"][k]), 3),
                       board_dev_deg=round(float(cons["rot_dev_deg"][k]), 3), handeye_px=round(he_px[k], 3))
        elif role == "holdout":
            row["holdout_error_mm"] = round(hold_err[held.index(s)], 3)
        per_sample.append(row)
    return {"ok": not failures, "status": "ok" if not failures else "high_calibration_error",
            "reason": None if not failures else "; ".join(failures),
            "T_flange_camera": X, "T_base_board": T_bb, "quality": quality, "failures": failures,
            "rejected_samples": rejected, "per_sample": per_sample, "holdout_indices": [s.index for s in held],
            "passes_quality_gates": not failures, "method": "park_martin"}


# --- result records --------------------------------------------------------------------------------------------

def build_result(report: dict, session: dict, setup: CalibrationSetup, setup_sha: str) -> dict:
    """Provisional result record (never approved here)."""
    X = report["T_flange_camera"]
    mount = setup.camera_mount_estimate
    rec = {
        "schema_version": RESULT_SCHEMA,
        "calibration_type": "eye_in_hand_3d",
        "reference_frame": "flange",
        "camera_frame": "camera_color_optical",
        "convention": "T_A_B maps points in frame B to frame A; p_base = T_base_flange @ T_flange_camera @ p_camera",
        "method": report["method"],
        "session_id": session["session_id"],
        "robot_model": setup.robot.model,
        "robot_serial": session.get("robot_serial"),
        "robot_pose_source": session.get("robot_pose_source"),
        "simulation": bool(session.get("simulation")),
        "camera_model": session["camera"].get("model"),
        "camera_serial": session["camera"].get("serial"),
        "image_size": [session["camera"]["intrinsics"]["width"], session["camera"]["intrinsics"]["height"]],
        "intrinsics": session["camera"]["intrinsics"],
        "board": {**session["board"], "setup_sha256": session["setup_sha256"]},
        "T_flange_camera": np.round(X, 9).tolist(),
        "T_base_board_during_calibration": np.round(report["T_base_board"], 9).tolist(),
        "quality": report["quality"],
        "quality_gates": setup.quality_gates.model_dump(),
        "passes_quality_gates": report["passes_quality_gates"],
        "failures": report["failures"],
        "rejected_samples": report["rejected_samples"],
        "per_sample": report["per_sample"],
        "setup_sha256": setup_sha,
        "identity_sha256": identity_sha256(setup),
        "mount_estimate_mm": {"x_longitudinal": mount.x_longitudinal_mm, "y": mount.y_mm, "z": mount.z_mm,
                              "reference_frame_as_measured": mount.reference_frame_as_measured,
                              "rotation_rpy_deg": mount.rotation_rpy_deg, "used_as_calibration": False},
        "validation": [],
        "approved_for_localization": False,
        "created_at_utc": utc_now(),
    }
    rec["calibration_id"] = calibration_id(rec)
    return rec


def calibration_id(rec: dict) -> str:
    core = {k: rec[k] for k in ("T_flange_camera", "session_id", "camera_serial", "robot_serial", "setup_sha256",
                                "created_at_utc") if k in rec}
    return "hef-" + hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:12]


def activation_problems(rec: dict) -> list[str]:
    out = []
    if rec.get("reference_frame") != "flange" or rec.get("calibration_type") != "eye_in_hand_3d":
        out.append("not a flange eye-in-hand calibration")
    T = np.asarray(rec.get("T_flange_camera"), dtype=float)
    out += transform_problems(T) if T.shape == (4, 4) else ["T_flange_camera is not 4x4"]
    if not rec.get("passes_quality_gates"):
        out.append("quality gates not passed: " + "; ".join(rec.get("failures") or []))
    q = rec.get("quality") or {}
    val = [v for v in rec.get("validation") or [] if v.get("ok") is not None]
    if q.get("holdout_error_mm") is None and sum(1 for v in val if v["ok"]) < 3:
        out.append("no hold-out set: capture at least 3 independent validation views first")
    if any(v.get("ok") is False for v in val):
        out.append("an independent validation view exceeded the hold-out tolerance")
    if not rec.get("camera_serial") and not rec.get("simulation"):
        out.append("camera serial unknown")
    return out


def activate(rec: dict, calib_dir: str | Path, operator_note: str) -> dict:
    """Write the approved record as the active calibration; previous active file goes to history/."""
    d = Path(calib_dir)
    active = d / ACTIVE_FILE
    if active.exists():
        try:
            prev = json.loads(active.read_text(encoding="utf-8"))
            tag = prev.get("calibration_id") or datetime.now().strftime("%Y%m%d-%H%M%S")
        except json.JSONDecodeError:
            prev, tag = None, datetime.now().strftime("%Y%m%d-%H%M%S") + "-unreadable"
        hist = d / "history"
        hist.mkdir(parents=True, exist_ok=True)
        (hist / f"hand_eye_flange.{tag}.json").write_bytes(active.read_bytes())
    out = {**rec, "approved_for_localization": True, "approved_at_utc": utc_now(),
           "operator_note": operator_note[:500]}
    atomic_write_json(active, out)
    return out


@dataclass(frozen=True)
class ActiveCalibration:
    T_flange_camera: np.ndarray
    calibration_id: str
    record: dict


def load_active(calib_dir: str | Path, setup: CalibrationSetup | None,
                camera: dict | None) -> tuple[ActiveCalibration | None, str, str | None]:
    """Active calibration if it is valid for *this* setup and camera: `(cal, status, reason)`.

    `camera`: {"serial", "width", "height", "fx", "fy", "cx", "cy"} of the connected device (None = offline).
    """
    p = Path(calib_dir) / ACTIVE_FILE
    if not p.exists():
        legacy = Path(calib_dir) / "hand_eye.json"
        why = " (legacy hand_eye.json is TCP-frame T_tool_cam and is not used as a flange calibration)" if legacy.exists() else ""
        return None, "missing_calibration", "no active flange calibration: run Skills > Calibration" + why
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, "missing_calibration", f"{ACTIVE_FILE} unreadable: {exc}"
    if "T_tool_cam" in rec and "T_flange_camera" not in rec:
        return None, "missing_calibration", f"{ACTIVE_FILE} is a legacy TCP-frame file: recalibrate against the flange"
    probs = activation_problems(rec)
    if probs:
        return None, "missing_calibration", "active calibration invalid: " + "; ".join(probs)
    if not rec.get("approved_for_localization"):
        return None, "missing_calibration", "calibration not approved by the operator"
    if setup is None:
        return None, "missing_calibration", "calibration_setup.json is invalid"
    if rec.get("identity_sha256") != identity_sha256(setup):
        return None, "missing_calibration", ("robot/camera identity or camera mount changed in the setup since this "
                                             "calibration: recalibrate (or restore the previous setup)")
    if camera is None:
        return None, "camera_offline", "camera offline"
    if rec.get("camera_serial") and camera.get("serial") and rec["camera_serial"] != camera["serial"]:
        return None, "camera_mismatch", f"camera serial {camera['serial']} != calibrated {rec['camera_serial']}"
    if not rec.get("simulation") and not camera.get("serial"):
        return None, "camera_mismatch", "connected camera serial unknown"
    intr = rec.get("intrinsics") or {}
    if [intr.get("width"), intr.get("height")] != [camera.get("width"), camera.get("height")]:
        return None, "camera_mismatch", (f"camera resolution {camera.get('width')}x{camera.get('height')} differs from "
                                         f"the calibrated {intr.get('width')}x{intr.get('height')}")
    for k in ("fx", "fy", "cx", "cy"):
        a, b = float(intr.get(k, 0)), float(camera.get(k, 0))
        if abs(a - b) > max(0.5, 0.005 * abs(a)):
            return None, "camera_mismatch", f"camera intrinsics changed ({k}: {b:.2f} vs calibrated {a:.2f})"
    return ActiveCalibration(np.asarray(rec["T_flange_camera"], float), rec["calibration_id"], rec), "ok", None


def summarize_record(rec: dict | None) -> dict | None:
    if rec is None:
        return None
    keep = ("calibration_id", "session_id", "created_at_utc", "approved_at_utc", "approved_for_localization",
            "passes_quality_gates", "failures", "quality", "T_flange_camera", "camera_serial", "robot_serial",
            "robot_pose_source", "simulation", "image_size", "board", "mount_estimate_mm", "validation", "per_sample",
            "rejected_samples")
    return {k: rec.get(k) for k in keep}


def predict_board_error_mm(sample: Sample, X: np.ndarray, T_base_board: np.ndarray) -> float:
    return corner_errors_mm([sample], X, T_base_board)[0]


def as_array(v: Any) -> np.ndarray:
    return np.asarray(v, dtype=np.float64)
