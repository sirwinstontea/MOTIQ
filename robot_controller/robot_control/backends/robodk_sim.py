"""RoboDK backend — SIMULATION ONLY.

* Never calls ``Connect``/``ConnectSafe``, never generates/uploads/starts robot
  programs, never uses RUNMODE_RUN_ROBOT or any program-generation run mode.
* Sets and verifies ``RUNMODE_SIMULATE`` at connect and before every motion.
* Selects the robot strictly by configured name and verifies its item type.
* Targets are resolved by name from the RoboDK station (operator-taught).
* Gripper is symbolic: closing attaches the configured station object to the
  tool (``setParentStatic``). Feedback is always ``simulated``.

Units: RoboDK uses mm and degrees; this module converts to/from m and rad.
"""
from __future__ import annotations

import math
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from ..config import RobotConfig, SceneConfig
from ..controller import (Capabilities, GripStatus, MotionContext, MotionType, PathCheckResult,
                          PlannedSegment, RobotController, RobotState)
from ..models import ErrorCode, FeedbackSource, Joints, Pose, RobotControlError, utc_now

JOINT_TOL_DEG = 0.5


def _import_robodk():
    try:
        from robodk import robolink, robomath  # type: ignore
        return robolink, robomath
    except ImportError:
        pass
    # Fall back to the API bundled with a default Linux RoboDK install.
    bundled = os.path.expanduser("~/RoboDK/Python")
    if os.path.isdir(bundled) and bundled not in sys.path:
        sys.path.append(bundled)
        try:
            from robodk import robolink, robomath  # type: ignore
            return robolink, robomath
        except ImportError:
            pass
    raise RobotControlError(ErrorCode.BACKEND_NOT_CONNECTED,
                            "RoboDK Python API not importable: `pip install robodk` or keep ~/RoboDK/Python")


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


class RoboDKSimController(RobotController):
    backend_name = "robodk_sim"

    def __init__(self, robot: RobotConfig, scene: SceneConfig, *, port: int = 20500,
                 start_robodk: bool = False, robodk_start_script: str = "~/RoboDK/RoboDK-Start.sh"):
        self.robot_cfg = robot
        self.scene = scene
        self.port = port
        self.start_robodk = start_robodk
        self.start_script = os.path.expanduser(robodk_start_script)
        self.rl = self.rm = None
        self.RDK = None
        self.robot = self.tool = self.base = None
        self.connected = False
        self._stop = threading.Event()
        self._at_target: Optional[str] = None
        self._holding: Optional[str] = None
        self._held_item = None
        self._gripper = "open"
        self._grip_confirmed = False
        self._speeds = None

    # ------------------------------------------------------------------ units
    @staticmethod
    def _deg(rad):
        return [math.degrees(v) for v in rad]

    @staticmethod
    def _rad(deg):
        return [math.radians(v) for v in deg]

    def _pose_from_mat(self, mat) -> Pose:
        x, y, z, rx, ry, rz = self.rm.Pose_2_UR(mat)
        return Pose(x=x / 1000.0, y=y / 1000.0, z=z / 1000.0, rx=rx, ry=ry, rz=rz, frame_id="base")

    def _mat_from_pose(self, p: Pose):
        return self.rm.UR_2_Pose([p.x * 1000.0, p.y * 1000.0, p.z * 1000.0, p.rx, p.ry, p.rz])

    # ------------------------------------------------------------ lifecycle
    def connect(self):
        self.rl, self.rm = _import_robodk()
        rl = self.rl
        if not _port_open("127.0.0.1", self.port):
            if not self.start_robodk:
                raise RobotControlError(
                    ErrorCode.BACKEND_NOT_CONNECTED,
                    f"RoboDK GUI is not running on 127.0.0.1:{self.port}. Start it with "
                    f"{self.start_script} (or pass --start-robodk).")
        try:
            kwargs = {"port": self.port}
            if self.start_robodk:
                kwargs["robodk_path"] = self.start_script  # launcher sets LD_LIBRARY_PATH
            self.RDK = rl.Robolink(**kwargs)
        except Exception as e:
            raise RobotControlError(ErrorCode.BACKEND_NOT_CONNECTED, f"cannot connect to RoboDK: {e}") from e
        # The API is many small request/response calls. With Nagle's algorithm
        # each call stalls ~40 ms on delayed ACKs (measured 41 ms -> 0.2 ms),
        # which made the robot hesitate ~1 s before every move.
        try:
            self.RDK.COM.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (AttributeError, OSError):
            pass

        self._enforce_simulate()
        self._select_robot()
        self.connected = True

    def _enforce_simulate(self):
        rl = self.rl
        self.RDK.setRunMode(rl.RUNMODE_SIMULATE)
        mode = self.RDK.RunMode()
        if mode != rl.RUNMODE_SIMULATE:
            raise RobotControlError(ErrorCode.MODE_VIOLATION, f"RoboDK run mode is {mode}, expected RUNMODE_SIMULATE")

    def _select_robot(self):
        rl = self.rl
        name = self.robot_cfg.robot_item_name
        # Item lookups are scoped to the active station: activate ours if open.
        if self.robot_cfg.station_name:
            for stn in self.RDK.ItemList(rl.ITEM_TYPE_STATION):
                if stn.Name() == self.robot_cfg.station_name:
                    self.RDK.setActiveStation(stn)
                    break
        robots = [r for r in self.RDK.ItemList(rl.ITEM_TYPE_ROBOT) if r.Name() == name]
        if not robots and self.robot_cfg.station_path:
            path = Path(self.robot_cfg.station_path).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path
            if path.is_file():
                self.RDK.AddFile(str(path.resolve()))
                robots = [r for r in self.RDK.ItemList(rl.ITEM_TYPE_ROBOT) if r.Name() == name]
        if not robots:
            raise RobotControlError(
                ErrorCode.CONFIG_ERROR,
                f"no robot named '{name}' in the RoboDK station. Run `python -m robot_control.cli "
                f"setup-station` or load your station ({self.robot_cfg.station_path}).")
        if len(robots) > 1:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, f"{len(robots)} robots are named '{name}'; names must be unique")
        robot = robots[0]
        if robot.Type() != rl.ITEM_TYPE_ROBOT:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, f"item '{name}' is not a robot")
        self.robot = robot

        tool = self.RDK.Item(self.robot_cfg.tool_frame, rl.ITEM_TYPE_TOOL)
        if not tool.Valid() or tool.Parent() != robot:
            raise RobotControlError(ErrorCode.CONFIG_ERROR,
                                    f"tool '{self.robot_cfg.tool_frame}' not found on robot '{name}'")
        base = self.RDK.Item(self.robot_cfg.base_frame, rl.ITEM_TYPE_FRAME)
        if not base.Valid() or robot.Parent() != base:
            raise RobotControlError(ErrorCode.CONFIG_ERROR,
                                    f"base frame '{self.robot_cfg.base_frame}' is not the parent of '{name}'")
        self.tool, self.base = tool, base
        robot.setPoseTool(tool)
        robot.setPoseFrame(base)

    def disconnect(self):
        self.connected = False
        self.RDK = None

    def capabilities(self):
        geom = self.connected and self._has_collision_geometry()
        return Capabilities(backend=self.backend_name, robot_model=self.robot_cfg.model, dof=self.robot_cfg.dof,
                            simulation=True, motion_enabled=self.connected, gripper="symbolic",
                            path_check="collision" if geom else "feasibility_only",
                            feedback_source=FeedbackSource.SIMULATED,
                            notes=["RoboDK digital twin; collision results are not proof of physical clearance",
                                   "gripper is symbolic (object attach/detach in simulation)"])

    # ------------------------------------------------------------- state
    def _require(self):
        if not self.connected or self.RDK is None:
            raise RobotControlError(ErrorCode.BACKEND_NOT_CONNECTED, "RoboDK backend not connected")

    def get_state(self):
        if not self.connected:
            return RobotState(backend=self.backend_name, robot_model=self.robot_cfg.model, connected=False,
                              mode="simulation", timestamp_utc=utc_now(), sampled_monotonic=time.monotonic(),
                              feedback_source=FeedbackSource.SIMULATED)
        try:
            jdeg = self.robot.Joints().list()
            pose = self._pose_from_mat(self.robot.Pose())   # tool w.r.t. active frame (= base)
            busy = bool(self.robot.Busy())
        except Exception as e:
            raise RobotControlError(ErrorCode.BACKEND_ERROR, f"RoboDK state read failed: {e}") from e
        return RobotState(backend=self.backend_name, robot_model=self.robot_cfg.model, connected=True,
                          mode="simulation", joints=Joints(values=self._rad(jdeg)), tcp_pose=pose,
                          gripper_state=self._gripper, holding_object_id=self._holding,
                          grip_confirmed=self._grip_confirmed, fault=None, busy=busy, timestamp_utc=utc_now(),
                          sampled_monotonic=time.monotonic(), feedback_source=FeedbackSource.SIMULATED)

    def _has_collision_geometry(self) -> bool:
        objs = [o for o in self.RDK.ItemList(self.rl.ITEM_TYPE_OBJECT) if o.Visible()]
        return len(objs) > 0

    def check_ready(self):
        if not self.connected:
            return ["RoboDK backend not connected"]
        rl = self.rl
        problems = []
        try:
            if self.RDK.RunMode() != rl.RUNMODE_SIMULATE:
                problems.append("RoboDK run mode is not RUNMODE_SIMULATE")
            state, msg = self.robot.ConnectedState()
            if state in (rl.ROBOTCOM_READY, rl.ROBOTCOM_WORKING, rl.ROBOTCOM_WAITING):
                problems.append(f"robot driver connection detected ({msg}); refusing (simulation-only build)")
            if self.robot.getLink(rl.ITEM_TYPE_TOOL) != self.tool:
                problems.append("active tool changed")
            if self.robot.getLink(rl.ITEM_TYPE_FRAME) != self.base:
                problems.append("active reference frame changed")
            if len(self.robot.Joints().list()) != self.robot_cfg.dof:
                problems.append("robot DOF does not match config")
        except Exception as e:
            problems.append(f"RoboDK readiness query failed: {e}")
        return problems

    def show_status(self, text):
        if self.connected:
            try:
                self.RDK.ShowMessage(text, False)       # status bar, no popup
            except Exception:
                pass

    def apply_motion_limits(self, tcp_speed_m_s, tcp_accel_m_s2, joint_speed_rad_s, joint_accel_rad_s2):
        self._speeds = (tcp_speed_m_s * 1000.0, math.degrees(joint_speed_rad_s),
                        tcp_accel_m_s2 * 1000.0, math.degrees(joint_accel_rad_s2))
        if self.connected:
            self.robot.setSpeed(*self._speeds)
            self.robot.setRounding(0)

    # ------------------------------------------------------------ planning
    def _target_item(self, target_id):
        t = self.RDK.Item(target_id, self.rl.ITEM_TYPE_TARGET)
        if not t.Valid() or t.Name() != target_id:
            return None
        return t

    def target_exists(self, target_id):
        self._require()
        return self._target_item(target_id) is not None

    def plan_segment(self, target_id, motion: MotionType, start_joints=None):
        self._require()
        t = self._target_item(target_id)
        if t is None:
            raise RobotControlError(ErrorCode.UNKNOWN_ID, f"target '{target_id}' not found in RoboDK station")
        start_deg = self._deg(start_joints) if start_joints is not None else self.robot.Joints().list()
        tool_pose = self.tool.PoseTool()
        if t.isJointTarget():
            end_deg = t.Joints().list()
        else:
            # Target pose w.r.t. robot base, then IK closest to the start joints
            # (keeps arm configuration; avoids flips through the workspace).
            pose_base = self.base.PoseAbs().inv() * t.PoseAbs()
            sol = self.robot.SolveIK(pose_base, start_deg, tool_pose)
            end_deg = sol.list() if len(sol.list()) == self.robot_cfg.dof else None
        if end_deg is None:
            return PlannedSegment(target_id, motion, self._rad(start_deg), None, None)
        end_pose = self._pose_from_mat(self.robot.SolveFK(end_deg, tool_pose))
        return PlannedSegment(target_id, motion, self._rad(start_deg), self._rad(end_deg), end_pose)

    def test_segment(self, seg):
        """MoveJ_Test / MoveL_Test move the simulated robot: snapshot and restore."""
        self._require()
        rl = self.rl
        if seg.end_joints is None:
            return PathCheckResult(ok=False, conclusive=True, reason="unreachable")
        # Without geometry a "0 collisions" answer means nothing; the policy
        # rejects such results when require_collision_geometry is set.
        has_geom = self._has_collision_geometry()
        snapshot = self.robot.Joints().list()
        # Freeze the 3D view: the test sweeps the simulated robot along the path
        # and we put it back afterwards; without this the arm visibly jumps.
        self.RDK.Render(False)
        try:
            self.RDK.setCollisionActive(rl.COLLISION_ON)
            start = self._deg(seg.start_joints)
            end = self._deg(seg.end_joints)
            if seg.motion == "joint":
                res = self.robot.MoveJ_Test(start, end, 1.0)
            else:
                end_pose_mm = self.robot.SolveFK(end, self.tool.PoseTool())
                res = self.robot.MoveL_Test(start, end_pose_mm, 2.0)
        finally:
            self.robot.setJoints(snapshot)
            self.RDK.Render(True)
        restored = self.robot.Joints().list()
        if max(abs(a - b) for a, b in zip(restored, snapshot)) > 1e-3:
            return PathCheckResult(ok=False, conclusive=False, reason="could not restore robot after path test")
        if res == 0:
            return PathCheckResult(ok=True, conclusive=True, geometry_checked=has_geom,
                                   reason=f"RoboDK {seg.motion} path test: 0 collisions (digital twin only)"
                                   + ("" if has_geom else "; NO collision geometry in station"))
        if res > 0:
            return PathCheckResult(ok=False, conclusive=True, collisions=res, geometry_checked=has_geom,
                                   reason=f"RoboDK predicts {res} colliding pair(s) on path to '{seg.target_id}'")
        why = {-1: "linear move not feasible (singularity/limits)", -2: "target not reachable"}.get(res, f"code {res}")
        return PathCheckResult(ok=False, conclusive=True, reason=f"RoboDK: {why}")

    # ------------------------------------------------------------ motion
    def _execute(self, end_deg, motion: MotionType, ctx: MotionContext, label: str):
        self._require()
        self._enforce_simulate()            # execution boundary
        problems = self.check_ready()
        if problems:
            raise RobotControlError(ErrorCode.MODE_VIOLATION, "; ".join(problems))
        if self._speeds:
            self.robot.setSpeed(*self._speeds)
        self._stop.clear()
        try:
            if motion == "joint":
                self.robot.MoveJ(end_deg, False)
            else:
                self.robot.MoveL(end_deg, False)
        except Exception as e:
            raise RobotControlError(ErrorCode.BACKEND_ERROR, f"RoboDK rejected move to {label}: {e}") from e
        deadline = time.monotonic() + ctx.timeout_s
        time.sleep(0.05)
        while True:
            try:
                busy = self.robot.Busy()
            except Exception as e:
                raise RobotControlError(ErrorCode.BACKEND_ERROR, f"RoboDK error during move to {label}: {e}") from e
            if not busy:
                break
            if ctx.cancel.is_set() or self._stop.is_set() or time.monotonic() > deadline:
                self.robot.Stop()
                if ctx.cancel.is_set():
                    raise RobotControlError(ErrorCode.CANCELLED, f"move to {label} cancelled")
                if self._stop.is_set():
                    raise RobotControlError(ErrorCode.STOPPED, f"move to {label} stopped")
                raise RobotControlError(ErrorCode.TIMEOUT, f"move to {label} exceeded {ctx.timeout_s:.1f} s")
            time.sleep(0.02)
        # Feedback reconciliation: did the simulated robot actually arrive?
        actual = self.robot.Joints().list()
        err = max(abs(a - b) for a, b in zip(actual, end_deg))
        if err > JOINT_TOL_DEG:
            raise RobotControlError(ErrorCode.BACKEND_ERROR,
                                    f"robot did not reach {label} (joint error {err:.2f} deg; collision or stop?)")

    def move_to_named_target(self, seg, ctx):
        if seg.end_joints is None:
            raise RobotControlError(ErrorCode.UNREACHABLE, f"target '{seg.target_id}' unreachable")
        self._execute(self._deg(seg.end_joints), seg.motion, ctx, f"'{seg.target_id}'")
        self._at_target = seg.target_id

    def move_joints(self, joints_rad, ctx):
        self._execute(self._deg(joints_rad), "joint", ctx, "joint position")
        self._at_target = None

    def move_linear(self, pose, ctx):
        sol = self.robot.SolveIK(self._mat_from_pose(pose), self.robot.Joints(), self.tool.PoseTool())
        if len(sol.list()) != self.robot_cfg.dof:
            raise RobotControlError(ErrorCode.UNREACHABLE, "linear target unreachable")
        self._execute(sol.list(), "linear", ctx, "linear pose")
        self._at_target = None

    # ------------------------------------------------------------ gripper (symbolic)
    def _status(self):
        return GripStatus(self._gripper, self._holding, self._grip_confirmed, FeedbackSource.SIMULATED)

    def open_gripper(self):
        self._require()
        if self._held_item is not None:
            self._held_item.setParentStatic(self.base)   # object stays where it was released
        self._held_item = None
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
            self._holding = None          # closed on nothing
            return self._status()
        item = self.RDK.Item(obj.station_item, self.rl.ITEM_TYPE_OBJECT)
        if not item.Valid():
            raise RobotControlError(ErrorCode.CONFIG_ERROR, f"station object '{obj.station_item}' not found")
        # Only "grip" if the simulated object is really between the fingers:
        # object origin is its bottom centre, so compare TCP with its middle.
        tcp = self.base.PoseAbs() * self.robot.Pose()       # active frame is the base
        centre = item.PoseAbs() * self.rm.transl(0, 0, obj.dimensions_m[2] * 500.0)
        dist_mm = self.rm.norm(self.rm.subs3(tcp.Pos(), centre.Pos()))
        if dist_mm > 15.0:
            self._holding = None          # closed on nothing: object is not here
            return self._status()
        item.setParentStatic(self.tool)
        self._held_item = item
        self._holding = object_id
        return self._status()

    def verify_grip(self):
        self._require()
        self._grip_confirmed = bool(self._held_item is not None and self._held_item.Parent() == self.tool
                                    and self._gripper == "closed")
        if not self._grip_confirmed:
            self._holding = None
        return self._status()

    def get_gripper_state(self):
        return self._status()

    def stop(self):
        self._stop.set()
