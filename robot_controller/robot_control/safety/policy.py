"""Centralised, fail-closed safety policy.

Called before every action and before every motion segment. Any doubt
(missing data, inconclusive check, unknown config) => not approved.
This is software risk reduction, not a certified protective system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..config import RobotConfig, SafetyConfig
from ..controller import PlannedSegment, RobotController, RobotState
from ..models import ErrorCode, SafetyChecks
from ..scene_registry import Detection
from .limits import joints_within_limits, pose_within_workspace
from .trajectory import PathChecker
from .watchdog import state_age_ms


@dataclass
class SafetyDecision:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    code: Optional[ErrorCode] = None

    @classmethod
    def ok(cls, *notes: str) -> "SafetyDecision":
        return cls(True, list(notes))

    @classmethod
    def deny(cls, code: ErrorCode, *reasons: str) -> "SafetyDecision":
        return cls(False, list(reasons), code)

    def to_checks(self) -> SafetyChecks:
        return SafetyChecks(approved=self.approved, reasons=self.reasons)


class SafetyPolicy:
    def __init__(self, robot: RobotConfig, safety: SafetyConfig, path_checker: PathChecker):
        self.robot = robot
        self.safety = safety
        self.path_checker = path_checker
        self.backend_name = "unknown"     # set by the executor from the controller

    # 1. backend / mode -------------------------------------------------------
    def check_backend(self, controller: RobotController) -> SafetyDecision:
        name = controller.backend_name
        if name not in self.safety.allowed_backends:
            return SafetyDecision.deny(ErrorCode.MODE_VIOLATION, f"backend '{name}' not in allowed_backends")
        if name == "physical_disabled":
            return SafetyDecision.deny(ErrorCode.PHYSICAL_BACKEND_DISABLED,
                                       "physical backend is disabled in this build")
        try:
            caps = controller.capabilities()
        except Exception as e:
            return SafetyDecision.deny(ErrorCode.BACKEND_ERROR, f"capabilities unavailable: {e}")
        if not caps.simulation:
            return SafetyDecision.deny(ErrorCode.MODE_VIOLATION, "backend is not in simulation mode")
        if not caps.motion_enabled:
            return SafetyDecision.deny(ErrorCode.MODE_VIOLATION, "backend reports motion disabled")
        if caps.robot_model != self.robot.model or caps.dof != self.robot.dof:
            return SafetyDecision.deny(ErrorCode.CONFIG_ERROR,
                                       f"backend robot {caps.robot_model}/{caps.dof} != config {self.robot.model}/{self.robot.dof}")
        try:
            problems = controller.check_ready()
        except Exception as e:
            return SafetyDecision.deny(ErrorCode.BACKEND_ERROR, f"readiness check failed: {e}")
        if problems:
            return SafetyDecision.deny(ErrorCode.MODE_VIOLATION, *problems)
        return SafetyDecision.ok(f"backend {name} in simulation mode", "readiness checks passed")

    # 2. robot state ------------------------------------------------------------
    def check_state(self, st: RobotState) -> SafetyDecision:
        if not st.connected:
            return SafetyDecision.deny(ErrorCode.BACKEND_NOT_CONNECTED, "robot backend not connected")
        if st.fault:
            return SafetyDecision.deny(ErrorCode.FAULT_LATCHED, f"robot reports fault: {st.fault}")
        if st.joints is None or len(st.joints.values) != self.robot.dof:
            return SafetyDecision.deny(ErrorCode.STALE_DATA, "joint feedback missing or wrong dof")
        age = state_age_ms(st.sampled_monotonic)
        if age > self.safety.max_state_age_ms:
            return SafetyDecision.deny(ErrorCode.STALE_DATA,
                                       f"robot state is {age:.0f} ms old (max {self.safety.max_state_age_ms:.0f} ms)")
        return SafetyDecision.ok(f"state fresh ({age:.0f} ms)")

    # 4/5. motion segment -------------------------------------------------------
    def check_segment(self, seg: PlannedSegment) -> SafetyDecision:
        if seg.end_joints is None or seg.end_pose is None:
            return SafetyDecision.deny(ErrorCode.UNREACHABLE, f"no IK solution for target '{seg.target_id}'")
        reasons = joints_within_limits(seg.end_joints, self.robot)
        reasons += pose_within_workspace(seg.end_pose, self.safety.workspace, self.robot)
        if reasons:
            return SafetyDecision.deny(ErrorCode.SAFETY_VETO, *reasons)
        if self.safety.require_path_check:
            res = self.path_checker.check(seg)
            if not res.conclusive:
                return SafetyDecision.deny(ErrorCode.PATH_CHECK_INCONCLUSIVE,
                                           f"path check inconclusive for '{seg.target_id}': {res.reason}")
            if not res.ok:
                code = (ErrorCode.COLLISION_PREDICTED if res.collisions else
                        ErrorCode.NEAR_SINGULAR if res.singularity else ErrorCode.UNREACHABLE)
                return SafetyDecision.deny(code, res.reason or f"path to '{seg.target_id}' rejected")
            # The mock has no geometry by design (and says so); digital-twin
            # backends must have tested real collision geometry.
            if self.safety.require_collision_geometry and not res.geometry_checked and self.backend_name != "mock":
                return SafetyDecision.deny(ErrorCode.PATH_CHECK_INCONCLUSIVE,
                                           f"no collision geometry was checked for '{seg.target_id}'")
            return SafetyDecision.ok(f"segment to '{seg.target_id}' ({seg.motion}) within limits; path check: {res.reason or 'ok'}")
        return SafetyDecision.ok(f"segment to '{seg.target_id}' within limits (path check disabled by config)")

    # 6. vision -----------------------------------------------------------------
    def check_detection(self, det: Optional[Detection]) -> SafetyDecision:
        if det is None:
            return SafetyDecision.deny(ErrorCode.PRECONDITION_FAILED, "object not identified; call identify_object first")
        if det.is_mock and not self.safety.allow_mock_detector:
            return SafetyDecision.deny(ErrorCode.SAFETY_VETO, "mock detections are not allowed by safety config")
        if det.confidence < self.safety.min_detection_confidence:
            return SafetyDecision.deny(ErrorCode.SAFETY_VETO,
                                       f"detection confidence {det.confidence:.2f} < {self.safety.min_detection_confidence:.2f}")
        if det.age_s() > self.safety.max_detection_age_s:
            return SafetyDecision.deny(ErrorCode.STALE_DATA,
                                       f"detection is {det.age_s():.1f} s old (max {self.safety.max_detection_age_s} s)")
        if det.pose is not None:
            # No calibrated camera->robot transform in v1: raw camera poses are never used.
            return SafetyDecision.deny(ErrorCode.SAFETY_VETO, "detection carries a camera pose but no calibration is configured")
        return SafetyDecision.ok(f"detection ok (source={det.source}, conf={det.confidence:.2f}"
                                 + (", MOCK" if det.is_mock else "") + "); grasp uses taught named targets")

    # 7. shake --------------------------------------------------------------------
    def check_shake(self, pattern_id: str, pattern, cycles: int, grip_confirmed: bool,
                    payload_kg: Optional[float]) -> SafetyDecision:
        reasons = []
        if cycles < 1:
            reasons.append("cycles must be >= 1")
        if cycles > pattern.max_cycles:
            reasons.append(f"cycles {cycles} > pattern '{pattern_id}' max {pattern.max_cycles}")
        if cycles > self.safety.max_shake_cycles:
            reasons.append(f"cycles {cycles} > safety max {self.safety.max_shake_cycles}")
        if pattern.requires_grip and not grip_confirmed:
            reasons.append("pattern requires a verified grip")
        if payload_kg is None:
            reasons.append("object mass unknown; shaking requires a known payload")
        elif payload_kg > pattern.max_payload_kg:
            reasons.append(f"payload {payload_kg} kg > pattern max {pattern.max_payload_kg} kg")
        if reasons:
            return SafetyDecision.deny(ErrorCode.SAFETY_VETO, *reasons)
        return SafetyDecision.ok(f"pattern '{pattern_id}' x{cycles} within bounds")
