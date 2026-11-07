import threading
import time

import pytest

from robot_control.models import ActionStatus, ErrorCode, RobotControlError, TaskState
from robot_control.task_executor import TRANSITIONS
from tests.conftest import demo_task

S = TaskState


def test_happy_path_completes(make_executor):
    ex = make_executor()
    st = ex.submit(demo_task(), wait=True)
    assert st.status == ActionStatus.SUCCEEDED and st.state == S.COMPLETED
    assert all(v == ActionStatus.SUCCEEDED for v in st.actions.values())
    visited = [b for _a, b in ex.transition_log]
    expected = ["VALIDATING", "IDENTIFYING", "IDENTIFIED", "APPROACH_PICK", "AT_PICK", "GRIPPING",
                "VERIFYING_GRIP", "LIFTING", "SHAKING", "APPROACH_PLACE", "AT_PLACE", "RELEASING",
                "RETREATING", "COMPLETED"]
    assert visited == expected
    assert ex.c.history == ["joint:test_cube_approach", "linear:test_cube_grasp", "linear:test_cube_lift",
                            "joint:shake_center", "linear:shake_left", "linear:shake_right",
                            "linear:shake_left", "linear:shake_right", "linear:shake_center",
                            "joint:delivery_approach", "linear:delivery_place", "linear:delivery_retreat"]
    g = ex.c.get_gripper_state()
    assert g.state == "open" and g.holding_object_id is None


def test_events_are_uniform_and_simulated(make_executor):
    ex = make_executor()
    st = ex.submit(demo_task(), wait=True)
    assert st.events
    for ev in st.events:
        d = ev.model_dump(mode="json")
        assert d["backend"] == "mock" and d["robot_model"] == "UR5e" and d["timestamp_utc"].endswith("Z")
        if d["feedback"]:
            assert d["feedback"]["feedback_source"] == "simulated"
            assert d["feedback"]["feedback_source"] != "hardware_confirmed"
    ident = next(e for e in st.events if e.action == "identify_object" and e.status == ActionStatus.SUCCEEDED)
    assert ident.result["is_mock"] is True and "MOCK" in ident.result["note"]


def test_second_task_after_completion(make_executor):
    ex = make_executor()
    ex.submit(demo_task("t1"), wait=True)
    st = ex.submit(demo_task("t2"), wait=True)
    assert st.status == ActionStatus.SUCCEEDED


def test_transition_table_is_explicit():
    assert S.FAULT in TRANSITIONS[S.SHAKING]
    assert TRANSITIONS[S.FAULT] == {S.IDLE}
    assert S.LIFTING not in TRANSITIONS[S.IDLE]
    assert S.SHAKING not in TRANSITIONS[S.GRIPPING]      # cannot shake before grip is verified


def test_fault_latches_until_operator_reset(make_executor):
    from robot_control.models import ErrorCode as EC
    ex = make_executor(faults={"close_gripper": EC.GRIPPER_FAULT})
    st = ex.submit(demo_task("t1"), wait=True)
    assert st.status == ActionStatus.FAILED and ex.state == S.FAULT
    assert st.actions["a2"] == ActionStatus.FAILED and st.actions["a3"] == ActionStatus.CANCELLED
    assert st.error.code == EC.GRIPPER_FAULT and st.error.auto_retry is False
    with pytest.raises(RobotControlError) as ei:
        ex.submit(demo_task("t2"))
    assert ei.value.code == ErrorCode.FAULT_LATCHED
    with pytest.raises(RobotControlError):
        ex.reset_fault(operator="")
    ex.reset_fault(operator="alice")
    ex.c.faults.clear()
    ex.c.open_gripper()
    assert ex.submit(demo_task("t3"), wait=True).status == ActionStatus.SUCCEEDED


def test_no_automatic_retry(make_executor):
    ex = make_executor(faults={"move:test_cube_grasp": ErrorCode.BACKEND_ERROR})
    ex.submit(demo_task(), wait=True)
    assert ex.c.history == ["joint:test_cube_approach", "joint:home"]  # failed segment not retried; arm goes home


def test_plan_errors_rejected_before_motion(make_executor):
    ex = make_executor()
    task = {"schema_version": "1.0", "task_id": "bad", "actions": [
        {"action_id": "p", "action": "place_object", "parameters": {"destination_id": "delivery_zone"}}]}
    st = ex.submit(task, wait=True)
    assert st.status == ActionStatus.REJECTED and st.error.code == ErrorCode.PRECONDITION_FAILED
    assert ex.c.history == ["joint:home"]


def test_unknown_object_rejected(make_executor):
    ex = make_executor()
    t = demo_task()
    t["actions"][0]["parameters"]["object_id"] = "blue_ball"
    st = ex.submit(t, wait=True)
    assert st.status == ActionStatus.REJECTED and st.error.code == ErrorCode.UNKNOWN_ID
    assert ex.c.history == ["joint:home"]


def test_pick_without_identify_rejected(make_executor):
    ex = make_executor()
    t = demo_task()
    t["actions"] = t["actions"][1:]
    st = ex.submit(t, wait=True)
    assert st.status == ActionStatus.REJECTED and st.actions["a2"] == ActionStatus.REJECTED
    assert ex.c.history == ["joint:home"]


def test_concurrency_one_task_at_a_time(make_executor):
    ex = make_executor(segment_duration_s=0.05)
    ex.submit(demo_task("t1"))
    with pytest.raises(RobotControlError) as ei:
        ex.submit(demo_task("t2"))
    assert ei.value.code == ErrorCode.BUSY
    ex.wait(10)
    assert ex.get_task("t1").status == ActionStatus.SUCCEEDED


def test_duplicate_task_id_and_idempotency(make_executor):
    ex = make_executor()
    t = demo_task("t1")
    t["idempotency_key"] = "k-1"
    first = ex.submit(t, wait=True)
    again = ex.submit(t)                     # same key => same task, not re-executed
    assert again is first and len(ex.c.history) == 12
    t2 = demo_task("t1")
    with pytest.raises(RobotControlError) as ei:
        ex.submit(t2)
    assert ei.value.code == ErrorCode.DUPLICATE_TASK


def test_cancellation(make_executor):
    ex = make_executor(segment_duration_s=0.2)
    ex.submit(demo_task("t1"))
    time.sleep(0.3)
    ex.cancel("t1")
    ex.wait(10)
    st = ex.get_task("t1")
    assert st.status == ActionStatus.CANCELLED and ex.state == S.CANCELLED
    assert len(ex.c.history) < 12
    with pytest.raises(RobotControlError):
        ex.submit(demo_task("t2"))           # cancellation also needs operator reset


def test_stop_robot(make_executor):
    ex = make_executor(segment_duration_s=0.2)
    ex.submit(demo_task("t1"))
    time.sleep(0.3)
    ex.stop_robot()
    ex.wait(10)
    assert ex.get_task("t1").status == ActionStatus.CANCELLED


def test_timeout(make_executor, safety_dict):
    from robot_control.config import SafetyConfig
    safety_dict["segment_timeout_s"] = 0.05
    ex = make_executor(segment_duration_s=1.0, safety=SafetyConfig.model_validate(safety_dict))
    st = ex.submit(demo_task(), wait=True)
    assert st.status == ActionStatus.FAILED and st.error.code == ErrorCode.TIMEOUT and ex.state == S.FAULT


def test_release_while_holding_rejected(make_executor):
    ex = make_executor()
    t = demo_task()
    t["actions"] = t["actions"][:2] + [{"action_id": "r", "action": "release_object", "parameters": {}}]
    st = ex.submit(t, wait=True)
    assert st.status == ActionStatus.REJECTED and ex.c.history == ["joint:home"]


def test_move_object_then_place(make_executor):
    ex = make_executor()
    t = demo_task()
    t["actions"].insert(3, {"action_id": "mv", "action": "move_object",
                            "parameters": {"object_id": "test_cube", "destination_id": "delivery_zone"}})
    st = ex.submit(t, wait=True)
    assert st.status == ActionStatus.SUCCEEDED
    mv = next(e for e in st.events if e.action_id == "mv" and e.status == ActionStatus.SUCCEEDED)
    assert mv.result["object_state"] == "held_at_destination_approach"
    pl = next(e for e in st.events if e.action_id == "a4" and e.status == ActionStatus.SUCCEEDED)
    assert pl.result["object_state"] == "released_at_destination"


def test_plan_and_live_steps(make_executor):
    snaps = []
    ex = make_executor()
    ex.on_step = snaps.append
    st = ex.submit(demo_task(), wait=True)
    plan = [(s.kind, s.target_id) for s in st.plan]
    assert plan[:7] == [("detect", None), ("gripper", None), ("move", "test_cube_approach"), ("move", "test_cube_grasp"),
                        ("gripper", None), ("verify", None), ("move", "test_cube_lift")]
    assert len(st.plan) == 17          # identify 1 + pick 6 + shake 6 (start, 2x2, end) + place 4
    statuses = {s.status for s in st.plan}
    assert statuses <= {"done", "skipped"}
    assert st.plan[1].status == "skipped"              # gripper was already open
    # live snapshots: NOW/NEXT always consistent
    mid = [s for s in snaps if s["now"] and s["now"]["target_id"] == "test_cube_grasp"][0]
    assert mid["next"]["description"].startswith("close gripper")
    assert mid["now"]["target_xyz_m"] == [-0.45, -0.2, 0.025]


def test_failed_step_marked(make_executor):
    ex = make_executor(faults={"close_gripper": ErrorCode.GRIPPER_FAULT})
    st = ex.submit(demo_task(), wait=True)
    failed = [s for s in st.plan if s.status == "failed"]
    assert len(failed) == 1 and failed[0].description.startswith("close gripper")
    snap = ex.live_status()
    assert snap["error"]["code"] == "GRIPPER_FAULT"
