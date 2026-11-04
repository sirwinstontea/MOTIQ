"""Expansion of a step's `primitive_sequence` (template schema 1.1) into tool waypoints + a log for the sequence checks.

Semantics follow the template's own action library (action_primitives[].description / urscript):
  MOVE_ABOVE            movel to object position + [0,0,height_m], orientation unchanged
  MOVE_UP/DOWN/LEFT/..  movel relative to the current TCP pose, directions from robot.direction_mapping
  MOVE_TO_POSE          movel to an absolute pose in the BASE frame (rotation vector in rad)
  ROTATE_ROLL/PITCH/YAW movel, rotation about the tool X / Y / Z axis at the same position
  MOVE_JOINTS / HOME    movej (joint-space "free" edge)
  OPEN_/CLOSE_GRIPPER, WAIT, STOP   no motion; recorded in the log
The robot is assumed to start at robot.home_joints_deg (stated in the log/assumptions).
"""
from __future__ import annotations

import math

import numpy as np

from .kinematics import rot_x, rot_z
from .skill_model import as_float

DIRS = {"MOVE_RIGHT": "right", "MOVE_LEFT": "left", "MOVE_FORWARD": "forward", "MOVE_BACKWARD": "backward", "MOVE_UP": "up", "MOVE_DOWN": "down"}
DEFAULT_MAP = {"forward": (0, 1), "backward": (0, -1), "left": (1, 1), "right": (1, -1), "up": (2, 1), "down": (2, -1)}
HORIZONTAL = {"MOVE_RIGHT", "MOVE_LEFT", "MOVE_FORWARD", "MOVE_BACKWARD"}
DEFAULT_HOME = [0.0, -90.0, 90.0, -90.0, -90.0, 0.0]


def rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_vec(w) -> np.ndarray:
    w = np.asarray(w, dtype=float)
    th = float(np.linalg.norm(w))
    if th < 1e-12:
        return np.eye(3)
    k = w / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * (K @ K)


def library(skill) -> dict:
    return {p.get("id"): p for p in (skill.raw.get("action_primitives") or []) if isinstance(p, dict)}


def op_limits(skill) -> dict:
    r = skill.raw.get("robot") if isinstance(skill.raw.get("robot"), dict) else {}
    return r.get("operational_limits") if isinstance(r.get("operational_limits"), dict) else {}


def home_joints_rad(skill) -> np.ndarray:
    r = skill.raw.get("robot") if isinstance(skill.raw.get("robot"), dict) else {}
    h = r.get("home_joints_deg")
    if not (isinstance(h, list) and len(h) == 6 and all(as_float(v) is not None for v in h)):
        h = DEFAULT_HOME
    return np.radians([float(v) for v in h])


def dir_vector(skill, name: str) -> np.ndarray:
    r = skill.raw.get("robot") if isinstance(skill.raw.get("robot"), dict) else {}
    dm = r.get("direction_mapping") if isinstance(r.get("direction_mapping"), dict) else {}
    v = np.zeros(3)
    spec = dm.get(name)
    if isinstance(spec, dict) and str(spec.get("axis", "")).lower() in "xyz" and as_float(spec.get("sign")) is not None:
        v["xyz".index(str(spec["axis"]).lower())] = float(spec["sign"])
    else:
        ax, sg = DEFAULT_MAP[name]
        v[ax] = sg
    return v


def tcp_pose_from_q(planner, q, L):
    T = planner.p.robot.kinematics().fk(q)
    return T[:3, 3] + T[:3, 2] * L, T[:3, :3].copy()


def param(step_params: dict, lib: dict, name: str):
    """Value given in the step, else the library default, plus the library spec (min/max)."""
    spec = (lib.get("parameters") or {}).get(name) or {}
    v = step_params.get(name, spec.get("default"))
    return v, spec


def expand_primitives(planner, step, params, plan, add, state, get_held, set_held, issue) -> None:
    """Append waypoints for `step.raw['primitive_sequence']`. `state` keeps pos/R/grip between steps."""
    from .motion import PlanIssue  # noqa: F401  (issue() builds it)
    skill, L = planner.skill, params.tcp_offset_m
    libs = library(skill)
    ol = op_limits(skill)
    seq = step.raw.get("primitive_sequence")
    table_z = planner.p.cell.table_z_base_m
    if state.get("pos") is None:
        q0 = home_joints_rad(skill)
        p0, R0 = tcp_pose_from_q(planner, q0, L)
        state.update(pos=p0, R=R0, q=q0)
        add("start-home", step, p0, R0, "free", "robot assumed to start at robot.home_joints_deg")
        plan.waypoints[-1].q_fixed = q0
        plan.seq_log.append(dict(step=step.index, prim="START", pos=p0.copy(), grip=None, held=None))
    for k, item in enumerate(seq):
        if not isinstance(item, dict):
            issue(step, "fail", "primitive", f"primitive_sequence[{k}] is not an object")
            continue
        pid = str(item.get("primitive", "")).strip().upper()
        sp = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
        lib = libs.get(pid)
        if lib is None and pid:
            issue(step, "unknown", "primitive", f"primitive '{pid}' is not defined in action_primitives", [f"definition of primitive '{pid}'"])
            continue
        # parameter ranges from the library
        bad = False
        for name, val in sp.items():
            _, spec = param(sp, lib, name)
            x = as_float(val)
            if x is None or not spec:
                continue
            lo, hi = as_float(spec.get("min")), as_float(spec.get("max"))
            if (lo is not None and x < lo - 1e-9) or (hi is not None and x > hi + 1e-9):
                issue(step, "fail", "primitive_params", f"{pid}.{name}={x:g} is outside the allowed range [{lo if lo is not None else '-'}, {hi if hi is not None else '-'}] defined for this primitive (the controller would refuse it)")
                bad = True
        if bad:
            continue
        rec = dict(step=step.index, prim=pid, params=dict(sp), pos=state["pos"].copy(), grip=state.get("grip"), held=get_held().id if get_held() else None)
        speed = as_float(sp.get("speed_mps")) if "speed_mps" in sp else as_float(((lib.get("parameters") or {}).get("speed_mps") or {}).get("default"))
        rec["speed"] = speed
        if pid in DIRS:
            d = as_float(sp.get("distance_m", ((lib.get("parameters") or {}).get("distance_m") or {}).get("default")))
            v = dir_vector(skill, DIRS[pid]) * float(d)
            tgt = state["pos"] + v
            if pid == "MOVE_DOWN" and tgt[2] < table_z + (as_float(ol.get("min_z_above_table_m")) or 0.0) - 1e-9:
                issue(step, "fail", "primitive_params", f"MOVE_DOWN would put the tool at z={tgt[2]:.3f} m, below min_z_above_table_m ({(as_float(ol.get('min_z_above_table_m')) or 0):g} m above the table): refused by the controller")
                continue
            rec["horizontal"] = pid in HORIZONTAL
            rec["start_z"] = float(state["pos"][2])
            add(f"{pid.lower()}", step, tgt, state["R"], "linear", f"{d:g} m")
            state["pos"] = tgt
        elif pid == "MOVE_ABOVE":
            obj = skill.object_by_ref(sp.get("target"))
            if obj is None or obj.position is None:
                issue(step, "unknown", "target_position", f"MOVE_ABOVE target '{sp.get('target')}' has no known position", [f"position of '{sp.get('target')}'"])
                continue
            h = as_float(sp.get("height_m", 0.15))
            tgt = planner.to_base(obj.position) + np.array([0, 0, h])
            rec["horizontal"] = float(np.linalg.norm(tgt[:2] - state["pos"][:2])) > 0.01
            rec["start_z"] = float(state["pos"][2])
            add("move-above", step, tgt, state["R"], "linear", f"above {obj.id}")
            state["pos"] = tgt
        elif pid == "MOVE_TO_POSE":
            xyz = [as_float(sp.get(a)) for a in "xyz"]
            if any(v is None for v in xyz):
                issue(step, "unknown", "target_position", "MOVE_TO_POSE without x, y, z", ["x, y, z of the pose"])
                continue
            tgt = np.array(xyz, dtype=float)
            rv = [as_float(sp.get(a)) for a in ("rx", "ry", "rz")]
            R = state["R"] if any(v is None for v in rv) else rot_vec(rv)
            rec["horizontal"] = float(np.linalg.norm(tgt[:2] - state["pos"][:2])) > 0.01
            rec["start_z"] = float(state["pos"][2])
            add("move-to-pose", step, tgt, R, "linear", "absolute pose (base frame)")
            state["pos"], state["R"] = tgt, R
        elif pid in ("ROTATE_ROLL", "ROTATE_PITCH", "ROTATE_YAW"):
            a = math.radians(as_float(sp.get("angle_deg", 15)))
            Rr = {"ROTATE_ROLL": rot_x, "ROTATE_PITCH": rot_y, "ROTATE_YAW": rot_z}[pid](a)
            R = state["R"] @ Rr
            rec["tilt"] = pid != "ROTATE_YAW" and abs(a) > 1e-9
            add(pid.lower(), step, state["pos"], R, "linear", f"{math.degrees(a):g} deg")
            state["R"] = R
        elif pid in ("HOME", "MOVE_JOINTS"):
            if pid == "HOME":
                q = home_joints_rad(skill)
            else:
                js = sp.get("joints_deg")
                if not (isinstance(js, list) and len(js) == 6 and all(as_float(v) is not None for v in js)):
                    issue(step, "unknown", "joint_target", "MOVE_JOINTS needs joints_deg with 6 numbers", ["six joint angles in degrees"])
                    continue
                q = np.radians([float(v) for v in js])
            p, R = tcp_pose_from_q(planner, q, L)
            add(pid.lower(), step, p, R, "free", "joint-space move (movej)")
            plan.waypoints[-1].q_fixed = q
            state.update(pos=p, R=R, q=q)
            rec["q"] = q
        elif pid == "MOVE_JOINT":
            q = state.get("q")
            jn = str(sp.get("joint", ""))
            names = ["shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"]
            if q is None or jn not in names:
                issue(step, "unknown", "joint_target", "MOVE_JOINT needs a known current joint state and a valid joint name", ["current joint angles (after HOME or MOVE_JOINTS)"])
                continue
            q = q.copy()
            i = names.index(jn)
            tdeg = as_float(sp.get("target_deg"))
            q[i] = math.radians(tdeg) if tdeg is not None else q[i] + math.radians(as_float(sp.get("delta_deg", 10)))
            p, R = tcp_pose_from_q(planner, q, L)
            add("move-joint", step, p, R, "free", f"{jn}")
            plan.waypoints[-1].q_fixed = q
            state.update(pos=p, R=R, q=q)
            rec["q"] = q
        elif pid == "OPEN_GRIPPER":
            state["grip"] = "open"
            rec["width"] = as_float(sp.get("width_m", ((lib.get("parameters") or {}).get("width_m") or {}).get("default")))
            if get_held() is not None:
                plan.release_points.append(len(plan.waypoints) - 1)
                rec["released"] = get_held().id
                set_held(None)
        elif pid == "CLOSE_GRIPPER":
            state["grip"] = "closed"
            rec["force_pct"] = as_float(sp.get("force_pct", ((lib.get("parameters") or {}).get("force_pct") or {}).get("default")))
            obj = skill.object_by_ref(step.target)
            if obj is not None and not obj.is_container and plan.waypoints:
                plan.hold_points.append(len(plan.waypoints) - 1)
                plan.carried[obj.id] = obj
                set_held(obj)
                rec["grasped"] = obj.id
        elif pid in ("WAIT", "STOP"):
            rec["seconds"] = as_float(sp.get("seconds", 0.5)) if pid == "WAIT" else 0.0
        else:
            issue(step, "unknown", "primitive", f"primitive '{pid}' has no known physical meaning", [f"meaning of '{pid}'"])
            continue
        rec["pos_after"] = state["pos"].copy()
        rec["grip_after"] = state.get("grip")
        rec["held_after"] = get_held().id if get_held() else None
        plan.seq_log.append(rec)
