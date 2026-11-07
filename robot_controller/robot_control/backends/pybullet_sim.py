"""PyBullet backend. Simulation only: the arm and the four demo parts live in PyBullet.

Named targets come from the scene file. Inverse kinematics uses the UR5e DH model.
The gripper is symbolic: closing parents the part to the flange. Feedback is always simulated.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Optional

from ..config import RobotConfig, SceneConfig
from ..controller import (Capabilities, GripStatus, MotionContext, MotionType, PathCheckResult,
                          PlannedSegment, RobotController, RobotState)
from ..models import ErrorCode, FeedbackSource, Joints, Pose, RobotControlError, utc_now
from ..sim.world import BulletWorld, T_to_pose

JOINT_TOL_RAD = math.radians(0.5)


def pose_at_camera_xy(taught_pose, camera_xyz):
    """TCP pose over the point the camera measured.

    The camera decides the table position. The height and the tool orientation stay the ones taught
    for this move (hover, grasp, lift, drop). The depth value is not the tool height: it is often
    the tabletop, which is below the workspace floor.
    """
    return [float(camera_xyz[0]), float(camera_xyz[1]), float(taught_pose[2]), *taught_pose[3:]]


class _Joints:
    def __init__(self, deg: list[float]):
        self._deg = list(deg)

    def list(self) -> list[float]:
        return list(self._deg)


class _ArmView:
    """The degree-valued joint handle URSim sync uses while it mirrors a move."""

    def __init__(self, world: BulletWorld):
        self.world = world

    def setJoints(self, deg: list[float]) -> None:
        self.world.set_joints([math.radians(v) for v in deg])

    def Joints(self) -> _Joints:
        return _Joints([math.degrees(v) for v in self.world.joints()])


class PyBulletSimController(RobotController):
    backend_name = "pybullet"

    def __init__(self, robot: RobotConfig, scene: SceneConfig, **_ignored):
        self.robot_cfg = robot
        self.scene = scene
        self.world: BulletWorld | None = None
        self.robot: _ArmView | None = None
        self.connected = False
        self._stop = threading.Event()
        self._at_target: Optional[str] = None
        self._holding: Optional[str] = None
        self._gripper = "open"
        self._grip_confirmed = False
        self._joint_speed = 0.3
        self._targets = {name: list(t.pose_m_rad) for name, t in scene.targets.items() if t.pose_m_rad}
        self._retargeted: set[str] = set()

    def connect(self):
        self.world = BulletWorld(
            tcp_offset=self.robot_cfg.tcp_offset_m_rad,
            joint_min=self.robot_cfg.limits.joint_min_rad,
            joint_max=self.robot_cfg.limits.joint_max_rad,
        )
        home = self.scene.targets.get(self.scene.home_target)
        if home is not None and home.joints_rad:
            self.world.set_joints(home.joints_rad)
        self.robot = _ArmView(self.world)
        self.connected = True

    def disconnect(self):
        self.connected = False
        if self.world is not None:
            self.world.close()
            self.world = None

    def _require(self):
        if not self.connected or self.world is None:
            raise RobotControlError(ErrorCode.BACKEND_NOT_CONNECTED, "PyBullet world is not connected")

    def _enforce_simulate(self):
        """Kept so the URSim adapter can call it. This backend never leaves simulation."""
        self._require()

    def capabilities(self):
        return Capabilities(backend=self.backend_name, robot_model=self.robot_cfg.model, dof=self.robot_cfg.dof,
                            simulation=True, motion_enabled=self.connected, gripper="symbolic",
                            path_check="collision", feedback_source=FeedbackSource.SIMULATED,
                            notes=["PyBullet cell: UR5e kinematics, demo parts, symbolic gripper"])

    def get_state(self):
        self._require()
        pose = Pose.from_list(self.world.tcp_pose())
        return RobotState(backend=self.backend_name, robot_model=self.robot_cfg.model, connected=True,
                          mode="simulation", joints=Joints(values=self.world.joints()), tcp_pose=pose,
                          gripper_state=self._gripper, holding_object_id=self._holding,
                          grip_confirmed=self._grip_confirmed, fault=None, busy=self.world.moving,
                          timestamp_utc=utc_now(), sampled_monotonic=time.monotonic(),
                          feedback_source=FeedbackSource.SIMULATED)

    def check_ready(self):
        return [] if self.connected else ["PyBullet world is not connected"]

    def apply_motion_limits(self, tcp_speed_m_s, tcp_accel_m_s2, joint_speed_rad_s, joint_accel_rad_s2):
        self._joint_speed = max(0.05, float(joint_speed_rad_s))

    def target_exists(self, target_id):
        self._require()
        t = self.scene.targets.get(target_id)
        return t is not None and (t.joints_rad is not None or target_id in self._targets)

    def _ignored_part(self, target_id: str) -> set[str]:
        """Parts the arm is allowed to touch on this move.

        The part being picked is ignored on its own approach, grasp and lift. A part parented to the
        flange is ignored on every later move: the gripper is inside it, so it is not an obstacle.
        """
        ignored: set[str] = set()
        held = self.world._held if self.world is not None else None
        if held:
            ignored.add(held)
        for obj in self.scene.objects.values():
            names = [obj.approach_target, obj.grasp_target, obj.lift_target]
            push = getattr(obj, "push", None)
            if push is not None:
                names += [push.above_target, push.start_target, push.end_target, push.lift_target]
            if target_id in names and obj.station_item:
                ignored.add(obj.station_item)
        ignored.update(self._parts_inside(ignored))
        return ignored

    def _parts_inside(self, names: set[str]) -> set[str]:
        """Parts standing inside a container the arm is already allowed to touch."""
        if self.world is None:
            return set()
        extra: set[str] = set()
        for name in names:
            origin = self.world.object_bottom(name)
            size = self.world.sizes.get(name)
            if origin is None or not size:
                continue
            for other in self.world.sizes:
                if other in names or other in extra:
                    continue
                bottom = self.world.object_bottom(other)
                if bottom is None:
                    continue
                if (abs(float(bottom[0] - origin[0])) <= size[0] / 2
                        and abs(float(bottom[1] - origin[1])) <= size[1] / 2):
                    extra.add(other)
        return extra

    def retarget_pick(self, object_id, xyz_m=None):
        self._require()
        obj = self.scene.objects.get(object_id)
        if obj is None:
            return None
        if xyz_m is not None:
            grasp = self.scene.targets.get(obj.grasp_target)
            if grasp is None or grasp.pose_m_rad is None:
                return None
            camera = [float(v) for v in xyz_m]
            wrote = False
            for tid in (obj.approach_target, obj.grasp_target, obj.lift_target):
                cfg = self.scene.targets.get(tid)
                if cfg is None or cfg.pose_m_rad is None or tid not in self._targets:
                    continue
                self._targets[tid] = pose_at_camera_xy(cfg.pose_m_rad, camera)
                self._retargeted.add(tid)
                wrote = True
            return [round(v, 4) for v in camera] if wrote else None
        if not obj.station_item or self.world is None:
            return None
        bottom = self.world.object_bottom(obj.station_item)
        if bottom is None or self._holding == object_id:
            return None
        ox, oy, oz = (float(v) for v in bottom)
        for tid in (obj.approach_target, obj.grasp_target, obj.lift_target):
            cfg = self.scene.targets.get(tid)
            if cfg is None or cfg.pose_m_rad is None or tid not in self._targets:
                continue
            self._targets[tid] = [ox, oy, cfg.pose_m_rad[2] + oz, *cfg.pose_m_rad[3:]]
            self._retargeted.add(tid)
        return [round(ox, 4), round(oy, 4), round(oz, 4)]

    def _shift_targets(self, target_ids, camera_xyz, anchor_xy) -> Optional[list[float]]:
        """Move a group of targets so `anchor_xy` lands on the camera point. Heights stay taught."""
        dx = float(camera_xyz[0]) - float(anchor_xy[0])
        dy = float(camera_xyz[1]) - float(anchor_xy[1])
        wrote = False
        for tid in target_ids:
            cfg = self.scene.targets.get(tid)
            if cfg is None or cfg.pose_m_rad is None or tid not in self._targets:
                continue
            pose = cfg.pose_m_rad
            self._targets[tid] = [float(pose[0]) + dx, float(pose[1]) + dy, float(pose[2]), *pose[3:]]
            self._retargeted.add(tid)
            wrote = True
        if not wrote:
            return None
        return [round(float(camera_xyz[0]), 4), round(float(camera_xyz[1]), 4), round(float(camera_xyz[2]), 4)]

    def retarget_point(self, target_id, xyz_m):
        """One look or hover pose: its XY becomes the camera point. Height stays taught."""
        self._require()
        cfg = self.scene.targets.get(target_id)
        if cfg is None or cfg.pose_m_rad is None or target_id not in self._targets:
            return None
        self._targets[target_id] = pose_at_camera_xy(cfg.pose_m_rad, xyz_m)
        self._retargeted.add(target_id)
        return [round(float(v), 4) for v in xyz_m]

    def retarget_push(self, object_id, xyz_m):
        """Slide the taught push path so it starts beside where the camera saw the object."""
        self._require()
        obj = self.scene.objects.get(object_id)
        if obj is None or obj.push is None:
            return None
        grasp = self.scene.targets.get(obj.grasp_target)
        if grasp is None or grasp.pose_m_rad is None:
            return None
        push = obj.push
        return self._shift_targets(
            [push.above_target, push.start_target, push.end_target, push.lift_target],
            xyz_m, grasp.pose_m_rad[:2])

    def retarget_place(self, destination_id, xyz_m):
        """Drop where the camera saw the container, at the taught height above it."""
        self._require()
        dest = self.scene.destinations.get(destination_id)
        if dest is None:
            return None
        place = self.scene.targets.get(dest.place_target)
        if place is None or place.pose_m_rad is None:
            return None
        return self._shift_targets(
            [dest.approach_target, dest.place_target, dest.retreat_target],
            xyz_m, place.pose_m_rad[:2])

    def plan_segment(self, target_id, motion: MotionType, start_joints=None):
        self._require()
        if not self.target_exists(target_id):
            raise RobotControlError(ErrorCode.UNKNOWN_ID, f"target '{target_id}' is not in the scene")
        t = self.scene.targets[target_id]
        start = list(start_joints) if start_joints is not None else self.world.joints()
        if t.joints_rad and target_id not in self._retargeted:
            end = list(t.joints_rad)
        else:
            pose = self._targets.get(target_id) or t.pose_m_rad
            if pose is None:
                return PlannedSegment(target_id, motion, start, None, None)
            sol = self.world.ik(pose, start)
            if sol is None:
                return PlannedSegment(target_id, motion, start, None, None)
            end = [float(v) for v in sol]
        return PlannedSegment(target_id, motion, start, end, Pose.from_list(T_to_pose(self.world.tcp_T(end))))

    def test_segment(self, seg):
        self._require()
        if seg.end_joints is None:
            return PathCheckResult(ok=False, conclusive=True, reason="unreachable")
        ignore = self._ignored_part(seg.target_id)
        start, end = seg.start_joints, seg.end_joints
        for i in range(9):
            u = i / 8
            q = [a + (b - a) * u for a, b in zip(start, end)]
            hit = self.world.hits(q, ignore)
            if hit:
                return PathCheckResult(ok=False, conclusive=True, collisions=len(hit), geometry_checked=True,
                                       reason=f"PyBullet: arm meets {', '.join(hit)} on the way to '{seg.target_id}'")
        return PathCheckResult(ok=True, conclusive=True, geometry_checked=True,
                               reason="PyBullet path: no contact with the other parts")

    def _execute(self, end, motion: MotionType, ctx: MotionContext, label: str):
        self._require()
        self._enforce_simulate()
        problems = self.check_ready()
        if problems:
            raise RobotControlError(ErrorCode.MODE_VIOLATION, "; ".join(problems))
        start = self.world.joints()
        span = max(abs(a - b) for a, b in zip(start, end))
        duration = max(0.15, span / self._joint_speed)
        self._stop.clear()
        self.world.moving = True
        t0 = time.monotonic()
        deadline = t0 + ctx.timeout_s
        try:
            while True:
                u = min(1.0, (time.monotonic() - t0) / duration)
                self.world.set_joints([a + (b - a) * u for a, b in zip(start, end)])
                if u >= 1.0:
                    break
                if ctx.cancel.is_set():
                    raise RobotControlError(ErrorCode.CANCELLED, f"move to {label} cancelled")
                if self._stop.is_set():
                    raise RobotControlError(ErrorCode.STOPPED, f"move to {label} stopped")
                if time.monotonic() > deadline:
                    raise RobotControlError(ErrorCode.TIMEOUT, f"move to {label} exceeded {ctx.timeout_s:.1f} s")
                time.sleep(0.02)
        finally:
            self.world.moving = False
            self.world._publish()
        err = max(abs(a - b) for a, b in zip(self.world.joints(), end))
        if err > JOINT_TOL_RAD:
            raise RobotControlError(ErrorCode.BACKEND_ERROR, f"arm did not reach {label}")

    def move_to_named_target(self, seg, ctx):
        if seg.end_joints is None:
            raise RobotControlError(ErrorCode.UNREACHABLE, f"target '{seg.target_id}' unreachable")
        self._execute(seg.end_joints, seg.motion, ctx, f"'{seg.target_id}'")
        self._at_target = seg.target_id

    def move_joints(self, joints_rad, ctx):
        self._execute(list(joints_rad), "joint", ctx, "joint position")
        self._at_target = None

    def move_linear(self, pose, ctx):
        self._require()
        sol = self.world.ik([pose.x, pose.y, pose.z, pose.rx, pose.ry, pose.rz], self.world.joints())
        if sol is None:
            raise RobotControlError(ErrorCode.UNREACHABLE, "linear target unreachable")
        self._execute([float(v) for v in sol], "linear", ctx, "linear pose")
        self._at_target = None

    def _status(self):
        return GripStatus(self._gripper, self._holding, self._grip_confirmed, FeedbackSource.SIMULATED)

    def open_gripper(self):
        self._require()
        self.world.release()
        self._holding = None
        self._gripper = "open"
        self._grip_confirmed = False
        return self._status()

    def close_gripper(self, object_id):
        self._require()
        self._gripper = "closed"
        self._grip_confirmed = False
        obj = self.scene.objects.get(object_id) if object_id else None
        if obj is None or not obj.station_item or self._at_target != obj.grasp_target:
            self._holding = None
            return self._status()
        bottom = self.world.object_bottom(obj.station_item)
        if bottom is None:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, f"part '{obj.station_item}' is not in the cell")
        tcp = self.world.tcp_T()[:3, 3]
        centre = [float(bottom[0]), float(bottom[1]), float(bottom[2]) + obj.dimensions_m[2] / 2.0]
        dist = float(math.dist([float(v) for v in tcp], centre))
        if dist > 0.04:
            self._holding = None
            return self._status()
        self.world.grab(obj.station_item)
        self._holding = object_id
        return self._status()

    def begin_push(self, object_id):
        self._require()
        obj = self.scene.objects.get(object_id)
        if obj is None or not obj.station_item:
            return False
        if self._holding is not None:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED, "cannot push while holding an object")
        bottom = self.world.object_bottom(obj.station_item)
        if bottom is None:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, f"part '{obj.station_item}' is not in the cell")
        tcp = self.world.tcp_T()[:3, 3]
        gap = math.hypot(tcp[0] - float(bottom[0]), tcp[1] - float(bottom[1])) - max(obj.dimensions_m[:2]) / 2
        if gap > 0.045:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED,
                                    f"'{object_id}' is not beside the gripper ({gap * 1000:.0f} mm away)")
        self.world.grab(obj.station_item)
        return True

    def end_push(self):
        if self._holding is None and self.world is not None:
            self.world.release()

    def verify_grip(self):
        self._require()
        self._grip_confirmed = bool(self._holding and self._gripper == "closed" and self.world._held)
        if not self._grip_confirmed:
            self._holding = None
        return self._status()

    def get_gripper_state(self):
        return self._status()

    def reset_to_start(self) -> None:
        """Unstick a fault: drop whatever is held, put the parts back, open the gripper, go home."""
        self._require()
        self._stop.clear()
        self.world.restore()
        self._holding = None
        self._gripper = "open"
        self._grip_confirmed = False
        self._at_target = None
        self._retargeted.clear()
        self._targets = {name: list(t.pose_m_rad) for name, t in self.scene.targets.items() if t.pose_m_rad}
        home = self.scene.targets.get(self.scene.home_target)
        if home is not None and home.joints_rad:
            self.world.set_joints(home.joints_rad)
            self._at_target = self.scene.home_target

    def stop(self):
        self._stop.set()
