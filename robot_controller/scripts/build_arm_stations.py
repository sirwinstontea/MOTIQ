#!/usr/bin/env python3
"""Build a RoboDK demo cell for every extra arm (UR3e, xArm5, Elite CS63/CS66) from the RoboDK library .rdk files.

For each arm, SIMULATION ONLY:
  1. extracts the robot model from the library station (MOTIQ/<file>.rdk) to stations/<Model>.robot
  2. places the UR5e demo cell (table, delivery pad, the 4 objects, every taught target) within THIS arm's reach:
     the whole cell is shifted along the base X axis and the high poses (approach/lift/scan) are lowered until
     RoboDK's IK reaches every target with the gripper TCP, in the same arm configuration as home
  3. writes config/scenes/<robot_id>.yaml (the scene the robot-chooser and controller load for that arm)
  4. builds stations/<Model>_demo.rdk (robot + Gripper TCP + targets + table + the 4 object meshes)
  5. prints the catalog values (workspace box, joint limits read from the RoboDK model)

    cd robot_controller && .venv/bin/python scripts/build_arm_stations.py [--only ur_ur3e] [--port 20500]

Needs a running RoboDK with NO controller connected (it opens/closes station tabs). The UR5e station is untouched.
The layout is a stand-in for a real cell: re-teach every target before any real use.
"""
from __future__ import annotations

import argparse
import copy
import math
import sys
from pathlib import Path

import yaml

RC = Path(__file__).resolve().parents[1]
MOTIQ = RC.parent
sys.path.insert(0, str(RC))

from robot_control.backends.robodk_sim import _import_robodk  # noqa: E402
from robot_control.config import RobotConfig, SceneConfig  # noqa: E402

TCP = [0.0, 0.0, 0.15, 0.0, 0.0, 0.0]          # same symbolic gripper TCP as the UR5e cell (hardware.yaml)
BASE_SCENE = RC / "config" / "scene.example.yaml"
OBJ_FRAME_UR5E = (-0.62, 0.0)                    # Objects_Frame position in the UR5e cell (m)
TABLE_NEAR_MAX_X = -0.10                         # table edge stays 100 mm in front of the base axis
GRASP_Z = 0.06                                   # poses at/below this height are object geometry: never changed

ARMS = [
    # id, catalog model (file names), RoboDK item name, source library station
    ("ur_ur3e", "UR3e", "UR3e", "UR3e.rdk"),
    ("ufactory_xarm5", "xArm5", "xArm5", "XARM5.rdk"),
    ("elite_cs63", "CS63", "CS63", "EliteRobotsCS63.rdk"),
    ("elite_cs66", "CS66", "CS66", "EliteRobotsCS66.rdk"),
]


def walk(item):
    for c in item.Childs():
        yield c
        yield from walk(c)


def extract_robot(RDK, rl, src: Path, out: Path):
    """Save the robot of a library station as a .robot file; returns (home_joints_deg, lower_deg, upper_deg)."""
    keep = RDK.ActiveStation()
    st = RDK.AddFile(str(src))
    try:
        robots = [i for i in walk(st) if i.Type() == rl.ITEM_TYPE_ROBOT]
        if len(robots) != 1:
            raise SystemExit(f"{src.name}: expected 1 robot, found {len(robots)}")
        r = robots[0]
        lo, hi, _ = r.JointLimits()
        home = r.Joints().list()
        r.Save(str(out))
        if not out.is_file():
            raise SystemExit(f"RoboDK did not write {out}")
        return home, lo.list(), hi.list()
    finally:
        RDK.setActiveStation(st)
        RDK.CloseStation()
        if keep.Valid():
            RDK.setActiveStation(keep)


def transform_scene(base: dict, dx: float, kz: float) -> dict:
    """Shift the cell by dx along X; lower every height above GRASP_Z by factor kz."""
    s = copy.deepcopy(base)

    def mv(p):
        p = list(p)
        p[0] = round(p[0] + dx, 4)
        if p[2] > GRASP_Z:
            p[2] = round(GRASP_Z + (p[2] - GRASP_Z) * kz, 4)
        return p

    for name, t in s["targets"].items():
        if name == s["home_target"]:
            continue
        t["pose_m_rad"] = mv(t["pose_m_rad"])
    for g in s.get("station_geometry", {}).values():
        g["pose_m_rad"] = [round(g["pose_m_rad"][0] + dx, 4)] + list(g["pose_m_rad"][1:])
    t = s["station_geometry"]["table"]
    far, near = t["pose_m_rad"][0] - t["size_m"][0] / 2, t["pose_m_rad"][0] + t["size_m"][0] / 2
    if near > TABLE_NEAR_MAX_X:                  # the table must not run under the robot base
        t["size_m"] = [round(TABLE_NEAR_MAX_X - far, 4)] + list(t["size_m"][1:])
        t["pose_m_rad"] = [round((TABLE_NEAR_MAX_X + far) / 2, 4)] + list(t["pose_m_rad"][1:])
    return s


def _box_triangles(sx, sy, sz):
    x, y = sx / 2, sy / 2
    v = [(-x, -y, 0), (x, -y, 0), (x, y, 0), (-x, y, 0), (-x, -y, sz), (x, -y, sz), (x, y, sz), (-x, y, sz)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return [list(v[i]) for f in faces for i in f]


class Probe:
    """A scratch station with the robot + TCP + box stand-ins for the table, pad and objects: tests IK AND
    collisions of a candidate layout."""

    def __init__(self, RDK, rl, rm, robot_file: Path, home_deg):
        self.RDK, self.rl, self.rm = RDK, rl, rm
        self.keep = RDK.ActiveStation()
        self.st = RDK.AddStation("MOTIQ layout probe")
        self.robot = RDK.AddFile(str(robot_file), self.st)
        self.tool = rm.UR_2_Pose([v * 1000 if i < 3 else v for i, v in enumerate(TCP)])
        self.lo, self.hi, _ = self.robot.JointLimits()
        n = len(home_deg)
        # IK seeds = arm configurations to try: the library home, the same facing the other way, and the usual
        # elbow-up postures of UR-style arms. The layout keeps the first one that is reachable AND collision-free.
        seeds = [list(home_deg)]
        if n == 6:
            seeds += [[0, -90, 90, -90, -90, 0], [0, -90, -90, -90, 90, 0], [0, -60, -110, -100, 90, 0],
                      [0, -120, 110, -80, -90, 0], [0, -30, -60, 0, 90, 0], [0, 30, -90, 0, 60, 0]]
        self.seeds = []
        for sd in seeds:
            for j1 in (0, 180, -180):
                q = list(sd)
                q[0] = sd[0] + j1
                if q not in self.seeds and all(lo <= v <= hi for v, lo, hi in zip(q, self.lo.list(), self.hi.list())):
                    self.seeds.append(q)
        self.boxes = []
        self.RDK.setCollisionActive(rl.COLLISION_ON)

    def close(self):
        self.RDK.setActiveStation(self.st)
        self.RDK.CloseStation()
        if self.keep.Valid():
            self.RDK.setActiveStation(self.keep)

    def set_obstacles(self, scene):
        for b in self.boxes:
            b.Delete()
        self.boxes = []
        self.RDK.Render(False)
        boxes = [(g["size_m"], g["pose_m_rad"][:3]) for g in scene["station_geometry"].values()]
        for o in scene["objects"].values():          # objects stand on the table at their grasp xy
            g = scene["targets"][o["grasp_target"]]["pose_m_rad"]
            d = o["dimensions_m"]
            boxes.append(([d[0], d[1], d[2] - 0.002], [g[0], g[1], 0.002]))
        for i, (size, pos) in enumerate(boxes):
            it = self.RDK.AddShape(_box_triangles(*(v * 1000 for v in size)))
            it.setPose(self.rm.transl(*(v * 1000 for v in pos)))
            self.boxes.append(it)
        for i, a in enumerate(self.boxes):            # obstacle vs obstacle contact is not a robot collision
            for b in self.boxes[i + 1:]:
                self.RDK.setCollisionActivePair(self.rl.COLLISION_OFF, a, b)

    def ik(self, pose_m_rad, approx):
        p = pose_m_rad
        mat = self.rm.UR_2_Pose([p[0] * 1000, p[1] * 1000, p[2] * 1000, p[3], p[4], p[5]])
        sol = self.robot.SolveIK(mat, approx, self.tool).list()
        if len(sol) < len(approx):
            return None
        if any(q < lo - 1e-6 or q > hi + 1e-6 for q, lo, hi in zip(sol, self.lo.list(), self.hi.list())):
            return None
        return sol

    def collides(self, joints) -> bool:
        self.robot.setJoints(joints)
        return self.RDK.Collisions() > 0

    def fk(self, joints_deg):
        x, y, z, rx, ry, rz = self.rm.Pose_2_UR(self.robot.SolveFK(joints_deg) * self.tool)
        return [round(x / 1000, 5), round(y / 1000, 5), round(z / 1000, 5), round(rx, 5), round(ry, 5), round(rz, 5)]

    def homes_for(self, scene):
        """Home candidates = tool down above the middle of the table, as high as the scan poses, one per seed."""
        g = scene["station_geometry"]["table"]["pose_m_rad"]
        z = max(t["pose_m_rad"][2] for n, t in scene["targets"].items() if n.startswith("scan_"))
        for seed in self.seeds:
            for zz in (z, z - 0.05, z - 0.1):
                sol = self.ik([g[0], 0.0, zz, math.pi, 0, 0], seed)
                if sol and not self.collides(sol):
                    yield sol
                    break

    def check(self, scene, home_deg) -> bool:
        """Every target reachable near the home configuration (no flips) and collision-free there."""
        for name, t in scene["targets"].items():
            if name == scene["home_target"]:
                continue
            sol = self.ik(t["pose_m_rad"], home_deg)
            if sol is None or max(abs(a - b) for a, b in zip(sol, home_deg)) > 150 or self.collides(sol):
                return False
        return True


def find_layout(probe: Probe, base: dict):
    """Smallest change from the UR5e layout that the arm reaches without collisions: dx from 0 towards the base,
    kz from 1 down."""
    probe.RDK.Render(False)
    try:
        for kz in (1.0, 0.85, 0.7, 0.6, 0.5):
            for dx in [i * 0.02 for i in range(0, 21)]:    # 0 .. 0.40 m closer to the base
                s = transform_scene(base, dx, kz)
                probe.set_obstacles(s)
                for home in probe.homes_for(s):
                    if probe.check(s, home):
                        return s, dx, kz, home
    finally:
        probe.RDK.Render(True)
    return None


def workspace_for(scene) -> dict:
    xs = [t["pose_m_rad"][0] for t in scene["targets"].values() if t.get("pose_m_rad")]
    ys = [t["pose_m_rad"][1] for t in scene["targets"].values() if t.get("pose_m_rad")]
    zs = [t["pose_m_rad"][2] for t in scene["targets"].values() if t.get("pose_m_rad")]
    r = lambda v: round(v, 3)  # noqa: E731
    return {"frame_id": "base", "x_m": [r(min(xs) - 0.05), r(max(xs) + 0.05)],
            "y_m": [r(min(ys) - 0.05), r(max(ys) + 0.05)], "z_m": [0.015, r(max(zs) + 0.05)]}


def write_scene(path: Path, scene: dict, rid: str, model: str, dx: float, kz: float):
    head = (f"# Demo scene for the {model} ({rid}): the UR5e demo cell (scene.example.yaml) moved within this arm's\n"
            f"# reach by scripts/build_arm_stations.py: cell shifted {dx * 1000:+.0f} mm along base X, heights above\n"
            f"# {GRASP_Z * 1000:.0f} mm scaled by {kz}. Every target was IK + collision checked in RoboDK (150 mm TCP).\n"
            "# SIMULATION PLACEHOLDERS: re-teach every target on a real cell. Units: m, rad (rotation vector).\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(head + yaml.safe_dump(scene, sort_keys=False, width=120), encoding="utf-8")


def build_station(RDK, rl, rm, rid, model, item, dof, robot_file: Path, scene_path: Path, dx: float) -> Path:
    from robot_control.backends.robodk_station import build_demo_station
    from robot_control.config import load_scene_config
    from robot_control.sim_objects.robodk_import import run_import

    rcfg = RobotConfig.model_construct(**{   # station building needs no limits (catalog holds them)
        "model": model, "dof": dof,
        "backend": "robodk_sim", "station_name": f"{model} Controller Demo",
        "station_path": str(RC / "stations" / f"{model}_demo.rdk"), "robot_library_file": str(robot_file),
        "robot_item_name": item, "base_frame": f"{model} Base", "tool_frame": "Gripper TCP",
        "gripper_kind": "symbolic", "tcp_offset_m_rad": TCP,
    })
    scene = load_scene_config(str(scene_path))
    out = Path(build_demo_station(rcfg, scene))
    msgs = []
    if run_import(replace=True, out=msgs.append) != 0:
        raise SystemExit(f"{model}: object import failed:\n  " + "\n  ".join(msgs))
    base = RDK.Item(rcfg.base_frame, rl.ITEM_TYPE_FRAME)
    frame = RDK.Item("Objects_Frame", rl.ITEM_TYPE_FRAME)
    frame.setParentStatic(base)
    frame.setPose(rm.transl((OBJ_FRAME_UR5E[0] + dx) * 1000, OBJ_FRAME_UR5E[1] * 1000, 0) * rm.rotz(math.pi / 2))
    frame.setVisible(True, False)
    table = RDK.Item("table", rl.ITEM_TYPE_OBJECT)
    for o in frame.Childs():                     # objects stand on the table; that contact is not a collision
        RDK.setCollisionActivePair(rl.COLLISION_OFF, o, table)
    RDK.Save(str(out))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", action="append", help="robot id(s) to build (default: all)")
    ap.add_argument("--port", type=int, default=20500)
    args = ap.parse_args()
    rl, rm = _import_robodk()
    RDK = rl.Robolink(port=args.port)
    RDK.setRunMode(rl.RUNMODE_SIMULATE)
    base = yaml.safe_load(BASE_SCENE.read_text(encoding="utf-8"))
    SceneConfig.model_validate(base)
    results = {}
    for rid, model, item, src in ARMS:
        if args.only and rid not in args.only:
            continue
        src_path = MOTIQ / src
        if not src_path.is_file():
            src_path = RC / "stations" / "library" / src
        robot_file = RC / "stations" / f"{model}.robot"
        home_lib, lo, hi = extract_robot(RDK, rl, src_path, robot_file)
        print(f"[{rid}] robot model -> {robot_file.relative_to(RC)} ({len(lo)} joints)")

        probe = Probe(RDK, rl, rm, robot_file, home_lib)
        try:
            found = find_layout(probe, base)
            home_pose = probe.fk(found[3]) if found else None
        finally:
            probe.close()
        if not found:
            print(f"[{rid}] NO reachable layout found - skipped", file=sys.stderr)
            continue
        scene, dx, kz, home = found
        scene["targets"][scene["home_target"]] = {
            "joints_rad": [round(math.radians(q), 5) for q in home],
            "pose_m_rad": home_pose,
            "description": f"safe home posture (tool down above the table, solved by RoboDK IK for the {model})"}
        scene_path = RC / "config" / "scenes" / f"{rid}.yaml"
        write_scene(scene_path, scene, rid, model, dx, kz)
        SceneConfig.model_validate(yaml.safe_load(scene_path.read_text(encoding="utf-8")))
        print(f"[{rid}] layout: shift {dx * 1000:+.0f} mm, height x{kz} -> {scene_path.relative_to(RC)}")

        station = build_station(RDK, rl, rm, rid, model, item, len(lo), robot_file, scene_path, dx)
        print(f"[{rid}] station -> {station.relative_to(RC)}")
        results[rid] = {"workspace": workspace_for(scene),
                        "joint_min_rad": [round(math.radians(v), 4) for v in lo],
                        "joint_max_rad": [round(math.radians(v), 4) for v in hi]}
    keep = [s for s in RDK.ItemList(rl.ITEM_TYPE_STATION) if s.Name() == "UR5e Controller Demo"]
    if keep:
        RDK.setActiveStation(keep[0])
    print(yaml.safe_dump(results, sort_keys=False, default_flow_style=None, width=140))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
