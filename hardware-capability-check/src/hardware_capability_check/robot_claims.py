"""The skill file states facts about the robot (`robot`), its safe operating limits and an action library.
Every one of those statements is tested against the official UR5e profile and the real tool, because a skill that
assumes a 1.0 m/s TCP, a ±360° elbow or an actuated Robotiq gripper is only executable if the cell really has them.
"""
from __future__ import annotations

import math
import re

import numpy as np

from .primitives import home_joints_rad, library, op_limits
from .report import Finding, Status
from .skill_model import as_float

SRC = ["ur5e:limits"]


def check_robot_claims(ck) -> None:
    sk, r = ck.skill, ck.robot
    claim = sk.raw.get("robot")
    if not isinstance(claim, dict):
        return
    out = ck.add
    model = str(claim.get("model", ""))
    norm = lambda x: re.sub(r"[^a-z0-9]", "", str(x).lower())  # noqa: E731
    if model and norm(r.id) not in norm(model) and norm(r.name) not in norm(model) and norm(model) not in norm(r.name):
        out(Finding("robot_claims", Status.FAIL, f"skill is written for '{model}', this cell has '{r.name}'", basis="documented"))
    dof = as_float(claim.get("degrees_of_freedom"))
    if dof is not None and int(dof) != 6:
        out(Finding("robot_claims", Status.FAIL, f"skill assumes {int(dof)} DOF, the robot has 6", basis="documented"))

    def upper(name, claimed, doc, unit, tol=1e-9, scale=1.0):
        c = as_float(claimed)
        if c is None:
            return
        if c > doc * (1 + tol) + 1e-12:
            out(Finding("robot_claims", Status.FAIL, f"skill assumes {name} {c * scale:g} {unit}, the documented value is {doc * scale:g} {unit}", sources=SRC, basis="documented"))
        else:
            out(Finding("robot_claims", Status.PASS, f"{name} {c * scale:g} {unit} is within the documented {doc * scale:g} {unit}", sources=SRC, basis="documented"))

    upper("payload", claim.get("payload_kg"), r.payload_rated_kg, "kg")
    upper("reach", claim.get("reach_m"), r.reach_m, "m")
    upper("max TCP speed", claim.get("max_tcp_speed_mps"), r.tcp_speed_max_m_s, "m/s")
    # repeatability: the skill must not claim a SMALLER (better) error than documented
    rep = as_float(claim.get("pose_repeatability_m"))
    if rep is not None:
        if rep * 1000 < r.repeatability_mm - 1e-9:
            out(Finding("robot_claims", Status.FAIL, f"skill assumes repeatability {rep * 1000:g} mm, documented is +-{r.repeatability_mm:g} mm", sources=SRC, basis="documented"))
        else:
            out(Finding("robot_claims", Status.PASS, f"repeatability {rep * 1000:g} mm matches the documented +-{r.repeatability_mm:g} mm", sources=SRC, basis="documented"))
    # joints
    conflicts = []
    for j in claim.get("joints", []) or []:
        i = int(as_float(j.get("index")) if as_float(j.get("index")) is not None else -1)
        if not 0 <= i < 6:
            continue
        rng = j.get("range_deg")
        if isinstance(rng, list) and len(rng) == 2:
            lo, hi = math.radians(float(rng[0])), math.radians(float(rng[1]))
            if lo < r.joint_min_rad[i] - 1e-6 or hi > r.joint_max_rad[i] + 1e-6:
                conflicts.append(f"{j.get('id', i)}: skill {rng[0]:g}..{rng[1]:g} deg vs profile {math.degrees(r.joint_min_rad[i]):g}..{math.degrees(r.joint_max_rad[i]):g} deg")
        sp = as_float(j.get("max_speed_dps"))
        if sp is not None and math.radians(sp) > r.joint_speed_max_rad_s * (1 + 1e-9):
            out(Finding("robot_claims", Status.FAIL, f"skill assumes {j.get('id')} speed {sp:g} deg/s, documented is {math.degrees(r.joint_speed_max_rad_s):g} deg/s", sources=SRC, basis="documented"))
    if conflicts:
        out(Finding("robot_claims", Status.CONDITIONAL, "skill's joint ranges are wider than the ranges this checker enforces (manual: elbow +-160 deg; datasheet +-360 deg; ROS +-180 deg): " + "; ".join(conflicts),
                    conditions=["every motion is also valid inside the stricter manual ranges (it is checked that way here, so this only matters for runs that need the wider range)"],
                    sources=SRC, basis="documented"))
    # direction mapping
    dm = claim.get("direction_mapping")
    if isinstance(dm, dict) and dm.get("calibrated") is False:
        out(Finding("direction_mapping", Status.CONDITIONAL, "left/right/forward/back mapping to base axes is marked 'calibrated: false' in the skill",
                    conditions=["direction_mapping verified on the real cell with the teach pendant"], needs=["calibrated direction mapping"], basis="assumed"))
    # gripper claim vs real tool
    g = claim.get("gripper")
    if isinstance(g, dict):
        t = ck.tool
        stroke = as_float(g.get("stroke_m"))
        if not t.actuated:
            out(Finding("gripper_claims", Status.FAIL, f"skill assumes a gripper ('{g.get('model')}', {('%.0f mm stroke' % (stroke * 1000)) if stroke else 'stroke n/a'}, rq_open/rq_close) but the cell tool '{t.name}' is rigid and has no actuator",
                        needs=["an actuated gripper, or a skill that needs no grasp"], basis="documented"))
        else:
            if stroke is not None and t.opening_max_m is not None and stroke > t.opening_max_m + 1e-9:
                out(Finding("gripper_claims", Status.FAIL, f"skill assumes stroke {stroke * 1000:.0f} mm, the tool opens {t.opening_max_m * 1000:.0f} mm", basis="computed"))
            if "to_be_confirmed" in str(g.get("model", "")).lower().replace(" ", "_"):
                out(Finding("gripper_claims", Status.CONDITIONAL, "skill's gripper model is 'TO_BE_CONFIRMED'", conditions=["tool matches the skill's open/close commands (rq_* or tool digital output 0)"], basis="assumed"))
    # home pose
    if isinstance(claim.get("home_joints_deg"), list):
        q = home_joints_rad(sk)
        bad = [i for i in range(6) if q[i] < r.joint_min_rad[i] - 1e-6 or q[i] > r.joint_max_rad[i] + 1e-6]
        T = r.kinematics().fk(q)
        z = float(T[2, 3])
        if bad:
            out(Finding("home_pose", Status.FAIL, f"home_joints_deg is outside the joint limits at joint(s) {bad}", basis="computed"))
        elif z < ck.cell.table_z_base_m + 0.05:
            out(Finding("home_pose", Status.FAIL, f"the home pose puts the flange only {z - ck.cell.table_z_base_m:.3f} m above the table", basis="computed"))
        else:
            down = float(-T[2, 2])
            out(Finding("home_pose", Status.PASS if down > 0.99 else Status.CONDITIONAL, f"home pose is inside the limits, flange {z:.2f} m above the base plane, tool {'pointing down' if down > 0.99 else 'not pointing straight down'}",
                        conditions=[] if down > 0.99 else ["tool orientation at home as intended"], basis="computed"))
    check_operational_limits(ck, claim)


def check_operational_limits(ck, claim) -> None:
    ol = claim.get("operational_limits")
    if not isinstance(ol, dict):
        return
    r = ck.robot
    pairs = [("max_tcp_speed_mps", r.tcp_speed_max_m_s, "m/s"), ("max_joint_speed_dps", math.degrees(r.joint_speed_max_rad_s), "deg/s")]
    for key, doc, unit in pairs:
        v = as_float(ol.get(key))
        if v is not None and v > doc:
            ck.add(Finding("operational_limits", Status.FAIL, f"operational {key} {v:g} {unit} exceeds the hardware maximum {doc:g} {unit}", basis="documented"))
    a, b, c = as_float(ol.get("approach_speed_mps")), as_float(ol.get("max_tcp_speed_mps")), as_float(ol.get("default_step_m"))
    if a is not None and b is not None and a > b:
        ck.add(Finding("operational_limits", Status.FAIL, "approach_speed_mps is above max_tcp_speed_mps (contradictory limits)", basis="computed"))
    if c is not None and as_float(ol.get("max_step_m")) is not None and c > as_float(ol.get("max_step_m")):
        ck.add(Finding("operational_limits", Status.FAIL, "default_step_m is above max_step_m (contradictory limits)", basis="computed"))
    ztab, zsafe = as_float(ol.get("min_z_above_table_m")), as_float(ol.get("safe_travel_height_m"))
    if ztab is not None and zsafe is not None and zsafe <= ztab:
        ck.add(Finding("operational_limits", Status.FAIL, "safe_travel_height_m must exceed min_z_above_table_m", basis="computed"))
    check_library(ck, ol)


def check_library(ck, ol) -> None:
    """Every primitive's built-in speeds/accelerations (parsed from its URScript) against the operational limits and the arm."""
    r = ck.robot
    vmax, amax, jmax = as_float(ol.get("max_tcp_speed_mps")), as_float(ol.get("max_tcp_acceleration_mps2")), as_float(ol.get("max_joint_speed_dps"))
    for pid, p in library(ck.skill).items():
        us = str(p.get("urscript", ""))
        for m in re.finditer(r"movel\(.*?a=([0-9.]+),\s*v=([0-9.]+)\)", us):
            a, v = float(m.group(1)), float(m.group(2))
            if vmax is not None and v > vmax + 1e-9:
                ck.add(Finding("primitive_library", Status.FAIL, f"{pid}: fixed TCP speed v={v:g} m/s in its URScript exceeds max_tcp_speed_mps {vmax:g}", basis="computed"))
            if amax is not None and a > amax + 1e-9:
                ck.add(Finding("primitive_library", Status.FAIL, f"{pid}: fixed acceleration a={a:g} m/s^2 exceeds max_tcp_acceleration_mps2 {amax:g}", basis="computed"))
        for m in re.finditer(r"movej\(.*?a=([0-9.]+),\s*v=([0-9.]+)\)", us):
            v = float(m.group(2))
            if jmax is not None and math.degrees(v) > jmax + 1e-6:
                ck.add(Finding("primitive_library", Status.FAIL, f"{pid}: fixed joint speed {math.degrees(v):.0f} deg/s exceeds max_joint_speed_dps {jmax:g}", basis="computed"))
        sp = ((p.get("parameters") or {}).get("speed_mps") or {})
        d = as_float(sp.get("default"))
        if d is not None and vmax is not None and d > vmax + 1e-9:
            ck.add(Finding("primitive_library", Status.FAIL, f"{pid}: default speed {d:g} m/s exceeds max_tcp_speed_mps {vmax:g}", basis="computed"))
        sd = as_float((((p.get("parameters") or {}).get("speed_dps")) or {}).get("default"))
        if sd is not None and jmax is not None and sd > jmax:
            ck.add(Finding("primitive_library", Status.FAIL, f"{pid}: default joint speed {sd:g} deg/s exceeds max_joint_speed_dps {jmax:g}", basis="computed"))
        width = as_float(((p.get("parameters") or {}).get("width_m") or {}).get("max"))
        if width is not None and ck.tool.actuated and ck.tool.opening_max_m is not None and width > ck.tool.opening_max_m + 1e-9:
            ck.add(Finding("primitive_library", Status.FAIL, f"{pid}: width up to {width * 1000:.0f} mm exceeds the tool opening {ck.tool.opening_max_m * 1000:.0f} mm", basis="computed"))
