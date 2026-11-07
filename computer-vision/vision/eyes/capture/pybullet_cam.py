"""Flange camera rendered in PyBullet. Color and depth, with the arm pose taken from the controller.

The four demo meshes sit on the table where the UR5e cell puts them. The camera is bolted to the
flange. Each frame copies the flange and the part poses from the controller's sim_state.json.
Until that file exists the camera looks down from the scan pose.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np

from ..calibration.hand_eye import T_to_pose, TcpReading, pose_to_T
from .reader import make_disconnected_frame

D435_COLOR_VFOV_DEG = 42.0
_SIM_STATE = Path(__file__).resolve().parents[4] / "robot_controller" / "runs" / "sim_state.json"

_OBJECTS_DIR = Path(__file__).resolve().parents[4] / "robot_controller" / "objects"
# Bottom-centre of each mesh in the robot base (m). Objects_Frame is (-0.62, 0) with rz=90,
# and each display pose is a translation along that frame's X, so the row runs along base Y.
_OBJECTS = (
    ("Blue_Bucket_01", "blue_bucket_01/model/blue_bucket_01.stl", (0.0, 0.65, 0.9, 1.0), (-0.62, -0.24, 0.0), 0.115, 0.070),
    ("Green_Ring_02", "green_ring_02/model/green_ring_02.stl", (0.1, 0.85, 0.2, 1.0), (-0.62, -0.07, 0.0), 0.007, 0.070),
    ("Orange_Screw_03", "orange_screw_03/model/orange_screw_03.stl", (1.0, 0.38, 0.02, 1.0), (-0.62, 0.08, 0.0), 0.090, 0.015),
    ("White_Bucket_Lid_04", "white_bucket_lid_04/model/white_bucket_lid_04.stl", (0.93, 0.93, 0.93, 1.0), (-0.62, 0.24, 0.0), 0.053, 0.070),
)
# Tool-down look over the middle of the table, used until the controller publishes a flange.
_LOOK_DOWN = (-0.55, 0.0, 0.40, math.pi, 0.0, 0.0)


def _quat_xyzw(R: np.ndarray) -> list[float]:
    """Rotation matrix -> quaternion xyzw (PyBullet's order)."""
    m = np.asarray(R, dtype=float)
    t = float(np.trace(m))
    if t > 0.0:
        s = math.sqrt(t + 1.0) * 2.0
        w, x, y, z = 0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        w, x, y, z = (m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        w, x, y, z = (m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        w, x, y, z = (m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s
    return [float(x), float(y), float(z), float(w)]


def intrinsics_from_fov(width: int, height: int, vfov_deg: float):
    from .realsense import Intrinsics
    f = (height / 2.0) / math.tan(math.radians(vfov_deg) / 2.0)
    return Intrinsics(fx=f, fy=f, cx=width / 2.0, cy=height / 2.0, width=width, height=height)


class PyBulletFlange:
    """Flange pose that belongs to the picture just rendered. Read-only."""

    def __init__(self, reader: "PyBulletCameraReader"):
        self.reader = reader
        self.last_error: str | None = None

    def __call__(self) -> TcpReading | None:
        T = self.reader.flange_in_base()
        if T is None:
            self.last_error = "no flange pose yet"
            return None
        self.last_error = None
        return TcpReading(pose=tuple(T_to_pose(T)), t=time.time(), moving=self.reader.flange_moving)


class PyBulletCameraReader:
    """Color + depth from a PyBullet camera mounted on the flange. Same interface as `RealSenseReader`."""

    mode = "pybullet"

    def __init__(self, source_name: str = "arm", robot_name: str | None = None,
                 mount: Sequence[float] = (0.0, -0.07, 0.03, 0.0, 0.0, 0.0),
                 width: int = 640, height: int = 480, vfov_deg: float = D435_COLOR_VFOV_DEG,
                 near_m: float = 0.05, far_m: float = 2.0, fps: float = 5.0):
        import pybullet as p  # local: the package is only needed in this mode

        self._p = p
        self.source_name = source_name
        self.robot_name = robot_name
        self.T_F_C = pose_to_T(mount)
        self.width, self.height, self.fps = int(width), int(height), float(fps)
        self.near_m, self.far_m = float(near_m), float(far_m)
        self.intrinsics = intrinsics_from_fov(self.width, self.height, vfov_deg)
        self.device_name = "PyBullet D435"
        self.cam_depth: object = object()  # depth is rendered; the pipeline must not invent a table ray
        self._default_flange = pose_to_T(_LOOK_DOWN)
        self._flange = self._default_flange.copy()
        self.flange_moving = False

        self.client = p.connect(p.DIRECT)
        p.setGravity(0, 0, 0, physicsClientId=self.client)
        self._bodies = self._build_world()

        self._lock = threading.Lock()
        self._color = make_disconnected_frame(self.width, self.height, source_name, "PyBullet: in attesa")
        self._depth: np.ndarray | None = None
        self._frame_id, self._ts, self._connected = 0, time.time(), False
        self._running, self._thread = False, None
        self.last_error: str | None = None
        print("[PyBullet] camera sulla flangia: colore e profondità")

    def _build_world(self) -> dict[str, int]:
        p = self._p
        cid = self.client
        table = p.createVisualShape(
            p.GEOM_BOX, halfExtents=[0.60, 0.50, 0.01], rgbaColor=[0.55, 0.42, 0.28, 1.0], physicsClientId=cid)
        p.createMultiBody(baseMass=0, baseVisualShapeIndex=table, basePosition=[-0.55, 0.0, -0.01], physicsClientId=cid)
        bodies: dict[str, int] = {}
        for name, rel, rgba, origin, height, radius in _OBJECTS:
            stl = _OBJECTS_DIR / rel
            if stl.is_file():
                vis = p.createVisualShape(
                    p.GEOM_MESH, fileName=str(stl), meshScale=[0.001, 0.001, 0.001],
                    rgbaColor=list(rgba), physicsClientId=cid)
                pos = origin
            else:
                vis = p.createVisualShape(p.GEOM_CYLINDER, radius=radius, length=height, rgbaColor=list(rgba),
                                           physicsClientId=cid)
                pos = (origin[0], origin[1], origin[2] + height / 2.0)
            bodies[name] = p.createMultiBody(
                baseMass=0, baseVisualShapeIndex=vis, basePosition=list(pos), physicsClientId=cid)
        return bodies

    def flange_in_base(self) -> np.ndarray | None:
        return None if self._flange is None else self._flange.copy()

    def camera_in_base(self) -> np.ndarray:
        return self._flange @ self.T_F_C

    def _follow_arm(self) -> None:
        """Copy the controller's flange and part poses. Missing file: keep the scan pose."""
        try:
            state = json.loads(_SIM_STATE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        flange = state.get("flange")
        if isinstance(flange, list) and len(flange) == 6:
            self._flange = pose_to_T(flange)
        self.flange_moving = bool(state.get("moving"))
        for name, body in self._bodies.items():
            obj = (state.get("objects") or {}).get(name) or {}
            xyz, quat = obj.get("xyz"), obj.get("quat")
            if not xyz or not quat:
                continue
            self._p.resetBasePositionAndOrientation(body, list(xyz), list(quat), physicsClientId=self.client)

    def _grab(self) -> tuple[np.ndarray, np.ndarray] | None:
        p = self._p
        self._follow_arm()
        T = self.camera_in_base()
        eye = T[:3, 3]
        forward = T[:3, 2]
        up = -T[:3, 1]  # camera Y points down; PyBullet's up vector points up
        view = p.computeViewMatrix(eye.tolist(), (eye + forward).tolist(), up.tolist(), physicsClientId=self.client)
        proj = p.computeProjectionMatrixFOV(
            fov=D435_COLOR_VFOV_DEG, aspect=self.width / self.height,
            nearVal=self.near_m, farVal=self.far_m, physicsClientId=self.client)
        _, _, rgba, depth_buf, _ = p.getCameraImage(
            self.width, self.height, view, proj,
            renderer=p.ER_TINY_RENDERER, physicsClientId=self.client)
        rgba_u8 = np.reshape(np.asarray(rgba, dtype=np.uint8), (self.height, self.width, 4))
        color = cv2.cvtColor(rgba_u8, cv2.COLOR_RGBA2BGR)
        buf = np.reshape(depth_buf, (self.height, self.width)).astype(np.float64)
        # OpenGL z-buffer -> Z along the camera axis, in mm. 1.0 is the far plane (no hit).
        z = self.far_m * self.near_m / (self.far_m - (self.far_m - self.near_m) * buf)
        z[buf >= 0.999] = 0.0
        depth = np.clip(np.rint(z * 1000.0), 0, 65535).astype(np.uint16)
        return color, depth

    def _read_into_latest(self) -> bool:
        ts = time.time()
        try:
            got = self._grab()
        except Exception as exc:  # noqa: BLE001 - keep the last picture and try again
            self.last_error, got = f"{type(exc).__name__}: {exc}", None
        if got is None:
            self._connected = False
            return False
        with self._lock:
            self._color, self._depth = got
            self._frame_id += 1
            self._ts, self._connected = ts, True
        return True

    def read_once(self) -> tuple[bool, np.ndarray, int, float]:
        if not self._running:
            self._read_into_latest()
        return self.get_latest()

    def get_latest(self) -> tuple[bool, np.ndarray, int, float]:
        with self._lock:
            return self._connected, self._color.copy(), self._frame_id, self._ts

    def get_depth(self) -> np.ndarray | None:
        with self._lock:
            return None if self._depth is None else self._depth.copy()

    def get_frames(self) -> tuple[bool, np.ndarray, np.ndarray | None, int, float]:
        with self._lock:
            depth = None if self._depth is None else self._depth.copy()
            return self._connected, self._color.copy(), depth, self._frame_id, self._ts

    @property
    def is_connected(self) -> bool:
        return self._connected

    def start_background(self) -> None:
        if self._running:
            return
        self._read_into_latest()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name=f"PyBullet-{self.source_name}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            t0 = time.perf_counter()
            self._read_into_latest()
            time.sleep(max(0.0, 1.0 / self.fps - (time.perf_counter() - t0)))

    def stop(self) -> None:
        self._running = False
        if self._thread is not None and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None
        try:
            self._p.disconnect(physicsClientId=self.client)
        except Exception:  # noqa: BLE001
            pass
