"""Verified, read-only UR5e flange pose `T_base_flange` (MOTIQ_Codex_Calibration_Skill_EN.md, Phase A).

READ ONLY. This module only ever creates `rtde_receive.RTDEReceiveInterface`, which cannot move the robot. It never
imports the control interface, never sends URScript, and never changes the TCP, installation or safety settings.

Why not just `getActualTCPPose()`: that is the *configured TCP*, which is generally not the mechanical flange. The
flange is obtained in one of two ways (`calibration_setup.json` -> `robot.flange_pose_method`):

- `tcp_minus_verified_offset` (default): `T_base_flange = T_base_tcp @ inv(T_flange_tcp)`, with the active TCP
  offset copied from the pendant and confirmed by the operator. It is cross-checked against forward kinematics of the
  actual joints with nominal UR5e DH parameters: a wrong or stale TCP offset (a gripper is 10-20 cm) fails the check,
  while the precise pose still comes from the controller's own calibrated kinematics.
- `joint_fk_nominal`: actual joints + nominal DH only. Absolute error of a few mm (no per-robot kinematic
  calibration); reported with its own source string so it is never confused with the verified method.

Readings carry local monotonic and wall-clock timestamps, a `moving` flag (TCP speed and joint speeds) and a reason
when invalid. Nothing is ever invented: on any error the reading is `valid=False` with a reason.

Clocks: robot readings use `time.monotonic()` for freshness and `time.time()` to compare with camera frames, whose
`ts` is host `time.time()` taken when the grab starts (RealSense hardware timestamps are not used: different clock
domain). Capture brackets the frame between two stationary readings, so the exact frame instant does not matter.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

# Nominal UR5e DH parameters (Universal Robots, "DH parameters for calculations of kinematics and dynamics").
# Standard DH; result is base -> flange (tool0) in the controller's base frame.
UR5E_DH_D = (0.1625, 0.0, 0.0, 0.1333, 0.0997, 0.0996)
UR5E_DH_A = (0.0, -0.425, -0.3922, 0.0, 0.0, 0.0)
UR5E_DH_ALPHA = (math.pi / 2, 0.0, 0.0, math.pi / 2, -math.pi / 2, 0.0)

MOVING_TCP_SPEED_M_S = 0.001     # > 1 mm/s = moving
MOVING_JOINT_SPEED_RAD_S = 0.002  # > ~0.11 deg/s on any joint = moving


def rotvec_to_R(rv: Sequence[float]) -> np.ndarray:
    r = np.asarray(rv, dtype=np.float64)
    th = float(np.linalg.norm(r))
    if th < 1e-12:
        return np.eye(3)
    k = r / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * (K @ K)


def pose_to_T(pose: Sequence[float]) -> np.ndarray:
    """UR pose `[x, y, z, rx, ry, rz]` (m, rotation vector in rad) -> 4x4."""
    T = np.eye(4)
    T[:3, :3] = rotvec_to_R(pose[3:6])
    T[:3, 3] = pose[:3]
    return T


def ur5e_fk_flange(q: Sequence[float]) -> np.ndarray:
    """Forward kinematics base -> flange for joint angles `q` (rad), nominal UR5e DH."""
    if len(q) != 6:
        raise ValueError(f"UR5e has 6 joints, got {len(q)}")
    T = np.eye(4)
    for th, d, a, al in zip(q, UR5E_DH_D, UR5E_DH_A, UR5E_DH_ALPHA):
        ct, st, ca, sa = math.cos(th), math.sin(th), math.cos(al), math.sin(al)
        T = T @ np.array([[ct, -st * ca, st * sa, a * ct],
                          [st, ct * ca, -ct * sa, a * st],
                          [0.0, sa, ca, d],
                          [0.0, 0.0, 0.0, 1.0]])
    return T


def flange_from_tcp(T_base_tcp: np.ndarray, T_flange_tcp: np.ndarray) -> np.ndarray:
    """`T_base_flange = T_base_tcp @ inv(T_flange_tcp)`. Never assume T_base_tcp == T_base_flange."""
    return T_base_tcp @ np.linalg.inv(T_flange_tcp)


def pose_delta(Ta: np.ndarray, Tb: np.ndarray) -> tuple[float, float]:
    """(translation difference in mm, rotation difference in degrees) between two 4x4 poses."""
    dt = float(np.linalg.norm(Ta[:3, 3] - Tb[:3, 3])) * 1000.0
    c = (np.trace(Ta[:3, :3].T @ Tb[:3, :3]) - 1.0) / 2.0
    return dt, math.degrees(math.acos(max(-1.0, min(1.0, c))))


@dataclass(frozen=True)
class FlangeReading:
    T_base_flange: np.ndarray | None   # (4, 4), translation in metres; None when invalid
    captured_monotonic_s: float        # local monotonic clock before the RTDE reads
    received_monotonic_s: float        # ... after the RTDE reads
    wall_s: float                      # time.time() after the reads (to compare with camera frame timestamps)
    robot_serial: str | None
    source: str                        # e.g. "ur_rtde_receive_verified_flange"
    moving: bool
    valid: bool
    reason: str | None = None
    status: str = "ok"                 # typed status for the app when invalid
    simulation: bool = False
    diagnostics: dict = field(default_factory=dict)

    def age_ms(self, now_monotonic: float | None = None) -> float:
        return ((now_monotonic if now_monotonic is not None else time.monotonic()) - self.received_monotonic_s) * 1000.0

    def summary(self) -> dict:
        return {"valid": self.valid, "status": self.status, "reason": self.reason, "source": self.source,
                "moving": self.moving, "simulation": self.simulation, "robot_serial": self.robot_serial,
                "age_ms": round(self.age_ms(), 1),
                "T_base_flange": None if self.T_base_flange is None else np.round(self.T_base_flange, 6).tolist(),
                "diagnostics": self.diagnostics}


def invalid_reading(source: str, status: str, reason: str, *, simulation: bool = False,
                    robot_serial: str | None = None, diagnostics: dict | None = None) -> FlangeReading:
    now_m, now_w = time.monotonic(), time.time()
    return FlangeReading(None, now_m, now_m, now_w, robot_serial, source, False, False, reason, status, simulation,
                         diagnostics or {})


class FlangeProvider(Protocol):
    simulation: bool

    def read(self) -> FlangeReading: ...

    def status(self) -> dict: ...


def stable_pair(a: FlangeReading, b: FlangeReading, tol_mm: float, tol_deg: float,
                max_age_ms: float) -> tuple[bool, str, str | None]:
    """Both readings valid, fresh, not moving and the same pose within tolerance. `(ok, status, reason)`."""
    for r in (a, b):
        if not r.valid or r.T_base_flange is None:
            return False, r.status if r.status != "ok" else "invalid_flange_pose", r.reason or "flange pose unavailable"
        if r.moving:
            return False, "robot_moving", "the robot is moving: wait until it is completely still"
    if b.age_ms() > max_age_ms:  # the closing reading must be fresh when the pair is judged
        return False, "stale_robot_pose", f"robot pose is {b.age_ms():.0f} ms old (limit {max_age_ms:.0f} ms)"
    if (b.received_monotonic_s - b.captured_monotonic_s) * 1000.0 > max_age_ms:
        return False, "stale_robot_pose", f"robot read took longer than {max_age_ms:.0f} ms"
    dt, dr = pose_delta(a.T_base_flange, b.T_base_flange)
    if dt > tol_mm or dr > tol_deg:
        return False, "robot_moving", f"flange moved {dt:.2f} mm / {dr:.2f}° during capture (limit {tol_mm} mm / {tol_deg}°)"
    return True, "ok", None


class RtdeFlangeProvider:
    """Physical UR5e over read-only RTDE (`pip install ur_rtde`). `setup_fn()` returns the current
    `CalibrationSetup` so TCP-offset edits in the app apply without a restart."""

    simulation = False

    def __init__(self, robot_ip: str, setup_fn: Callable[[], Any],
                 receive_factory: Callable[[str], Any] | None = None, retry_s: float = 2.0):
        self.robot_ip = robot_ip
        self.setup_fn = setup_fn
        self.retry_s = retry_s
        self._factory = receive_factory
        self._rtde: Any = None
        self._retry_at = 0.0
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self.last_reading: FlangeReading | None = None

    def _connect(self) -> Any:
        if self._rtde is not None:
            return self._rtde
        if time.monotonic() < self._retry_at:  # bounded reconnect: one attempt every retry_s
            raise ConnectionError(self.last_error or "waiting before reconnecting to the robot")
        try:
            if self._factory is not None:
                self._rtde = self._factory(self.robot_ip)
            else:
                import rtde_receive  # type: ignore  # read-only interface; never the control interface
                self._rtde = rtde_receive.RTDEReceiveInterface(self.robot_ip)
        except Exception as exc:  # noqa: BLE001
            self._retry_at = time.monotonic() + self.retry_s
            self.last_error = f"cannot connect to the UR at {self.robot_ip}: {type(exc).__name__}: {exc}"
            raise ConnectionError(self.last_error) from exc
        return self._rtde

    def read(self) -> FlangeReading:
        with self._lock:
            r = self._read()
            self.last_reading = r
            return r

    def _read(self) -> FlangeReading:
        setup = self.setup_fn()
        robot = setup.robot if setup is not None else None
        serial = robot.robot_serial if robot is not None else None
        method = robot.flange_pose_method if robot is not None else "tcp_minus_verified_offset"
        src = "ur_rtde_receive_verified_flange" if method == "tcp_minus_verified_offset" else "ur_rtde_receive_joint_fk_nominal"
        if robot is None:
            return invalid_reading(src, "invalid_flange_pose", "calibration_setup.json is invalid", robot_serial=serial)
        if robot.pose_mode != "physical_read_only":
            return invalid_reading(src, "invalid_flange_pose", "setup pose_mode is 'simulation' but a physical robot "
                                   "provider is configured", robot_serial=serial)
        try:
            rt = self._connect()
            if hasattr(rt, "isConnected") and not rt.isConnected():
                if hasattr(rt, "reconnect"):
                    rt.reconnect()
                if not rt.isConnected():
                    raise ConnectionError("RTDE connection lost")
            t0 = time.monotonic()
            tcp = [float(v) for v in rt.getActualTCPPose()]
            q = [float(v) for v in rt.getActualQ()]
            speed = [float(v) for v in rt.getActualTCPSpeed()]
            qd = [float(v) for v in rt.getActualQd()]
            t1 = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            self._rtde = None
            self._retry_at = time.monotonic() + self.retry_s
            self.last_error = str(exc) if isinstance(exc, ConnectionError) else f"{type(exc).__name__}: {exc}"
            return invalid_reading(src, "robot_offline", self.last_error, robot_serial=serial)
        self.last_error = None
        vals = tcp + q + speed + qd
        if len(tcp) != 6 or len(q) != 6 or not all(math.isfinite(v) for v in vals):
            return invalid_reading(src, "invalid_flange_pose", "robot returned a non-finite or malformed pose",
                                   robot_serial=serial)
        moving = (float(np.linalg.norm(speed[:3])) > MOVING_TCP_SPEED_M_S
                  or max(abs(v) for v in qd) > MOVING_JOINT_SPEED_RAD_S)
        T_tcp, T_fk = pose_to_T(tcp), ur5e_fk_flange(q)
        diag: dict = {"joints_rad": [round(v, 6) for v in q], "tcp_pose": [round(v, 6) for v in tcp]}
        if method == "joint_fk_nominal":
            T_flange = T_fk
            diag["note"] = "nominal DH: absolute error of a few mm"
        else:
            if robot.active_tcp_offset_m_rad is None or not robot.active_tcp_offset_confirmed:
                return invalid_reading(src, "invalid_flange_pose", "active TCP offset not entered/confirmed: the TCP "
                                       "pose cannot be converted to the flange", robot_serial=serial, diagnostics=diag)
            T_flange = flange_from_tcp(T_tcp, pose_to_T(robot.active_tcp_offset_m_rad))
            dt, dr = pose_delta(T_flange, T_fk)
            diag["fk_crosscheck_mm"], diag["fk_crosscheck_deg"] = round(dt, 2), round(dr, 3)
            if dt > robot.fk_crosscheck_tol_mm or dr > robot.fk_crosscheck_tol_deg:
                return invalid_reading(
                    src, "invalid_flange_pose",
                    f"flange from TCP disagrees with joint kinematics by {dt:.1f} mm / {dr:.2f}°: the TCP offset in "
                    "the setup does not match the active TCP on the pendant", robot_serial=serial, diagnostics=diag)
        return FlangeReading(T_flange, t0, t1, time.time(), serial, src, moving, True, None, "ok", False, diag)

    def status(self) -> dict:
        return {"provider": "rtde_receive", "robot_ip": self.robot_ip, "simulation": False,
                "connected": self._rtde is not None, "last_error": self.last_error}


class MockFlangeProvider:
    """Simulated flange pose for tests and demos. Always reported as SIMULATION; refused in physical mode."""

    simulation = True
    source = "mock_simulation"

    def __init__(self, pose_fn: Callable[[], np.ndarray | None], setup_fn: Callable[[], Any] | None = None,
                 moving_fn: Callable[[], bool] | None = None, robot_serial: str | None = "SIMULATED"):
        self.pose_fn, self.setup_fn, self.moving_fn = pose_fn, setup_fn, moving_fn
        self.robot_serial = robot_serial
        self.last_error: str | None = None

    def read(self) -> FlangeReading:
        setup = self.setup_fn() if self.setup_fn else None
        if setup is not None and setup.robot.pose_mode == "physical_read_only":
            return invalid_reading(self.source, "invalid_flange_pose", "simulation robot provider selected while the "
                                   "setup is in physical mode: refusing simulated poses", simulation=True)
        t0 = time.monotonic()
        T = self.pose_fn()
        t1 = time.monotonic()
        if T is None:
            return invalid_reading(self.source, "robot_offline", "simulated robot has no pose", simulation=True)
        moving = bool(self.moving_fn()) if self.moving_fn else False
        return FlangeReading(np.asarray(T, dtype=np.float64).copy(), t0, t1, time.time(), self.robot_serial,
                             self.source, moving, True, None, "ok", True)

    def status(self) -> dict:
        return {"provider": "mock", "simulation": True, "connected": True, "last_error": None}
