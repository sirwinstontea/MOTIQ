"""Model-specific limit checks. All inputs in m / rad."""
from __future__ import annotations

import math

from ..config import RobotConfig, Workspace
from ..models import Pose


def joints_within_limits(joints: list[float], robot: RobotConfig) -> list[str]:
    lim = robot.limits
    if len(joints) != robot.dof:
        return [f"joint array has {len(joints)} values, model {robot.model} has dof={robot.dof}"]
    out = []
    for i, (q, lo, hi) in enumerate(zip(joints, lim.joint_min_rad, lim.joint_max_rad)):
        if not math.isfinite(q):
            out.append(f"joint {i} is not finite")
        elif q < lo or q > hi:
            out.append(f"joint {i} = {q:.3f} rad outside [{lo:.3f}, {hi:.3f}] rad ({robot.model})")
    return out


def pose_within_workspace(pose: Pose, ws: Workspace, robot: RobotConfig) -> list[str]:
    out = []
    vals = pose.as_list()
    if not all(math.isfinite(v) for v in vals):
        return ["pose contains non-finite values"]
    if pose.frame_id != ws.frame_id:
        return [f"pose frame '{pose.frame_id}' does not match workspace frame '{ws.frame_id}'"]
    if ws.mode == "kinematic":
        # No table-footprint box or nominal reach sphere: actual model IK and swept collision tests decide reach.
        if pose.z < ws.z_m[0]:
            return [f"TCP z = {pose.z:.3f} m below configured table clearance {ws.z_m[0]:.3f} m"]
        return []
    for ax, v in (("x_m", pose.x), ("y_m", pose.y), ("z_m", pose.z)):
        lo, hi = getattr(ws, ax)
        if v < lo or v > hi:
            out.append(f"TCP {ax[0]} = {v:.3f} m outside workspace [{lo:.3f}, {hi:.3f}] m")
    r = math.sqrt(pose.x ** 2 + pose.y ** 2 + pose.z ** 2)
    if r > robot.limits.reach_m:
        out.append(f"TCP distance {r:.3f} m exceeds {robot.model} reach {robot.limits.reach_m} m")
    return out
