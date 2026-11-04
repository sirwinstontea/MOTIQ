"""Helpers shared by the template checks (finding lookups)."""
from __future__ import annotations

from .report import Status

_ORDER = {Status.FAIL: 4, Status.UNKNOWN: 3, Status.CONDITIONAL: 2, Status.PASS: 1, Status.INFO: 0}


def worst(statuses):
    real = [s for s in statuses if s != Status.INFO]
    return max(real, key=lambda s: _ORDER[s]) if real else Status.PASS


def all_findings(ck):
    return [f for lst in ck.step_findings.values() for f in lst] + list(ck.global_findings)


def motion_findings(ck):
    return [f for f in all_findings(ck) if f.check == "reach_and_path"]


def vision_status(ck, obj_id=None):
    fs = [f for f in all_findings(ck) if f.check == "vision"]
    if obj_id:
        fs = [f for f in fs if f"'{obj_id}'" in f.message] or fs
    return (worst([f.status for f in fs]) if fs else Status.CONDITIONAL), fs


def steps_of(ck, specs, canon):
    return [s for s in ck.skill.steps if specs[s.index] and specs[s.index].canonical == canon]
