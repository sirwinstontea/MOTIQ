"""Frames and units for the digital twin, in one place.

Conventions: `T_A_B` (4x4) maps points expressed in frame B into frame A. Inside this module every translation is in
METERS and every joint angle in RADIANS unless the name says otherwise (`_deg`, `_mm`). RoboDK's millimeters and
degrees appear only at the two conversion helpers `joints_rad_to_robodk_deg` and `T_m_to_mm` / `T_mm_to_m`.

Frames (spec section 4):
    B  physical UR5e base               F  physical UR5e flange (never the active TCP)
    R  RoboDK virtual UR5e base         S  RoboDK station root
    P  RoboDK parent of a marker/object O  CAD model origin
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

# Nominal UR5e DH (Universal Robots "DH parameters for calculations of kinematics and dynamics"), base -> flange.
# Same values as computer-vision/vision/eyes/capture/ur_flange.py (separate venv, so not imported from there).
UR5E_DH_D = (0.1625, 0.0, 0.0, 0.1333, 0.0997, 0.0996)
UR5E_DH_A = (0.0, -0.425, -0.3922, 0.0, 0.0, 0.0)
UR5E_DH_ALPHA = (math.pi / 2, 0.0, 0.0, math.pi / 2, -math.pi / 2, 0.0)


def joints_rad_to_robodk_deg(q_rad: Sequence[float]) -> list[float]:
    """UR actual joints (rad, base..wrist3 order) -> RoboDK joint values (deg, same order). The ONLY place it happens."""
    q = [float(v) for v in q_rad]
    if len(q) != 6:
        raise ValueError(f"UR5e has 6 joints, got {len(q)}")
    if not all(math.isfinite(v) for v in q):
        raise ValueError(f"non-finite joint value in {q}")
    return [math.degrees(v) for v in q]


def ur5e_fk_flange(q_rad: Sequence[float]) -> np.ndarray:
    """T_B_F (m) for joint angles q (rad), nominal UR5e DH. Used only for like-for-like flange comparisons."""
    if len(q_rad) != 6:
        raise ValueError(f"UR5e has 6 joints, got {len(q_rad)}")
    T = np.eye(4)
    for th, d, a, al in zip(q_rad, UR5E_DH_D, UR5E_DH_A, UR5E_DH_ALPHA):
        ct, st, ca, sa = math.cos(th), math.sin(th), math.cos(al), math.sin(al)
        T = T @ np.array([[ct, -st * ca, st * sa, a * ct],
                          [st, ct * ca, -ct * sa, a * st],
                          [0.0, sa, ca, d],
                          [0.0, 0.0, 0.0, 1.0]])
    return T


def rigid_problems(T: np.ndarray, name: str = "T") -> list[str]:
    """Why `T` is not a proper rigid transform (empty list = fine)."""
    T = np.asarray(T, dtype=float)
    if T.shape != (4, 4):
        return [f"{name} must be 4x4, got {T.shape}"]
    if not np.all(np.isfinite(T)):
        return [f"{name} has non-finite values"]
    out = []
    if not np.allclose(T[3], [0, 0, 0, 1], atol=1e-9):
        out.append(f"{name} last row must be [0, 0, 0, 1]")
    R = T[:3, :3]
    if not np.allclose(R.T @ R, np.eye(3), atol=1e-4):
        out.append(f"{name} rotation is not orthonormal")
    elif np.linalg.det(R) < 0:
        out.append(f"{name} rotation is a reflection (det < 0)")
    return out


def rigid_inverse(T: np.ndarray) -> np.ndarray:
    R, t = T[:3, :3], T[:3, 3]
    out = np.eye(4)
    out[:3, :3] = R.T
    out[:3, 3] = -R.T @ t
    return out


def pose_delta(T_a: np.ndarray, T_b: np.ndarray) -> tuple[float, float]:
    """(translation mm, rotation deg) between two poses expressed in the same frame, same units (m)."""
    d = rigid_inverse(T_a) @ T_b
    ang = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(d[:3, :3]) - 1) / 2))))
    return float(np.linalg.norm(d[:3, 3]) * 1000.0), ang


def point_B_to_R(T_R_B: np.ndarray, p_B_m: Sequence[float]) -> np.ndarray:
    """^R p = ^R T_B · ^B p (m)."""
    p = np.asarray([*p_B_m, 1.0], dtype=float)
    if p.shape != (4,) or not np.all(np.isfinite(p)):
        raise ValueError(f"bad point {p_B_m!r}")
    return (np.asarray(T_R_B, float) @ p)[:3]


def marker_T_R(p_R_m: Sequence[float]) -> np.ndarray:
    """DISPLAY-ONLY marker pose at a visible point: identity orientation in R. Not the object's real orientation."""
    T = np.eye(4)
    T[:3, 3] = np.asarray(p_R_m, dtype=float)
    return T


def pose_in_parent(T_S_P: np.ndarray, T_S_R: np.ndarray, T_R_X: np.ndarray) -> np.ndarray:
    """^P T_X = inv(^S T_P) · ^S T_R · ^R T_X. What `item.setPose()` needs when the item's parent is P (all m)."""
    return rigid_inverse(np.asarray(T_S_P, float)) @ np.asarray(T_S_R, float) @ np.asarray(T_R_X, float)


def tabletop_contact_B(p_B_visible_m: Sequence[float], table_z_B_m: float) -> np.ndarray:
    """APPROXIMATE CAD origin (centre of the lowest flat face) of an upright object standing on a horizontal table,
    in the physical base B: x, y of the visible centroid, z = the measured table height, NOT the visible-surface
    height. Valid only when the object really stands on the table and is seen roughly from above."""
    return np.array([p_B_visible_m[0], p_B_visible_m[1], table_z_B_m], dtype=float)


def replace_translation(T_S_O_current: np.ndarray, p_S_m: Sequence[float]) -> np.ndarray:
    """Same orientation as the current CAD pose (underconstrained: kept, not guessed), new origin."""
    out = np.array(T_S_O_current, dtype=float)
    out[:3, 3] = np.asarray(p_S_m, dtype=float)
    return out


def T_m_to_mm(T: np.ndarray) -> list[list[float]]:
    M = np.array(T, dtype=float)
    M[:3, 3] *= 1000.0
    return M.tolist()


def T_mm_to_m(M) -> np.ndarray:
    T = np.array([[float(M[i, j]) for j in range(4)] for i in range(4)]) if not isinstance(M, (list, np.ndarray)) \
        else np.array(M, dtype=float)
    T[:3, 3] /= 1000.0
    return T


def rotz(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    T = np.eye(4)
    T[:2, :2] = [[c, -s], [s, c]]
    return T


def transl(x: float, y: float, z: float) -> np.ndarray:
    T = np.eye(4)
    T[:3, 3] = (x, y, z)
    return T
