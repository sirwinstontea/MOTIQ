"""Forward / inverse kinematics, Jacobian and static joint torques for UR-type 6-axis arms.

Everything is driven by the DH table from the robot profile (no hard-coded UR5e numbers), so a
UR3e / UR10e profile works with the same code. Conventions follow Universal Robots' DH article:
    T_i = Rz(theta_i) * Tz(d_i) * Tx(a_i) * Rx(alpha_i)

The inverse kinematics is the closed-form (up to 8 solutions) solution for arms with three parallel
joint axes and a spherical-ish wrist offset (all UR arms). It is validated against the forward
kinematics in the test-suite, so a wrong sign anywhere is caught.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

TWO_PI = 2.0 * math.pi
GRAVITY = 9.80665


def rot_x(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)


def rot_z(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)


def make_T(R: np.ndarray, p) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = p
    return T


def dh_transform(theta: float, d: float, a: float, alpha: float) -> np.ndarray:
    ct, st = math.cos(theta), math.sin(theta)
    ca, sa = math.cos(alpha), math.sin(alpha)
    return np.array(
        [
            [ct, -st * ca, st * sa, a * ct],
            [st, ct * ca, -ct * sa, a * st],
            [0.0, sa, ca, d],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


def wrap_to_pi(x: float) -> float:
    return (x + math.pi) % TWO_PI - math.pi


def tool_down_rotation(yaw: float = 0.0) -> np.ndarray:
    """Rotation whose tool z-axis points straight down (world -z) with a free yaw about the vertical."""
    # x axis along (cos yaw, sin yaw, 0); z axis down; y = z x x
    x = np.array([math.cos(yaw), math.sin(yaw), 0.0])
    z = np.array([0.0, 0.0, -1.0])
    y = np.cross(z, x)
    return np.column_stack([x, y, z])


@dataclass
class URKinematics:
    d: list[float]
    a: list[float]
    alpha: list[float]
    joint_min: list[float]  # rad
    joint_max: list[float]  # rad
    link_mass: list[float] = field(default_factory=list)  # kg, link 1..6
    link_com: list[list[float]] = field(default_factory=list)  # m, in DH frame i (i=1..6)

    # ------------------------------------------------------------------ forward kinematics
    def link_transforms(self, q) -> list[np.ndarray]:
        """Cumulative transforms T_0i for i=1..6 (frame i expressed in the base frame)."""
        T = np.eye(4)
        out = []
        for i in range(6):
            T = T @ dh_transform(q[i], self.d[i], self.a[i], self.alpha[i])
            out.append(T.copy())
        return out

    def fk(self, q) -> np.ndarray:
        """Flange pose (frame 6) in the base frame."""
        return self.link_transforms(q)[-1]

    # ------------------------------------------------------------------ inverse kinematics
    def ik(self, T06: np.ndarray) -> list[np.ndarray]:
        """All closed-form solutions (joint angles wrapped to (-pi, pi]) reaching flange pose T06."""
        d1, d4, d5, d6 = self.d[0], self.d[3], self.d[4], self.d[5]
        a2, a3 = self.a[1], self.a[2]
        sols: list[np.ndarray] = []
        p06 = T06[:3, 3]
        z6 = T06[:3, 2]
        p05 = p06 - d6 * z6

        rho = math.hypot(p05[0], p05[1])
        if rho < abs(d4) - 1e-9:
            return sols  # wrist centre inside the shoulder-offset cylinder: unreachable
        psi = math.atan2(p05[1], p05[0])
        phi = math.acos(max(-1.0, min(1.0, d4 / rho))) if rho > 1e-12 else math.pi / 2
        theta1_options = [psi + phi + math.pi / 2, psi - phi + math.pi / 2]

        for th1 in theta1_options:
            s1, c1 = math.sin(th1), math.cos(th1)
            arg5 = (p06[0] * s1 - p06[1] * c1 - d4) / d6
            if abs(arg5) > 1.0 + 1e-9:
                continue
            arg5 = max(-1.0, min(1.0, arg5))
            for sign5 in (1.0, -1.0):
                th5 = sign5 * math.acos(arg5)
                T01 = dh_transform(th1, self.d[0], self.a[0], self.alpha[0])
                T16 = np.linalg.inv(T01) @ T06
                s5 = math.sin(th5)
                if abs(s5) < 1e-9:
                    th6 = 0.0  # wrist singularity: theta6 free, pick 0 (theta4 absorbs the rotation)
                else:
                    th6 = math.atan2(-T16[2, 1] / s5, T16[2, 0] / s5)
                T45 = dh_transform(th5, self.d[4], self.a[4], self.alpha[4])
                T56 = dh_transform(th6, self.d[5], self.a[5], self.alpha[5])
                T14 = T16 @ np.linalg.inv(T56) @ np.linalg.inv(T45)
                p13 = (T14 @ np.array([0.0, -d4, 0.0, 1.0]))[:3]
                n2 = float(p13[0] ** 2 + p13[1] ** 2)
                c3 = (n2 - a2 * a2 - a3 * a3) / (2.0 * a2 * a3)
                if abs(c3) > 1.0 + 1e-9:
                    continue
                c3 = max(-1.0, min(1.0, c3))
                for sign3 in (1.0, -1.0):
                    th3 = sign3 * math.acos(c3)
                    th2 = -math.atan2(p13[1], -p13[0]) + math.asin(
                        max(-1.0, min(1.0, a3 * math.sin(th3) / math.sqrt(max(n2, 1e-18))))
                    )
                    T12 = dh_transform(th2, self.d[1], self.a[1], self.alpha[1])
                    T23 = dh_transform(th3, self.d[2], self.a[2], self.alpha[2])
                    T34 = np.linalg.inv(T12 @ T23) @ T14
                    th4 = math.atan2(T34[1, 0], T34[0, 0])
                    q = np.array([th1, th2, th3, th4, th5, th6])
                    q = np.array([wrap_to_pi(v) for v in q])
                    sols.append(q)
        # keep only solutions that really reproduce the pose (guards against any formula slip)
        good = []
        for q in sols:
            if self.pose_error(self.fk(q), T06) < 1e-6:
                good.append(q)
        return good

    @staticmethod
    def pose_error(Ta: np.ndarray, Tb: np.ndarray) -> float:
        dp = float(np.linalg.norm(Ta[:3, 3] - Tb[:3, 3]))
        dR = float(np.linalg.norm(Ta[:3, :3] - Tb[:3, :3]))
        return dp + dR

    # ------------------------------------------------------------------ joint limits
    def within_limits(self, q, margin: float = 0.0) -> bool:
        return all(self.joint_min[i] + margin <= q[i] <= self.joint_max[i] - margin for i in range(6))

    def limit_variants(self, q: np.ndarray) -> list[np.ndarray]:
        """All 2*pi-shifted equivalents of q that lie inside the joint limits (joints with +-360 deg
        can reach an angle by two different numbers). Returns [] if some joint cannot be satisfied."""
        per_joint: list[list[float]] = []
        for i in range(6):
            opts = []
            for k in (-2, -1, 0, 1, 2):
                v = q[i] + k * TWO_PI
                if self.joint_min[i] - 1e-9 <= v <= self.joint_max[i] + 1e-9:
                    opts.append(v)
            if not opts:
                return []
            per_joint.append(opts)
        # cartesian product is at most 2^6 = 64 combos
        variants = [np.array([])]
        for opts in per_joint:
            variants = [np.append(v, o) for v in variants for o in opts]
        return variants

    # ------------------------------------------------------------------ Jacobian / conditioning
    def jacobian(self, q) -> np.ndarray:
        """6x6 geometric Jacobian of the flange (linear rows 0-2, angular rows 3-5) in the base frame."""
        Ts = self.link_transforms(q)
        pe = Ts[-1][:3, 3]
        J = np.zeros((6, 6))
        z_prev = np.array([0.0, 0.0, 1.0])
        p_prev = np.zeros(3)
        for i in range(6):
            J[:3, i] = np.cross(z_prev, pe - p_prev)
            J[3:, i] = z_prev
            z_prev = Ts[i][:3, 2]
            p_prev = Ts[i][:3, 3]
        return J

    def manipulability_info(self, q) -> dict:
        J = self.jacobian(q)
        sv = np.linalg.svd(J, compute_uv=False)
        sv_min = float(sv[-1])
        sv_max = float(sv[0])
        return {
            "sigma_min": sv_min,
            "sigma_max": sv_max,
            "inv_condition": sv_min / sv_max if sv_max > 0 else 0.0,
            "yoshikawa": float(abs(np.linalg.det(J))),
        }

    # ------------------------------------------------------------------ statics
    def gravity_torques(self, q, payload_mass: float = 0.0, payload_com_flange=(0.0, 0.0, 0.0)) -> np.ndarray:
        """Static joint torques [N m] needed to hold the arm + payload against gravity at pose q.

        Base frame z is assumed to point up (upright mounting). The payload CoM is given in the
        flange frame. Link masses / CoMs come from the profile (UR DH article)."""
        Ts = self.link_transforms(q)
        masses: list[tuple[float, np.ndarray]] = []
        for i in range(6):
            if i < len(self.link_mass) and self.link_mass[i] > 0:
                com = np.array(self.link_com[i] if i < len(self.link_com) else [0, 0, 0], dtype=float)
                masses.append((self.link_mass[i], (Ts[i] @ np.append(com, 1.0))[:3], i))  # type: ignore[arg-type]
        if payload_mass > 0:
            pc = (Ts[-1] @ np.append(np.array(payload_com_flange, dtype=float), 1.0))[:3]
            masses.append((payload_mass, pc, 5))  # type: ignore[arg-type]
        tau = np.zeros(6)
        g = np.array([0.0, 0.0, -GRAVITY])
        axes = [np.array([0.0, 0.0, 1.0])] + [Ts[i][:3, 2] for i in range(5)]
        origins = [np.zeros(3)] + [Ts[i][:3, 3] for i in range(5)]
        for m, pc, last in masses:  # type: ignore[misc]
            F = m * g
            for j in range(last + 1):
                # joint j moves every link with index >= j (link i hangs off joint i)
                tau[j] += float(np.dot(axes[j], np.cross(pc - origins[j], F)))
        return tau

    # ------------------------------------------------------------------ link geometry (for table / self checks)
    def joint_origins(self, q) -> list[np.ndarray]:
        """Positions of base, joint-2 .. flange origins (7 points) in the base frame."""
        Ts = self.link_transforms(q)
        return [np.zeros(3)] + [T[:3, 3].copy() for T in Ts]
