"""constraints.object_constraints: orientation/upright, containment, speed/force limits; unknown types -> UNKNOWN."""
from __future__ import annotations

from .report import Finding, Status
from .template_util import motion_findings, steps_of, worst

KNOWN = {"orientation", "upright", "containment", "inside", "speed_limit", "max_speed", "force_limit", "max_force"}


def orientation(ck, specs, c, obj) -> None:
    val = c.get("required_value", "upright")
    tilts = [s for s in ck.skill.steps if specs[s.index] and specs[s.index].canonical == "TILT"]
    if tilts:
        ck.add(Finding("orientation_constraint", Status.FAIL, f"'{c.get('object')}' must stay {val} but {tilts[0].id} tilts the tool", basis="computed"), tilts[0])
        return
    if not (ck.tool.actuated or ck.tool.passive_modes):
        ck.add(Finding("orientation_constraint", Status.FAIL, f"'{c.get('object')}' cannot be kept {val}: the tool cannot hold it at all", basis="computed"))
        return
    mf = motion_findings(ck)
    st = worst([f.status for f in mf]) if mf else Status.UNKNOWN
    if st in (Status.FAIL, Status.UNKNOWN):
        ck.add(Finding("orientation_constraint", st, f"the carry path with fixed tool orientation could not be validated for '{c.get('object')}'", basis="computed"))
    else:
        ck.add(Finding("orientation_constraint", Status.CONDITIONAL, f"carry moves keep the tool orientation fixed, so '{c.get('object')}' stays {val} if it does not rotate in the grasp",
                       conditions=["the grasp holds the object without rotating it"], basis="computed"))
def containment(ck, specs, c, obj) -> None:
    cont = ck.skill.object_by_ref(c.get("container"))
    if cont is None or not cont.is_container or cont.position is None:
        ck.add(Finding("containment_constraint", Status.UNKNOWN, f"container '{c.get('container')}' is missing or has no position", needs=["container with position and interior size"], basis="unknown"))
        return
    puts = bool(steps_of(ck, specs, "PLACE")) or any(r.get("released") == getattr(obj, "id", None) for r in getattr(ck, "seq_log_all", []))
    if not puts:
        ck.add(Finding("containment_constraint", Status.FAIL, f"'{c.get('object')}' must end inside '{cont.id}' but no step puts it there", basis="computed"))


def limit(ck, ctype, c) -> None:
    v = c.get("value", c.get("max"))
    try:
        v = float(v)
    except (TypeError, ValueError):
        ck.add(Finding("object_constraint", Status.UNKNOWN, f"{ctype} has no numeric value", needs=["numeric limit"], basis="unknown"))
        return
    if "speed" in ctype:
        ck.add(Finding("speed", Status.PASS if 0 < v <= ck.robot.tcp_speed_max_m_s else Status.FAIL, f"speed limit {v:g} m/s vs arm maximum {ck.robot.tcp_speed_max_m_s:g} m/s", basis="documented"))
    else:
        ok = v >= ck.robot.ft_accuracy_n
        ck.add(Finding("force", Status.PASS if ok else Status.CONDITIONAL, f"force limit {v:g} N vs force sensor accuracy +-{ck.robot.ft_accuracy_n:g} N",
                       conditions=[] if ok else ["limit enforced without sensor feedback"], basis="documented"))


def check_object_constraints(ck, specs) -> None:
    for c in ck.skill.constraints.get("object_constraints", []) or []:
        if not isinstance(c, dict):
            continue
        t = str(c.get("type", "")).strip().lower()
        obj = ck.skill.object_by_ref(c.get("object"))
        if t not in KNOWN:
            ck.add(Finding("object_constraint", Status.UNKNOWN, f"constraint '{t}' on '{c.get('object')}' has no hardware interpretation", needs=[f"physical meaning of constraint '{t}'"], basis="unknown"))
        elif t in ("orientation", "upright"):
            orientation(ck, specs, c, obj)
        elif t in ("containment", "inside"):
            containment(ck, specs, c, obj)
        else:
            limit(ck, t, c)
