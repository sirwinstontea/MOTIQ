"""RoboDK executor connecting to local RoboDK station and translating primitives to robot movements."""
from __future__ import annotations
import math
from typing import Any, Dict, List, Optional
from .base import BaseExecutor
from ..schemas import StepFeedback

try:
    from robodk import robolink, robomath
    ROBODK_AVAILABLE = True
except ImportError:
    ROBODK_AVAILABLE = False


class RoboDKExecutor(BaseExecutor):
    def __init__(self, robot_name: str = "UR5e", tool_name: str = "Gripper"):
        if not ROBODK_AVAILABLE:
            raise ImportError("RoboDK library is not installed. Run 'pip install robodk' to use RoboDKExecutor.")
        self.robot_name = robot_name
        self.tool_name = tool_name
        self.rdk = None
        self.robot = None
        self.tool = None

    def connect(self) -> bool:
        self.rdk = robolink.Robolink()
        self.robot = self.rdk.Item(self.robot_name, robolink.ITEM_TYPE_ROBOT)
        if not self.robot.Valid():
            raise RuntimeError(f"Robot '{self.robot_name}' not found in the open RoboDK station.")

        self.tool = self.rdk.Item(self.tool_name, robolink.ITEM_TYPE_TOOL)
        self.rdk.setRunMode(robolink.RUNMODE_SIMULATE)
        return True

    def execute(self, primitive: str, args: Dict[str, Any]) -> StepFeedback:
        if not self.rdk or not self.robot:
            self.connect()

        current_pose = self.robot.Pose()

        # Primitives mapping (metri -> millimetri per RoboDK)
        if primitive == "MOVE_DOWN":
            dist_mm = float(args.get("distance_m", 0.05)) * 1000.0
            new_pose = robomath.transl(0, 0, -dist_mm) * current_pose
            self.robot.MoveL(new_pose)

        elif primitive == "MOVE_UP":
            dist_mm = float(args.get("distance_m", 0.05)) * 1000.0
            new_pose = robomath.transl(0, 0, dist_mm) * current_pose
            self.robot.MoveL(new_pose)

        elif primitive == "MOVE_FORWARD":
            dist_mm = float(args.get("distance_m", 0.05)) * 1000.0
            new_pose = robomath.transl(dist_mm, 0, 0) * current_pose
            self.robot.MoveL(new_pose)

        elif primitive == "MOVE_BACKWARD":
            dist_mm = float(args.get("distance_m", 0.05)) * 1000.0
            new_pose = robomath.transl(-dist_mm, 0, 0) * current_pose
            self.robot.MoveL(new_pose)

        elif primitive == "MOVE_RIGHT":
            dist_mm = float(args.get("distance_m", 0.05)) * 1000.0
            new_pose = robomath.transl(0, -dist_mm, 0) * current_pose
            self.robot.MoveL(new_pose)

        elif primitive == "MOVE_LEFT":
            dist_mm = float(args.get("distance_m", 0.05)) * 1000.0
            new_pose = robomath.transl(0, dist_mm, 0) * current_pose
            self.robot.MoveL(new_pose)

        elif primitive == "MOVE_ABOVE":
            target_name = args.get("target")
            height_mm = float(args.get("height_m", 0.15)) * 1000.0
            target_item = self.rdk.Item(target_name)
            if target_item.Valid():
                target_pose = target_item.PoseAbs()
                # Maintain tool orientation facing down while taking target X, Y + approach height Z
                pos = target_pose.Pos()
                approach_pose = robomath.transl(pos[0], pos[1], pos[2] + height_mm) * robomath.rotx(math.pi)
                self.robot.MoveL(approach_pose)

        elif primitive == "OPEN_GRIPPER":
            if self.tool and self.tool.Valid():
                self.tool.DetachAll()

        elif primitive == "CLOSE_GRIPPER":
            if self.tool and self.tool.Valid():
                self.tool.AttachClosest()

        elif primitive == "HOME":
            self.robot.MoveJ([0, -90, 0, -90, 0, 0])

        elif primitive == "WAIT":
            pause_ms = int(float(args.get("seconds", 1.0)) * 1000)
            self.rdk.Pause(pause_ms)

        tcp = self.get_tcp_pose()
        return StepFeedback(success=True, tcp_pose=tcp, message=f"Executed {primitive} in RoboDK")

    def get_tcp_pose(self) -> List[float]:
        if not self.robot:
            return [0.0] * 6
        pose = self.robot.Pose()
        pos = pose.Pos()
        # convert mm to meters for xyz
        return [pos[0] / 1000.0, pos[1] / 1000.0, pos[2] / 1000.0, 0.0, 0.0, 0.0]

    def stop(self) -> None:
        if self.robot:
            self.robot.Stop()
