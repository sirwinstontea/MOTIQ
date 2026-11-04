"""Freshness and deadline helpers."""
from __future__ import annotations

import time

from ..models import ErrorCode, RobotControlError


def state_age_ms(sampled_monotonic: float) -> float:
    return (time.monotonic() - sampled_monotonic) * 1000.0


class Deadline:
    """Action-level timeout. ``check()`` raises TIMEOUT once expired."""

    def __init__(self, seconds: float, what: str):
        self.what = what
        self.seconds = seconds
        self.end = time.monotonic() + seconds

    def remaining(self) -> float:
        return self.end - time.monotonic()

    def check(self) -> None:
        if self.remaining() <= 0:
            raise RobotControlError(ErrorCode.TIMEOUT, f"{self.what} exceeded {self.seconds:.1f} s")
