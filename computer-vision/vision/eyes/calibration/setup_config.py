"""Operator-editable calibration setup: `data/calibration/calibration_setup.json`.

One typed, validated schema (Pydantic) shared by the setup file and its committed template
(`calibration_setup.example.json`). Nothing here is a calibration result: the computed, quality-checked
`T_flange_camera` lives in `hand_eye_flange.json` (see `flange_calibration.py`).

Rules enforced here (MOTIQ_Codex_Calibration_Skill_EN.md §1):
- the rough mount offsets (75 mm X, 35 mm Z, ...) are metadata only: `use_as_calibration` can never be true;
- missing values stay `null` (inner corners, Y offset, rotation, serials): nothing is invented;
- the robot is read-only (`allow_automatic_robot_motion` / `send_robot_motion_commands` are fixed to false);
- board geometry problems are reported as a list for the app instead of failing the whole file.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SCHEMA_VERSION = "1.0.0"

# computer-vision/data/calibration, independent of the current directory
CALIB_DIR = Path(__file__).resolve().parents[3] / "data" / "calibration"
SETUP_FILE = "calibration_setup.json"
EXAMPLE_FILE = "calibration_setup.example.json"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _finite(v: float | None) -> float | None:
    if v is not None and not math.isfinite(v):
        raise ValueError("must be a finite number")
    return v


class RobotSetup(_Strict):
    model: Literal["UR5e"] = "UR5e"
    # "simulation" exists only for demos/tests and is always shown as SIMULATION in the app
    pose_mode: Literal["physical_read_only", "simulation"] = "physical_read_only"
    calibration_frame: Literal["flange"] = "flange"
    robot_serial: str | None = None
    # How T_base_flange is obtained from read-only RTDE (capture/ur_flange.py):
    #   tcp_minus_verified_offset: T_base_tcp @ inv(T_flange_tcp), with the active TCP offset copied from the pendant
    #       (Installation > General > TCP) and confirmed; cross-checked against joint forward kinematics.
    #   joint_fk_nominal: actual joints + nominal UR5e DH (few-mm absolute error; no per-robot kinematic calibration).
    flange_pose_method: Literal["tcp_minus_verified_offset", "joint_fk_nominal"] = "tcp_minus_verified_offset"
    active_tcp_offset_m_rad: list[float] | None = Field(
        default=None, description="Active TCP offset [x, y, z, rx, ry, rz] in m and rad, as configured on the pendant")
    active_tcp_offset_confirmed: bool = False
    fk_crosscheck_tol_mm: float = Field(default=10.0, gt=0, le=50)
    fk_crosscheck_tol_deg: float = Field(default=2.0, gt=0, le=10)

    @field_validator("active_tcp_offset_m_rad")
    @classmethod
    def _six_finite(cls, v: list[float] | None) -> list[float] | None:
        if v is None:
            return None
        if len(v) != 6:
            raise ValueError("needs 6 values [x, y, z, rx, ry, rz] (m, rad)")
        for x in v:
            _finite(x)
        if any(abs(x) > 1.0 for x in v[:3]):
            raise ValueError("TCP translation above 1 m: values must be in metres, not millimetres")
        return v


class CameraSetup(_Strict):
    model: str = "Intel RealSense D435"
    serial: str | None = None
    coordinate_frame: Literal["camera_color_optical"] = "camera_color_optical"


class MountEstimate(_Strict):
    reference_frame_as_measured: Literal["unconfirmed_tcp_or_flange", "flange", "tcp"] = "unconfirmed_tcp_or_flange"
    x_longitudinal_mm: float | None = None
    y_mm: float | None = None
    z_mm: float | None = None
    x_sign_confirmed: bool = False
    z_sign_confirmed: bool = False
    rotation_rpy_deg: list[float] | None = None
    status: Literal["rough_estimate_only"] = "rough_estimate_only"
    use_as_calibration: Literal[False] = False

    check_finite = field_validator("x_longitudinal_mm", "y_mm", "z_mm")(classmethod(lambda cls, v: _finite(v)))

    @field_validator("rotation_rpy_deg")
    @classmethod
    def _rpy(cls, v: list[float] | None) -> list[float] | None:
        if v is not None:
            if len(v) != 3:
                raise ValueError("needs 3 values [roll, pitch, yaw] in degrees")
            for x in v:
                _finite(x)
        return v


class BoardSetup(_Strict):
    type: Literal["chessboard", "aruco_4"] = "chessboard"
    square_size_mm: float = Field(default=21.8, gt=0, le=500, description="Side of ONE square, not the whole board")
    inner_corners_columns: int | None = Field(default=None, ge=3, le=50, description="Inner corners, not squares")
    inner_corners_rows: int | None = Field(default=None, ge=3, le=50, description="Inner corners, not squares")
    orientation_reference_confirmed: bool = False

    check_finite = field_validator("square_size_mm")(classmethod(lambda cls, v: _finite(v)))


class CaptureSetup(_Strict):
    recommended_samples: int = Field(default=20, ge=3, le=100)
    min_samples_to_solve: int = Field(default=12, ge=3, le=100)
    robot_must_be_stationary: Literal[True] = True
    allow_automatic_robot_motion: Literal[False] = False


class LocalizationSetup(_Strict):
    frame: Literal["robot_base"] = "robot_base"
    point_semantics: Literal["visible_surface_centroid_3d"] = "visible_surface_centroid_3d"
    send_robot_motion_commands: Literal[False] = False
    min_depth_valid_ratio: float = Field(default=0.5, gt=0, le=1)
    min_depth_points: int = Field(default=30, ge=1)
    edge_margin: float = Field(default=0.005, ge=0, lt=0.2, description="bbox closer than this to the border = cut")


class QualityGates(_Strict):
    """Initial warning thresholds, not guaranteed accuracy. Refine them with real measurements."""
    max_sample_reprojection_px: float = Field(default=1.5, gt=0)
    max_board_spread_mm: float = Field(default=5.0, gt=0)
    max_board_rotation_spread_deg: float = Field(default=1.0, gt=0)
    max_holdout_error_mm: float = Field(default=10.0, gt=0)
    min_max_rotation_deg: float = Field(default=20.0, gt=0, description="largest rotation between two samples")
    min_second_axis_rotation_deg: float = Field(default=5.0, gt=0, description="rotation about a second axis")
    stationary_tol_mm: float = Field(default=1.0, gt=0)
    stationary_tol_deg: float = Field(default=0.5, gt=0)
    stationary_dwell_s: float = Field(default=0.25, ge=0.05, le=5)
    max_pose_age_ms: float = Field(default=250.0, gt=0)


class CalibrationSetup(_Strict):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    robot: RobotSetup = Field(default_factory=RobotSetup)
    camera: CameraSetup = Field(default_factory=CameraSetup)
    camera_mount_estimate: MountEstimate = Field(default_factory=MountEstimate)
    board: BoardSetup = Field(default_factory=BoardSetup)
    capture: CaptureSetup = Field(default_factory=CaptureSetup)
    localization: LocalizationSetup = Field(default_factory=LocalizationSetup)
    quality_gates: QualityGates = Field(default_factory=QualityGates)

    @model_validator(mode="after")
    def _min_le_recommended(self) -> CalibrationSetup:
        if self.capture.min_samples_to_solve > self.capture.recommended_samples:
            raise ValueError("capture.min_samples_to_solve must be <= capture.recommended_samples")
        return self


# --- problems the app shows (the file itself is valid, but a step cannot run yet) --------------------------------

def orientation_ambiguous(cols: int, rows: int) -> bool:
    """A chessboard whose pattern maps onto itself under a 180° rotation: the corner order can flip between images.

    Squares are (cols+1) x (rows+1); opposite corner squares have the same colour when cols + rows is even.
    """
    return (cols + rows) % 2 == 0


def board_problems(cfg: CalibrationSetup) -> list[str]:
    """Why sample capture cannot start with this board (empty = ready)."""
    b = cfg.board
    if b.type == "aruco_4":
        return []
    out = []
    if b.inner_corners_columns is None or b.inner_corners_rows is None:
        out.append("Enter the number of INNER corners (columns x rows) of the chessboard: count the points where four "
                   "squares meet, not the squares.")
        return out
    if b.inner_corners_columns == b.inner_corners_rows:
        out.append("Square inner-corner pattern (N x N) is ambiguous under 90° rotation: use a board with different "
                   "column and row counts, or the ArUco board.")
    return out


def board_warnings(cfg: CalibrationSetup) -> list[str]:
    b = cfg.board
    if b.type != "chessboard" or b.inner_corners_columns is None or b.inner_corners_rows is None:
        return []
    if orientation_ambiguous(b.inner_corners_columns, b.inner_corners_rows) and not b.orientation_reference_confirmed:
        return [f"A {b.inner_corners_columns}x{b.inner_corners_rows} inner-corner board looks the same rotated by "
                "180°. Corner order is canonicalized per image and flipped samples are rejected at solve time; an "
                "odd x even inner-corner board (or ArUco) avoids this entirely."]
    return []


def flange_problems(cfg: CalibrationSetup) -> list[str]:
    """Why a verified T_base_flange cannot be produced with this configuration (empty = ok)."""
    r = cfg.robot
    if r.flange_pose_method == "tcp_minus_verified_offset":
        if r.active_tcp_offset_m_rad is None:
            return ["Enter the active TCP offset exactly as configured on the pendant (Installation > General > TCP), "
                    "in metres and radians. [0,0,0,0,0,0] if the TCP is the flange."]
        if not r.active_tcp_offset_confirmed:
            return ["Confirm that the active TCP offset matches the pendant (it converts the TCP pose to the flange)."]
    return []


def mount_estimate_notes(cfg: CalibrationSetup) -> list[str]:
    m = cfg.camera_mount_estimate
    notes = ["Mount offsets are a rough estimate and are never used as the calibration."]
    if m.reference_frame_as_measured == "unconfirmed_tcp_or_flange":
        notes.append("Measurement reference (TCP or flange) is unconfirmed.")
    if m.reference_frame_as_measured == "tcp":
        notes.append("Measured from the TCP: compared in flange coordinates via T_flange_tcp only.")
    if not (m.x_sign_confirmed and m.z_sign_confirmed):
        notes.append("Axis signs are unconfirmed.")
    if m.y_mm is None:
        notes.append("Y offset unknown.")
    if m.rotation_rpy_deg is None:
        notes.append("Mount rotation unknown (not assumed zero).")
    return notes


def mount_estimate_in_flange_m(cfg: CalibrationSetup) -> list[float] | None:
    """Camera position estimate in flange coordinates (m), only when reference and signs are confirmed.

    Measured from the TCP: p_flange = T_flange_tcp @ p_tcp (needs the confirmed TCP offset). Used only to warn when the
    solved translation is far from the estimate; never as a transform.
    """
    import numpy as np

    from .hand_eye import pose_to_T

    m = cfg.camera_mount_estimate
    if m.reference_frame_as_measured not in ("flange", "tcp") or not (m.x_sign_confirmed and m.z_sign_confirmed):
        return None
    if m.x_longitudinal_mm is None or m.z_mm is None:
        return None
    p = np.array([m.x_longitudinal_mm, m.y_mm or 0.0, m.z_mm, 1000.0]) / 1000.0
    if m.reference_frame_as_measured == "tcp":
        r = cfg.robot
        if r.active_tcp_offset_m_rad is None or not r.active_tcp_offset_confirmed:
            return None
        p = pose_to_T(r.active_tcp_offset_m_rad) @ p
    return [float(v) for v in p[:3]]


# --- hashing ------------------------------------------------------------------------------------------------------

def _canonical(d: Any) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")


def setup_sha256(cfg: CalibrationSetup) -> str:
    return hashlib.sha256(_canonical(cfg.model_dump(mode="json"))).hexdigest()


def identity_sha256(cfg: CalibrationSetup) -> str:
    """Hash of what invalidates an active calibration when edited: robot/camera identity, frames, camera mount.

    Board geometry and quality gates only affect new sessions; the TCP offset only affects how flange poses are read.
    """
    d = cfg.model_dump(mode="json")
    keep = {"robot": {k: d["robot"][k] for k in ("model", "robot_serial", "calibration_frame")},
            "camera": d["camera"], "camera_mount_estimate": d["camera_mount_estimate"]}
    return hashlib.sha256(_canonical(keep)).hexdigest()


# --- files --------------------------------------------------------------------------------------------------------

def atomic_write_json(path: str | Path, data: Any) -> Path:
    """Write JSON via a temporary file + rename: readers never see half a file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return p


def validation_errors(exc: ValidationError) -> list[dict]:
    return [{"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in exc.errors()]


def parse_setup(data: Any) -> tuple[CalibrationSetup | None, list[dict]]:
    try:
        return CalibrationSetup.model_validate(data), []
    except ValidationError as exc:
        return None, validation_errors(exc)


def load_setup(calib_dir: str | Path = CALIB_DIR) -> tuple[CalibrationSetup | None, list[dict], str]:
    """`(setup, errors, source_file)`. Without `calibration_setup.json` the template is used (and reported)."""
    d = Path(calib_dir)
    for name in (SETUP_FILE, EXAMPLE_FILE):
        p = d / name
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                return None, [{"field": name, "message": f"invalid JSON: {exc}"}], name
            cfg, errors = parse_setup(raw)
            return cfg, errors, name
    return CalibrationSetup(), [], "defaults"


def save_setup(cfg: CalibrationSetup, calib_dir: str | Path = CALIB_DIR) -> Path:
    return atomic_write_json(Path(calib_dir) / SETUP_FILE, cfg.model_dump(mode="json"))
