"""URSim + PyBullet synchronized backend (SIMULATION ONLY: URSim on localhost).

Every approved segment runs on two simulators in lock-step:

1. PyBullet resolves the named target, solves IK and collision-tests the path
   exactly as ``pybullet`` does (inherited).
2. The motion is executed on **URSim** (the real UR controller software, no
   hardware) through RTDE: ``moveJ`` for joint segments, ``moveL_FK`` for linear
   ones, with the configured speed/acceleration limits.
3. While URSim moves, its *actual* joints are streamed into the PyBullet arm,
   so the cell shows what the controller is really doing (held objects follow).
4. After the move, URSim's joints must match the planned joints and PyBullet must
   match URSim (feedback reconciliation). Before every move both must already
   be in sync, URSim must be RUNNING with safety NORMAL and not stopped.

Host restriction: ``ursim_host`` must be a loopback address. A real arm is never
on 127.0.0.1, so this backend cannot command physical hardware. Controlling a
real UR needs a separate, reviewed adapter (see docs/SAFETY_BOUNDARIES.md).

The gripper stays symbolic (PyBullet attach/detach); URSim has no gripper.
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
from .pybullet_sim import PyBulletSimController

SYNC_TOL_DEG = 0.5            # PyBullet vs URSim joints before a move
ARRIVE_TOL_DEG = 0.5          # URSim actual vs planned joints after a move
MIRROR_PERIOD_S = 0.02        # 50 Hz joint streaming into PyBullet
IDLE_MIRROR_PERIOD_S = 0.05   # 20 Hz between tasks (jogging in PolyScope)
IDLE_MIRROR_DEADBAND_DEG = 0.01
SCRIPT_START_TIMEOUT_S = 5.0  # wait for a re-uploaded RTDE control script to report running
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


class URSimSyncController(PyBulletSimController):
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
        # Idle mirror: PyBullet follows URSim between tasks too (e.g. jogging in PolyScope).
        self._mirror_stop = threading.Event()
        self._mirror_pause = threading.Event()   # set while collision tests move the PyBullet model
        self._mirror_thread: threading.Thread | None = None
        self._executing = threading.Event()      # set while _execute drives URSim

    # ------------------------------------------------------------ idle mirror
    def _start_idle_mirror(self):
        self._mirror_stop.clear()
        self._mirror_thread = threading.Thread(target=self._idle_mirror_loop, name="ursim-idle-mirror", daemon=True)
        self._mirror_thread.start()

    def _stop_idle_mirror(self):
        self._mirror_stop.set()
        t, self._mirror_thread = self._mirror_thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2.0)

    def _idle_mirror_loop(self):
        """Copy URSim joints into PyBullet whenever URSim moves."""
        import rtde_receive
        rec = None
        last = None
        while not self._mirror_stop.is_set():
            try:
                if rec is None:
                    rec = rtde_receive.RTDEReceiveInterface(self.ursim_host)
                    last = None
                q = [math.degrees(v) for v in rec.getActualQ()]
                changed = last is None or max(abs(a - b) for a, b in zip(q, last)) > IDLE_MIRROR_DEADBAND_DEG
                if changed and not self._mirror_pause.is_set() and self.robot is not None:
                    self.robot.setJoints(q)
                    if last is not None and not self._executing.is_set():
                        self._at_target = None   # moved by hand: no longer at a taught target
                    last = q
            except Exception as e:     # URSim restarted: drop the receive link and retry
                log.debug("idle mirror: %s; reconnecting", e)
                try:
                    rec and rec.disconnect()
                except Exception:
                    pass
                rec = None
                self._mirror_stop.wait(1.0)
                continue
            self._mirror_stop.wait(IDLE_MIRROR_PERIOD_S)
        try:
            rec and rec.disconnect()
        except Exception:
            pass

    def test_segment(self, seg):
        # Collision tests move the PyBullet arm and restore it; keep the mirror off it meanwhile.
        self._mirror_pause.set()
        try:
            return super().test_segment(seg)
        finally:
            self._mirror_pause.clear()

    def _ensure_control_script(self):
        """Stopping the program in PolyScope (needed to jog) kills the RTDE control script; re-upload it."""
        with self._rtde_lock:
            c = self.rtde_c
            if c is None:
                return
            # URSim was restarted (the bridge starts it on demand in SIM=1): the receive link is dead too
            r = self.rtde_r
            try:
                if r is not None and not r.isConnected():
                    log.info("URSim RTDE receive disconnected (URSim restarted?); reconnecting")
                    r.reconnect()
            except Exception as e:
                log.warning("could not reconnect URSim RTDE receive: %s", e)
            try:
                if not c.isConnected():
                    c.reconnect()
                if not c.isProgramRunning():
                    log.info("URSim control script not running (stopped in PolyScope?); re-uploading")
                    c.reuploadScript()
                    # isProgramRunning() lags the upload by a few hundred ms (measured).
                    deadline = time.monotonic() + SCRIPT_START_TIMEOUT_S
                    while not c.isProgramRunning() and time.monotonic() < deadline:
                        time.sleep(0.1)
                    c.setTcp(list(self.robot_cfg.tcp_offset_m_rad))
            except Exception as e:
                log.warning("could not restart the URSim control script: %s", e)

    # ------------------------------------------------------------ lifecycle
    def connect(self):
        super().connect()             # PyBullet cell: arm, parts, home joints
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
        # Same TCP on both sides, so URSim TCP readouts and moveL paths match the PyBullet tool.
        with self._rtde_lock:
            self.rtde_c.setTcp(list(self.robot_cfg.tcp_offset_m_rad))
        self._mirror()                # initial sync: URSim is the source of truth for the pose
        self._start_idle_mirror()

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
        self._stop_idle_mirror()
        self._close_rtde()
        super().disconnect()

    def capabilities(self):
        caps = super().capabilities()
        return Capabilities(**{**caps.model_dump(), "backend": self.backend_name,
                               "motion_enabled": self.connected and self.rtde_c is not None,
                               "notes": caps.notes + [f"motion executed on URSim at {self.ursim_host} "
                                                      "(controller simulator, no hardware) and mirrored into PyBullet",
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
        # Only task preflight/execution call this (not status polling), so taking URSim back
        # from PolyScope here happens only when a task is actually requested.
        self._ensure_control_script()
        try:
            problems += self._ursim_problems()
            if not problems:
                ur = self._ursim_joints_deg()
                dk = self.robot.Joints().list()
                err = max(abs(a - b) for a, b in zip(ur, dk))
                if err > SYNC_TOL_DEG:
                    problems.append(f"PyBullet and URSim out of sync by {err:.2f} deg (was the arm jogged "
                                    "in URSim? reconnect to re-sync from URSim)")
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
        self._executing.set()
        try:
            self._execute_locked(end_deg, motion, ctx, label)
        finally:
            self._executing.clear()

    def _execute_locked(self, end_deg, motion: MotionType, ctx: MotionContext, label: str):
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
            raise RobotControlError(ErrorCode.BACKEND_ERROR, f"PyBullet failed to follow URSim at {label}")

    def _follow(self, end_deg, motion: MotionType, ctx: MotionContext, label: str):
        """Stream URSim joints into PyBullet until the async move has started AND finished."""
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
