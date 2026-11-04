"""Physical robot adapter contract — DISABLED in this build.

Diagnostics and capabilities work; every motion and gripper call raises
``PHYSICAL_BACKEND_DISABLED``. Future manufacturer adapters (e.g. UR RTDE for
UR5e / UR3e as *separate* model configs) belong behind this interface after a
supervised commissioning process. No vendor driver is imported here.
"""
from __future__ import annotations

import time

from ..config import RobotConfig
from ..controller import Capabilities, RobotController, RobotState
from ..models import ErrorCode, FeedbackSource, RobotControlError, utc_now

_MSG = "physical robot motion is disabled in this build (simulation only)"


def _refuse(*_a, **_k):
    raise RobotControlError(ErrorCode.PHYSICAL_BACKEND_DISABLED, _MSG)


class PhysicalDisabledController(RobotController):
    backend_name = "physical_disabled"

    def __init__(self, robot: RobotConfig):
        self.robot = robot

    def connect(self):
        # Deliberately does NOT open any driver/network connection.
        return None

    def disconnect(self):
        return None

    def capabilities(self):
        return Capabilities(backend=self.backend_name, robot_model=self.robot.model, dof=self.robot.dof,
                            simulation=False, motion_enabled=False, gripper="disabled", path_check="none",
                            feedback_source=FeedbackSource.UNVERIFIED,
                            notes=[_MSG, "future: vendor adapter after supervised commissioning"])

    def get_state(self):
        return RobotState(backend=self.backend_name, robot_model=self.robot.model, connected=False,
                          mode="disabled", fault=_MSG, timestamp_utc=utc_now(),
                          sampled_monotonic=time.monotonic(), feedback_source=FeedbackSource.UNVERIFIED)

    def check_ready(self):
        return [_MSG]

    def target_exists(self, target_id):
        return False

    plan_segment = _refuse
    test_segment = _refuse
    move_to_named_target = _refuse
    move_joints = _refuse
    move_linear = _refuse
    open_gripper = _refuse
    close_gripper = _refuse
    verify_grip = _refuse
    get_gripper_state = _refuse

    def stop(self):
        return None
