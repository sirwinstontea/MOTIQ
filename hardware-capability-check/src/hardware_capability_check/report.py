"""Result objects of a hardware capability check and their text / JSON rendering."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np


class Status(str, Enum):
    PASS = "pass"                  # hardware can do it; proven with the numbers in `evidence`
    CONDITIONAL = "conditional"    # can do it IF the listed conditions on unknown values hold
    UNKNOWN = "unknown"            # cannot decide: required information is missing
    FAIL = "fail"                  # cannot be done with this hardware
    INFO = "info"                  # context, no verdict


class Verdict(str, Enum):
    """Final answer: only YES or NO. YES = no blocker found and nothing undecidable (conditions may still be listed).
    NO = a hard blocker exists OR something required cannot be decided (missing information counts as NO)."""
    YES = "yes"
    NO = "no"


_ORDER = {Status.FAIL: 4, Status.UNKNOWN: 3, Status.CONDITIONAL: 2, Status.PASS: 1, Status.INFO: 0}


def worst(statuses: list[Status]) -> Status:
    real = [s for s in statuses if s != Status.INFO]
    if not real:
        return Status.INFO
    return max(real, key=lambda s: _ORDER[s])


def _jsonable(x: Any) -> Any:
    if isinstance(x, np.ndarray):
        return [_jsonable(v) for v in x.tolist()]
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, Enum):
        return x.value
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, float):
        return round(x, 6)
    return x


@dataclass
class Finding:
    check: str                      # e.g. "reach", "joint_limits", "payload"
    status: Status
    message: str
    step_id: str | None = None
    evidence: dict = field(default_factory=dict)
    conditions: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)       # information the user should supply
    sources: list[str] = field(default_factory=list)     # profile fields / documents the verdict rests on
    basis: str = "computed"          # documented | computed | assumed | unknown


@dataclass
class StepReport:
    step_id: str
    action: str
    target: str | None
    description: str
    status: Status
    findings: list[Finding]


@dataclass
class FeasibilityReport:
    skill_id: str
    skill_name: str
    robot: str
    tool: str
    camera: str
    verdict: Verdict
    physically_possible_if_conditions_hold: bool
    detail: str                     # why: feasible | feasible_with_conditions | needs_validation | infeasible
    summary: str
    steps: list[StepReport]
    global_findings: list[Finding]
    conditions: list[str]
    missing_information: list[str]
    assumptions: list[str]
    metrics: dict
    profile_conflicts: list[dict]
    parse_issues: list[str]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    schema_version: str = "feasibility_report/1"

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    # --------------------------------------------------------------------------- text rendering
    def to_text(self) -> str:
        icon = {"pass": "[PASS]", "conditional": "[COND]", "unknown": "[????]", "fail": "[FAIL]", "info": "[info]"}
        lines: list[str] = []
        lines.append("=" * 78)
        lines.append(f"HARDWARE CAPABILITY CHECK - {self.skill_name} ({self.skill_id})")
        lines.append(f"robot: {self.robot} | tool: {self.tool} | camera: {self.camera}")
        lines.append("=" * 78)
        lines.append(f"VERDICT: {self.verdict.value.upper()}  (detail: {self.detail})")
        lines.append(self.summary)
        lines.append("")
        for sr in self.steps:
            lines.append(f"{icon[sr.status.value]} {sr.step_id}  {sr.action}  {sr.target or ''}  - {sr.description}")
            for f in sr.findings:
                if f.status == Status.INFO and not f.message:
                    continue
                lines.append(f"      {icon[f.status.value]} {f.check}: {f.message}")
                for c in f.conditions:
                    lines.append(f"            if: {c}")
        if self.global_findings:
            lines.append("")
            lines.append("Whole-skill checks:")
            for f in self.global_findings:
                lines.append(f"  {icon[f.status.value]} {f.check}: {f.message}")
                for c in f.conditions:
                    lines.append(f"        if: {c}")
        if self.metrics:
            lines.append("")
            lines.append("Key numbers:")
            for k, v in self.metrics.items():
                lines.append(f"  {k}: {v}")
        if self.missing_information:
            lines.append("")
            lines.append("Information needed to remove the doubt:")
            for m in self.missing_information:
                lines.append(f"  - {m}")
        if self.conditions:
            lines.append("")
            lines.append("Conditions under which the positive results hold:")
            for c in self.conditions:
                lines.append(f"  - {c}")
        if self.assumptions:
            lines.append("")
            lines.append("Assumptions used (change profiles/*.yaml to correct them):")
            for a in self.assumptions:
                lines.append(f"  - {a}")
        if self.parse_issues:
            lines.append("")
            lines.append("Skill file notes:")
            for p in self.parse_issues:
                lines.append(f"  - {p}")
        lines.append("")
        lines.append("Scope: physical capability of the listed hardware only. This is not a safety certification,")
        lines.append("collision-free trajectory plan, or approval to run the robot.")
        return "\n".join(lines)
