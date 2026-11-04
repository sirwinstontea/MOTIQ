"""Physical checks on a step's primitive_sequence (template schema 1.1): does the *sequence* really do what the step claims?

 gripper_actuation   OPEN/CLOSE_GRIPPER need an actuated tool; opening width vs tool; force_pct vs needed grip force
 grasp_geometry      at CLOSE_GRIPPER the tool contact point must be inside the object's volume (else it closes on air)
 release_geometry    drop height at OPEN_GRIPPER, and the object must end inside the container's interior
 sequence_order      constraints.execution_constraints: open_gripper_before_descending, lift_before_horizontal_travel
 operational_limits  respect_operational_limits: speeds / step sizes / accelerations vs robot.operational_limits
 object_detection    'Gripper reports object detected' needs grasp feedback from the tool
"""
from __future__ import annotations

import re

import numpy as np

from .primitives import library, op_limits
from .report import Finding, Status
from .skill_model import as_float

GRAVITY = 9.81
FRICTION = 0.5


def _flag(ck, name):
    return bool((ck.skill.execution_flags or {}).get(name))


def check_sequences(ck, plan, params) -> None:
    log = [r for r in plan.seq_log if r.get("prim") != "START"]
    if not log:
        return
    skill, tool = ck.skill, ck.tool
    ol = op_limits(skill)
    steps = {s.index: s for s in skill.steps}
    table_z = ck.cell.table_z_base_m
    grasp_off = {}                                        # object id -> (center_z - tcp_z) at the grasp
    # ---- gripper actuation + order + geometry, in sequence order
    grip = None
    for r in log:
        st = steps[r["step"]]
        p = r["prim"]
        if p in ("OPEN_GRIPPER", "CLOSE_GRIPPER") and not tool.actuated:
            ck.add(Finding("gripper_actuation", Status.FAIL, f"{p} in {st.id}: '{tool.name}' has no actuator, the command does nothing",
                           needs=["an actuated gripper (the skill's robot.gripper assumes rq_open()/rq_close())"], basis="documented"), st)
        if p == "OPEN_GRIPPER":
            grip = "open"
            w = r.get("width")
            if tool.actuated and tool.opening_max_m is not None and w is not None and w > tool.opening_max_m + 1e-9:
                ck.add(Finding("gripper_actuation", Status.FAIL, f"OPEN_GRIPPER width {w * 1000:.0f} mm exceeds the tool's maximum opening {tool.opening_max_m * 1000:.0f} mm", basis="computed"), st)
            obj = skill.object_by_ref(st.target)
            if tool.actuated and obj is not None and obj.size is not None and w is not None and not r.get("released"):
                need = float(min(obj.size[:2])) + 0.004
                if w < need:
                    ck.add(Finding("gripper_actuation", Status.FAIL, f"opened to {w * 1000:.0f} mm but '{obj.id}' is {float(min(obj.size[:2])) * 1000:.0f} mm wide: the fingers cannot straddle it", basis="computed"), st)
            if r.get("released"):
                release_geometry(ck, r, st, grasp_off)
        elif p == "CLOSE_GRIPPER":
            grip = "closed"
            if r.get("grasped"):
                grasp_geometry(ck, r, st, grasp_off)
            force_check(ck, r, st)
        elif p == "MOVE_DOWN" and _flag(ck, "open_gripper_before_descending") and not r.get("held"):
            if r.get("grip") != "open":
                ck.add(Finding("sequence_order", Status.FAIL, f"{st.id}: MOVE_DOWN with the gripper not opened first (open_gripper_before_descending is required)", basis="computed"), st)
        if r.get("horizontal") and _flag(ck, "lift_before_horizontal_travel"):
            need_z = table_z + (as_float(ol.get("safe_travel_height_m")) or 0.0)
            if r["start_z"] < need_z - 1e-6:
                ck.add(Finding("sequence_order", Status.FAIL, f"{st.id}: {p} starts at z={r['start_z']:.3f} m, below the safe travel height ({need_z:.3f} m): lift_before_horizontal_travel violated", basis="computed"), st)
        if r.get("tilt") and r.get("held"):
            for c in skill.constraints.get("object_constraints", []) or []:
                if isinstance(c, dict) and str(c.get("type", "")).lower() in ("orientation", "upright") and skill.object_by_ref(c.get("object")) is not None and skill.object_by_ref(c.get("object")).id == r["held"]:
                    ck.add(Finding("orientation_constraint", Status.FAIL, f"{st.id}: {p} tilts the tool while '{r['held']}' must stay {c.get('required_value', 'upright')}", basis="computed"), st)
    # ---- operational limits
    if _flag(ck, "respect_operational_limits") and ol:
        vmax = as_float(ol.get("max_tcp_speed_mps"))
        app = as_float(ol.get("approach_speed_mps"))
        stepmax = as_float(ol.get("max_step_m"))
        for r in log:
            st = steps[r["step"]]
            v = r.get("speed")
            if v is not None and vmax is not None and v > vmax + 1e-9:
                ck.add(Finding("operational_limits", Status.FAIL, f"{st.id}: {r['prim']} speed {v:g} m/s exceeds operational max_tcp_speed_mps {vmax:g}", basis="computed"), st)
            if r["prim"] == "MOVE_DOWN" and v is not None and app is not None and v > app + 1e-9:
                ck.add(Finding("operational_limits", Status.FAIL, f"{st.id}: MOVE_DOWN speed {v:g} m/s exceeds approach_speed_mps {app:g}", basis="computed"), st)
            d = as_float((r.get("params") or {}).get("distance_m"))
            if d is not None and stepmax is not None and d > stepmax + 1e-9:
                ck.add(Finding("operational_limits", Status.FAIL, f"{st.id}: {r['prim']} step {d:g} m exceeds max_step_m {stepmax:g}", basis="computed"), st)
    # ---- grasp feedback
    wants = any("object detected" in str(x).lower() or "grasp is verified" in str(x).lower() for s in skill.steps for k in ("postconditions", "success_conditions") for x in (s.raw.get(k) or []))
    if wants and tool.actuated and (tool.raw.get("tool", {}).get("sensing") or {}).get("grasp_feedback") is None:
        ck.add(Finding("object_detection", Status.CONDITIONAL, "the skill needs the gripper to report 'object detected' (rq_is_object_detected); the tool profile declares no grasp feedback",
                       conditions=["gripper provides object-detection feedback (or the camera verifies the grasp)"], needs=["tool grasp feedback"], basis="assumed"))


def grasp_geometry(ck, r, st, grasp_off) -> None:
    obj = ck.skill.objects.get(r["grasped"])
    if obj is None or obj.position is None:
        return
    c = ck.planner.to_base(obj.position)
    size = obj.size if obj.size is not None else np.array([0.04] * 3)
    top, bottom = c[2] + size[2] / 2, c[2] - size[2] / 2
    tcp = r["pos"]
    dxy = float(np.hypot(tcp[0] - c[0], tcp[1] - c[1]))
    ev = {"tcp_z_m": round(float(tcp[2]), 3), "object_z_range_m": [round(float(bottom), 3), round(float(top), 3)], "xy_offset_m": round(dxy, 3), "object_size_basis": obj.size_basis}
    grasp_off[obj.id] = float(c[2] - tcp[2])
    if dxy > max(size[0], size[1]) / 2 + 0.01:
        ck.add(Finding("grasp_geometry", Status.FAIL, f"gripper closes {dxy * 100:.1f} cm beside '{obj.id}', not around it", evidence=ev, basis="computed"), st)
    elif tcp[2] > top + 0.005 or tcp[2] < bottom:
        ck.add(Finding("grasp_geometry", Status.FAIL, f"tool contact point at z={tcp[2]:.3f} m is outside '{obj.id}' (z {bottom:.3f}-{top:.3f} m): the gripper closes on air", evidence=ev, basis="computed"), st)
    elif tcp[2] > top - 0.005:
        ck.add(Finding("grasp_geometry", Status.CONDITIONAL, f"tool contact point is at the top face of '{obj.id}' (z={tcp[2]:.3f} m vs top {top:.3f} m): holds only if the fingers extend below the contact point",
                       evidence=ev, conditions=["fingertips extend at least a few mm below the TCP (tool geometry)"], needs=["finger geometry / TCP definition"], basis="computed"), st)
    else:
        ck.add(Finding("grasp_geometry", Status.PASS, f"gripper closes around '{obj.id}' (TCP z={tcp[2]:.3f} m inside z {bottom:.3f}-{top:.3f} m)", evidence=ev, basis="computed"), st)


def force_check(ck, r, st) -> None:
    obj = ck.skill.objects.get(r.get("grasped") or "")
    pct = r.get("force_pct")
    tool = ck.tool
    if not tool.actuated or obj is None or obj.mass_kg is None or tool.grip_force_n is None or pct is None:
        return
    f = tool.grip_force_n * pct / 100.0
    need = obj.mass_kg * GRAVITY / (2 * FRICTION)
    if f < need:
        ck.add(Finding("grasp_force", Status.FAIL, f"force_pct {pct:g}% = {f:.1f} N is below the {need:.1f} N needed to hold {obj.mass_kg:g} kg", basis="computed"), st)
    else:
        ck.add(Finding("grasp_force", Status.PASS, f"force_pct {pct:g}% = {f:.1f} N holds {obj.mass_kg:g} kg (needs {need:.1f} N)", basis="computed"), st)


def release_geometry(ck, r, st, grasp_off) -> None:
    obj = ck.skill.objects.get(r["released"])
    if obj is None:
        return
    size = obj.size if obj.size is not None else np.array([0.04] * 3)
    tcp = r["pos"]
    off = grasp_off.get(obj.id, 0.0)
    bottom = tcp[2] + off - size[2] / 2
    floor_z, where = ck.cell.table_z_base_m, "the table"
    tgt = ck.skill.object_by_ref(st.target)
    inside = None
    if tgt is not None and tgt.is_container and tgt.position is not None:
        c = ck.planner.to_base(tgt.position)
        inner = tgt.inner_size if tgt.inner_size is not None else tgt.size
        if inner is not None:
            half = np.array(inner[:2]) / 2
            inside = bool(abs(tcp[0] - c[0]) <= half[0] - size[0] / 2 + 0.005 and abs(tcp[1] - c[1]) <= half[1] - size[1] / 2 + 0.005)
        floor_z, where = float(c[2]), f"the floor of '{tgt.id}'"
    drop = float(bottom - floor_z)
    ev = {"drop_height_m": round(drop, 3), "released_over": where, "object_size_basis": obj.size_basis}
    if inside is False:
        ck.add(Finding("release_geometry", Status.FAIL, f"'{obj.id}' is released outside the interior of '{tgt.id}' (tool at {tcp[0]:.2f}, {tcp[1]:.2f} m)", evidence=ev, basis="computed"), st)
        return
    if drop < -0.002:
        ck.add(Finding("release_geometry", Status.FAIL, f"the object's bottom would be {-drop * 1000:.0f} mm below {where} at release: the tool is pressed into the surface", evidence=ev, basis="computed"), st)
    elif drop <= 0.01:
        ck.add(Finding("release_geometry", Status.PASS, f"released {max(drop, 0) * 1000:.0f} mm above {where}", evidence=ev, basis="computed"), st)
    else:
        ck.add(Finding("release_geometry", Status.CONDITIONAL, f"'{obj.id}' is dropped {drop * 100:.1f} cm onto {where}; it may bounce or tip, which would break 'stays upright / stable'",
                       evidence=ev, conditions=[f"a {drop * 100:.1f} cm drop does not tip or bounce the object out (object/surface physics)"], basis="computed"), st)
