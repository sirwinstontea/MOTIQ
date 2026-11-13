"""Typed models for the AI action contract, tasks, poses, state and feedback.

Units at every domain boundary: metres (m) and radians (rad). Backends convert
explicitly to vendor units (RoboDK uses mm and degrees).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import SCHEMA_VERSION

ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


def utc_now() -> str:
    """Real UTC timestamp, ISO-8601 with milliseconds and a Z suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Strict(BaseModel):
    """Base for all contract models: unknown fields are rejected."""
    model_config = ConfigDict(extra="forbid", frozen=False)


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------

class ActionName(str, Enum):
    GET_ROBOT_STATE = "get_robot_state"
    GET_SCENE = "get_scene"
    IDENTIFY_OBJECT = "identify_object"
    PICK_OBJECT = "pick_object"
    MOVE_OBJECT = "move_object"
    PLACE_OBJECT = "place_object"
    RELEASE_OBJECT = "release_object"
    MOVE_TO_TARGET = "move_to_target"
    MOVE_RELATIVE = "move_relative"
    MOVE_JOINT = "move_joint"
    FOLLOW_PATH = "follow_path"
    CIRCLE_OBJECT = "circle_object"
    PLACE_ON_TABLE = "place_on_table"
    SHAKE_OBJECT = "shake_object"
    PERFORM_MOTION = "perform_motion"
    PUSH_OBJECT = "push_object"
    GET_TASK_STATUS = "get_task_status"
    CANCEL_TASK = "cancel_task"
    STOP_ROBOT = "stop_robot"


# Control calls are served by the API directly; they cannot be steps of a task.
CONTROL_ACTIONS = {ActionName.GET_TASK_STATUS, ActionName.CANCEL_TASK, ActionName.STOP_ROBOT}


class TaskState(str, Enum):
    IDLE = "IDLE"
    VALIDATING = "VALIDATING"
    IDENTIFYING = "IDENTIFYING"
    IDENTIFIED = "IDENTIFIED"
    APPROACH_PICK = "APPROACH_PICK"
    AT_PICK = "AT_PICK"
    GRIPPING = "GRIPPING"
    VERIFYING_GRIP = "VERIFYING_GRIP"
    LIFTING = "LIFTING"
    MOVING = "MOVING"
    TRANSPORTING = "TRANSPORTING"
    SHAKING = "SHAKING"
    PUSHING = "PUSHING"
    APPROACH_PLACE = "APPROACH_PLACE"
    AT_PLACE = "AT_PLACE"
    RELEASING = "RELEASING"
    RETREATING = "RETREATING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAULT = "FAULT"


class ActionStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class FeedbackSource(str, Enum):
    SIMULATED = "simulated"            # mock or RoboDK digital twin
    UNVERIFIED = "unverified"          # command issued, no confirmation available
    HARDWARE_CONFIRMED = "hardware_confirmed"   # never produced by this build


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    UNKNOWN_ID = "UNKNOWN_ID"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    SAFETY_VETO = "SAFETY_VETO"
    COLLISION_PREDICTED = "COLLISION_PREDICTED"
    UNREACHABLE = "UNREACHABLE"
    NEAR_SINGULAR = "NEAR_SINGULAR"
    PATH_CHECK_INCONCLUSIVE = "PATH_CHECK_INCONCLUSIVE"
    STALE_DATA = "STALE_DATA"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    STOPPED = "STOPPED"
    GRIPPER_FAULT = "GRIPPER_FAULT"
    GRIP_NOT_CONFIRMED = "GRIP_NOT_CONFIRMED"
    BACKEND_ERROR = "BACKEND_ERROR"
    BACKEND_NOT_CONNECTED = "BACKEND_NOT_CONNECTED"
    MODE_VIOLATION = "MODE_VIOLATION"
    PHYSICAL_BACKEND_DISABLED = "PHYSICAL_BACKEND_DISABLED"
    CONFIG_ERROR = "CONFIG_ERROR"
    BUSY = "BUSY"
    FAULT_LATCHED = "FAULT_LATCHED"
    DUPLICATE_TASK = "DUPLICATE_TASK"


class RobotControlError(Exception):
    """Typed error raised by backends, safety and executor."""

    def __init__(self, code: ErrorCode, message: str, recoverable: bool = False):
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
        self.recoverable = recoverable


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def _require_finite(values, what: str):
    for v in values:
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise ValueError(f"{what} must contain only finite numbers")
    return values


class Pose(Strict):
    """TCP pose: position in metres, orientation as a rotation vector in radians."""
    x: float
    y: float
    z: float
    rx: float
    ry: float
    rz: float
    frame_id: str = "base"
    units: Literal["m_rad_rotvec"] = "m_rad_rotvec"

    @model_validator(mode="after")
    def _finite(self):
        _require_finite([self.x, self.y, self.z, self.rx, self.ry, self.rz], "pose")
        return self

    def as_list(self) -> list[float]:
        return [self.x, self.y, self.z, self.rx, self.ry, self.rz]

    @classmethod
    def from_list(cls, v, frame_id: str = "base") -> "Pose":
        if len(v) != 6:
            raise ValueError("pose needs 6 values [x,y,z,rx,ry,rz]")
        return cls(x=v[0], y=v[1], z=v[2], rx=v[3], ry=v[4], rz=v[5], frame_id=frame_id)


class Joints(Strict):
    values: list[float]
    units: Literal["rad"] = "rad"

    @field_validator("values")
    @classmethod
    def _finite(cls, v):
        return _require_finite(v, "joints")


# --------------------------------------------------------------------------
# Action parameters (one strict model per tool)
# --------------------------------------------------------------------------

class NoParams(Strict):
    pass


class ObjectParams(Strict):
    object_id: str = Field(pattern=ID_PATTERN)


class MoveObjectParams(Strict):
    object_id: str = Field(pattern=ID_PATTERN)
    destination_id: str = Field(pattern=ID_PATTERN)


class DestinationParams(Strict):
    destination_id: str = Field(pattern=ID_PATTERN)


class TargetParams(Strict):
    target_id: str = Field(pattern=ID_PATTERN)


class RelativeMoveParams(Strict):
    direction: Literal["up", "down", "left", "right", "forward", "backward"]
    distance_m: float = Field(gt=0)
    reference_frame: Literal["base", "tool", "world"] = "base"

    @field_validator("distance_m")
    @classmethod
    def _distance_finite(cls, v):
        _require_finite([v], "distance_m")
        return v


class JointMoveParams(Strict):
    """One-based joint index; angles are radians at the controller boundary."""
    joint_index: int = Field(ge=1, strict=True)
    delta_rad: Optional[float] = None
    target_rad: Optional[float] = None

    @model_validator(mode="after")
    def _one_target(self):
        if (self.delta_rad is None) == (self.target_rad is None):
            raise ValueError("provide exactly one of delta_rad or target_rad")
        _require_finite([self.delta_rad if self.delta_rad is not None else self.target_rad], "joint angle")
        return self


class CartesianPoint(Strict):
    x: float
    y: float
    z: float

    @model_validator(mode="after")
    def _finite(self):
        _require_finite([self.x, self.y, self.z], "waypoint")
        return self


class PathParams(Strict):
    waypoints: list[CartesianPoint] = Field(min_length=1, max_length=256)


class CircleParams(ObjectParams):
    radius_m: float = Field(gt=0, allow_inf_nan=False)
    z_m: float = Field(allow_inf_nan=False)
    clockwise: bool = False
    max_chord_error_m: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)


class TablePlaceParams(Strict):
    location: Optional[Literal["center"]] = None
    x_m: Optional[float] = Field(default=None, allow_inf_nan=False)
    y_m: Optional[float] = Field(default=None, allow_inf_nan=False)
    direction: Optional[Literal["left", "right", "forward", "backward"]] = None
    distance_m: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _target(self):
        absolute = self.location is None and self.x_m is not None and self.y_m is not None and self.direction is None and self.distance_m is None
        relative = self.location is None and self.x_m is None and self.y_m is None and self.direction is not None and self.distance_m is not None
        center = self.location == "center" and all(v is None for v in (self.x_m, self.y_m, self.direction, self.distance_m))
        if not (absolute or relative or center):
            raise ValueError("provide location=center OR x_m and y_m OR direction and distance_m")
        return self


class PushParams(ObjectParams):
    direction: Optional[Literal["left", "right", "forward", "backward"]] = None
    distance_m: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _pair(self):
        if (self.direction is None) != (self.distance_m is None):
            raise ValueError("direction and distance_m must be specified together")
        return self


class ShakeParams(Strict):
    pattern_id: str = Field(pattern=ID_PATTERN)
    # Hard contract bound; the per-pattern max_cycles in the scene is stricter.
    cycles: int = Field(ge=1, le=10, strict=True)


class MotionParams(Strict):
    motion_id: str = Field(pattern=ID_PATTERN)


class TaskIdParams(Strict):
    task_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")


PARAMS_BY_ACTION: dict[ActionName, type[Strict]] = {
    ActionName.GET_ROBOT_STATE: NoParams,
    ActionName.GET_SCENE: NoParams,
    ActionName.IDENTIFY_OBJECT: ObjectParams,
    ActionName.PICK_OBJECT: ObjectParams,
    ActionName.MOVE_OBJECT: MoveObjectParams,
    ActionName.PLACE_OBJECT: DestinationParams,
    ActionName.RELEASE_OBJECT: NoParams,
    ActionName.MOVE_TO_TARGET: TargetParams,
    ActionName.MOVE_RELATIVE: RelativeMoveParams,
    ActionName.MOVE_JOINT: JointMoveParams,
    ActionName.FOLLOW_PATH: PathParams,
    ActionName.CIRCLE_OBJECT: CircleParams,
    ActionName.PLACE_ON_TABLE: TablePlaceParams,
    ActionName.SHAKE_OBJECT: ShakeParams,
    ActionName.PERFORM_MOTION: MotionParams,
    ActionName.PUSH_OBJECT: PushParams,
    ActionName.GET_TASK_STATUS: TaskIdParams,
    ActionName.CANCEL_TASK: TaskIdParams,
    ActionName.STOP_ROBOT: NoParams,
}


class ActionRequest(Strict):
    action_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    action: ActionName
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_params(self):
        model = PARAMS_BY_ACTION[self.action]
        # Re-validate strictly; stores the normalised dict back.
        self.parameters = model.model_validate(self.parameters).model_dump()
        return self


class TaskRequest(Strict):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    task_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    idempotency_key: Optional[str] = Field(default=None, max_length=128)
    actions: list[ActionRequest] = Field(min_length=1, max_length=32)
    speed_percent: Optional[float] = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _checks(self):
        if self.speed_percent is not None:
            _require_finite([self.speed_percent], "speed_percent")
        ids = [a.action_id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("action_id values must be unique within a task")
        for a in self.actions:
            if a.action in CONTROL_ACTIONS:
                raise ValueError(f"'{a.action.value}' is a control call, not a task step; use the API endpoint")
        return self


# --------------------------------------------------------------------------
# Feedback / results
# --------------------------------------------------------------------------

class Feedback(Strict):
    tcp_pose: Optional[Pose] = None
    joints: Optional[Joints] = None
    holding_object_id: Optional[str] = None
    grip_confirmed: bool = False
    gripper_state: Optional[Literal["open", "closed", "unknown"]] = None
    feedback_source: FeedbackSource = FeedbackSource.SIMULATED
    data_age_ms: Optional[float] = None
    detector: Optional[str] = None


class ErrorInfo(Strict):
    code: ErrorCode
    message: str
    recoverable: bool = False
    auto_retry: Literal[False] = False   # never retry physical moves automatically


class SafetyChecks(Strict):
    approved: bool
    reasons: list[str] = Field(default_factory=list)


class Event(Strict):
    """Uniform JSON record for every action result and executor event."""
    schema_version: str = SCHEMA_VERSION
    event_id: int
    task_id: Optional[str]
    action_id: Optional[str]
    action: Optional[str]
    state: TaskState
    status: Union[ActionStatus, Literal["INFO"]]
    timestamp_utc: str = Field(default_factory=utc_now)
    backend: str
    robot_model: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    feedback: Optional[Feedback] = None
    error: Optional[ErrorInfo] = None
    safety_checks: Optional[SafetyChecks] = None
    message: Optional[str] = None
    result: Optional[dict[str, Any]] = None


class PlanStep(Strict):
    """One step of the resolved plan, so callers can see what is running now and next."""
    index: int
    action_id: str
    action: str
    kind: Literal["detect", "move", "gripper", "verify", "query"]
    description: str
    motion: Optional[Literal["joint", "linear"]] = None
    target_id: Optional[str] = None
    target_xyz_m: Optional[list[float]] = None      # taught target position, base frame
    status: Literal["pending", "running", "done", "skipped", "failed"] = "pending"
    started_utc: Optional[str] = None
    finished_utc: Optional[str] = None


class TaskStatus(Strict):
    schema_version: str = SCHEMA_VERSION
    task_id: str
    status: ActionStatus
    state: TaskState
    current_action_id: Optional[str] = None
    actions: dict[str, ActionStatus]
    events: list[Event] = Field(default_factory=list)
    plan: list[PlanStep] = Field(default_factory=list)
    current_step: Optional[int] = None
    speed_percent: Optional[float] = None          # speed this task ran at (% of model max)
    motion_limits: Optional[dict] = None            # effective m/s, rad/s values for this task
    execution: Optional[dict[str, Any]] = None      # final robot/backend/motion summary
    error: Optional[ErrorInfo] = None
    started_utc: Optional[str] = None
    finished_utc: Optional[str] = None
