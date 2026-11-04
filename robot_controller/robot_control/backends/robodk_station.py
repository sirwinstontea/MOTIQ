"""Build a DEMO RoboDK station from config (simulation only).

Creates, in a NEW station tab (your other stations are left untouched):
robot (from a RoboDK library .robot file), symbolic gripper TCP, the scene's
named targets ("taught" from config), demo box geometry, and the scene objects.
Then saves it to ``station_path``.

The geometry is a stand-in for the real cell. Collision checks against it are
a digital-twin aid, not proof of physical clearance.
"""
from __future__ import annotations

import math
from pathlib import Path

from ..config import RobotConfig, SceneConfig
from ..models import ErrorCode, RobotControlError
from .robodk_sim import _import_robodk


def _box_triangles(sx, sy, sz):
    x, y = sx / 2, sy / 2
    v = [(-x, -y, 0), (x, -y, 0), (x, y, 0), (-x, y, 0), (-x, -y, sz), (x, -y, sz), (x, y, sz), (-x, y, sz)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return [list(v[i]) for f in faces for i in f]


def build_demo_station(robot_cfg: RobotConfig, scene: SceneConfig, port: int = 20500) -> str:
    rl, rm = _import_robodk()
    if not robot_cfg.robot_library_file or not Path(robot_cfg.robot_library_file).expanduser().is_file():
        raise RobotControlError(ErrorCode.CONFIG_ERROR,
                                f"robot_library_file not found: {robot_cfg.robot_library_file}. Download the "
                                f"{robot_cfg.model} .robot file from https://robodk.com/library")
    try:
        RDK = rl.Robolink(port=port)
    except Exception as e:
        raise RobotControlError(ErrorCode.BACKEND_NOT_CONNECTED, f"cannot connect to RoboDK: {e}") from e
    RDK.setRunMode(rl.RUNMODE_SIMULATE)

    name = robot_cfg.station_name or f"{robot_cfg.model} Controller Demo"
    # Replace only a previous copy of OUR demo station.
    for stn in RDK.ItemList(rl.ITEM_TYPE_STATION):
        if stn.Name() == name:
            RDK.setActiveStation(stn)
            RDK.CloseStation()
    station = RDK.AddStation(name)
    RDK.Render(False)

    robot = RDK.AddFile(str(Path(robot_cfg.robot_library_file).expanduser().resolve()), station)
    if not robot.Valid() or robot.Type() != rl.ITEM_TYPE_ROBOT:
        raise RobotControlError(ErrorCode.CONFIG_ERROR, "loading the robot file did not create a robot")
    robot.setName(robot_cfg.robot_item_name)
    base = robot.Parent()
    base.setName(robot_cfg.base_frame)

    o = robot_cfg.tcp_offset_m_rad
    tool = robot.AddTool(rm.UR_2_Pose([o[0] * 1000, o[1] * 1000, o[2] * 1000, o[3], o[4], o[5]]),
                         robot_cfg.tool_frame)
    robot.setPoseTool(tool)
    robot.setPoseFrame(base)

    home = scene.targets[scene.home_target]
    if home.joints_rad:
        robot.setJoints([math.degrees(q) for q in home.joints_rad])

    # Demo geometry + scene objects (boxes)
    items = {}
    for gname, box in scene.station_geometry.items():
        it = RDK.AddShape(_box_triangles(*(s * 1000 for s in box.size_m)))
        it.setName(gname)
        it.setParent(base)
        p = box.pose_m_rad
        it.setPose(rm.UR_2_Pose([p[0] * 1000, p[1] * 1000, p[2] * 1000, p[3], p[4], p[5]]))
        it.setColor(list(box.color))
        items[gname] = it
    for gname, box in scene.station_geometry.items():
        for other in box.no_collision_with:
            if other in items:
                RDK.setCollisionActivePair(rl.COLLISION_OFF, items[gname], items[other])

    # Named targets (TCP poses / joint targets) in the robot base frame
    for tname, t in scene.targets.items():
        tgt = RDK.AddTarget(tname, base, robot)
        if t.joints_rad is not None:
            tgt.setAsJointTarget()
            tgt.setJoints([math.degrees(q) for q in t.joints_rad])
        else:
            p = t.pose_m_rad
            tgt.setAsCartesianTarget()
            tgt.setPose(rm.UR_2_Pose([p[0] * 1000, p[1] * 1000, p[2] * 1000, p[3], p[4], p[5]]))

    RDK.setCollisionActive(rl.COLLISION_ON)
    RDK.Render(True)
    out = Path(robot_cfg.station_path or f"stations/{robot_cfg.model}_demo.rdk").expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    RDK.Save(str(out))
    return str(out)
