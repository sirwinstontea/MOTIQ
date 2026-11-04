"""Base executor interface and MockExecutor."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from ..schemas import StepFeedback


class BaseExecutor(ABC):
    @abstractmethod
    def connect(self) -> bool:
        pass

    @abstractmethod
    def execute(self, primitive: str, args: Dict[str, Any]) -> StepFeedback:
        pass

    @abstractmethod
    def get_tcp_pose(self) -> List[float]:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass


class MockExecutor(BaseExecutor):
    """In-memory simulated executor for testing without RoboDK installation."""
    def __init__(self):
        self.connected = False
        self.tcp_pose = [0.40, 0.0, 0.20, 3.1415, 0.0, 0.0]  # x, y, z, rx, ry, rz
        self.gripper_closed = False
        self.execution_log: List[Dict[str, Any]] = []

    def connect(self) -> bool:
        self.connected = True
        return True

    def execute(self, primitive: str, args: Dict[str, Any]) -> StepFeedback:
        if not self.connected:
            self.connect()

        self.execution_log.append({"primitive": primitive, "args": args})

        if primitive == "OPEN_GRIPPER":
            self.gripper_closed = False
            return StepFeedback(success=True, tcp_pose=self.tcp_pose, gripper_closed=False, message="Gripper opened")

        elif primitive == "CLOSE_GRIPPER":
            self.gripper_closed = True
            return StepFeedback(success=True, tcp_pose=self.tcp_pose, gripper_closed=True, message="Gripper closed (object grasped)")

        elif primitive == "MOVE_DOWN":
            dist = float(args.get("distance_m", 0.05))
            self.tcp_pose[2] -= dist
            return StepFeedback(success=True, tcp_pose=self.tcp_pose, gripper_closed=self.gripper_closed, message=f"Moved down {dist}m")

        elif primitive == "MOVE_UP":
            dist = float(args.get("distance_m", 0.05))
            self.tcp_pose[2] += dist
            return StepFeedback(success=True, tcp_pose=self.tcp_pose, gripper_closed=self.gripper_closed, message=f"Moved up {dist}m")

        elif primitive == "MOVE_ABOVE":
            target = args.get("target", "unknown")
            height = float(args.get("height_m", 0.15))
            self.tcp_pose[2] = height
            return StepFeedback(success=True, tcp_pose=self.tcp_pose, gripper_closed=self.gripper_closed, message=f"Moved above {target} at height {height}m")

        return StepFeedback(success=True, tcp_pose=self.tcp_pose, gripper_closed=self.gripper_closed, message=f"Executed {primitive}")

    def get_tcp_pose(self) -> List[float]:
        return list(self.tcp_pose)

    def stop(self) -> None:
        self.connected = False
