"""Configuration models and loaders (robot model, scene, safety).

Invalid or missing configuration raises ``RobotControlError(CONFIG_ERROR)``;
callers must treat that as a rejection (fail closed).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .models import ErrorCode, RobotControlError

BackendName = Literal["mock", "robodk_sim", "ursim_sync", "physical_disabled"]


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _finite(vals, what):
    if any((not isinstance(v, (int, float))) or not math.isfinite(v) for v in vals):
        raise ValueError(f"{what}: values must be finite numbers")


# --------------------------------------------------------------------------
# Robot model
# --------------------------------------------------------------------------

class ModelLimits(_Cfg):
    joint_min_rad: list[float]
    joint_max_rad: list[float]
    max_joint_speed_rad_s: list[float]
    max_joint_accel_rad_s2: float = Field(gt=0)
    max_tcp_speed_m_s: float = Field(gt=0)
    max_tcp_accel_m_s2: float = Field(gt=0)
    max_payload_kg: float = Field(gt=0)
    reach_m: float = Field(gt=0)
    source: str = Field(description="where these numbers come from (datasheet/version)")


class RobotConfig(_Cfg):
    model: str
    dof: int = Field(ge=1, le=12)
    backend: BackendName
    station_path: Optional[str] = None
    station_name: Optional[str] = None            # RoboDK station tab to use/create
    robot_library_file: Optional[str] = None      # .robot file used by `setup-station`
    robot_item_name: str
    base_frame: str
    tool_frame: str
    gripper_kind: Literal["symbolic", "none"] = "symbolic"
    tcp_offset_m_rad: list[float] = Field(default_factory=lambda: [0, 0, 0, 0, 0, 0])
    tool_mass_kg: float = Field(ge=0, default=0.0)
    ursim_host: str = "127.0.0.1"                 # ursim_sync backend: URSim on localhost only
    limits: ModelLimits
    capabilities: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self):
        lim = self.limits
        for name in ("joint_min_rad", "joint_max_rad", "max_joint_speed_rad_s"):
            vals = getattr(lim, name)
            if len(vals) != self.dof:
                raise ValueError(f"limits.{name} has {len(vals)} values but dof={self.dof}")
            _finite(vals, name)
        if any(lo >= hi for lo, hi in zip(lim.joint_min_rad, lim.joint_max_rad)):
            raise ValueError("every joint_min_rad must be < joint_max_rad")
        if any(v <= 0 for v in lim.max_joint_speed_rad_s):
            raise ValueError("max_joint_speed_rad_s must be positive")
        if len(self.tcp_offset_m_rad) != 6:
            raise ValueError("tcp_offset_m_rad needs 6 values")
        _finite(self.tcp_offset_m_rad, "tcp_offset_m_rad")
        return self


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------

class Workspace(_Cfg):
    mode: Literal["box", "kinematic"] = "box"
    frame_id: str = "base"
    x_m: tuple[float, float]
    y_m: tuple[float, float]
    z_m: tuple[float, float]

    @model_validator(mode="after")
    def _check(self):
        for ax in ("x_m", "y_m", "z_m"):
            lo, hi = getattr(self, ax)
            _finite([lo, hi], ax)
            if lo >= hi:
                raise ValueError(f"workspace {ax}: min must be < max")
        return self


class SafetyConfig(_Cfg):
    allowed_backends: list[BackendName] = Field(default_factory=lambda: ["mock", "robodk_sim"])
    # Either ONE percentage of the robot model's maximums...
    speed_percent_of_model_max: Optional[float] = Field(default=None, gt=0, le=100)
    # ...or all four absolute values (m/s, m/s^2, rad/s, rad/s^2). Not both.
    tcp_speed_m_s: Optional[float] = Field(default=None, gt=0)
    tcp_accel_m_s2: Optional[float] = Field(default=None, gt=0)
    joint_speed_rad_s: Optional[float] = Field(default=None, gt=0)
    joint_accel_rad_s2: Optional[float] = Field(default=None, gt=0)
    workspace: Workspace
    max_state_age_ms: float = Field(gt=0)
    segment_timeout_s: float = Field(gt=0)
    action_timeout_s: float = Field(gt=0)
    min_detection_confidence: float = Field(ge=0, le=1)
    max_detection_age_s: float = Field(gt=0)
    max_shake_cycles: int = Field(ge=1, le=10)
    require_path_check: bool = True
    require_collision_geometry: bool = True
    allow_mock_detector: bool = True

    @model_validator(mode="after")
    def _speed_source(self):
        if self.workspace.mode == "kinematic" and not (self.require_path_check and self.require_collision_geometry):
            raise ValueError("kinematic workspace requires full path checking and collision geometry")
        absolute = [self.tcp_speed_m_s, self.tcp_accel_m_s2, self.joint_speed_rad_s, self.joint_accel_rad_s2]
        if self.speed_percent_of_model_max is not None:
            if any(v is not None for v in absolute):
                raise ValueError("set speed_percent_of_model_max OR the absolute speed/accel values, not both")
        elif any(v is None for v in absolute):
            raise ValueError("set speed_percent_of_model_max, or all of tcp_speed_m_s, tcp_accel_m_s2, "
                             "joint_speed_rad_s, joint_accel_rad_s2")
        return self


# --------------------------------------------------------------------------
# Scene
# --------------------------------------------------------------------------

class TargetDef(_Cfg):
    """A named, operator-taught target.

    Cartesian target: ``pose_m_rad`` only. Joint target: ``joints_rad`` (plus an
    optional ``pose_m_rad`` = expected TCP pose, used by the mock backend; the
    RoboDK backend computes the pose with forward kinematics instead).
    """
    pose_m_rad: Optional[list[float]] = None      # [x,y,z,rx,ry,rz] in frame_id, rotvec
    joints_rad: Optional[list[float]] = None
    frame_id: str = "base"
    description: str = ""

    @model_validator(mode="after")
    def _check(self):
        if self.pose_m_rad is None and self.joints_rad is None:
            raise ValueError("target needs pose_m_rad and/or joints_rad")
        for vals in (self.pose_m_rad, self.joints_rad):
            if vals is not None:
                _finite(vals, "target")
        if self.pose_m_rad is not None and len(self.pose_m_rad) != 6:
            raise ValueError("pose_m_rad needs 6 values")
        return self

    @property
    def is_joint_target(self) -> bool:
        return self.joints_rad is not None


class ObjectDef(_Cfg):
    avoid_collision: bool = False
    dimensions_m: tuple[float, float, float]
    mass_kg: Optional[float] = Field(default=None, ge=0)
    approach_target: str
    grasp_target: str
    lift_target: str
    class_labels: list[str] = Field(default_factory=list)   # vision labels mapped to this ID
    station_item: Optional[str] = None                       # RoboDK object to attach (simulation)
    push: Optional[PushDef] = None                           # None = this object cannot be pushed


class PushDef(_Cfg):
    """An operator-taught push path for one object, only named targets: above it, beside it (gripper not touching),
    pushed (`end`), lifted clear. The object is moved by `distance_m` along the start->end line."""
    above_target: str
    start_target: str
    end_target: str
    lift_target: str
    distance_m: float = Field(gt=0, le=0.15)
    description: str = ""


class DestinationDef(_Cfg):
    support_surface: Literal["taught", "table", "container"] = "taught"
    container_id: Optional[str] = None
    approach_target: str
    place_target: str
    retreat_target: str


class PatternDef(_Cfg):
    """An operator-approved bounded motion pattern built only from named targets."""
    max_cycles: int = Field(ge=1, le=10)
    requires_grip: bool = True
    max_payload_kg: float = Field(gt=0)
    start_target: str
    cycle_targets: list[str] = Field(min_length=1)
    end_target: str
    description: str = ""


class MotionDef(_Cfg):
    targets: list[str] = Field(min_length=1)
    requires_empty_gripper: bool = False
    description: str = ""


class BoxDef(_Cfg):
    """Simple box geometry for building a DEMO RoboDK station (simulation only).

    It is a stand-in for the real cell; collision results against it say
    nothing about physical clearance.
    """
    avoid_collision: bool = False
    size_m: tuple[float, float, float]
    pose_m_rad: list[float]               # bottom-centre of the box, in base frame
    color: tuple[float, float, float, float] = (0.6, 0.6, 0.6, 1.0)
    no_collision_with: list[str] = Field(default_factory=list)


class SearchDef(_Cfg):
    """Camera mounted on the arm head: named look poses the arm visits to find objects.

    Only named targets (same safety checks as any move_to_target). The bridge uses this; the
    controller itself never moves on its own.
    """
    camera: str = "arm_mounted"                       # arm_mounted | fixed
    scan_targets: list[str] = Field(min_length=1)      # visited in this order by a full scan
    look_first: str = "object_approach"                # object_approach = first look straight down at the object's slot
    settle_s: float = Field(default=0.6, ge=0, le=5)   # wait after arriving, so frames aren't motion-blurred
    look_s: float = Field(default=1.5, gt=0, le=10)    # how long to watch at each pose
    description: str = ""


class SceneConfig(_Cfg):
    collision_policy: Literal["all", "table_and_tagged"] = "all"
    table_geometry: str = "table"
    home_target: str
    targets: dict[str, TargetDef]
    objects: dict[str, ObjectDef]
    destinations: dict[str, DestinationDef]
    patterns: dict[str, PatternDef]
    motions: dict[str, MotionDef] = Field(default_factory=dict)
    station_geometry: dict[str, BoxDef] = Field(default_factory=dict)
    search: Optional[SearchDef] = None

    @model_validator(mode="after")
    def _refs(self):
        known = set(self.targets)
        refs = [("home_target", self.home_target)]
        for oid, o in self.objects.items():
            refs += [(f"objects.{oid}", t) for t in (o.approach_target, o.grasp_target, o.lift_target)]
            if o.push:
                refs += [(f"objects.{oid}.push", t) for t in (o.push.above_target, o.push.start_target,
                                                               o.push.end_target, o.push.lift_target)]
        for did, d in self.destinations.items():
            if d.support_surface == "container" and d.container_id not in self.objects:
                raise ValueError(f"destinations.{did}: container_id must name a registered object")
            refs += [(f"destinations.{did}", t) for t in (d.approach_target, d.place_target, d.retreat_target)]
        for pid, p in self.patterns.items():
            refs += [(f"patterns.{pid}", t) for t in [p.start_target, *p.cycle_targets, p.end_target]]
        for mid, m in self.motions.items():
            refs += [(f"motions.{mid}", t) for t in m.targets]
        if self.search:
            refs += [("search.scan_targets", t) for t in self.search.scan_targets]
        missing = [f"{where} -> '{t}'" for where, t in refs if t not in known]
        if missing:
            raise ValueError("scene references unknown targets: " + ", ".join(missing))
        return self


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _load_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise RobotControlError(ErrorCode.CONFIG_ERROR, f"config file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RobotControlError(ErrorCode.CONFIG_ERROR, f"invalid YAML in {p}: {e}") from e
    if not isinstance(data, dict):
        raise RobotControlError(ErrorCode.CONFIG_ERROR, f"{p} must contain a mapping")
    return data


def _parse(model, data: dict, what: str):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise RobotControlError(ErrorCode.CONFIG_ERROR, f"invalid {what} config: {e}") from e


def load_robot_config(path) -> RobotConfig:
    return _parse(RobotConfig, _load_yaml(path), "robot")


def load_safety_config(path) -> SafetyConfig:
    return _parse(SafetyConfig, _load_yaml(path), "safety")


def load_scene_config(path) -> SceneConfig:
    return _parse(SceneConfig, _load_yaml(path), "scene")


class MotionLimits(_Cfg):
    """Effective speeds/accelerations applied to the backend (m/s, m/s^2, rad/s, rad/s^2)."""
    tcp_speed_m_s: float
    tcp_accel_m_s2: float
    joint_speed_rad_s: float
    joint_accel_rad_s2: float
    joint_speed_limits_rad_s: list[float]
    source: str


def motion_limits(robot: RobotConfig, safety: SafetyConfig) -> MotionLimits:
    """Resolve the speeds to use. A percentage scales the MODEL's maximums
    (joint speed uses the slowest joint's maximum, so no joint exceeds it)."""
    lim = robot.limits
    pct = safety.speed_percent_of_model_max
    if pct is not None:
        f = pct / 100.0
        return MotionLimits(tcp_speed_m_s=f * lim.max_tcp_speed_m_s, tcp_accel_m_s2=f * lim.max_tcp_accel_m_s2,
                            joint_speed_rad_s=f * min(lim.max_joint_speed_rad_s),
                            joint_accel_rad_s2=f * lim.max_joint_accel_rad_s2,
                            joint_speed_limits_rad_s=[f * v for v in lim.max_joint_speed_rad_s],
                            source=f"{pct:g}% of {robot.model} model maximums")
    return MotionLimits(tcp_speed_m_s=safety.tcp_speed_m_s, tcp_accel_m_s2=safety.tcp_accel_m_s2,
                        joint_speed_rad_s=safety.joint_speed_rad_s, joint_accel_rad_s2=safety.joint_accel_rad_s2,
                        joint_speed_limits_rad_s=[min(safety.joint_speed_rad_s, v)
                                                  for v in lim.max_joint_speed_rad_s],
                        source="absolute values from safety config")


def motion_limits_for_percent(robot: RobotConfig, percent: float) -> MotionLimits:
    """Resolve an atomic task override without mutating the default safety config."""
    try:
        pct = float(percent)
    except (TypeError, ValueError) as e:
        raise RobotControlError(ErrorCode.VALIDATION_ERROR, "speed percent must be a number") from e
    if not math.isfinite(pct) or not 1 <= pct <= 100:
        raise RobotControlError(ErrorCode.VALIDATION_ERROR, f"speed must be between 1 and 100 %, got {percent!r}")
    f = pct / 100.0
    lim = robot.limits
    return MotionLimits(tcp_speed_m_s=f * lim.max_tcp_speed_m_s,
                        tcp_accel_m_s2=f * lim.max_tcp_accel_m_s2,
                        joint_speed_rad_s=f * min(lim.max_joint_speed_rad_s),
                        joint_accel_rad_s2=f * lim.max_joint_accel_rad_s2,
                        joint_speed_limits_rad_s=[f * v for v in lim.max_joint_speed_rad_s],
                        source=f"{pct:g}% of {robot.model} model maximums (task override)")


def cross_validate(robot: RobotConfig, safety: SafetyConfig, scene: SceneConfig) -> None:
    """Checks that combine the three files. Raises CONFIG_ERROR on any mismatch."""
    lim = robot.limits
    ml = motion_limits(robot, safety)
    problems = []
    if ml.tcp_speed_m_s > lim.max_tcp_speed_m_s:
        problems.append(f"tcp_speed {ml.tcp_speed_m_s} > model max {lim.max_tcp_speed_m_s} m/s")
    if ml.tcp_accel_m_s2 > lim.max_tcp_accel_m_s2:
        problems.append(f"tcp_accel {ml.tcp_accel_m_s2} > model max {lim.max_tcp_accel_m_s2} m/s^2")
    if ml.joint_speed_rad_s > min(lim.max_joint_speed_rad_s):
        problems.append(f"joint_speed {ml.joint_speed_rad_s} > slowest joint max {min(lim.max_joint_speed_rad_s)} rad/s")
    if ml.joint_accel_rad_s2 > lim.max_joint_accel_rad_s2:
        problems.append("joint_accel exceeds model max")
    for name, t in scene.targets.items():
        if t.joints_rad is not None and len(t.joints_rad) != robot.dof:
            problems.append(f"target '{name}' has {len(t.joints_rad)} joints, robot dof={robot.dof}")
    for oid, o in scene.objects.items():
        if o.mass_kg is not None and o.mass_kg + robot.tool_mass_kg > lim.max_payload_kg:
            problems.append(f"object '{oid}' + tool exceeds payload {lim.max_payload_kg} kg")
    for pid, p in scene.patterns.items():
        if p.max_cycles > safety.max_shake_cycles:
            problems.append(f"pattern '{pid}' max_cycles {p.max_cycles} > safety max {safety.max_shake_cycles}")
    if problems:
        raise RobotControlError(ErrorCode.CONFIG_ERROR, "; ".join(problems))
