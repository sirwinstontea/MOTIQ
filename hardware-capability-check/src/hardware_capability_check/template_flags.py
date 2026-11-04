"""constraints.execution_constraints flags of the skill template.

  collision_avoidance          arm vs table verified here; everything else needs an obstacle model
  require_workspace_validation every position must be reachable (derived from reach_and_path)
  require_grasp_validation     needs grasp feedback (tool sensing) or a camera that sees the grasp
  allow_adaptive_replanning    needs fresh perception + calibration
Handled in sequence_checks.py: open_gripper_before_descending, lift_before_horizontal_travel, respect_operational_limits.
"""
from __future__ import annotations

from .report import Finding, Status
from .template_util import motion_findings, steps_of, vision_status, worst


def check_collision(ck) -> None:
    conds = ["a motion planner with a model of every obstacle (UR controller has only safety planes and joint/TCP limits)"]
    needs = ["obstacle model of the cell (fixtures, box walls, camera mount)"]
    for s in ck.skill.steps:
        tgt = ck.skill.object_by_ref(s.target)
        if tgt is None or not tgt.is_container:
            continue
        inner = tgt.inner_size if tgt.inner_size is not None else (tgt.size if tgt.size is not None and tgt.size_basis == "stated" else None)
        r = ck.tool.envelope_radius_m
        if inner is not None and r is not None and float(min(inner[:2])) / 2 - r < 0.005:
            ck.add(Finding("collision_avoidance", Status.FAIL, f"tool radius {r * 1000:.0f} mm does not fit into the {min(inner[:2]) * 1000:.0f} mm opening of '{tgt.id}'", basis="computed"), s)
            return
        if r is None:
            conds.append(f"the tool tip fits into the opening of '{tgt.id}'")
            needs.append("tool envelope radius")
    ck.add(Finding("collision_avoidance", Status.CONDITIONAL, "arm-vs-table clearance is verified; collisions with anything else (box walls, fixtures, cables) need an external planner",
                   conditions=conds, needs=needs, basis="assumed"))


def check_workspace(ck) -> None:
    mf = motion_findings(ck)
    st = worst([f.status for f in mf]) if mf else Status.UNKNOWN
    if st == Status.FAIL:
        ck.add(Finding("workspace_validation", Status.FAIL, "a required position/path is outside the arm's valid workspace (see reach_and_path)", basis="computed"))
    elif st == Status.UNKNOWN:
        ck.add(Finding("workspace_validation", Status.UNKNOWN, "no motion could be validated", needs=["positions for every motion step"], basis="unknown"))
    else:
        ck.add(Finding("workspace_validation", st, "every required position is inside the arm's workspace",
                       conditions=[] if st == Status.PASS else ["cell assumptions (base pose, tool length) are correct"], basis="computed"))
def check_grasp_validation(ck, specs) -> None:
    if not ck.tool.actuated:
        return                                   # no grasp at all: already a FAIL elsewhere
    fb = (ck.tool.raw.get("tool", {}).get("sensing") or {}).get("grasp_feedback")
    for s in steps_of(ck, specs, "PICK"):
        vst, _ = vision_status(ck, getattr(ck.skill.object_by_ref(s.target), "id", None))
        if fb:
            ck.add(Finding("grasp_verification", Status.PASS, "the tool reports grasp success", basis="documented"), s)
        elif vst != Status.FAIL:
            ck.add(Finding("grasp_verification", Status.CONDITIONAL, "no declared grasp feedback; the grasp can only be verified by the camera after lifting",
                           conditions=["gripper reports object detected, or the camera sees the object held"], needs=["tool grasp feedback"], basis="assumed"), s)
        else:
            ck.add(Finding("grasp_verification", Status.FAIL, "no way to verify the grasp: no tool feedback and the camera cannot see the object", basis="computed"), s)


def check_adaptive(ck) -> None:
    vst, _ = vision_status(ck)
    if vst == Status.FAIL:
        ck.add(Finding("adaptive_replanning", Status.FAIL, "replanning needs fresh observations but the camera cannot see the workspace", basis="computed"))
    else:
        ck.add(Finding("adaptive_replanning", Status.CONDITIONAL, "the arm accepts new targets at run time (URScript/RTDE); works if perception is calibrated to the robot",
                       conditions=["hand-eye (camera-to-robot) calibration", "controller program that accepts updated target poses"], needs=["camera-to-robot calibration"], basis="assumed"))


def check_flags(ck, specs) -> None:
    flags = ck.skill.execution_flags or {}
    if flags.get("collision_avoidance"):
        check_collision(ck)
    if flags.get("require_workspace_validation"):
        check_workspace(ck)
    if flags.get("require_grasp_validation"):
        check_grasp_validation(ck, specs)
    if flags.get("allow_adaptive_replanning"):
        check_adaptive(ck)
