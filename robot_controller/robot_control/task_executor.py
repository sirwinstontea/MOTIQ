"""Sequential task runner with an explicit FSM, cancellation and fault latching.

* One task at a time; the executor owns the robot while a task runs.
* VALIDATING runs a full preflight (backend/mode, state freshness, IDs,
  target existence, reachability, and a symbolic dry-run of grip state)
  before any motion.
* Every action and every motion segment passes through ``SafetyPolicy``.
* Any failure, timeout, stale data or safety veto stops the task and latches
  FAULT. Only ``reset_fault(operator=...)`` clears it — never the AI.
* No automatic retries of motion.
"""
from __future__ import annotations

import itertools
import logging
import threading
from typing import Callable, Optional

from .action_registry import error_info, validate_task
from .config import RobotConfig, SafetyConfig, cross_validate, motion_limits, motion_limits_for_percent
from .controller import MotionContext, MotionType, RobotController
from .planning import DIRECTIONS, circle_waypoints, table_surface
from .performance import record_task, snapshot as performance_snapshot, timed
from .models import (ActionName, ActionRequest, ActionStatus, ErrorCode, ErrorInfo, Event, Feedback, PlanStep,
                     RobotControlError, SafetyChecks, TaskRequest, TaskState, TaskStatus, Pose, utc_now)
from .safety.limits import joints_within_limits, pose_within_workspace
from .safety.policy import SafetyDecision, SafetyPolicy
from .safety.trajectory import BackendPathChecker, PathChecker
from .safety.watchdog import Deadline, state_age_ms
from .scene_registry import SceneRegistry
from .vision import Detector, NoDetector

log = logging.getLogger("robot_control.executor")
S = TaskState

# --------------------------------------------------------------------------
# Explicit transition table
# --------------------------------------------------------------------------
ACTION_ENTRY = {S.IDENTIFYING, S.APPROACH_PICK, S.MOVING, S.TRANSPORTING, S.SHAKING, S.APPROACH_PLACE, S.RELEASING,
                S.PUSHING}
RESTING = {S.VALIDATING, S.IDENTIFIED, S.LIFTING, S.MOVING, S.TRANSPORTING, S.SHAKING, S.RETREATING, S.RELEASING,
           S.PUSHING}
TERMINAL_STOP = {S.FAULT, S.CANCELLED}

TRANSITIONS: dict[TaskState, set[TaskState]] = {s: set() for s in TaskState}
TRANSITIONS[S.IDLE] = {S.VALIDATING}
TRANSITIONS[S.COMPLETED] = {S.VALIDATING, S.IDLE}
TRANSITIONS[S.FAULT] = {S.IDLE}          # operator reset only
TRANSITIONS[S.CANCELLED] = {S.IDLE}      # operator reset only
for _s in RESTING:
    TRANSITIONS[_s] |= ACTION_ENTRY | {S.COMPLETED}
TRANSITIONS[S.IDENTIFYING] |= {S.IDENTIFIED}
TRANSITIONS[S.APPROACH_PICK] |= {S.AT_PICK}
TRANSITIONS[S.AT_PICK] |= {S.GRIPPING}
TRANSITIONS[S.GRIPPING] |= {S.VERIFYING_GRIP}
TRANSITIONS[S.VERIFYING_GRIP] |= {S.LIFTING}
TRANSITIONS[S.APPROACH_PLACE] |= {S.AT_PLACE}
TRANSITIONS[S.AT_PLACE] |= {S.RELEASING}
TRANSITIONS[S.RELEASING] |= {S.RETREATING}
for _s in TaskState:
    if _s not in {S.IDLE, S.COMPLETED} | TERMINAL_STOP:
        TRANSITIONS[_s] |= {S.FAULT, S.CANCELLED}

REJECT_CODES = {ErrorCode.SAFETY_VETO, ErrorCode.COLLISION_PREDICTED, ErrorCode.UNREACHABLE,
                ErrorCode.NEAR_SINGULAR,
                ErrorCode.PATH_CHECK_INCONCLUSIVE, ErrorCode.STALE_DATA, ErrorCode.PRECONDITION_FAILED,
                ErrorCode.UNKNOWN_ID, ErrorCode.MODE_VIOLATION, ErrorCode.PHYSICAL_BACKEND_DISABLED,
                ErrorCode.CONFIG_ERROR, ErrorCode.UNSUPPORTED, ErrorCode.VALIDATION_ERROR,
                ErrorCode.UNKNOWN_ACTION, ErrorCode.NOT_AVAILABLE}


JOINT_MATCH_RAD = 0.009   # ~0.5 deg: actual start must match the checked start


class _Veto(RobotControlError):
    def __init__(self, decision: SafetyDecision):
        super().__init__(decision.code or ErrorCode.SAFETY_VETO, "; ".join(decision.reasons))
        self.decision = decision


class TaskExecutor:
    def __init__(self, controller: RobotController, robot_cfg: RobotConfig, safety_cfg: SafetyConfig,
                 scene: SceneRegistry, detector: Optional[Detector] = None,
                 path_checker: Optional[PathChecker] = None,
                 on_event: Optional[Callable[[Event], None]] = None,
                 on_step: Optional[Callable[[dict], None]] = None):
        cross_validate(robot_cfg, safety_cfg, scene.cfg)       # CONFIG_ERROR => refuse to construct
        self.c = controller
        self.motion_limits = motion_limits(robot_cfg, safety_cfg)
        self.robot_cfg = robot_cfg
        self.safety_cfg = safety_cfg
        self.scene = scene
        self.detector = detector or NoDetector()
        self.policy = SafetyPolicy(robot_cfg, safety_cfg, path_checker or BackendPathChecker(controller))
        self.policy.backend_name = controller.backend_name
        self.on_event = on_event
        self.on_step = on_step
        self._lock = threading.RLock()
        self._ids = itertools.count(1)
        self.state: TaskState = S.IDLE
        self._latched: Optional[ErrorInfo] = None
        self._tasks: dict[str, TaskStatus] = {}
        self._idem: dict[str, str] = {}
        self._active: Optional[str] = None
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.transition_log: list[tuple[str, str]] = []
        self._cur_status: Optional[TaskStatus] = None
        self._cur_action: Optional[ActionRequest] = None
        self._active_motion_limits = self.motion_limits

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        self.c.connect()
        ml = self.motion_limits
        self.c.apply_motion_limits(ml.tcp_speed_m_s, ml.tcp_accel_m_s2, ml.joint_speed_rad_s, ml.joint_accel_rad_s2)

    # ------------------------------------------------------------------ FSM
    def _go(self, new: TaskState) -> None:
        with self._lock:
            if new not in TRANSITIONS[self.state]:
                raise RobotControlError(ErrorCode.BACKEND_ERROR,
                                        f"illegal state transition {self.state.value} -> {new.value}")
            self.transition_log.append((self.state.value, new.value))
            self.state = new

    def reset_fault(self, operator: str) -> None:
        """Explicit operator action. Not exposed as an AI tool."""
        if not operator or not operator.strip():
            raise RobotControlError(ErrorCode.VALIDATION_ERROR, "operator name required to reset")
        with self._lock:
            if self._active:
                raise RobotControlError(ErrorCode.BUSY, "cannot reset while a task is running")
            log.warning("operator %s reset latched state %s", operator, self.state.value)
            if self.state != S.IDLE:
                self._go(S.IDLE)
            self._latched = None

    # ------------------------------------------------------------------ speed
    def reset_simulation_fault(self) -> dict:
        """Explicit UI recovery, never motion or automatic retry. Hardware/URSim excluded."""
        with self._lock:
            if self.c.backend_name != "robodk_sim":
                raise RobotControlError(ErrorCode.MODE_VIOLATION, "reset is restricted to robodk_sim")
            if self._active:
                raise RobotControlError(ErrorCode.BUSY, "cannot reset while a task is active")
            self._require(self.policy.check_backend(self.c))
            st = self.c.get_state()
            self._require(self.policy.check_state(st))
            if st.busy:
                raise RobotControlError(ErrorCode.BUSY, "simulated robot is still moving")
            reasons = joints_within_limits(st.joints.values, self.robot_cfg)
            if st.tcp_pose is None:
                reasons.append("TCP feedback unavailable")
            else:
                reasons += pose_within_workspace(st.tcp_pose, self.safety_cfg.workspace, self.robot_cfg)
            if reasons:
                raise RobotControlError(ErrorCode.SAFETY_VETO, "; ".join(reasons))
            self.reset_fault("simulation UI")
            self._emit(None, None, "INFO", message="simulation fault reset explicitly; no motion or retry")
            return {"reset": True, "executor_state": self.state.value, "simulation": True,
                    "robot_model": self.robot_cfg.model, "moved": False}

    @property
    def speed_percent(self) -> Optional[float]:
        """Current speed as % of the model maximums (None if the config uses absolute values)."""
        return self.safety_cfg.speed_percent_of_model_max

    def speed_info(self) -> dict:
        return {"speed_percent": self.speed_percent, "motion_limits": self.motion_limits.model_dump(mode="json"),
                "min_percent": 1, "max_percent": 100}

    def set_speed_percent(self, percent) -> dict:
        """Change speed/acceleration to ``percent`` of the robot model's maximums.
        Refused while a task runs, so a move never changes speed halfway; it applies to the next task."""
        try:
            pct = round(float(percent), 1)
        except (TypeError, ValueError):
            raise RobotControlError(ErrorCode.VALIDATION_ERROR, f"speed percent must be a number, got {percent!r}")
        with self._lock:
            if self._active is not None:
                raise RobotControlError(ErrorCode.BUSY,
                                        f"task '{self._active}' is running; change the speed after it finishes")
            try:
                new_cfg = SafetyConfig.model_validate({**self.safety_cfg.model_dump(), "speed_percent_of_model_max": pct,
                                                       "tcp_speed_m_s": None, "tcp_accel_m_s2": None,
                                                       "joint_speed_rad_s": None, "joint_accel_rad_s2": None})
            except ValueError:
                raise RobotControlError(ErrorCode.VALIDATION_ERROR, f"speed must be > 0 and <= 100 %, got {pct:g}")
            cross_validate(self.robot_cfg, new_cfg, self.scene.cfg)   # never above the model maximums
            ml = motion_limits(self.robot_cfg, new_cfg)
            self.c.apply_motion_limits(ml.tcp_speed_m_s, ml.tcp_accel_m_s2, ml.joint_speed_rad_s, ml.joint_accel_rad_s2)
            self.safety_cfg, self.motion_limits = new_cfg, ml
            self.policy.safety = new_cfg
        log.warning("speed set to %g%% (%s)", pct, ml.source)
        return self.speed_info()

    @property
    def latched(self) -> Optional[ErrorInfo]:
        return self._latched

    # ------------------------------------------------------------------ events
    def _feedback(self) -> Optional[Feedback]:
        try:
            st = self.c.get_state()
        except Exception:
            return None
        return Feedback(tcp_pose=st.tcp_pose, joints=st.joints, holding_object_id=st.holding_object_id,
                        grip_confirmed=st.grip_confirmed, gripper_state=st.gripper_state,
                        feedback_source=st.feedback_source, data_age_ms=round(state_age_ms(st.sampled_monotonic), 1),
                        detector=self.detector.name)

    def _emit(self, task: Optional[TaskStatus], action: Optional[ActionRequest], status, *, error=None,
              safety: Optional[SafetyChecks] = None, message=None, result=None, with_feedback=True) -> Event:
        ev = Event(event_id=next(self._ids), task_id=task.task_id if task else None,
                   action_id=action.action_id if action else None,
                   action=action.action.value if action else None, state=self.state, status=status,
                   backend=self.c.backend_name, robot_model=self.robot_cfg.model,
                   parameters=action.parameters if action else {},
                   feedback=self._feedback() if with_feedback else None, error=error, safety_checks=safety,
                   message=message, result=result)
        if task is not None:
            task.events.append(ev)
        log.info("event %s", ev.model_dump_json(exclude_none=True))
        if self.on_event:
            self.on_event(ev)
        return ev

    # ------------------------------------------------------------------ public API
    def submit(self, task_data, wait: bool = False) -> TaskStatus:
        task = task_data if isinstance(task_data, TaskRequest) else validate_task(task_data)
        with self._lock:
            if task.idempotency_key and task.idempotency_key in self._idem:
                return self._tasks[self._idem[task.idempotency_key]]
            if self._latched is not None:
                raise RobotControlError(ErrorCode.FAULT_LATCHED,
                                        f"executor latched in {self.state.value}: {self._latched.message}; operator reset required")
            if self._active is not None:
                raise RobotControlError(ErrorCode.BUSY, f"task '{self._active}' is running; one task at a time")
            if task.task_id in self._tasks:
                raise RobotControlError(ErrorCode.DUPLICATE_TASK, f"task_id '{task.task_id}' already used")
            status = TaskStatus(task_id=task.task_id, status=ActionStatus.PENDING, state=self.state,
                                actions={a.action_id: ActionStatus.PENDING for a in task.actions})
            self._tasks[task.task_id] = status
            if task.idempotency_key:
                self._idem[task.idempotency_key] = task.task_id
            self._active = task.task_id
            self._cancel.clear()
            self._thread = threading.Thread(target=self._run, args=(task, status), daemon=True,
                                            name=f"task-{task.task_id}")
            self._thread.start()
        if wait:
            self._thread.join()
        return status

    def wait(self, timeout: Optional[float] = None) -> None:
        t = self._thread
        if t:
            t.join(timeout)

    def get_task(self, task_id: str) -> TaskStatus:
        if task_id not in self._tasks:
            raise RobotControlError(ErrorCode.UNKNOWN_ID, f"unknown task '{task_id}'")
        return self._tasks[task_id]

    def cancel(self, task_id: str) -> TaskStatus:
        st = self.get_task(task_id)
        if self._active == task_id:
            self._cancel.set()
        return st

    def stop_robot(self) -> None:
        """Controlled stop request: cancels the running task and stops motion. Not an E-stop."""
        self._cancel.set()
        self.c.stop()

    def robot_state(self) -> dict:
        st = self.c.get_state()
        d = st.model_dump(mode="json")
        d.pop("sampled_monotonic", None)
        d["data_age_ms"] = round(state_age_ms(st.sampled_monotonic), 1)
        d["executor_state"] = self.state.value
        d["active_task"] = self._active
        d["latched_error"] = self._latched.model_dump(mode="json") if self._latched else None
        d["capabilities"] = self.c.capabilities().model_dump(mode="json")
        d["motion_limits"] = self.motion_limits.model_dump(mode="json")
        d["speed_percent"] = self.speed_percent
        d["workspace"] = self.safety_cfg.workspace.model_dump(mode="json")
        d["reach_m"] = self.robot_cfg.limits.reach_m
        return d

    # ------------------------------------------------------------------ run loop
    def _run(self, task: TaskRequest, status: TaskStatus) -> None:
        task_limits = (motion_limits_for_percent(self.robot_cfg, task.speed_percent)
                       if task.speed_percent is not None else self.motion_limits)
        status.started_utc = utc_now()
        status.status = ActionStatus.RUNNING
        status.speed_percent = task.speed_percent if task.speed_percent is not None else self.speed_percent
        status.motion_limits = task_limits.model_dump(mode="json")
        current: Optional[ActionRequest] = None
        self._cur_status, self._cur_action = status, None
        try:
            self._active_motion_limits = task_limits
            self.c.apply_motion_limits(task_limits.tcp_speed_m_s, task_limits.tcp_accel_m_s2,
                                       task_limits.joint_speed_rad_s, task_limits.joint_accel_rad_s2)
            if self.state == S.COMPLETED:
                self._go(S.IDLE)
            self._go(S.VALIDATING)
            status.state = self.state
            self._emit(status, None, "INFO", message=f"validating task (preflight); speed {task_limits.source}")
            self._preflight(task)
            status.plan = self._build_plan(task)
            self._emit(status, None, "INFO", message=f"plan ready: {len(status.plan)} steps", with_feedback=False)
            self._notify()
            for action in task.actions:
                current = action
                self._cur_action = action
                self._check_cancel()
                status.current_action_id = action.action_id
                status.actions[action.action_id] = ActionStatus.RUNNING
                safety = self._action_gate()
                self._emit(status, action, ActionStatus.RUNNING, safety=safety.to_checks())
                result = self._dispatch(action)
                status.actions[action.action_id] = ActionStatus.SUCCEEDED
                status.state = self.state
                self._emit(status, action, ActionStatus.SUCCEEDED, result=result)
            self._close_step("done")
            self._go(S.COMPLETED)
            status.state = self.state
            status.execution = self._execution_summary(task)
            status.status = ActionStatus.SUCCEEDED
            self._notify("task completed")
            self._emit(status, None, "INFO", message="task completed")
        except RobotControlError as e:
            self._finish_error(status, current, e, task)
        except Exception as e:  # unexpected => fault, never success
            log.exception("unexpected executor error")
            self._finish_error(status, current, RobotControlError(ErrorCode.BACKEND_ERROR, repr(e)), task)
        finally:
            self.c.clear_transient_plans()
            if status.execution is None:
                status.execution = self._execution_summary(task)
            try:
                default = self.motion_limits
                self.c.apply_motion_limits(default.tcp_speed_m_s, default.tcp_accel_m_s2,
                                           default.joint_speed_rad_s, default.joint_accel_rad_s2)
            except Exception:
                log.exception("could not restore default motion limits after task")
            self._active_motion_limits = self.motion_limits
            status.finished_utc = utc_now()
            status.current_action_id = None
            with self._lock:
                self._active = None

    def _execution_summary(self, task: TaskRequest) -> dict:
        out = {"robot_model": self.robot_cfg.model, "backend": self.c.backend_name,
               "simulation": True, "requested_speed_percent": task.speed_percent,
               "accepted_speed_percent": self._cur_status.speed_percent if self._cur_status else None,
               "applied_motion_limits": (self._cur_status.motion_limits if self._cur_status else None),
               "performance": performance_snapshot()}
        try:
            st = self.c.get_state()
            out["final_tcp_pose"] = st.tcp_pose.model_dump(mode="json") if st.tcp_pose else None
            out["final_joints_rad"] = list(st.joints.values) if st.joints else None
        except Exception as e:
            out["feedback_error"] = str(e)
        return out

    def _finish_error(self, status: TaskStatus, action: Optional[ActionRequest], e: RobotControlError,
                      task: TaskRequest):
        info = error_info(e)
        self._close_step("failed")
        if e.code in (ErrorCode.CANCELLED, ErrorCode.STOPPED):
            astatus, tstatus, final = ActionStatus.CANCELLED, ActionStatus.CANCELLED, S.CANCELLED
        elif e.code in REJECT_CODES:
            astatus, tstatus, final = ActionStatus.REJECTED, ActionStatus.REJECTED, S.FAULT
        else:
            astatus, tstatus, final = ActionStatus.FAILED, ActionStatus.FAILED, S.FAULT
        try:
            self.c.stop()
        except Exception:
            pass
        if action is not None:
            status.actions[action.action_id] = astatus
        for aid, st in status.actions.items():
            if st == ActionStatus.PENDING:      # never started
                status.actions[aid] = ActionStatus.CANCELLED
        with self._lock:
            self._go(final)                     # FAULT/CANCELLED reachable from every active state
            self._latched = info
        status.state = self.state
        status.execution = self._execution_summary(task)
        status.status = tstatus
        status.error = info
        safety = SafetyChecks(approved=False, reasons=list(e.decision.reasons)) if isinstance(e, _Veto) else None
        self._emit(status, action, astatus, error=info, safety=safety,
                   message="task stopped; operator reset required before the next task")
        self._notify(f"STOPPED: {info.code.value}: {info.message}")

    def _check_cancel(self):
        if self._cancel.is_set():
            raise RobotControlError(ErrorCode.CANCELLED, "task cancelled by request")

    # ------------------------------------------------------------------ gates
    def _require(self, d: SafetyDecision) -> SafetyDecision:
        if not d.approved:
            raise _Veto(d)
        return d

    def _action_gate(self) -> SafetyDecision:
        b = self._require(self.policy.check_backend(self.c))
        s = self._require(self.policy.check_state(self.c.get_state()))
        return SafetyDecision.ok(*b.reasons, *s.reasons)

    def _preflight(self, task: TaskRequest) -> None:
        self._action_gate()
        sc = self.scene
        targets: list[str] = []
        st = self.c.get_state()
        holding, gripper = st.holding_object_id, st.gripper_state
        for a in task.actions:
            p = a.parameters
            n = a.action
            if n == ActionName.IDENTIFY_OBJECT:
                sc.object(p["object_id"])
            elif n == ActionName.PICK_OBJECT:
                o = sc.object(p["object_id"])
                if holding:
                    raise RobotControlError(ErrorCode.PRECONDITION_FAILED, f"{a.action_id}: already holding '{holding}'")
                if o.mass_kg is not None and o.mass_kg + self.robot_cfg.tool_mass_kg > self.robot_cfg.limits.max_payload_kg:
                    raise RobotControlError(ErrorCode.SAFETY_VETO, f"{a.action_id}: payload exceeds model limit")
                self.c.validate_pick(p["object_id"])
                self.c.retarget_pick(p["object_id"])     # simulation: targets over the object's real pose
                targets += [o.approach_target, o.grasp_target, o.lift_target]
                holding, gripper = p["object_id"], "closed"
            elif n == ActionName.MOVE_OBJECT:
                sc.object(p["object_id"])
                d = sc.destination(p["destination_id"])
                if holding != p["object_id"]:
                    raise RobotControlError(ErrorCode.PRECONDITION_FAILED,
                                            f"{a.action_id}: move_object requires holding '{p['object_id']}'")
                if self.c.backend_name != "robodk_sim" or d.support_surface == "taught":
                    targets.append(d.approach_target)
            elif n == ActionName.PLACE_OBJECT:
                d = sc.destination(p["destination_id"])
                if not holding:
                    raise RobotControlError(ErrorCode.PRECONDITION_FAILED, f"{a.action_id}: place_object requires a held object")
                if self.c.backend_name != "robodk_sim" or d.support_surface == "taught":
                    targets += [d.approach_target, d.place_target, d.retreat_target]
                holding, gripper = None, "open"
            elif n == ActionName.RELEASE_OBJECT:
                if holding:
                    raise RobotControlError(ErrorCode.PRECONDITION_FAILED,
                                            f"{a.action_id}: release while holding is only allowed via place_object")
                gripper = "open"
            elif n == ActionName.PUSH_OBJECT:
                o = sc.object(p["object_id"])
                if o.avoid_collision:
                    raise RobotControlError(ErrorCode.SAFETY_VETO, "object is tagged avoid_collision; push refused")
                if o.push is None:
                    raise RobotControlError(ErrorCode.PRECONDITION_FAILED,
                                            f"{a.action_id}: '{p['object_id']}' cannot be pushed (no push path in the scene)")
                if holding:
                    raise RobotControlError(ErrorCode.PRECONDITION_FAILED,
                                            f"{a.action_id}: push_object needs an empty gripper (holding '{holding}')")
                if p.get("direction"):
                    if self.c.backend_name != "robodk_sim":
                        raise RobotControlError(ErrorCode.MODE_VIOLATION, "directional push is RoboDK simulation-only")
                    self.c.prepare_push(p["object_id"], p["direction"], p["distance_m"])
                targets += [o.push.above_target, o.push.start_target, o.push.end_target, o.push.lift_target]
                gripper = "closed"
            elif n in (ActionName.FOLLOW_PATH, ActionName.CIRCLE_OBJECT, ActionName.PLACE_ON_TABLE):
                if self.c.backend_name not in ("robodk_sim", "mock"):
                    raise RobotControlError(ErrorCode.MODE_VIOLATION, "custom paths and table placement are simulation-only")
                if n == ActionName.CIRCLE_OBJECT:
                    sc.object(p["object_id"])
                if n == ActionName.PLACE_ON_TABLE:
                    if not holding:
                        raise RobotControlError(ErrorCode.PRECONDITION_FAILED, "place_on_table requires a held object")
                    x, y = self._placement_xy(p, holding)
                    table_surface(sc.cfg, x, y, sc.object(holding).dimensions_m)
                    holding, gripper = None, "open"
            elif n == ActionName.MOVE_TO_TARGET:
                sc.target(p["target_id"])
                targets.append(p["target_id"])
            elif n == ActionName.MOVE_RELATIVE:
                caps = self.c.capabilities()
                if not caps.cartesian_relative or p["reference_frame"] not in caps.reference_frames:
                    raise RobotControlError(ErrorCode.UNSUPPORTED,
                                            f"{self.robot_cfg.model} on {self.c.backend_name} does not support "
                                            f"relative Cartesian motion in frame '{p['reference_frame']}'")
                seg = self.c.plan_cartesian_relative(p["direction"], p["distance_m"], p["reference_frame"])
                self._require(self.policy.check_segment(seg))
            elif n == ActionName.MOVE_JOINT:
                if not self.c.capabilities().joint_motion:
                    raise RobotControlError(ErrorCode.UNSUPPORTED,
                                            f"{self.robot_cfg.model} on {self.c.backend_name} does not support joint motion")
                if p["joint_index"] > self.robot_cfg.dof:
                    raise RobotControlError(ErrorCode.VALIDATION_ERROR,
                                            f"joint_index {p['joint_index']} outside 1..{self.robot_cfg.dof}")
                seg = self.c.plan_joint_move(p["joint_index"], delta_rad=p.get("delta_rad"),
                                             target_rad=p.get("target_rad"))
                self._require(self.policy.check_segment(seg))
            elif n == ActionName.SHAKE_OBJECT:
                pat = sc.pattern(p["pattern_id"])
                if p["cycles"] > pat.max_cycles or p["cycles"] > self.safety_cfg.max_shake_cycles:
                    raise RobotControlError(ErrorCode.SAFETY_VETO,
                                            f"{a.action_id}: cycles {p['cycles']} exceeds limit "
                                            f"(pattern {pat.max_cycles}, safety {self.safety_cfg.max_shake_cycles})")
                if pat.requires_grip and not holding:
                    raise RobotControlError(ErrorCode.PRECONDITION_FAILED, f"{a.action_id}: shake requires a held object")
                targets += [pat.start_target, *pat.cycle_targets, pat.end_target]
            elif n == ActionName.PERFORM_MOTION:
                m = sc.motion(p["motion_id"])
                if m.requires_empty_gripper and holding:
                    raise RobotControlError(ErrorCode.PRECONDITION_FAILED, f"{a.action_id}: motion requires empty gripper")
                targets += m.targets
        # Existence + reachability (IK, joint limits, workspace) of every referenced target.
        for t in dict.fromkeys(targets):
            sc.target(t)
            if not self.c.target_exists(t):
                raise RobotControlError(ErrorCode.UNKNOWN_ID, f"target '{t}' is registered but missing in the {self.c.backend_name} station")
            seg = self.c.plan_segment(t, "joint")
            if seg.end_joints is None or seg.end_pose is None:
                raise RobotControlError(ErrorCode.UNREACHABLE, f"target '{t}' is not reachable")
            reasons = joints_within_limits(seg.end_joints, self.robot_cfg) + \
                pose_within_workspace(seg.end_pose, self.safety_cfg.workspace, self.robot_cfg)
            if reasons:
                raise _Veto(SafetyDecision.deny(ErrorCode.SAFETY_VETO, f"target '{t}': " + "; ".join(reasons)))

    # ------------------------------------------------------------------ motion segment
    def _segment(self, target_id: str, motion: MotionType, deadline: Deadline) -> None:
        self._segments([(target_id, motion)], deadline)

    def _segments(self, moves: list[tuple[str, MotionType]], deadline: Deadline,
                  on_move: Optional[dict] = None, between: Optional[Callable[[], object]] = None) -> None:
        """Run consecutive moves with no gripper change in between.

        All segments are planned (chained: each starts where the previous one
        ends) and safety/collision-checked BEFORE the first one moves, so the
        robot does not stop to think between them. Immediately before each move
        the actual joints must equal the start the check assumed; otherwise that
        segment is re-planned and re-checked from the real position.
        """
        self._check_cancel()
        deadline.check()
        self._require(self.policy.check_state(self.c.get_state()))
        planned = []
        start = None
        for target_id, motion in moves:
            seg = self.c.plan_segment(target_id, motion, start)
            decision = self._require(self.policy.check_segment(seg))
            self._emit(self._cur_status, self._cur_action, "INFO", safety=decision.to_checks(),
                       message=f"segment {motion} -> {target_id} approved", with_feedback=False)
            planned.append(seg)
            start = seg.end_joints
        for i, seg in enumerate(planned):
            if i and between:
                between()                    # e.g. grip still verified during a shake
            if on_move and seg.target_id in on_move:
                on_move[seg.target_id]()     # FSM sub-state for this move
            self._check_cancel()
            deadline.check()
            st = self.c.get_state()
            self._require(self.policy.check_state(st))
            if max(abs(a - b) for a, b in zip(st.joints.values, seg.start_joints)) > JOINT_MATCH_RAD:
                seg = self.c.plan_segment(seg.target_id, seg.motion)        # re-plan from where we really are
                decision = self._require(self.policy.check_segment(seg))
                self._emit(self._cur_status, self._cur_action, "INFO", safety=decision.to_checks(),
                           message=f"segment {seg.motion} -> {seg.target_id} re-checked from actual position",
                           with_feedback=False)
            self._step("move", seg.target_id)
            ctx = MotionContext(cancel=self._cancel,
                                timeout_s=max(0.01, min(self.safety_cfg.segment_timeout_s, deadline.remaining())))
            self.c.move_to_named_target(seg, ctx)

    # ------------------------------------------------------------------ actions
    def _dispatch(self, a: ActionRequest) -> dict:
        handler = {
            ActionName.GET_ROBOT_STATE: self._a_state,
            ActionName.GET_SCENE: self._a_scene,
            ActionName.IDENTIFY_OBJECT: self._a_identify,
            ActionName.PICK_OBJECT: self._a_pick,
            ActionName.MOVE_OBJECT: self._a_move_object,
            ActionName.PLACE_OBJECT: self._a_place,
            ActionName.RELEASE_OBJECT: self._a_release,
            ActionName.MOVE_TO_TARGET: self._a_move_to_target,
            ActionName.MOVE_RELATIVE: self._a_move_relative,
            ActionName.MOVE_JOINT: self._a_move_joint,
            ActionName.FOLLOW_PATH: self._a_follow_path,
            ActionName.CIRCLE_OBJECT: self._a_circle_object,
            ActionName.PLACE_ON_TABLE: self._a_place_on_table,
            ActionName.SHAKE_OBJECT: self._a_shake,
            ActionName.PERFORM_MOTION: self._a_motion,
            ActionName.PUSH_OBJECT: self._a_push,
        }.get(a.action)
        if handler is None:
            raise RobotControlError(ErrorCode.UNSUPPORTED, f"'{a.action.value}' is not a task step")
        return handler(a.parameters, Deadline(self.safety_cfg.action_timeout_s, a.action.value))

    def _dynamic_segment(self, seg, dl: Deadline) -> dict:
        decision = self._require(self.policy.check_segment(seg))
        self._emit(self._cur_status, self._cur_action, "INFO", safety=decision.to_checks(),
                   message=f"segment {seg.motion} -> {seg.target_id} approved", with_feedback=False)
        self._step("move", seg.target_id)
        ctx = MotionContext(cancel=self._cancel,
                            timeout_s=max(0.01, min(self.safety_cfg.segment_timeout_s, dl.remaining())))
        self.c.move_to_named_target(seg, ctx)
        st = self.c.get_state()
        self._require(self.policy.check_state(st))
        return {"motion_type": seg.motion, "executed_trajectory_type": seg.motion,
                "requested_target": seg.requested,
                "final_tcp_pose": st.tcp_pose.model_dump(mode="json") if st.tcp_pose else None,
                "final_joints_rad": list(st.joints.values) if st.joints else None,
                "speed_percent": self._cur_status.speed_percent if self._cur_status else None,
                "applied_motion_limits": self._active_motion_limits.model_dump(mode="json")}

    def _a_move_relative(self, p, dl):
        self._go(S.MOVING)
        seg = self.c.plan_cartesian_relative(p["direction"], p["distance_m"], p["reference_frame"])
        return self._dynamic_segment(seg, dl)

    def _a_move_joint(self, p, dl):
        self._go(S.MOVING)
        seg = self.c.plan_joint_move(p["joint_index"], delta_rad=p.get("delta_rad"),
                                     target_rad=p.get("target_rad"))
        return self._dynamic_segment(seg, dl)

    def _plan_pose_path(self, poses, dl):
        planned, start = [], None
        for pose in poses:
            self._check_cancel()
            dl.check()
            seg = self.c.plan_pose(pose, start)
            self._require(self.policy.check_segment(seg))
            planned.append(seg)
            start = seg.end_joints
        return planned

    def _execute_pose_path(self, planned, dl):
        self._step("move")
        for seg in planned:
            self._check_cancel()
            dl.check()
            self._action_gate()
            actual = self.c.get_joints().values
            if len(actual) != len(seg.start_joints) or max(abs(a-b) for a,b in zip(actual, seg.start_joints)) > JOINT_MATCH_RAD:
                raise RobotControlError(ErrorCode.STALE_DATA, "robot start changed since path validation; no automatic replan")
            self._dynamic_segment(seg, dl)

    def _a_follow_path(self, p, dl):
        self._go(S.MOVING)
        current = self.c.get_tcp_pose()
        poses = [Pose(**point, rx=current.rx, ry=current.ry, rz=current.rz) for point in p["waypoints"]]
        self._execute_pose_path(self._plan_pose_path(poses, dl), dl)
        return {"trajectory_type": "linear_waypoints", "waypoints": len(poses), "orientation_preserved": True}

    def _a_circle_object(self, p, dl):
        self._go(S.MOVING)
        current = self.c.get_tcp_pose()
        poses = circle_waypoints(self.c.object_position(p["object_id"]), p["radius_m"], p["z_m"], p["clockwise"], current,
                                 p.get("max_chord_error_m"))
        self._execute_pose_path(self._plan_pose_path(poses, dl), dl)
        return {"trajectory_type": "polygonal_circle", "waypoints": len(poses), "object_id": p["object_id"],
                "radius_m": p["radius_m"], "z_m": p["z_m"], "global_detour_planner": False,
                "max_chord_error_m": p.get("max_chord_error_m")}

    def _a_place_on_table(self, p, dl):
        g = self._require_held()
        oid = g.holding_object_id
        x, y = self._placement_xy(p, oid)
        target = self.c.table_placement_pose(oid, x, y)
        # Derive vertical clearance from this object's configured approach/grasp poses, not a robot-global constant.
        obj = self.scene.object(oid)
        above = self.scene.target(obj.approach_target).pose_m_rad
        grasp = self.scene.target(obj.grasp_target).pose_m_rad
        if above is None or grasp is None or above[2] <= grasp[2]:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, "table placement requires a positive taught approach clearance")
        approach = target.model_copy(update={"z": target.z + above[2] - grasp[2]})
        self._go(S.APPROACH_PLACE)
        paths = self._plan_pose_path([approach, target, approach], dl)
        self._execute_pose_path(paths[:2], dl)
        self._go(S.AT_PLACE)
        self._require_held(oid)
        self._go(S.RELEASING)
        released = self.c.open_gripper()
        if released.state != "open" or released.holding_object_id is not None:
            raise RobotControlError(ErrorCode.GRIPPER_FAULT, "release not confirmed")
        self._go(S.RETREATING)
        self._execute_pose_path(paths[2:], dl)
        return {"object_id": oid, "object_state": "released_on_table", "x_m": x, "y_m": y}

    def _placement_xy(self, p, oid):
        if p.get("location") == "center":
            return self.c.table_center()
        if p.get("direction"):
            position = self.c.object_position(oid)
            dx, dy = DIRECTIONS[p["direction"]]
            return position[0] + dx * p["distance_m"], position[1] + dy * p["distance_m"]
        return p["x_m"], p["y_m"]

    def _a_state(self, p, dl):
        self._step("query")
        return self.robot_state()

    def _a_scene(self, p, dl):
        self._step("query")
        return self.scene_snapshot()

    def scene_snapshot(self):
        result = self.scene.snapshot()
        observation = self.c.scene_observation()
        for oid, values in observation.get("objects", {}).items():
            if oid in result["objects"]:
                result["objects"][oid].update(values)
        result["position_source"] = observation.get("position_source", "configuration_only")
        result["perception_detector"] = self.detector.name
        result["camera_verified"] = False
        return result

    def _a_identify(self, p, dl):
        self._go(S.IDENTIFYING)
        self._step("detect")
        det = self.detector.identify(p["object_id"], self.scene)
        self.scene.record_detection(det)
        self._require(self.policy.check_detection(det))
        self._go(S.IDENTIFIED)
        return {"object_id": det.object_id, "class_label": det.class_label, "confidence": det.confidence,
                "source": det.source, "is_mock": det.is_mock, "timestamp_utc": det.timestamp_utc,
                "note": "MOCK detection for demo purposes — not real vision" if det.is_mock else
                        "vision recognition; motion uses live RoboDK geometry, not uncalibrated camera coordinates" if self.c.backend_name == "robodk_sim" else
                        "vision detection; camera coordinates ignored (no calibration), taught targets used"}

    def _grip(self):
        return self.c.get_gripper_state()

    def _a_pick(self, p, dl):
        oid = p["object_id"]
        obj = self.scene.object(oid)
        self._require(self.policy.check_detection(self.scene.detection(oid)))
        g = self._grip()
        if g.holding_object_id:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED, f"already holding '{g.holding_object_id}'")
        self.c.validate_pick(oid)
        self._go(S.APPROACH_PICK)
        if g.state != "open":
            self._step("gripper")
            self.c.open_gripper()            # make sure fingers are open before approaching
        # Recognition may come from the simulated camera; geometric targets come from simulator state.
        at = self.c.retarget_pick(oid)
        self._segments([(obj.approach_target, "joint"), (obj.grasp_target, "linear")], dl,
                       on_move={obj.grasp_target: lambda: self._go(S.AT_PICK)})
        self._go(S.GRIPPING)
        self._step("gripper")
        self.c.close_gripper(oid)
        self._go(S.VERIFYING_GRIP)
        self._step("verify")
        g = self.c.verify_grip()
        if not g.confirmed or g.holding_object_id != oid:
            raise RobotControlError(ErrorCode.GRIP_NOT_CONFIRMED, f"grip on '{oid}' not confirmed ({g.source.value})")
        self._go(S.LIFTING)
        self._segment(obj.lift_target, "linear", dl)
        self.scene.clear_detection(oid)      # object moved; old detection no longer valid
        res = {"object_id": oid, "object_state": "held", "grip_confirmed": True, "grip_source": g.source.value}
        if at is not None:
            res["picked_at_m"] = at          # exact simulated object position used for the grasp
        return res

    def _require_held(self, oid: Optional[str] = None):
        g = self._grip()
        if not g.holding_object_id or not g.confirmed:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED, "no verified grip on an object")
        if oid and g.holding_object_id != oid:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED, f"holding '{g.holding_object_id}', not '{oid}'")
        return g

    def _a_move_object(self, p, dl):
        self._require_held(p["object_id"])
        d = self.scene.destination(p["destination_id"])
        self._go(S.TRANSPORTING)
        self.c.retarget_place(p["object_id"], p["destination_id"])
        self._segment(d.approach_target, "joint", dl)
        self._require_held(p["object_id"])
        return {"object_id": p["object_id"], "object_state": "held_at_destination_approach",
                "destination_id": p["destination_id"]}

    def _a_place(self, p, dl):
        g = self._require_held()
        oid = g.holding_object_id
        d = self.scene.destination(p["destination_id"])
        self._go(S.APPROACH_PLACE)
        dynamic = self.c.retarget_place(oid, p["destination_id"])
        if dynamic:
            paths, start = [], None
            for tid, motion in ((d.approach_target, "joint"), (d.place_target, "linear"), (d.retreat_target, "linear")):
                self._check_cancel()
                dl.check()
                seg = self.c.plan_segment(tid, motion, start)
                self._require(self.policy.check_segment(seg))
                paths.append(seg)
                start = seg.end_joints
            self._execute_pose_path(paths[:2], dl)
            self._go(S.AT_PLACE)
        else:
            self._segments([(d.approach_target, "joint"), (d.place_target, "linear")], dl,
                           on_move={d.place_target: lambda: self._go(S.AT_PLACE)})
        self._require_held(oid)
        self._go(S.RELEASING)
        self._step("gripper")
        g = self.c.open_gripper()
        if g.state != "open" or g.holding_object_id is not None:
            raise RobotControlError(ErrorCode.GRIPPER_FAULT, "gripper did not report open after release")
        self._go(S.RETREATING)
        verification = self.c.verify_placement(oid, p["destination_id"]) if dynamic else None
        if verification is not None and not verification["ok"]:
            raise RobotControlError(ErrorCode.BACKEND_ERROR, "released object did not reach the resolved live destination")
        if dynamic:
            self._execute_pose_path(paths[2:], dl)
        else:
            self._segment(d.retreat_target, "linear", dl)
        return {"object_id": oid, "object_state": "released_at_destination", "destination_id": p["destination_id"],
                "release_source": g.source.value, "verification": verification}

    def _a_release(self, p, dl):
        g = self._grip()
        if g.holding_object_id:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED, "use place_object to release a held object")
        self._go(S.RELEASING)
        self._step("gripper")
        if g.state == "open":
            return {"gripper": "open", "note": "already open; no command sent"}
        g = self.c.open_gripper()
        return {"gripper": g.state, "source": g.source.value}

    def _a_push(self, p, dl):
        """Closed, empty gripper: above the object -> down beside it -> push `distance_m` -> lift. Every segment is
        planned and safety/collision-checked like any other; the object moves only on the push segment."""
        oid = p["object_id"]
        obj = self.scene.object(oid)
        pu = obj.push
        if pu is None:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED, f"'{oid}' cannot be pushed (no push path)")
        if p.get("direction"):
            self.c.prepare_push(oid, p["direction"], p["distance_m"])
        self._require(self.policy.check_detection(self.scene.detection(oid)))
        g = self._grip()
        if g.holding_object_id:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED, f"push needs an empty gripper (holding "
                                    f"'{g.holding_object_id}')")
        self._go(S.PUSHING)
        # Validate the complete approach, contact stroke and retreat before moving.
        planned, start = [], None
        for tid, motion in ((pu.above_target, "joint"), (pu.start_target, "linear"),
                            (pu.end_target, "linear"), (pu.lift_target, "linear")):
            self._check_cancel()
            dl.check()
            seg = self.c.plan_segment(tid, motion, start)
            self._require(self.policy.check_segment(seg))
            planned.append(seg)
            start = seg.end_joints
        if g.state != "closed":
            self._step("gripper")
            self.c.close_gripper(None)       # closed fingers push; nothing is grasped
        self._execute_pose_path(planned[:2], dl)
        carried = self.c.begin_push(oid)
        try:
            self._execute_pose_path(planned[2:3], dl)
        finally:
            if carried:
                self.c.end_push()
        self._execute_pose_path(planned[3:], dl)
        self.scene.clear_detection(oid)      # object moved; old detection no longer valid
        return {"object_id": oid, "object_state": "pushed", "distance_m": p.get("distance_m") or pu.distance_m,
                "direction": p.get("direction"),
                "object_model_moved": carried,
                "note": "the object's pick/place targets were taught for its old spot"}

    def _a_move_to_target(self, p, dl):
        g = self._grip()
        if g.holding_object_id and not g.confirmed:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED, "holding an object without verified grip")
        self._go(S.MOVING)
        self._segment(p["target_id"], "joint", dl)
        return {"target_id": p["target_id"]}

    def _a_shake(self, p, dl):
        pid, cycles = p["pattern_id"], p["cycles"]
        pat = self.scene.pattern(pid)
        g = self._grip()
        mass = self.scene.cfg.objects[g.holding_object_id].mass_kg if g.holding_object_id else None
        self._require(self.policy.check_shake(pid, pat, cycles, g.confirmed, mass))
        self._go(S.SHAKING)
        moves = [(pat.start_target, "joint")]
        moves += [(t, "linear") for _ in range(cycles) for t in pat.cycle_targets]
        moves.append((pat.end_target, "linear"))
        self._segments(moves, dl, between=self._require_held)
        return {"pattern_id": pid, "cycles": cycles, "object_id": g.holding_object_id}

    def _a_motion(self, p, dl):
        m = self.scene.motion(p["motion_id"])
        if m.requires_empty_gripper and self._grip().holding_object_id:
            raise RobotControlError(ErrorCode.PRECONDITION_FAILED, "motion requires an empty gripper")
        self._go(S.MOVING)
        self._segments([(t, "joint") for t in m.targets], dl)
        return {"motion_id": p["motion_id"], "targets": m.targets}

    # ------------------------------------------------------------------ plan / live status
    def _xyz(self, target_id: str) -> Optional[list[float]]:
        t = self.scene.cfg.targets.get(target_id)
        return [round(v, 4) for v in t.pose_m_rad[:3]] if t and t.pose_m_rad else None

    def _build_plan(self, task: TaskRequest) -> list[PlanStep]:
        steps: list[dict] = []

        def add(a, kind, desc, motion=None, target=None):
            steps.append(dict(action_id=a.action_id, action=a.action.value, kind=kind, description=desc,
                              motion=motion, target_id=target, target_xyz_m=self._xyz(target) if target else None))

        sc = self.scene.cfg
        for a in task.actions:
            p, n = a.parameters, a.action
            if n in (ActionName.GET_ROBOT_STATE, ActionName.GET_SCENE):
                add(a, "query", n.value.replace("_", " "))
            elif n == ActionName.IDENTIFY_OBJECT:
                add(a, "detect", f"identify {p['object_id']} (detector: {self.detector.name})")
            elif n == ActionName.PICK_OBJECT:
                o, oid = sc.objects[p["object_id"]], p["object_id"]
                add(a, "gripper", "open gripper (only if closed)")
                add(a, "move", f"approach {oid}", "joint", o.approach_target)
                add(a, "move", f"descend to grasp {oid}", "linear", o.grasp_target)
                add(a, "gripper", f"close gripper on {oid}")
                add(a, "verify", f"verify grip on {oid}")
                add(a, "move", f"lift {oid}", "linear", o.lift_target)
            elif n == ActionName.MOVE_OBJECT:
                d = sc.destinations[p["destination_id"]]
                add(a, "move", f"carry {p['object_id']} to {p['destination_id']}", "joint", d.approach_target)
            elif n == ActionName.PLACE_OBJECT:
                d, did = sc.destinations[p["destination_id"]], p["destination_id"]
                add(a, "move", f"approach {did}", "joint", d.approach_target)
                add(a, "move", f"lower into {did}", "linear", d.place_target)
                add(a, "gripper", "open gripper (release)")
                add(a, "move", f"retreat from {did}", "linear", d.retreat_target)
            elif n == ActionName.RELEASE_OBJECT:
                add(a, "gripper", "open gripper")
            elif n == ActionName.MOVE_TO_TARGET:
                add(a, "move", f"move to {p['target_id']}", "joint", p["target_id"])
            elif n == ActionName.MOVE_RELATIVE:
                target = f"relative_{p['direction']}"
                add(a, "move", f"move {p['distance_m']:g} m {p['direction']} in {p['reference_frame']} frame",
                    "linear", target)
            elif n == ActionName.MOVE_JOINT:
                target = f"joint_{p['joint_index']}"
                value = (f"by {p['delta_rad']:g} rad" if p.get("delta_rad") is not None
                         else f"to {p['target_rad']:g} rad")
                add(a, "move", f"move joint {p['joint_index']} {value}", "joint", target)
            elif n in (ActionName.FOLLOW_PATH, ActionName.CIRCLE_OBJECT, ActionName.PLACE_ON_TABLE):
                add(a, "move", n.value.replace('_', ' ') + " (all waypoints checked before execution)", "linear")
            elif n == ActionName.SHAKE_OBJECT:
                pat, cyc = sc.patterns[p["pattern_id"]], p["cycles"]
                add(a, "move", f"go to shake start ({p['pattern_id']})", "joint", pat.start_target)
                for c in range(1, cyc + 1):
                    for t in pat.cycle_targets:
                        add(a, "move", f"shake {c}/{cyc}: {t}", "linear", t)
                add(a, "move", "end shake", "linear", pat.end_target)
            elif n == ActionName.PERFORM_MOTION:
                for t in sc.motions[p["motion_id"]].targets:
                    add(a, "move", f"{p['motion_id']}: {t}", "joint", t)
        return [PlanStep(index=i + 1, **d) for i, d in enumerate(steps)]

    def _step(self, kind: str, target: Optional[str] = None) -> None:
        """Advance the plan to the next pending step of this kind (skipping
        steps that turned out to be unnecessary, e.g. 'open if closed')."""
        st = self._cur_status
        if st is None or not st.plan:
            return
        start = (st.current_step or 1) - 1
        aid = self._cur_action.action_id if self._cur_action else None
        for i in range(start, len(st.plan)):
            step = st.plan[i]
            if step.status not in ("pending", "running"):
                continue
            if step.action_id == aid and step.kind == kind and (target is None or step.target_id == target) \
                    and step.status == "pending":
                self._close_step("done")
                for j in range(start, i):            # passed over => not needed
                    if st.plan[j].status == "pending":
                        st.plan[j].status = "skipped"
                step.status, step.started_utc = "running", utc_now()
                st.current_step = step.index
                self._notify()
                return

    def _close_step(self, outcome: str) -> None:
        st = self._cur_status
        if st and st.current_step:
            step = st.plan[st.current_step - 1]
            if step.status == "running":
                step.status, step.finished_utc = outcome, utc_now()

    def live_status(self) -> dict:
        """Snapshot of what the robot is doing now, what comes next, and where it is."""
        st = self._cur_status
        now = nxt = None
        steps = []
        if st is not None and st.plan:
            steps = [s.model_dump(mode="json") for s in st.plan]
            if st.current_step:
                now = steps[st.current_step - 1]
            nxt = next((s for s in steps[(st.current_step or 0):] if s["status"] == "pending"), None)
        robot = None
        try:
            rs = self.c.get_state()
            import math
            robot = {
                "tcp_m": [round(v, 4) for v in rs.tcp_pose.as_list()[:3]] if rs.tcp_pose else None,
                "tcp_rotvec_rad": [round(v, 4) for v in rs.tcp_pose.as_list()[3:]] if rs.tcp_pose else None,
                "joints_deg": [round(math.degrees(q), 2) for q in rs.joints.values] if rs.joints else None,
                "gripper": rs.gripper_state, "holding": rs.holding_object_id,
                "grip_confirmed": rs.grip_confirmed, "moving": rs.busy,
                "feedback_source": rs.feedback_source.value, "frame": "base",
            }
        except Exception as e:
            robot = {"error": str(e)}
        done = sum(1 for s in steps if s["status"] in ("done", "skipped"))
        return {
            "timestamp_utc": utc_now(), "backend": self.c.backend_name, "robot_model": self.robot_cfg.model,
            "task_id": st.task_id if st else None, "task_status": st.status.value if st else None,
            "state": self.state.value, "progress": {"done": done, "total": len(steps)},
            "now": now, "next": nxt, "robot": robot, "steps": steps,
            "motion_limits": self._active_motion_limits.model_dump(mode="json"),
            "speed_percent": self.speed_percent,
            "task_speed_percent": st.speed_percent if st else None,
            "error": st.error.model_dump(mode="json") if st and st.error else None,
        }

    def _notify(self, override: Optional[str] = None) -> None:
        st = self._cur_status
        if override:
            text = override
        elif st and st.current_step:
            s = st.plan[st.current_step - 1]
            text = f"[{s.index}/{len(st.plan)}] {self.state.value}: {s.description}"
        else:
            text = f"{self.state.value}"
        self.c.show_status(text)
        if self.on_step:
            try:
                self.on_step(self.live_status())
            except Exception:
                log.exception("on_step callback failed")
