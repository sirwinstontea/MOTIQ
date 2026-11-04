"""Path check adapters.

The default delegates to the backend's ``test_segment`` (RoboDK: MoveJ_Test /
MoveL_Test with snapshot/restore of the simulated joints). A digital-twin check
is NOT proof of physical clearance.
"""
from __future__ import annotations

from typing import Protocol

from ..controller import PathCheckResult, PlannedSegment, RobotController


class PathChecker(Protocol):
    def check(self, seg: PlannedSegment) -> PathCheckResult: ...


class BackendPathChecker:
    def __init__(self, controller: RobotController):
        self.controller = controller

    def check(self, seg: PlannedSegment) -> PathCheckResult:
        try:
            return self.controller.test_segment(seg)
        except Exception as e:  # any failure => inconclusive => reject
            return PathCheckResult(ok=False, conclusive=False, reason=f"path check error: {e}")


class VetoPathChecker:
    """Test helper / operator override: vetoes listed targets as colliding."""

    def __init__(self, inner: PathChecker, veto_targets: set[str]):
        self.inner = inner
        self.veto_targets = set(veto_targets)

    def check(self, seg: PlannedSegment) -> PathCheckResult:
        if seg.target_id in self.veto_targets:
            return PathCheckResult(ok=False, conclusive=True, collisions=1,
                                   reason=f"collision predicted on segment to '{seg.target_id}'")
        return self.inner.check(seg)
