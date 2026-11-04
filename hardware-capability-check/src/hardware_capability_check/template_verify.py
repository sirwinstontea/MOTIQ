"""VERIFY parameters (check_containment / check_orientation / check_stability), failure policies, success_criteria."""
from __future__ import annotations

import numpy as np

from .report import Finding, Status
from .skill_model import find_value, parse_vec3
from .template_util import vision_status


def occluded(cp, target, centre_xy, inner_xy, wall_top) -> bool:
    """True if the camera->target line enters the container footprint below the rim (i.e. passes through a wall)."""
    d = target - cp
    for t in np.linspace(0.0, 1.0, 2000):
        p = cp + d * t
        if abs(p[0] - centre_xy[0]) <= inner_xy[0] / 2 and abs(p[1] - centre_xy[1]) <= inner_xy[1] / 2:
            return bool(p[2] < wall_top - 1e-6)
    return False


def verify_containment(ck, s, obj, vst) -> None:
    cont = next((o for o in ck.skill.objects.values() if o.is_container), None)
    for c in ck.skill.constraints.get("object_constraints", []) or []:
        if isinstance(c, dict) and str(c.get("type", "")).lower() in ("containment", "inside") and ck.skill.object_by_ref(c.get("object")) is obj:
            cont = ck.skill.object_by_ref(c.get("container")) or cont
    mount = ck.cam.mount
    if cont is None or cont.position is None:
        ck.add(Finding("verify_containment", Status.UNKNOWN, "no container with a position to verify against", needs=["container position"], basis="unknown"), s)
    elif mount is None:
        ck.add(Finding("verify_containment", Status.CONDITIONAL, "containment can be seen if the camera looks into the container from above; camera mounting unknown",
                       conditions=["camera line of sight reaches the inside of the container (not blocked by its walls)"], needs=["camera mounting"], basis="assumed"), s)
    elif str(mount.get("type", "")).lower() != "fixed":
        ck.add(Finding("verify_containment", Status.CONDITIONAL, "an eye-in-hand camera can look straight down into the container",
                       conditions=["arm moves the camera above the container for the check"], basis="assumed"), s)
    else:
        cp = parse_vec3(mount.get("position_m"))
        inner = cont.inner_size if cont.inner_size is not None else cont.size
        if cp is None or inner is None or cont.wall_height_m is None:
            ck.add(Finding("verify_containment", Status.UNKNOWN, "camera position or container interior/wall height unknown: occlusion cannot be tested", needs=["camera position", "container interior size and wall height"], basis="unknown"), s)
            return
        c0 = ck.planner.to_base(cont.position) if ck.skill.frame != "base" else cont.position
        size = float(obj.size[2]) if obj is not None and obj.size is not None else 0.04
        tgt = np.array([c0[0], c0[1], float(c0[2]) + size / 2 + 0.005])
        if occluded(cp, tgt, c0[:2], inner[:2], float(c0[2]) + cont.wall_height_m):
            ck.add(Finding("verify_containment", Status.FAIL, "the camera's line of sight to the object passes through the container wall: the inside cannot be seen from this mount", basis="computed"), s)
        else:
            ck.add(Finding("verify_containment", Status.FAIL if vst == Status.FAIL else Status.PASS, "the camera sees the object inside the container over the rim" if vst != Status.FAIL else "camera does not cover the container", basis="computed"), s)
def check_verify_params(ck, specs) -> None:
    for s in ck.skill.steps:
        sp = specs[s.index]
        if sp is None or sp.canonical != "VERIFY":
            continue
        obj = ck.skill.object_by_ref(s.target)
        vst, _ = vision_status(ck, obj.id if obj else None)
        want = lambda k: find_value(s.params, (k,), max_depth=2) is True  # noqa: E731
        base = Status.CONDITIONAL if vst in (Status.CONDITIONAL, Status.INFO) else vst
        if want("check_containment"):
            verify_containment(ck, s, obj, vst)
        if want("check_orientation"):
            ck.add(Finding("verify_orientation", base, "orientation is read from the camera's RGB-D data (object must be resolved with >= 20 px)",
                           conditions=["camera covers the object (see vision)"] if base == Status.CONDITIONAL else [], basis="computed"), s)
        if want("check_stability"):
            ck.add(Finding("verify_stability", base, "stability is judged from repeated frames after release (camera runs 30-90 fps)",
                           conditions=["camera covers the object (see vision)"] if base == Status.CONDITIONAL else [], basis="computed"), s)


def check_failure_policies(ck, specs) -> None:
    for s in ck.skill.steps:
        pol = str(s.raw.get("failure_policy", "")).upper()
        if not pol:
            continue
        vst, _ = vision_status(ck)
        if "REOBSERV" in pol:
            st = vst if vst in (Status.FAIL, Status.UNKNOWN) else (Status.PASS if vst == Status.PASS else Status.CONDITIONAL)
            ck.add(Finding("failure_policy", st, f"policy {pol}: the camera must be able to re-observe the scene", conditions=[] if st == Status.PASS else ["camera covers the workspace"], basis="computed"), s)
        elif "REPLAN" in pol:
            ck.add(Finding("failure_policy", Status.CONDITIONAL, f"policy {pol}: needs a run-time planner and calibrated perception", conditions=["run-time planner + hand-eye calibration"], basis="assumed"), s)
        elif "RETRY" in pol or pol in ("STOP", "REPORT_FAILURE", "ABORT", "SKIP", "CONTINUE", "STOP_AND_REPORT"):
            ck.add(Finding("failure_policy", Status.PASS, f"policy {pol} needs no extra hardware", basis="computed"), s)
        else:
            ck.add(Finding("failure_policy", Status.UNKNOWN, f"policy '{pol}' is not understood", needs=[f"meaning of failure policy '{pol}'"], basis="unknown"), s)


def check_success_criteria(ck, specs) -> None:
    sc = ck.skill.raw.get("success_criteria")
    wanted = [k for k, v in (sc.items() if isinstance(sc, dict) else []) if v is True and k.endswith("_verified")]
    if not wanted:
        return
    vst, _ = vision_status(ck)
    has_verify = any(specs[s.index] and specs[s.index].canonical == "VERIFY" for s in ck.skill.steps)
    if vst == Status.FAIL:
        ck.add(Finding("success_criteria", Status.FAIL, f"{', '.join(wanted)} need a camera that covers the objects, which it does not", basis="computed"))
    elif not has_verify:
        ck.add(Finding("success_criteria", Status.UNKNOWN, f"{', '.join(wanted)} are required but no VERIFY step performs the check", needs=["a VERIFY step"], basis="unknown"))
    else:
        ck.add(Finding("success_criteria", Status.PASS if vst == Status.PASS else Status.CONDITIONAL, f"{', '.join(wanted)} are covered by the VERIFY step(s)",
                       conditions=[] if vst == Status.PASS else ["camera covers the objects"], basis="computed"))
