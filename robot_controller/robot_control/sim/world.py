"""PyBullet cell: UR5e kinematics, the four demo parts, and the picture the vision server reads.

Motion uses the same nominal UR5e DH as the rest of the controller. PyBullet draws that arm and
the parts, and checks the path. Nothing here talks to a physical robot.
"""
from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path

import numpy as np

from ..digital_twin.frames import ur5e_fk_flange

_OBJECTS_DIR = Path(__file__).resolve().parents[2] / "objects"
STATE_PATH = Path(__file__).resolve().parents[2] / "runs" / "sim_state.json"

# Bottom-centre in the robot base (m). Same layout as the UR5e demo cell.
OBJECTS = (
    ("Blue_Bucket_01", "blue_bucket_01/model/blue_bucket_01.stl", (0.0, 0.65, 0.9, 1.0), (-0.62, -0.24, 0.0), (0.139, 0.139, 0.115)),
    ("Green_Ring_02", "green_ring_02/model/green_ring_02.stl", (0.1, 0.85, 0.2, 1.0), (-0.62, -0.07, 0.0), (0.139, 0.139, 0.007)),
    ("Orange_Screw_03", "orange_screw_03/model/orange_screw_03.stl", (1.0, 0.38, 0.02, 1.0), (-0.62, 0.08, 0.0), (0.030, 0.030, 0.090)),
    ("White_Bucket_Lid_04", "white_bucket_lid_04/model/white_bucket_lid_04.stl", (0.93, 0.93, 0.93, 1.0), (-0.62, 0.24, 0.0), (0.139, 0.139, 0.053)),
)


def _rotvec_to_R(r: np.ndarray) -> np.ndarray:
    th = float(np.linalg.norm(r))
    if th < 1e-12:
        return np.eye(3)
    k = r / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]], dtype=float)
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * (K @ K)


def _R_to_rotvec(R: np.ndarray) -> np.ndarray:
    c = float(np.clip((np.trace(R) - 1.0) / 2.0, -1.0, 1.0))
    th = math.acos(c)
    if th < 1e-8:
        return np.zeros(3)
    w = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]], dtype=float)
    return w * (th / (2.0 * math.sin(th)))


def pose_to_T(pose) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = _rotvec_to_R(np.asarray(pose[3:6], dtype=float))
    T[:3, 3] = np.asarray(pose[:3], dtype=float)
    return T


def T_to_pose(T: np.ndarray) -> list[float]:
    rv = _R_to_rotvec(T[:3, :3])
    return [float(v) for v in (*T[:3, 3], *rv)]


def _quat_xyzw(R: np.ndarray) -> list[float]:
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


class BulletWorld:
    """One simulated cell. `gui=True` opens the PyBullet window when a display is available."""

    def __init__(self, tcp_offset=(0.0, 0.0, 0.15, 0.0, 0.0, 0.0), joint_min=None, joint_max=None, gui: bool | None = None):
        import pybullet as p

        self._p = p
        self.tcp_offset = tuple(float(v) for v in tcp_offset)
        self.joint_min = np.array(joint_min if joint_min is not None else [-6.2832] * 6, dtype=float)
        self.joint_max = np.array(joint_max if joint_max is not None else [6.2832] * 6, dtype=float)
        self.joint_max[2] = min(float(self.joint_max[2]), math.pi)
        self.joint_min[2] = max(float(self.joint_min[2]), -math.pi)
        self._lock = threading.RLock()
        self.q = np.zeros(6)
        self.moving = False
        self._held: str | None = None
        self._hold_T: np.ndarray | None = None  # object origin in the flange frame
        show = gui if gui is not None else bool(os.environ.get("DISPLAY")) and os.environ.get("PYBULLET_GUI", "1") != "0"
        self.client = p.connect(p.GUI if show else p.DIRECT)
        if show:
            p.resetDebugVisualizerCamera(1.4, 40, -35, [-0.4, 0, 0.2], physicsClientId=self.client)
        p.setGravity(0, 0, 0, physicsClientId=self.client)
        self.bodies: dict[str, int] = {}
        self.origins: dict[str, list[float]] = {}
        self.sizes: dict[str, tuple[float, float, float]] = {}
        self._arm_ids: list[int] = []
        self._build()
        self.set_joints([0.0, -math.pi / 2, math.pi / 2, -math.pi / 2, -math.pi / 2, 0.0])

    def close(self) -> None:
        with self._lock:
            try:
                self._p.disconnect(physicsClientId=self.client)
            except Exception:
                pass

    def _build(self) -> None:
        p, cid = self._p, self.client
        table = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.6, 0.5, 0.01], physicsClientId=cid)
        vis = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.6, 0.5, 0.01], rgbaColor=[0.55, 0.42, 0.28, 1], physicsClientId=cid)
        p.createMultiBody(baseMass=0, baseCollisionShapeIndex=table, baseVisualShapeIndex=vis,
                          basePosition=[-0.55, 0.0, -0.01], physicsClientId=cid)
        for name, rel, rgba, origin, size in OBJECTS:
            stl = _OBJECTS_DIR / rel
            if stl.is_file():
                c = p.createCollisionShape(p.GEOM_MESH, fileName=str(stl), meshScale=[0.001, 0.001, 0.001], physicsClientId=cid)
                v = p.createVisualShape(p.GEOM_MESH, fileName=str(stl), meshScale=[0.001, 0.001, 0.001],
                                         rgbaColor=list(rgba), physicsClientId=cid)
                pos = origin
            else:
                r, h = max(size[0], size[1]) / 2, size[2]
                c = p.createCollisionShape(p.GEOM_CYLINDER, radius=r, height=h, physicsClientId=cid)
                v = p.createVisualShape(p.GEOM_CYLINDER, radius=r, length=h, rgbaColor=list(rgba), physicsClientId=cid)
                pos = (origin[0], origin[1], origin[2] + h / 2)
            self.bodies[name] = p.createMultiBody(baseMass=0, baseCollisionShapeIndex=c, baseVisualShapeIndex=v,
                                                   basePosition=list(pos), physicsClientId=cid)
            self.origins[name] = [float(v) for v in pos]
            self.sizes[name] = size
        for _ in range(8):
            col = p.createCollisionShape(p.GEOM_SPHERE, radius=0.045, physicsClientId=cid)
            vis = p.createVisualShape(p.GEOM_SPHERE, radius=0.04, rgbaColor=[0.75, 0.75, 0.78, 1], physicsClientId=cid)
            self._arm_ids.append(p.createMultiBody(baseMass=0, baseCollisionShapeIndex=col, baseVisualShapeIndex=vis,
                                                    basePosition=[0, 0, 0], physicsClientId=cid))

    def flange_T(self, q=None) -> np.ndarray:
        return ur5e_fk_flange(self.q if q is None else q)

    def tcp_T(self, q=None) -> np.ndarray:
        return self.flange_T(q) @ pose_to_T(self.tcp_offset)

    def tcp_pose(self) -> list[float]:
        with self._lock:
            return T_to_pose(self.tcp_T())

    def joints(self) -> list[float]:
        with self._lock:
            return [float(v) for v in self.q]

    def set_joints(self, q) -> None:
        with self._lock:
            self.q = np.array([float(v) for v in q], dtype=float)
            self._draw()
            self._publish()

    def _frames(self, q) -> list[np.ndarray]:
        """Partial flange frames, base plus one per joint, so the arm can be drawn."""
        from ..digital_twin.frames import UR5E_DH_A, UR5E_DH_ALPHA, UR5E_DH_D
        frames = [np.eye(4)]
        T = np.eye(4)
        for th, d, a, al in zip(q, UR5E_DH_D, UR5E_DH_A, UR5E_DH_ALPHA):
            ct, st, ca, sa = math.cos(th), math.sin(th), math.cos(al), math.sin(al)
            T = T @ np.array([[ct, -st * ca, st * sa, a * ct],
                              [st, ct * ca, -ct * sa, a * st],
                              [0.0, sa, ca, d],
                              [0.0, 0.0, 0.0, 1.0]])
            frames.append(T.copy())
        return frames

    def _draw(self) -> None:
        frames = self._frames(self.q)
        pts = [T[:3, 3] for T in frames]
        # Extra point at the TCP so the stick reaches the tool.
        pts.append(self.tcp_T()[:3, 3])
        p, cid = self._p, self.client
        for i, body in enumerate(self._arm_ids):
            a, b = pts[min(i, len(pts) - 1)], pts[min(i + 1, len(pts) - 1)]
            mid = (a + b) / 2
            p.resetBasePositionAndOrientation(body, mid.tolist(), [0, 0, 0, 1], physicsClientId=cid)
        if self._held and self._hold_T is not None:
            T = self.flange_T() @ self._hold_T
            p.resetBasePositionAndOrientation(self.bodies[self._held], T[:3, 3].tolist(), _quat_xyzw(T[:3, :3]),
                                              physicsClientId=cid)

    def object_pose(self, name: str) -> np.ndarray | None:
        body = self.bodies.get(name)
        if body is None:
            return None
        pos, orn = self._p.getBasePositionAndOrientation(body, physicsClientId=self.client)
        R = np.array(self._p.getMatrixFromQuaternion(orn), dtype=float).reshape(3, 3)
        T = np.eye(4)
        T[:3, :3] = R
        T[:3, 3] = pos
        return T

    def object_bottom(self, name: str) -> np.ndarray | None:
        T = self.object_pose(name)
        return None if T is None else T[:3, 3]

    def grab(self, name: str) -> bool:
        T_obj = self.object_pose(name)
        if T_obj is None:
            return False
        with self._lock:
            self._held = name
            self._hold_T = np.linalg.inv(self.flange_T()) @ T_obj
            self._publish()
        return True

    def release(self) -> None:
        """Let go. The cell has no gravity, so the part is set down on the table instead of hanging in the air."""
        with self._lock:
            name = self._held
            if name and name in self.bodies and self._hold_T is not None:
                T = self.flange_T() @ self._hold_T
                T = T.copy()
                T[2, 3] = 0.0
                self._p.resetBasePositionAndOrientation(
                    self.bodies[name], T[:3, 3].tolist(), _quat_xyzw(T[:3, :3]), physicsClientId=self.client)
            self._held = None
            self._hold_T = None
            self._publish()

    def restore(self) -> None:
        """Gripper empty, every part back on the spot it had when the cell started."""
        with self._lock:
            self._held = None
            self._hold_T = None
            for name, pos in self.origins.items():
                body = self.bodies.get(name)
                if body is None:
                    continue
                self._p.resetBasePositionAndOrientation(body, pos, [0, 0, 0, 1], physicsClientId=self.client)
            self._publish()

    def ik(self, tcp_pose, seed) -> np.ndarray | None:
        """Joints whose TCP matches `tcp_pose` (m, rotation vector), starting from `seed`."""
        goal = pose_to_T(tcp_pose)
        q = np.array(seed, dtype=float)
        for _ in range(50):
            T = self.tcp_T(q)
            dp = goal[:3, 3] - T[:3, 3]
            rv = _R_to_rotvec(T[:3, :3].T @ goal[:3, :3])
            if float(np.linalg.norm(dp)) < 1e-3 and float(np.linalg.norm(rv)) < 1e-2:
                return q
            J = np.zeros((6, 6))
            eps = 1e-4
            for i in range(6):
                dq = q.copy()
                dq[i] += eps
                Ti = self.tcp_T(dq)
                J[:3, i] = (Ti[:3, 3] - T[:3, 3]) / eps
                J[3:, i] = _R_to_rotvec(T[:3, :3].T @ Ti[:3, :3]) / eps
            err = np.concatenate([dp, rv])
            step = J.T @ np.linalg.solve(J @ J.T + 1e-3 * np.eye(6), err)
            q = np.clip(q + np.clip(step, -0.4, 0.4), self.joint_min, self.joint_max)
        T = self.tcp_T(q)
        if float(np.linalg.norm(goal[:3, 3] - T[:3, 3])) < 5e-3:
            return q
        return None

    def hits(self, q, ignore: set[str]) -> list[str]:
        """Names of demo parts the arm spheres touch at joints `q`."""
        frames = self._frames(q)
        pts = [T[:3, 3] for T in frames] + [self.tcp_T(q)[:3, 3]]
        hit = []
        for name, size in self.sizes.items():
            if name in ignore:
                continue
            origin = self.object_bottom(name)
            if origin is None:
                continue
            # Same margin on every side. Z used to take the full height as the half-extent, so the
            # box was twice as tall as the part and a drop into the bucket counted as a collision.
            half = np.array([size[0] / 2 + 0.03, size[1] / 2 + 0.03, size[2] / 2 + 0.02])
            centre = origin + np.array([0, 0, size[2] / 2])
            for pt in pts:
                d = np.abs(pt - centre)
                if np.all(d <= half):
                    hit.append(name)
                    break
        return hit

    def _publish(self) -> None:
        objects = {}
        for name in self.bodies:
            T = self.object_pose(name)
            if T is None:
                continue
            objects[name] = {"xyz": [round(float(v), 5) for v in T[:3, 3]],
                             "quat": [round(float(v), 5) for v in _quat_xyzw(T[:3, :3])]}
        payload = {"flange": [round(float(v), 5) for v in T_to_pose(self.flange_T())],
                   "moving": bool(self.moving), "held": self._held, "objects": objects}
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(STATE_PATH)
        except OSError:
            pass
