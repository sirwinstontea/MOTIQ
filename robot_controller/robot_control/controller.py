"""RobotController contract shared by all backends.

The contract is robot-agnostic: joint arrays have ``dof`` entries taken from
config, poses are metres + rotation-vector radians. ``move_joints`` and
``move_linear`` are INTERNAL: they are never exposed to the AI and are only
called by the task executor after the safety policy approved the segment.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from .models import FeedbackSource, Joints, Pose

MotionType = Literal["joint", "linear"]


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: str
    robot_model: str
    dof: int
    simulation: bool
    motion_enabled: bool
    gripper: Literal["symbolic", "none", "disabled"]
    path_check: Literal["collision", "feasibility_only", "none"]
    feedback_source: FeedbackSource
    notes: list[str] = []


class RobotState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: str
    robot_model: str
    connected: bool
    mode: Literal["simulation", "disabled"]
    joints: Optional[Joints] = None
    tcp_pose: Optional[Pose] = None
    gripper_state: Literal["open", "closed", "unknown"] = "unknown"
    holding_object_id: Optional[str] = None
    grip_confirmed: bool = False
    fault: Optional[str] = None
    busy: bool = False
    timestamp_utc: str
    sampled_monotonic: float          # time.monotonic() when sampled; used for freshness
    feedback_source: FeedbackSource


@dataclass
class PlannedSegment:
    """One motion segment, resolved before execution so safety can check it."""
    target_id: str
    motion: MotionType
    start_joints: list[float]             # rad
    end_joints: Optional[list[float]]     # rad; None if IK failed (=> unreachable)
    end_pose: Optional[Pose]              # m / rad, in base frame


@dataclass
class PathCheckResult:
    ok: bool
    conclusive: bool
    reason: str = ""
    collisions: int = 0
    geometry_checked: bool = False   # True only if real collision geometry was tested


@dataclass
class GripStatus:
    state: Literal["open", "closed", "unknown"]
    holding_object_id: Optional[str]
    confirmed: bool
    source: FeedbackSource


@dataclass
class MotionContext:
    """Passed to every motion so backends can honour cancel/stop/timeout."""
    cancel: threading.Event = field(default_factory=threading.Event)
    timeout_s: float = 20.0


class RobotController(ABC):
    backend_name: str = "abstract"

    # --- lifecycle -------------------------------------------------------
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    # --- state -----------------------------------------------------------
    @abstractmethod
    def get_state(self) -> RobotState: ...

    def get_tcp_pose(self) -> Pose:
        st = self.get_state()
        if st.tcp_pose is None:
            from .models import ErrorCode, RobotControlError
            raise RobotControlError(ErrorCode.NOT_AVAILABLE, "TCP pose not available")
        return st.tcp_pose

    def get_joints(self) -> Joints:
        st = self.get_state()
        if st.joints is None:
            from .models import ErrorCode, RobotControlError
            raise RobotControlError(ErrorCode.NOT_AVAILABLE, "joints not available")
        return st.joints

    @abstractmethod
    def check_ready(self) -> list[str]:
        """Return a list of problems; empty list means ready for simulated motion."""

    def show_status(self, text: str) -> None:
        """Optional: display a short status line in the simulator UI."""
        return None

    def apply_motion_limits(self, tcp_speed_m_s: float, tcp_accel_m_s2: float,
                            joint_speed_rad_s: float, joint_accel_rad_s2: float) -> None:
        """Set speeds/accelerations (already validated against model limits)."""
        return None

    # --- targets / planning ----------------------------------------------
    @abstractmethod
    def target_exists(self, target_id: str) -> bool: ...

    def retarget_pick(self, object_id: str) -> Optional[list[float]]:
        """Simulation only: move the object's approach/grasp/lift targets over where the simulated object really is
        and return its bottom-centre [x, y, z] (m, base frame). Default (mock / real hardware): no-op, None, so picks
        keep using the taught targets."""
        return None

    @abstractmethod
    def plan_segment(self, target_id: str, motion: MotionType,
                     start_joints: Optional[list[float]] = None) -> PlannedSegment: ...

    @abstractmethod
    def test_segment(self, seg: PlannedSegment) -> PathCheckResult:
        """Feasibility/collision test of a segment WITHOUT leaving the robot moved."""

    # --- motion (internal; only after safety approval) ---------------------
    @abstractmethod
    def move_to_named_target(self, seg: PlannedSegment, ctx: MotionContext) -> None: ...

    @abstractmethod
    def move_joints(self, joints_rad: list[float], ctx: MotionContext) -> None: ...

    @abstractmethod
    def move_linear(self, pose: Pose, ctx: MotionContext) -> None: ...

    # --- gripper ------------------------------------------------------------
    @abstractmethod
    def open_gripper(self) -> GripStatus: ...

    @abstractmethod
    def close_gripper(self, object_id: Optional[str]) -> GripStatus: ...

    @abstractmethod
    def verify_grip(self) -> GripStatus: ...

    @abstractmethod
    def get_gripper_state(self) -> GripStatus: ...

    # --- push -------------------------------------------------------------
    def begin_push(self, object_id: str) -> bool:
        """Called with the closed, empty gripper beside `object_id`, right before the push segment. A simulator moves
        the object model with the tool on that segment and returns True; the default only moves the robot (False).
        Raise PRECONDITION_FAILED if the object is not where the push path expects it."""
        return False

    def end_push(self) -> None:
        """After the push segment (also on failure): leave the pushed object where it is now."""

    # --- stop ---------------------------------------------------------------
    @abstractmethod
    def stop(self) -> None:
        """Controlled software stop request. NOT an emergency stop."""
