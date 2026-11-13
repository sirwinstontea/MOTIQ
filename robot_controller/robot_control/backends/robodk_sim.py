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

import logging
from contextlib import contextmanager
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
from ..planning import DIRECTIONS, table_surface
from ..performance import increment, measure, timed

log = logging.getLogger(__name__)

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
        self._transient_poses = {}  # task-local pick/place/push targets; never mutate taught station targets

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
        self.ensure_table_collision_pairs()
        self._add_missing_targets()
        self._hide_markers()
        self.connected = True

    def _add_missing_targets(self):
        """Scene targets added after the station was built (e.g. the arm camera's scan poses) are created here,
        exactly as `setup-station` would. Existing targets are never moved or changed."""
        rl = self.rl
        added = []
        for tname, t in self.scene.targets.items():
            if self._target_item(tname) is not None:
                continue
            tgt = self.RDK.AddTarget(tname, self.base, self.robot)
            if t.joints_rad is not None:
                tgt.setAsJointTarget()
                tgt.setJoints([math.degrees(q) for q in t.joints_rad])
            else:
                p = t.pose_m_rad
                tgt.setAsCartesianTarget()
                tgt.setPose(self.rm.UR_2_Pose([p[0] * 1000, p[1] * 1000, p[2] * 1000, p[3], p[4], p[5]]))
            tgt.setVisible(False)
            added.append(tname)
        if added:
            log.info("added scene targets missing from the station: %s", ", ".join(added))

    def _hide_markers(self):
        """Hide target arrows and frame/tool XYZ axes. Targets stay usable; objects stay visible."""
        rl = self.rl
        self.RDK.Render(False)
        try:
            for tgt in self.RDK.ItemList(rl.ITEM_TYPE_TARGET):
                tgt.setVisible(False)
            for it in self.RDK.ItemList(rl.ITEM_TYPE_FRAME) + self.RDK.ItemList(rl.ITEM_TYPE_TOOL):
                it.setVisible(True, False)          # keep the item (and children), hide its axes
        except Exception as e:                      # cosmetic only: never block a connect over it
            log.warning("could not hide RoboDK markers: %s", e)
        finally:
            self.RDK.Render(True)

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
                            cartesian_relative=True, joint_motion=True,
                            reference_frames=["base", "tool", "world"],
                            notes=["RoboDK digital twin; collision results are not proof of physical clearance",
                                   "gripper is symbolic (object attach/detach in simulation)"])

    # ------------------------------------------------------------- state
    def _require(self):
        if not self.connected or self.RDK is None:
            raise RobotControlError(ErrorCode.BACKEND_NOT_CONNECTED, "RoboDK backend not connected")

    @timed("state_read")
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

    @timed("backend_readiness")
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
            lower_mat, upper_mat, _joint_type = self.robot.JointLimits()
            lower, upper = lower_mat.list(), upper_mat.list()
            if len(lower) != self.robot_cfg.dof or len(upper) != self.robot_cfg.dof:
                problems.append("RoboDK joint limits are unavailable or have the wrong DOF")
            else:
                cfg_lo, cfg_hi = self.robot_cfg.limits.joint_min_rad, self.robot_cfg.limits.joint_max_rad
                model_lo, model_hi = self._rad(lower), self._rad(upper)
                for i, (cl, ch, ml, mh) in enumerate(zip(cfg_lo, cfg_hi, model_lo, model_hi), 1):
                    if cl < ml - 1e-3 or ch > mh + 1e-3:
                        problems.append(f"configured joint {i} limits exceed the RoboDK model limits")
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

    def retarget_pick(self, object_id):
        """Read simulator geometry (not a camera detection) and resolve task-local approach / grasp / lift targets
        over it. Each target keeps its taught orientation and its taught height above the object's bottom (taught
        with the object standing on the table, z = 0), so only where the object is changes. Skipped (None) when the
        object is held or has no station item. The moved targets still go through the normal IK + safety checks."""
        self._require()
        obj = self.scene.objects.get(object_id)
        if obj is None or not obj.station_item:
            return None
        item = self.RDK.Item(obj.station_item, self.rl.ITEM_TYPE_OBJECT)
        if not item.Valid() or item.Parent() == self.tool:
            return None
        base_abs = self.base.PoseAbs()
        ox, oy, oz = [v / 1000.0 for v in (base_abs.inv() * item.PoseAbs()).Pos()]
        for tid in (obj.approach_target, obj.grasp_target, obj.lift_target):
            cfg = self.scene.targets.get(tid)
            tgt = self._target_item(tid)
            if cfg is None or cfg.pose_m_rad is None or tgt is None:
                continue
            p = [ox, oy, cfg.pose_m_rad[2] + oz, *cfg.pose_m_rad[3:]]
            pose_base = self.rm.UR_2_Pose([p[0] * 1000, p[1] * 1000, p[2] * 1000, p[3], p[4], p[5]])
            self._transient_poses[tid] = self._pose_from_mat(pose_base)
        return [round(ox, 4), round(oy, 4), round(oz, 4)]

    @timed("planning")
    def plan_segment(self, target_id, motion: MotionType, start_joints=None):
        self._require()
        if target_id in self._transient_poses:
            seg = self.plan_pose(self._transient_poses[target_id], start_joints)
            seg.target_id = target_id
            seg.motion = motion
            return seg
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

    @staticmethod
    def _rotate_vector(mat, vector):
        return [sum(float(mat[i, j]) * vector[j] for j in range(3)) for i in range(3)]

    @timed("planning")
    def plan_cartesian_relative(self, direction, distance_m, reference_frame, start_joints=None):
        self._require()
        start_deg = self._deg(start_joints) if start_joints is not None else self.robot.Joints().list()
        if len(start_deg) != self.robot_cfg.dof:
            raise RobotControlError(ErrorCode.VALIDATION_ERROR, "start joint count does not match robot DOF")
        tool_pose = self.tool.PoseTool()
        current = self.robot.SolveFK(start_deg, tool_pose)
        vectors = {"forward": [1.0, 0.0, 0.0], "backward": [-1.0, 0.0, 0.0],
                   "left": [0.0, 1.0, 0.0], "right": [0.0, -1.0, 0.0],
                   "up": [0.0, 0.0, 1.0], "down": [0.0, 0.0, -1.0]}
        v = vectors[direction]
        if reference_frame == "tool":
            v = self._rotate_vector(current, v)
        elif reference_frame == "world":
            v = self._rotate_vector(self.base.PoseAbs().inv(), v)
        elif reference_frame != "base":
            raise RobotControlError(ErrorCode.VALIDATION_ERROR, f"unknown reference frame '{reference_frame}'")
        target = current.copy()
        pos = current.Pos()
        target.setPos([pos[i] + distance_m * 1000.0 * v[i] for i in range(3)])
        sol = self.robot.SolveIK(target, start_deg, tool_pose)
        end_deg = sol.list() if len(sol.list()) == self.robot_cfg.dof else None
        if end_deg is None:
            return PlannedSegment(f"relative_{direction}", "linear", self._rad(start_deg), None, None,
                                  requested={"direction": direction, "distance_m": distance_m,
                                             "reference_frame": reference_frame})
        return PlannedSegment(f"relative_{direction}", "linear", self._rad(start_deg), self._rad(end_deg),
                              self._pose_from_mat(target), requested={"direction": direction,
                              "distance_m": distance_m, "reference_frame": reference_frame})

    @timed("planning")
    def plan_joint_move(self, joint_index, *, delta_rad=None, target_rad=None, start_joints=None):
        self._require()
        if not 1 <= joint_index <= self.robot_cfg.dof:
            raise RobotControlError(ErrorCode.VALIDATION_ERROR,
                                    f"joint_index {joint_index} outside 1..{self.robot_cfg.dof}")
        start = list(start_joints) if start_joints is not None else self._rad(self.robot.Joints().list())
        end = list(start)
        end[joint_index - 1] = target_rad if target_rad is not None else end[joint_index - 1] + delta_rad
        end_pose = self._pose_from_mat(self.robot.SolveFK(self._deg(end), self.tool.PoseTool()))
        return PlannedSegment(f"joint_{joint_index}", "joint", start, end, end_pose,
                              requested={"joint_index": joint_index, "delta_rad": delta_rad,
                                         "target_rad": target_rad})

    @timed("pose_ik")
    def plan_pose(self, pose, start_joints=None):
        self._require()
        start = list(start_joints) if start_joints is not None else self.get_joints().values
        target = self._mat_from_pose(pose)
        sol = self.robot.SolveIK(target, self._deg(start), self.tool.PoseTool()).list()
        end = self._rad(sol) if len(sol) == self.robot_cfg.dof else None
        if end is not None:
            actual = self.robot.SolveFK(sol, self.tool.PoseTool())
            rotation_trace = sum(actual[i, j] * target[i, j] for i in range(3) for j in range(3))
            angle = math.acos(max(-1.0, min(1.0, (rotation_trace - 1) / 2)))
            if math.dist(actual.Pos(), target.Pos()) > 0.5 or angle > 1e-3:
                raise RobotControlError(ErrorCode.UNREACHABLE,
                                        "IK does not preserve requested position/orientation (unsupported pose or excessive residual)")
        return PlannedSegment("waypoint", "linear", start, end, pose if end else None,
                              requested={"pose": pose.model_dump(mode="json")})

    def object_position(self, object_id):
        obj = self.scene.objects[object_id]
        if not obj.station_item:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, "object has no station_item")
        item = self.RDK.Item(obj.station_item, self.rl.ITEM_TYPE_OBJECT)
        if not item.Valid():
            raise RobotControlError(ErrorCode.CONFIG_ERROR, f"station object '{obj.station_item}' missing")
        return [v / 1000 for v in (self.base.PoseAbs().inv() * item.PoseAbs()).Pos()]

    def _upright_object(self, object_id):
        obj = self.scene.objects[object_id]
        item = self.RDK.Item(obj.station_item, self.rl.ITEM_TYPE_OBJECT)
        if not item.Valid():
            raise RobotControlError(ErrorCode.CONFIG_ERROR, f"station object '{obj.station_item}' missing")
        mat = self.base.PoseAbs().inv() * item.PoseAbs()
        if abs(mat[2, 2] - 1) > 1e-5:
            raise RobotControlError(ErrorCode.UNSUPPORTED, "placement requires an upright object; tilted support is not modeled")
        return mat, math.atan2(mat[1, 0], mat[0, 0])

    def _validated_table(self):
        cfg = self.scene.station_geometry.get(self.scene.table_geometry)
        if cfg is None:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, "table geometry missing")
        item = self.RDK.Item(self.scene.table_geometry, self.rl.ITEM_TYPE_OBJECT)
        if not item.Valid():
            raise RobotControlError(ErrorCode.CONFIG_ERROR, "table missing in RoboDK")
        actual = self._pose_from_mat(self.base.PoseAbs().inv() * item.PoseAbs()).as_list()
        if any(abs(a-b) > 1e-5 for a,b in zip(actual, cfg.pose_m_rad)):
            raise RobotControlError(ErrorCode.CONFIG_ERROR, "RoboDK table pose does not match scene configuration in robot base frame")
        return cfg

    def _require_object_table_pair(self, item):
        table = self.RDK.Item(self.scene.table_geometry, self.rl.ITEM_TYPE_OBJECT)
        if not table.Valid() or not item.Valid():
            raise RobotControlError(ErrorCode.CONFIG_ERROR, "required object/table collision geometry is missing")
        if not any((a == table and b == item) or (a == item and b == table)
                   for a, b, _, _ in self.RDK.CollisionActivePairList()):
            raise RobotControlError(ErrorCode.CONFIG_ERROR,
                                    f"'{item.Name()}'/'{self.scene.table_geometry}' collision pair is disabled; "
                                    "enable it in the RoboDK collision map before pickup or carried-object motion")

    def _station_setup_guard(self):
        """Configuration edits only, never switch mode or connect a driver here."""
        if self.RDK.RunMode() != self.rl.RUNMODE_SIMULATE:
            raise RobotControlError(ErrorCode.MODE_VIOLATION, "station preparation requires RUNMODE_SIMULATE")
        if self.robot.ConnectedState()[0] != self.rl.ROBOTCOM_DISCONNECTED:
            raise RobotControlError(ErrorCode.MODE_VIOLATION, "station preparation requires a disconnected robot driver")
        if self.robot.Busy():
            raise RobotControlError(ErrorCode.BUSY, "station preparation refused while robot is moving")

    def _station_objects(self):
        items = self.RDK.ItemList(self.rl.ITEM_TYPE_OBJECT)
        result = []
        for oid, obj in self.scene.objects.items():
            if not obj.station_item:
                continue
            matches = [i for i in items if i.Name() == obj.station_item]
            if len(matches) != 1:
                raise RobotControlError(ErrorCode.CONFIG_ERROR,
                                        f"expected one station object '{obj.station_item}', found {len(matches)}")
            result.append((oid, matches[0]))
        return result

    def ensure_table_collision_pairs(self):
        """Idempotent startup policy: enable protection, never disable other pairs or move geometry.

        Saved legacy stations may predate the protected-table import policy. Reapply it on each connection
        rather than relying on an operator saving a one-off map edit. Partial updates fail closed.
        """
        self._station_setup_guard()
        self._validated_table()
        table = self.RDK.Item(self.scene.table_geometry, self.rl.ITEM_TYPE_OBJECT)
        objects = self._station_objects()
        original = self._collision_pairs()
        required = [(item, table, 0, 0) for _, item in objects]
        before = self._pair_keys(original)
        missing = [p for p in required if not self._pair_keys([p]).issubset(before)]
        if missing:
            self._write_collision_pairs(self.rl.COLLISION_ON, missing)
        self._verify_collision_map(before | self._pair_keys(required))
        log.info("protected object/table collision pairs verified (%d enabled)", len(missing))
        return len(missing)

    def repair_resting_contacts(self):
        """Explicit offline station edit, NOT a robot move or motion-planning fallback.

        Raise only unheld upright objects within 0.5 mm of table contact to the same 1 mm clearance
        used by table placement. Refuse deeper penetration, attached objects or ambiguous geometry.
        Preserve XY, orientation and parents. Validate all candidates before editing; roll back on failure.
        """
        self._station_setup_guard()
        table_cfg = self._validated_table()
        top = table_cfg.pose_m_rad[2] + table_cfg.size_m[2]
        table = self.RDK.Item(self.scene.table_geometry, self.rl.ITEM_TYPE_OBJECT)
        plans = []
        objects = self._station_objects()
        for oid, item in objects:
            parent = item.Parent()
            while parent.Valid():
                if parent.Type() in (self.rl.ITEM_TYPE_ROBOT, self.rl.ITEM_TYPE_TOOL):
                    raise RobotControlError(ErrorCode.PRECONDITION_FAILED,
                                            f"'{oid}' is attached to a robot/tool; station repair refused")
                parent = parent.Parent()
            relative, yaw = self._upright_object(oid)
            x, y, z = (v / 1000 for v in relative.Pos())
            table_surface(self.scene, x, y, self.scene.objects[oid].dimensions_m, yaw)
            if z < top - 0.0005:
                raise RobotControlError(ErrorCode.SAFETY_VETO,
                                        f"'{oid}' penetrates the table; requires operator geometry repair")
            if z <= top + 0.0005:
                original = item.PoseAbs()
                desired = self.base.PoseAbs() * self.rm.transl(0, 0, (top + 0.001 - z) * 1000) * relative
                plans.append((oid, item, original, desired))
        try:
            for _, item, _, desired in plans:
                self._station_setup_guard()
                item.setPoseAbs(desired)
            for oid, item in objects:
                if self.RDK.Collision(item, table):
                    raise RobotControlError(ErrorCode.SAFETY_VETO,
                                            f"'{oid}' still collides with table after resting-contact repair")
        except Exception as error:
            rollback_errors = []
            for _, item, original, _ in plans:
                try:
                    item.setPoseAbs(original)
                except Exception as rollback_error:
                    rollback_errors.append(str(rollback_error))
            if rollback_errors:
                raise RobotControlError(ErrorCode.BACKEND_ERROR,
                                        f"station repair failed ({error}); pose rollback incomplete: {rollback_errors}") from error
            raise
        return [oid for oid, _, _, _ in plans]

    def validate_pick(self, object_id):
        self._require()
        self._validated_table()
        obj = self.scene.objects.get(object_id)
        if obj is None or not obj.station_item:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, f"'{object_id}' has no configured station object")
        self._require_object_table_pair(self.RDK.Item(obj.station_item, self.rl.ITEM_TYPE_OBJECT))

    def _require_held_table_pair(self):
        for item in (getattr(self, "_held_item", None), getattr(self, "_pushed_item", None)):
            if item is not None:
                self._require_object_table_pair(item)

    def table_center(self):
        return tuple(self._validated_table().pose_m_rad[:2])

    def scene_observation(self):
        self._require()
        self._validated_table()
        objects = {}
        for oid, obj in self.scene.objects.items():
            if obj.station_item:
                position = self.object_position(oid)
                table = self.scene.station_geometry[self.scene.table_geometry]
                objects[oid] = {"position_m": position, "position_source": "robodk_station_geometry",
                                "table_clearance_m": position[2] - table.pose_m_rad[2] - table.size_m[2]}
        return {"objects": objects, "position_source": "robodk_station_geometry", "camera_verified": False}

    def table_placement_pose(self, object_id, x_m, y_m):
        obj = self.scene.objects[object_id]
        self._validated_table()
        _, yaw = self._upright_object(object_id)
        top = table_surface(self.scene, x_m, y_m, obj.dimensions_m, yaw)
        position = self.object_position(object_id)
        current = self.get_tcp_pose()
        return current.model_copy(update={"x": current.x + x_m - position[0],
            "y": current.y + y_m - position[1], "z": current.z + top + 0.001 - position[2]})

    def _destination_bottom(self, object_id, destination_id):
        self._validated_table()
        d = self.scene.destinations[destination_id]
        obj = self.scene.objects[object_id]
        _, yaw = self._upright_object(object_id)
        if d.support_surface == "container":
            if d.container_id == object_id:
                raise RobotControlError(ErrorCode.PRECONDITION_FAILED, "cannot place a container into itself")
            anchor = self.scene.objects[d.container_id]
            _, anchor_yaw = self._upright_object(d.container_id)
            x, y, z = self.object_position(d.container_id)
            top = table_surface(self.scene, x, y, anchor.dimensions_m, anchor_yaw)
            if z < top - 0.0005:
                raise RobotControlError(ErrorCode.SAFETY_VETO, "destination container already intersects the table; correct the station before retrying")
            table_surface(self.scene, x, y, obj.dimensions_m, yaw)
            # Symbolic release ABOVE the opening; no gravity/interior physics is claimed.
            return [x, y, z + anchor.dimensions_m[2] + 0.001]
        if d.support_surface == "table":
            taught = self.scene.targets[d.place_target].pose_m_rad
            if taught is None:
                raise RobotControlError(ErrorCode.CONFIG_ERROR, "table destination requires a Cartesian taught position")
            x, y = taught[:2]
            return [x, y, table_surface(self.scene, x, y, obj.dimensions_m, yaw) + 0.001]
        raise RobotControlError(ErrorCode.UNSUPPORTED, "destination has only legacy taught semantics")

    def retarget_place(self, object_id, destination_id):
        d = self.scene.destinations[destination_id]
        if d.support_surface == "taught":
            return False
        if self._holding != object_id:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED, "live placement requires the actual held object")
        bottom = self._destination_bottom(object_id, destination_id)
        position = self.object_position(object_id)
        tcp = self.get_tcp_pose()
        target = tcp.model_copy(update={axis: getattr(tcp, axis) + bottom[i] - position[i]
                                       for i, axis in enumerate(("x", "y", "z"))})
        place = self.scene.targets[d.place_target].pose_m_rad
        for tid in dict.fromkeys((d.approach_target, d.place_target, d.retreat_target)):
            taught = self.scene.targets[tid].pose_m_rad
            if place is None or taught is None:
                raise RobotControlError(ErrorCode.CONFIG_ERROR, "live placement requires Cartesian taught clearances")
            clearance = taught[2] - place[2]
            if tid != d.place_target and clearance <= 0:
                raise RobotControlError(ErrorCode.CONFIG_ERROR, "placement approach/retreat clearance must be positive")
            self._transient_poses[tid] = target.model_copy(update={"z": target.z + clearance})
        return True

    def verify_placement(self, object_id, destination_id):
        expected = self._destination_bottom(object_id, destination_id)
        actual = self.object_position(object_id)
        ok = self._holding != object_id and math.dist(actual, expected) <= 0.005
        return {"ok": ok, "object_id": object_id, "destination_id": destination_id,
                "actual_position_m": actual, "expected_position_m": expected,
                "verification_source": "robodk_station_geometry", "camera_verified": False,
                "note": "symbolic release above container; no gravity/interior confirmation" if
                self.scene.destinations[destination_id].support_surface == "container" else "geometric table placement check"}

    def prepare_push(self, object_id, direction, distance_m):
        obj = self.scene.objects[object_id]
        if obj.avoid_collision:
            raise RobotControlError(ErrorCode.SAFETY_VETO, "object is tagged avoid_collision; contact push refused")
        if obj.push is None:
            raise RobotControlError(ErrorCode.UNSUPPORTED, "object has no configured push posture")
        position = self.object_position(object_id)
        dx, dy = DIRECTIONS[direction]
        table_surface(self.scene, position[0], position[1], obj.dimensions_m)
        table_surface(self.scene, position[0] + dx * distance_m, position[1] + dy * distance_m, obj.dimensions_m)
        taught_start = self.scene.targets[obj.push.start_target].pose_m_rad
        taught_grasp = self.scene.targets[obj.grasp_target].pose_m_rad
        if taught_start is None or taught_grasp is None:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, "directional push requires a Cartesian taught contact offset")
        offset = math.dist(taught_start[:2], taught_grasp[:2])
        if offset <= max(obj.dimensions_m[:2]) / 2:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, "taught push offset does not clear object footprint")
        startxy = [position[0] - dx * offset, position[1] - dy * offset]
        for tid, xy in ((obj.push.above_target, startxy), (obj.push.start_target, startxy),
                        (obj.push.end_target, [startxy[0] + dx * distance_m, startxy[1] + dy * distance_m]),
                        (obj.push.lift_target, [startxy[0] + dx * distance_m, startxy[1] + dy * distance_m])):
            cfg = self.scene.targets[tid]
            if cfg.pose_m_rad is None:
                raise RobotControlError(ErrorCode.CONFIG_ERROR, "directional push requires Cartesian taught postures")
            pose = Pose.from_list([*xy, position[2] + cfg.pose_m_rad[2], *cfg.pose_m_rad[3:]])
            item = self._target_item(tid)
            if item is None:
                raise RobotControlError(ErrorCode.CONFIG_ERROR, f"push target '{tid}' missing")
            self._transient_poses[tid] = pose

    def clear_transient_plans(self):
        self._transient_poses.clear()

    @staticmethod
    def _item_key(item):
        # RoboDK Item.item is the local numeric handle; no remote Name()/Valid() call.
        return getattr(item, "item", id(item))

    @classmethod
    def _pair_keys(cls, pairs):
        # Treat reversed pair order as equivalent, preserving robot link indices.
        return {tuple(sorted(((cls._item_key(a), ia), (cls._item_key(b), ib))))
                for a, b, ia, ib in pairs}

    def _collision_pairs(self):
        increment("collision_map_reads")
        return list(self.RDK.CollisionActivePairList())

    @timed("collision_policy.resolve")
    def _contact_pairs(self):
        if self.backend_name != "robodk_sim":
            raise RobotControlError(ErrorCode.MODE_VIOLATION, "contact collision policy is RoboDK-only")
        table = self.RDK.Item(self.scene.table_geometry, self.rl.ITEM_TYPE_OBJECT)
        if not table.Valid():
            raise RobotControlError(ErrorCode.CONFIG_ERROR, "protected table geometry missing in station")
        # A snapshot, not a live list: updates must never change what is being iterated.
        pairs = self._collision_pairs()
        if not any((a == table and b == self.robot) or (b == table and a == self.robot)
                   for a, b, _, _ in pairs):
            raise RobotControlError(ErrorCode.CONFIG_ERROR, "table/robot collision pairs are not active")
        ignored = {o.station_item for o in self.scene.objects.values() if not o.avoid_collision and o.station_item}
        ignored.update(name for name, geometry in self.scene.station_geometry.items()
                       if name != self.scene.table_geometry and not geometry.avoid_collision)
        protected = {self.scene.table_geometry} | {
            o.station_item for o in self.scene.objects.values() if o.avoid_collision and o.station_item}
        protected.update(name for name, geometry in self.scene.station_geometry.items() if geometry.avoid_collision)
        ignored.difference_update(protected)
        names = {}
        def name(item):
            key = self._item_key(item)
            if key not in names:
                increment("item_name_queries")
                names[key] = item.Name()
            return names[key]
        robot_name, tool_name = name(self.robot), name(self.tool)
        ignored.difference_update((robot_name, tool_name))
        participants = ignored | {robot_name, tool_name}
        types = {}
        def is_contact_object(item):
            key = self._item_key(item)
            if key not in types:
                increment("item_type_queries")
                types[key] = item.Type()
            return types[key] == self.rl.ITEM_TYPE_OBJECT
        changed = []
        for a, b, ia, ib in pairs:
            if a == table or b == table or a == b:
                continue
            na, nb = name(a), name(b)
            if (na in ignored or nb in ignored) and na in participants and nb in participants:
                # An unrelated robot/tool with the same name as a registered object stays protected.
                if (na in ignored and not is_contact_object(a)) or (nb in ignored and not is_contact_object(b)):
                    continue
                changed.append((a, b, ia, ib))
        return pairs, changed

    def _write_collision_pairs(self, state, pairs, *, individual=False):
        batch = getattr(self.RDK, "setCollisionActivePairList", None)
        if callable(batch) and not individual:
            increment("collision_pair_batch_calls")
            # Batch return semantics are not documented consistently across versions.
            # Verify the resulting map instead of interpreting a truthy result as complete success.
            batch([state] * len(pairs), [p[0] for p in pairs], [p[1] for p in pairs],
                  [p[2] for p in pairs], [p[3] for p in pairs])
        else:
            failed = False
            for a, b, ia, ib in pairs:
                increment("collision_pair_individual_calls")
                try:
                    if not self.RDK.setCollisionActivePair(state, a, b, ia, ib):
                        failed = True
                except Exception:
                    failed = True
            if failed:
                raise RobotControlError(ErrorCode.BACKEND_ERROR, "could not update all collision pairs")

    def _verify_collision_map(self, expected):
        if self._pair_keys(self._collision_pairs()) != expected:
            raise RobotControlError(ErrorCode.BACKEND_ERROR, "collision map update could not be verified")

    @contextmanager
    def _contact_policy(self):
        """Operation-local batching; no path-validation cache or cross-operation metadata cache."""
        if self.scene.collision_policy != "table_and_tagged":
            yield
            return
        original, changed = self._contact_pairs()
        if not changed:
            yield
            return
        original_keys = self._pair_keys(original)
        try:
            try:
                with measure("collision_policy.apply"):
                    self._write_collision_pairs(self.rl.COLLISION_OFF, changed)
                    self._verify_collision_map(original_keys - self._pair_keys(changed))
            except RobotControlError:
                raise
            except Exception as exc:
                raise RobotControlError(ErrorCode.BACKEND_ERROR, f"contact collision policy failed: {exc}") from exc
            yield
        finally:
            # Restore ALL candidates, even if the batch changed only some before failing.
            with measure("collision_policy.restore"):
                try:
                    self._write_collision_pairs(self.rl.COLLISION_ON, changed)
                    self._verify_collision_map(original_keys)
                except Exception:
                    # Recovery only: attempt every individual restoration, then verify the map.
                    try:
                        self._write_collision_pairs(self.rl.COLLISION_ON, changed, individual=True)
                        self._verify_collision_map(original_keys)
                    except Exception as exc:
                        raise RobotControlError(ErrorCode.BACKEND_ERROR, "could not restore collision map") from exc

    def test_segment(self, seg):
        with self._contact_policy():
            return self._test_segment_with_policy(seg)

    @timed("path_test")
    def _test_segment_with_policy(self, seg):
        """MoveJ_Test / MoveL_Test move the simulated robot: snapshot and restore."""
        self._require()
        self._require_held_table_pair()
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
        why = {-1: "linear move crosses a singular or joint-limit configuration",
               -2: "target not reachable"}.get(res, f"code {res}")
        return PathCheckResult(ok=False, conclusive=True, singularity=res == -1, reason=f"RoboDK: {why}")

    # ------------------------------------------------------------ motion
    def _execute(self, end_deg, motion: MotionType, ctx: MotionContext, label: str):
        with self._contact_policy():
            return self._execute_with_policy(end_deg, motion, ctx, label)

    @timed("execution")
    def _execute_with_policy(self, end_deg, motion: MotionType, ctx: MotionContext, label: str):
        self._require()
        self._enforce_simulate()            # execution boundary
        self._require_held_table_pair()
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
            self._validated_table()
            _, yaw = self._upright_object(self._holding)
            x, y, z = self.object_position(self._holding)
            top = table_surface(self.scene, x, y, self.scene.objects[self._holding].dimensions_m, yaw)
            if z < top - 0.0005:
                raise RobotControlError(ErrorCode.SAFETY_VETO, "release refused: held object would penetrate the table")
            self._held_item.setParentStatic(self.base)   # object stays where it was released
        self._held_item = None
        self._holding = None
        self._gripper = "open"
        self._grip_confirmed = False
        return self._status()

    def close_gripper(self, object_id):
        self._require()
        # Recheck immediately before attachment: the map can change after preflight.
        if object_id:
            self.validate_pick(object_id)
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

    def begin_push(self, object_id):
        """Simulation only: the station object rides with the tool on the push segment (no physics engine). Refused
        if the object is not beside the gripper (e.g. already pushed, or picked and placed elsewhere)."""
        self._require()
        obj = self.scene.objects.get(object_id)
        if obj is None or not obj.station_item:
            return False
        if self._held_item is not None:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED, "cannot push while holding an object")
        item = self.RDK.Item(obj.station_item, self.rl.ITEM_TYPE_OBJECT)
        if not item.Valid():
            raise RobotControlError(ErrorCode.CONFIG_ERROR, f"station object '{obj.station_item}' not found")
        self._require_object_table_pair(item)
        tcp = (self.base.PoseAbs() * self.robot.Pose()).Pos()
        centre = item.PoseAbs().Pos()
        gap_mm = math.hypot(tcp[0] - centre[0], tcp[1] - centre[1]) - max(obj.dimensions_m[:2]) * 500.0
        if gap_mm > 45.0:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED,
                                    f"'{object_id}' is not beside the gripper ({gap_mm:.0f} mm away): it is no longer "
                                    "at the spot its push path was taught for")
        self._push_parent = item.Parent()
        item.setParentStatic(self.tool)
        self._pushed_item = item
        return True

    def end_push(self):
        item, parent = getattr(self, "_pushed_item", None), getattr(self, "_push_parent", None)
        self._pushed_item = self._push_parent = None
        if item is not None:
            item.setParentStatic(parent if parent is not None and parent.Valid() else self.base)

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
