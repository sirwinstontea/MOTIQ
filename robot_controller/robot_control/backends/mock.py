"""Deterministic in-memory simulator with fault injection. No RoboDK needed.

All feedback is ``feedback_source=simulated``. The mock has no real kinematics:
Cartesian targets get deterministic pseudo-joints so limit checks have input.
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


class MockController(RobotController):
    backend_name = "mock"

    def __init__(self, robot: RobotConfig, scene: SceneConfig, *,
                 faults: Optional[dict[str, ErrorCode]] = None,
                 unreachable_targets: Optional[set[str]] = None,
                 colliding_targets: Optional[set[str]] = None,
                 missing_targets: Optional[set[str]] = None,
                 segment_duration_s: float = 0.0,
                 stale_state: bool = False):
        self.robot = robot
        self.scene = scene
        self.faults = dict(faults or {})
        self.unreachable = set(unreachable_targets or ())
        self.colliding = set(colliding_targets or ())
        self.missing = set(missing_targets or ())
        self.segment_duration_s = segment_duration_s
        self.stale_state = stale_state
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.connected = False
        home = scene.targets[scene.home_target]
        self._joints = list(home.joints_rad) if home.joints_rad else [0.0] * robot.dof
        self._pose = Pose.from_list(home.pose_m_rad) if home.pose_m_rad else Pose.from_list([0.3, 0, 0.3, math.pi, 0, 0])
        self._at_target: Optional[str] = scene.home_target
        self._gripper = "open"
        self._holding: Optional[str] = None
        self._grip_confirmed = False
        self.history: list[str] = []          # executed segments, for tests/audit

    # --- helpers -------------------------------------------------------------
    def _fault(self, name: str):
        code = self.faults.get(name)
        if code is not None:
            raise RobotControlError(code, f"mock fault injected at {name}")

    def _require_connected(self):
        if not self.connected:
            raise RobotControlError(ErrorCode.BACKEND_NOT_CONNECTED, "mock backend not connected")

    def _pseudo_joints(self, pose: list[float]) -> list[float]:
        x, y, z = pose[:3]
        base = [math.atan2(y, x), -1.2 + 0.5 * z, 1.4 - 0.5 * z, -1.57, -1.57, 0.0]
        return (base + [0.0] * self.robot.dof)[: self.robot.dof]

    # --- lifecycle -----------------------------------------------------------
    def connect(self):
        self._fault("connect")
        self.connected = True

    def disconnect(self):
        self.connected = False

    def capabilities(self):
        return Capabilities(backend="mock", robot_model=self.robot.model, dof=self.robot.dof,
                            simulation=True, motion_enabled=True, gripper="symbolic",
                            path_check="feasibility_only", feedback_source=FeedbackSource.SIMULATED,
                            cartesian_relative=True, joint_motion=True,
                            reference_frames=["base", "tool", "world"],
                            notes=["pure-Python mock; no kinematics, no collision geometry"])

    def get_state(self):
        with self._lock:
            sampled = time.monotonic() - (10.0 if self.stale_state else 0.0)
            return RobotState(backend="mock", robot_model=self.robot.model, connected=self.connected,
                              mode="simulation", joints=Joints(values=list(self._joints)), tcp_pose=self._pose,
                              gripper_state=self._gripper, holding_object_id=self._holding,
                              grip_confirmed=self._grip_confirmed, fault=None, busy=False,
                              timestamp_utc=utc_now(), sampled_monotonic=sampled,
                              feedback_source=FeedbackSource.SIMULATED)

    def check_ready(self):
        problems = []
        if not self.connected:
            problems.append("mock backend not connected")
        return problems

    # --- planning ----------------------------------------------------------------
    def target_exists(self, target_id):
        return target_id in self.scene.targets and target_id not in self.missing

    def plan_segment(self, target_id, motion: MotionType, start_joints=None):
        if not self.target_exists(target_id):
            raise RobotControlError(ErrorCode.UNKNOWN_ID, f"target '{target_id}' not found in mock scene")
        t = self.scene.targets[target_id]
        start = list(start_joints) if start_joints is not None else list(self._joints)
        if target_id in self.unreachable:
            return PlannedSegment(target_id, motion, start, None, None)
        joints = list(t.joints_rad) if t.joints_rad else self._pseudo_joints(t.pose_m_rad)
        pose = Pose.from_list(t.pose_m_rad, t.frame_id) if t.pose_m_rad else self._pose
        return PlannedSegment(target_id, motion, start, joints, pose)

    @staticmethod
    def _rotated_vector(rotvec, vector):
        """Rodrigues rotation, sufficient for frame-semantics unit tests."""
        angle = math.sqrt(sum(v * v for v in rotvec))
        if angle < 1e-12:
            return list(vector)
        k = [v / angle for v in rotvec]
        c, s = math.cos(angle), math.sin(angle)
        cross = [k[1] * vector[2] - k[2] * vector[1],
                 k[2] * vector[0] - k[0] * vector[2],
                 k[0] * vector[1] - k[1] * vector[0]]
        dot = sum(a * b for a, b in zip(k, vector))
        return [vector[i] * c + cross[i] * s + k[i] * dot * (1 - c) for i in range(3)]

    def plan_cartesian_relative(self, direction, distance_m, reference_frame, start_joints=None):
        start = list(start_joints) if start_joints is not None else list(self._joints)
        current = self._pose
        vectors = {"forward": [1, 0, 0], "backward": [-1, 0, 0],
                   "left": [0, 1, 0], "right": [0, -1, 0],
                   "up": [0, 0, 1], "down": [0, 0, -1]}
        v = vectors[direction]
        if reference_frame == "tool":
            v = self._rotated_vector([current.rx, current.ry, current.rz], v)
        # The mock has no configured world/base transform, so world == base.
        xyz = [current.x + distance_m * v[0], current.y + distance_m * v[1],
               current.z + distance_m * v[2]]
        pose = Pose.from_list([*xyz, current.rx, current.ry, current.rz], "base")
        return PlannedSegment(f"relative_{direction}", "linear", start, self._pseudo_joints(pose.as_list()), pose,
                              requested={"direction": direction, "distance_m": distance_m,
                                         "reference_frame": reference_frame})

    def plan_joint_move(self, joint_index, *, delta_rad=None, target_rad=None, start_joints=None):
        start = list(start_joints) if start_joints is not None else list(self._joints)
        if not 1 <= joint_index <= self.robot.dof:
            raise RobotControlError(ErrorCode.VALIDATION_ERROR,
                                    f"joint_index {joint_index} outside 1..{self.robot.dof}")
        end = list(start)
        end[joint_index - 1] = target_rad if target_rad is not None else end[joint_index - 1] + delta_rad
        return PlannedSegment(f"joint_{joint_index}", "joint", start, end, self._pose,
                              requested={"joint_index": joint_index, "delta_rad": delta_rad,
                                         "target_rad": target_rad})

    def test_segment(self, seg):
        if seg.target_id in self.colliding:
            return PathCheckResult(ok=False, conclusive=True, collisions=1,
                                   reason=f"mock: collision configured on '{seg.target_id}'")
        if seg.end_joints is None:
            return PathCheckResult(ok=False, conclusive=True, reason="mock: unreachable")
        return PathCheckResult(ok=True, conclusive=True, reason="mock feasibility only (no geometry)")

    # --- motion --------------------------------------------------------------------
    def _simulate_motion(self, ctx: MotionContext):
        self._stop.clear()
        end = time.monotonic() + self.segment_duration_s
        deadline = time.monotonic() + ctx.timeout_s
        while time.monotonic() < end:
            if ctx.cancel.is_set():
                raise RobotControlError(ErrorCode.CANCELLED, "motion cancelled")
            if self._stop.is_set():
                raise RobotControlError(ErrorCode.STOPPED, "motion stopped by stop request")
            if time.monotonic() > deadline:
                raise RobotControlError(ErrorCode.TIMEOUT, f"segment exceeded {ctx.timeout_s:.1f} s")
            time.sleep(0.005)

    def move_to_named_target(self, seg: PlannedSegment, ctx: MotionContext):
        self._require_connected()
        self._fault("move")
        self._fault(f"move:{seg.target_id}")
        if seg.end_joints is None:
            raise RobotControlError(ErrorCode.UNREACHABLE, f"target '{seg.target_id}' unreachable")
        self._simulate_motion(ctx)
        with self._lock:
            self._joints = list(seg.end_joints)
            self._pose = seg.end_pose
            self._at_target = seg.target_id
            self.history.append(f"{seg.motion}:{seg.target_id}")

    def move_joints(self, joints_rad, ctx):
        self._require_connected()
        self._simulate_motion(ctx)
        with self._lock:
            self._joints = list(joints_rad)
            self._at_target = None

    def move_linear(self, pose, ctx):
        self._require_connected()
        self._simulate_motion(ctx)
        with self._lock:
            self._pose = pose
            self._at_target = None

    # --- gripper ----------------------------------------------------------------------
    def _status(self):
        return GripStatus(self._gripper, self._holding, self._grip_confirmed, FeedbackSource.SIMULATED)

    def open_gripper(self):
        self._require_connected()
        self._fault("open_gripper")
        with self._lock:
            self._gripper = "open"
            self._holding = None
            self._grip_confirmed = False
            return self._status()

    def close_gripper(self, object_id):
        self._require_connected()
        self._fault("close_gripper")
        with self._lock:
            self._gripper = "closed"
            obj = self.scene.objects.get(object_id) if object_id else None
            if obj is not None and self._at_target == obj.grasp_target:
                self._holding = object_id
            else:
                self._holding = None
            self._grip_confirmed = False   # only verify_grip confirms
            return self._status()

    def begin_push(self, object_id):
        self._require_connected()
        self._fault("begin_push")
        with self._lock:
            if self._holding:
                raise RobotControlError(ErrorCode.PRECONDITION_FAILED, "cannot push while holding an object")
            self.pushing = object_id
            return True

    def end_push(self):
        with self._lock:
            self.pushed = [*getattr(self, "pushed", []), getattr(self, "pushing", None)]
            self.pushing = None

    def verify_grip(self):
        self._require_connected()
        self._fault("verify_grip")
        with self._lock:
            self._grip_confirmed = self._gripper == "closed" and self._holding is not None
            return self._status()

    def get_gripper_state(self):
        with self._lock:
            return self._status()

    def stop(self):
        self._stop.set()
