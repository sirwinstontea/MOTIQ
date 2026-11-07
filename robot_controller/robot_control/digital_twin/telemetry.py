"""UR joint telemetry, RECEIVE ONLY.

`RTDEJointSource` creates only `rtde_receive.RTDEReceiveInterface`: it cannot move, stop, power or configure the robot.
It works the same against URSim (127.0.0.1) and the physical UR5e. `FakeJointSource` replays a slow scripted motion
so the PyBullet side can be exercised with no robot and no URSim at all.

A sample is STALE when the controller timestamp has not advanced for `stale_after_ms` (frozen stream) or when no
sample could be read: callers must then stop advancing the simulation, not show an old pose as live.
"""
from __future__ import annotations

import importlib
import math
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


@dataclass(frozen=True)
class JointSample:
    q_rad: tuple[float, ...]            # actual joint positions, base..wrist3, rad
    qd_rad_s: tuple[float, ...]         # actual joint speeds, rad/s
    controller_ts: float                # controller clock (s since controller start); fake: monotonic
    mono_s: float                       # host time.monotonic() when read
    wall_s: float                       # host time.time() when read
    tcp_pose: tuple[float, ...] | None = None   # ACTIVE TCP (not the flange), informational only

    def moving(self, tol_rad_s: float) -> bool:
        return any(abs(v) > tol_rad_s for v in self.qd_rad_s)

    def age_ms(self, now_mono: float | None = None) -> float:
        return ((time.monotonic() if now_mono is None else now_mono) - self.mono_s) * 1000.0


class TelemetryError(RuntimeError):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


class JointSource(Protocol):
    kind: str

    def read(self) -> JointSample: ...
    def close(self) -> None: ...


class RTDEJointSource:
    """Receive-only RTDE. Reconnects with exponential backoff; never busy-loops."""

    kind = "rtde_receive"

    def __init__(self, host: str, backoff_max_s: float = 5.0, factory: Callable | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.host, self.backoff_max_s, self.clock = host, backoff_max_s, clock
        self._factory = factory
        self._rx = None
        self._next_try = 0.0
        self._backoff = 0.5
        self.last_error: str | None = None

    def _make(self):
        if self._factory is not None:
            return self._factory(self.host)
        mod = importlib.import_module("rtde_receive")      # receive interface only, by design
        return mod.RTDEReceiveInterface(self.host)

    def _ensure(self):
        if self._rx is not None:
            try:
                if self._rx.isConnected():
                    return self._rx
            except Exception:  # noqa: BLE001 - treat as disconnected
                pass
            self._drop()
        now = self.clock()
        if now < self._next_try:
            raise TelemetryError("robot_offline", f"reconnecting to {self.host} in {self._next_try - now:.1f} s "
                                 f"(last error: {self.last_error})")
        try:
            self._rx = self._make()
            self._backoff = 0.5
            return self._rx
        except Exception as exc:  # noqa: BLE001 - controller unreachable / RTDE busy
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._next_try = now + self._backoff
            self._backoff = min(self._backoff * 2, self.backoff_max_s)
            raise TelemetryError("robot_offline", f"cannot open RTDE receive to {self.host}: {self.last_error}") from exc

    def _drop(self):
        rx, self._rx = self._rx, None
        if rx is not None:
            try:
                rx.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def read(self) -> JointSample:
        rx = self._ensure()
        try:
            q = tuple(float(v) for v in rx.getActualQ())
            qd = tuple(float(v) for v in rx.getActualQd())
            ts = float(rx.getTimestamp())
            try:
                tcp = tuple(float(v) for v in rx.getActualTCPPose())
            except Exception:  # noqa: BLE001 - informational only
                tcp = None
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._drop()
            raise TelemetryError("robot_offline", f"RTDE read failed: {self.last_error}") from exc
        if len(q) != 6 or not all(math.isfinite(v) for v in q):
            raise TelemetryError("invalid_joints", f"RTDE returned invalid joints {q}")
        self.last_error = None
        return JointSample(q, qd if len(qd) == 6 else (0.0,) * 6, ts, time.monotonic(), time.time(), tcp)

    def close(self) -> None:
        self._drop()


# A comfortable UR5e pose above the table (rad), the fake script swings around it.
FAKE_HOME_RAD = (0.0, -1.571, 1.571, -1.571, -1.571, 0.0)


@dataclass
class FakeJointSource:
    """Scripted 'pendant' motion for simulation without any robot: a slow swing that pauses every `period_s`, so the
    stationary-only object updates also get exercised. Purely synthetic, clearly labelled as such."""

    period_s: float = 12.0
    amplitude_rad: tuple[float, ...] = (0.5, 0.25, 0.3, 0.3, 0.4, 0.6)
    home_rad: tuple[float, ...] = FAKE_HOME_RAD
    clock: Callable[[], float] = time.monotonic
    kind: str = field(default="fake", init=False)
    _t0: float = field(default=-1.0, init=False)

    def _phase(self, t: float) -> tuple[float, float]:
        """(position factor, speed factor): moves for the first half of each period, holds still for the second."""
        x = (t % self.period_s) / self.period_s
        cycle = int(t // self.period_s)
        sign = 1.0 if cycle % 2 == 0 else -1.0
        if x < 0.5:
            s = 0.5 - 0.5 * math.cos(2 * math.pi * x)          # 0 -> 1 over the moving half
            ds = math.pi * math.sin(2 * math.pi * x) / self.period_s   # d s / d t
            return (s if sign > 0 else 1 - s), (ds if sign > 0 else -ds)
        return (1.0 if sign > 0 else 0.0), 0.0

    def read(self) -> JointSample:
        now = self.clock()
        if self._t0 < 0:
            self._t0 = now
        t = now - self._t0
        f, df = self._phase(t)
        q = tuple(h + a * f for h, a in zip(self.home_rad, self.amplitude_rad))
        qd = tuple(a * df for a in self.amplitude_rad)
        return JointSample(q, qd, t, time.monotonic(), time.time(), None)

    def close(self) -> None:
        pass


def make_source(cfg) -> JointSource:
    r = cfg.physical_robot
    if r.telemetry == "fake":
        return FakeJointSource()
    return RTDEJointSource(r.host, backoff_max_s=r.reconnect_backoff_max_s)


class StaleTracker:
    """Detects a frozen stream: the controller timestamp must advance within `stale_after_ms`."""

    def __init__(self, stale_after_ms: float, clock: Callable[[], float] = time.monotonic):
        self.stale_after_s, self.clock = stale_after_ms / 1000.0, clock
        self._last_ts: float | None = None
        self._last_change = clock()
        self.last_good: JointSample | None = None

    def update(self, s: JointSample | None) -> bool:
        """Feed a sample (or None on a read failure). Returns True when the stream is fresh."""
        now = self.clock()
        if s is not None and (self._last_ts is None or s.controller_ts != self._last_ts):
            self._last_ts, self._last_change, self.last_good = s.controller_ts, now, s
        return s is not None and (now - self._last_change) <= self.stale_after_s

    def age_ms(self) -> float | None:
        return None if self.last_good is None else (self.clock() - self._last_change) * 1000.0
