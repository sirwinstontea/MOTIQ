"""Backend factory. Physical motion is not available in this build."""
from __future__ import annotations

from ..config import RobotConfig, SceneConfig
from ..controller import RobotController
from ..models import ErrorCode, RobotControlError


def create_controller(robot: RobotConfig, scene: SceneConfig, backend: str | None = None, **kwargs) -> RobotController:
    name = backend or robot.backend
    if name == "mock":
        from .mock import MockController
        return MockController(robot, scene, **kwargs)
    if name == "robodk_sim":
        from .robodk_sim import RoboDKSimController
        return RoboDKSimController(robot, scene, **kwargs)
    if name == "physical_disabled":
        from .physical_disabled import PhysicalDisabledController
        return PhysicalDisabledController(robot)
    raise RobotControlError(ErrorCode.CONFIG_ERROR, f"unknown backend '{name}'")
