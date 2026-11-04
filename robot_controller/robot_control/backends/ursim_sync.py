"""URSim + RoboDK synchronized backend (SIMULATION ONLY: URSim on localhost).

Every approved segment runs on two simulators in lock-step:

1. RoboDK (digital twin) resolves the named target, solves IK and collision-tests
   the path exactly as ``robodk_sim`` does (inherited).
2. The motion is executed on **URSim** (the real UR controller software, no
   hardware) through RTDE: ``moveJ`` for joint segments, ``moveL_FK`` for linear
   ones, with the configured speed/acceleration limits.
3. While URSim moves, its *actual* joints are streamed into the RoboDK robot,
   so RoboDK shows what the controller is really doing (held objects follow).
4. After the move, URSim's joints must match the planned joints and RoboDK must
   match URSim (feedback reconciliation). Before every move both must already
   be in sync, URSim must be RUNNING with safety NORMAL and not stopped.

Host restriction: ``ursim_host`` must be a loopback address. A real arm is never
on 127.0.0.1, so this backend cannot command physical hardware. Controlling a
real UR needs a separate, reviewed adapter (see docs/SAFETY_BOUNDARIES.md).

The gripper stays symbolic (RoboDK attach/detach); URSim has no gripper.
"""
from __future__ import annotations

import ipaddress
import logging
import math
import threading
import time

from ..config import RobotConfig, SceneConfig
from ..controller import Capabilities, MotionContext, MotionType
from ..models import ErrorCode, RobotControlError
from .robodk_sim import RoboDKSimController

SYNC_TOL_DEG = 0.5            # RoboDK vs URSim joints before a move
ARRIVE_TOL_DEG = 0.5          # URSim actual vs planned joints after a move
MIRROR_PERIOD_S = 0.02        # 50 Hz joint streaming into RoboDK
UR_ROBOT_MODE_RUNNING = 7
UR_SAFETY_MODE_NORMAL = 1
STOP_DECEL_RAD_S2 = 2.0
STOP_DECEL_M_S2 = 1.0
START_GRACE_S = 1.5         # time allowed for URSim to begin an async move
START_RETRIES = 1           # re-send a dropped (never started) command once

log = logging.getLogger(__name__)


class _NotStarted(Exception):
    """URSim accepted an async command but never began moving."""


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class URSimSyncController(RoboDKSimController):
    backend_name = "ursim_sync"

    def __init__(self, robot: RobotConfig, scene: SceneConfig, *, ursim_host: str | None = None, **kwargs):
        super().__init__(robot, scene, **kwargs)
        self.ursim_host = ursim_host or robot.ursim_host
        if not is_loopback(self.ursim_host):
            raise RobotControlError(ErrorCode.MODE_VIOLATION,
                                    f"ursim_sync only talks to URSim on localhost; refusing host '{self.ursim_host}'. "
                                    "Real-arm control needs a separate reviewed adapter.")
        self.rtde_c = self.rtde_r = None
        # ur_rtde interfaces are not thread-safe; concurrent calls (e.g. a telemetry thread)
        # were observed to make URSim silently drop async move commands.
        self._rtde_lock = threading.RLock()
        self._si = None               # (tcp_v m/s, tcp_a m/s2, joint_v rad/s, joint_a rad/s2)

    # ------------------------------------------------------------ lifecycle
    def connect(self):
        super().connect()             # RoboDK: simulate mode, robot/tool/base selection
        try:
            import rtde_control
            import rtde_receive
        except ImportError as e:
            raise RobotControlError(ErrorCode.BACKEND_NOT_CONNECTED,
                                    "ur_rtde not installed: python -m pip install ur_rtde") from e
        try:
            self.rtde_r = rtde_receive.RTDEReceiveInterface(self.ursim_host)
        except Exception as e:
            raise RobotControlError(ErrorCode.BACKEND_NOT_CONNECTED,
                                    f"URSim RTDE not reachable at {self.ursim_host}: {e}. Is the ursim container "
                                    "running?") from e
        problems = self._ursim_problems(check_program=False)
        if problems:
            self._close_rtde()
            raise RobotControlError(ErrorCode.BACKEND_NOT_CONNECTED, "URSim not ready: " + "; ".join(problems))
        try:
            self.rtde_c = rtde_control.RTDEControlInterface(self.ursim_host)
        except Exception as e:
            self._close_rtde()
            raise RobotControlError(ErrorCode.BACKEND_NOT_CONNECTED,
                                    f"URSim RTDE control failed: {e} (enable Remote Control in PolyScope?)") from e
        # Same TCP on both sides, so URSim TCP readouts and moveL paths match RoboDK's tool.
        with self._rtde_lock:
            self.rtde_c.setTcp(list(self.robot_cfg.tcp_offset_m_rad))
        self._mirror()                # initial sync: URSim is the source of truth for the pose

    def _close_rtde(self):
        with self._rtde_lock:
            self._close_rtde_locked()

    def _close_rtde_locked(self):
        for iface, stop in ((self.rtde_c, True), (self.rtde_r, False)):
            if iface is None:
                continue
            try:
                if stop:
                    iface.stopScript()
                iface.disconnect()
            except Exception:
                pass
        self.rtde_c = self.rtde_r = None

    def disconnect(self):
        self._close_rtde()
        super().disconnect()

    def capabilities(self):
        caps = super().capabilities()
        return Capabilities(**{**caps.model_dump(), "backend": self.backend_name,
                               "motion_enabled": self.connected and self.rtde_c is not None,
                               "notes": caps.notes + [f"motion executed on URSim at {self.ursim_host} "
                                                      "(controller simulator, no hardware) and mirrored into RoboDK",
                                                      "localhost only: cannot command a physical arm"]})

    # ------------------------------------------------------------ helpers
    def _ursim_joints_deg(self):
        with self._rtde_lock:
            return [math.degrees(q) for q in self.rtde_r.getActualQ()]

    def _mirror(self):
        q = self._ursim_joints_deg()
        self.robot.setJoints(q)
        return q

    def _ursim_problems(self, check_program=True):
        with self._rtde_lock:
            return self._ursim_problems_locked(check_program)

    def _ursim_problems_locked(self, check_program):
        p = []
        r = self.rtde_r
        if r is None or not r.isConnected():
            return ["URSim RTDE receive not connected"]
        mode, safety = r.getRobotMode(), r.getSafetyMode()
        if mode != UR_ROBOT_MODE_RUNNING:
            p.append(f"URSim robot mode {mode} != RUNNING(7): power on + release brakes in PolyScope")
        if safety != UR_SAFETY_MODE_NORMAL:
            p.append(f"URSim safety mode {safety} != NORMAL(1)")
        if r.isProtectiveStopped():
            p.append("URSim is protective-stopped")
        if r.isEmergencyStopped():
            p.append("URSim is emergency-stopped")
        if check_program and (self.rtde_c is None or not self.rtde_c.isConnected()
                              or not self.rtde_c.isProgramRunning()):
            p.append("URSim RTDE control script not running")
        return p

    # ------------------------------------------------------------ state / readiness
    def check_ready(self):
        problems = super().check_ready()
        if not self.connected:
            return problems
        try:
            problems += self._ursim_problems()
            if not problems:
                ur = self._ursim_joints_deg()
                dk = self.robot.Joints().list()
                err = max(abs(a - b) for a, b in zip(ur, dk))
                if err > SYNC_TOL_DEG:
                    problems.append(f"RoboDK and URSim out of sync by {err:.2f} deg (was the robot jogged "
                                    "in RoboDK? reconnect to re-sync from URSim)")
        except Exception as e:
            problems.append(f"URSim readiness query failed: {e}")
        return problems

    def apply_motion_limits(self, tcp_speed_m_s, tcp_accel_m_s2, joint_speed_rad_s, joint_accel_rad_s2):
        super().apply_motion_limits(tcp_speed_m_s, tcp_accel_m_s2, joint_speed_rad_s, joint_accel_rad_s2)
        self._si = (tcp_speed_m_s, tcp_accel_m_s2, joint_speed_rad_s, joint_accel_rad_s2)

    # ------------------------------------------------------------ motion
    def _execute(self, end_deg, motion: MotionType, ctx: MotionContext, label: str):
        self._require()
        self._enforce_simulate()
        problems = self.check_ready()
        if problems:
            raise RobotControlError(ErrorCode.MODE_VIOLATION, "; ".join(problems))
        if self._si is None:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, "motion limits not applied before motion")
        tcp_v, tcp_a, j_v, j_a = self._si
        q = [math.radians(v) for v in end_deg]
        self._stop.clear()
        start_deg = self._ursim_joints_deg()
        try:
            for attempt in range(1 + START_RETRIES):
                self._send(q, motion, label, (tcp_v, tcp_a, j_v, j_a))
                try:
                    actual = self._follow(end_deg, motion, ctx, label)
                    break
                except _NotStarted:
                    # ur_rtde occasionally drops an async command issued right after the
                    # previous one finished. Safe to re-send only if URSim has not moved.
                    now = self._ursim_joints_deg()
                    moved = max(abs(a - b) for a, b in zip(now, start_deg))
                    with self._rtde_lock:
                        steady = self.rtde_c.isSteady()
                    if attempt == START_RETRIES or moved > 0.05 or not steady:
                        raise RobotControlError(ErrorCode.BACKEND_ERROR,
                                                f"URSim did not start the move to {label}") from None
                    log.warning("URSim dropped the move to %s; re-sending once", label)
        except BaseException:
            self._halt(motion)            # never leave URSim moving after an error/cancel
            try:
                self._mirror()
            except Exception:
                pass
            raise
        err = max(abs(a - b) for a, b in zip(actual, end_deg))
        if err > ARRIVE_TOL_DEG:
            raise RobotControlError(ErrorCode.BACKEND_ERROR,
                                    f"URSim did not reach {label} (joint error {err:.2f} deg)")
        dk = self.robot.Joints().list()
        if max(abs(a - b) for a, b in zip(actual, dk)) > SYNC_TOL_DEG:
            raise RobotControlError(ErrorCode.BACKEND_ERROR, f"RoboDK failed to follow URSim at {label}")

    def _follow(self, end_deg, motion: MotionType, ctx: MotionContext, label: str):
        """Stream URSim joints into RoboDK until the async move has started AND finished."""
        t0 = time.monotonic()
        deadline = t0 + ctx.timeout_s
        started = False
        while True:
            actual = self._mirror()
            with self._rtde_lock:
                r = self.rtde_r
                safety_stop = (r.isProtectiveStopped() or r.isEmergencyStopped()
                               or r.getSafetyMode() != UR_SAFETY_MODE_NORMAL)
                running = self.rtde_c.getAsyncOperationProgress() >= 0
                steady = self.rtde_c.isSteady()
            if safety_stop:
                raise RobotControlError(ErrorCode.SAFETY_VETO, f"URSim safety stop during move to {label}")
            near = max(abs(a - b) for a, b in zip(actual, end_deg)) <= ARRIVE_TOL_DEG
            if running or not steady:
                started = True
            if not running and steady and (started or near):
                if near or time.monotonic() - t0 > START_GRACE_S:
                    return actual      # arrived (or stopped short: caller reports the error)
            elif not started and time.monotonic() - t0 > START_GRACE_S:
                raise _NotStarted()
            if ctx.cancel.is_set():
                raise RobotControlError(ErrorCode.CANCELLED, f"move to {label} cancelled")
            if self._stop.is_set():
                raise RobotControlError(ErrorCode.STOPPED, f"move to {label} stopped")
            if time.monotonic() > deadline:
                raise RobotControlError(ErrorCode.TIMEOUT, f"move to {label} exceeded {ctx.timeout_s:.1f} s")
            time.sleep(MIRROR_PERIOD_S)

    def _send(self, q, motion: MotionType, label: str, si):
        tcp_v, tcp_a, j_v, j_a = si
        try:
            with self._rtde_lock:
                ok = (self.rtde_c.moveJ(q, j_v, j_a, True) if motion == "joint"
                      else self.rtde_c.moveL_FK(q, tcp_v, tcp_a, True))
        except Exception as e:
            raise RobotControlError(ErrorCode.BACKEND_ERROR, f"URSim rejected move to {label}: {e}") from e
        if ok is False:
            raise RobotControlError(ErrorCode.BACKEND_ERROR, f"URSim refused move to {label}")

    def _halt(self, motion: MotionType):
        try:
            with self._rtde_lock:
                self._halt_locked(motion)
        except Exception:
            pass

    def _halt_locked(self, motion: MotionType):
        try:
            if motion == "joint":
                self.rtde_c.stopJ(STOP_DECEL_RAD_S2)
            else:
                self.rtde_c.stopL(STOP_DECEL_M_S2)
        except Exception:
            pass

    def stop(self):
        super().stop()                # the motion loop sees the flag and calls stopJ/stopL
