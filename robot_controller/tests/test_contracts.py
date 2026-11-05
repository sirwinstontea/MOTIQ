import json

import pytest

from robot_control.action_registry import tool_specs, validate_task
from robot_control.models import ErrorCode, Event, Pose, RobotControlError, TaskState
from tests.conftest import demo_task


def _code(data):
    with pytest.raises(RobotControlError) as ei:
        validate_task(data)
    return ei.value.code


def test_valid_example_task_parses():
    t = validate_task(demo_task())
    assert [a.action.value for a in t.actions] == ["identify_object", "pick_object", "shake_object", "place_object"]


def test_example_files(tmp_path):
    from tests.conftest import ROOT
    assert validate_task((ROOT / "examples/pick_place.json").read_text()).task_id == "demo_001"
    assert validate_task((ROOT / "examples/move_to_target.json").read_text()).task_id == "move_001"
    assert _code((ROOT / "examples/invalid_action.json").read_text()) == ErrorCode.UNKNOWN_ACTION


@pytest.mark.parametrize("mutate,code", [
    (lambda t: t["actions"][0].update(action="moveJ"), ErrorCode.UNKNOWN_ACTION),
    (lambda t: t["actions"][0].update(action="run_script"), ErrorCode.UNKNOWN_ACTION),
    (lambda t: t.update(extra_field=1), ErrorCode.VALIDATION_ERROR),
    (lambda t: t["actions"][1]["parameters"].update(x=0.4), ErrorCode.VALIDATION_ERROR),       # raw XYZ smuggled
    (lambda t: t["actions"][2]["parameters"].update(cycles=-1), ErrorCode.VALIDATION_ERROR),
    (lambda t: t["actions"][2]["parameters"].update(cycles=50), ErrorCode.VALIDATION_ERROR),
    (lambda t: t["actions"][2]["parameters"].update(cycles=2.5), ErrorCode.VALIDATION_ERROR),
    (lambda t: t["actions"][2]["parameters"].update(cycles="2"), ErrorCode.VALIDATION_ERROR),
    (lambda t: t["actions"][1]["parameters"].pop("object_id"), ErrorCode.VALIDATION_ERROR),
    (lambda t: t["actions"][1]["parameters"].update(object_id="Red Cube!"), ErrorCode.VALIDATION_ERROR),
    (lambda t: t.update(schema_version="2.0"), ErrorCode.VALIDATION_ERROR),
    (lambda t: t.update(actions=[]), ErrorCode.VALIDATION_ERROR),
    (lambda t: t["actions"][1].update(action_id="a1"), ErrorCode.VALIDATION_ERROR),           # duplicate id
    (lambda t: t["actions"].append({"action_id": "c", "action": "cancel_task",
                                    "parameters": {"task_id": "x"}}), ErrorCode.VALIDATION_ERROR),
])
def test_malformed_tasks_rejected(mutate, code):
    t = demo_task()
    mutate(t)
    assert _code(t) == code


def test_nan_in_json_rejected():
    raw = json.dumps(demo_task()).replace('"cycles": 2', '"cycles": NaN')
    assert _code(raw) == ErrorCode.VALIDATION_ERROR


def test_nan_pose_rejected():
    with pytest.raises(ValueError):
        Pose(x=float("nan"), y=0, z=0, rx=0, ry=0, rz=0)
    with pytest.raises(ValueError):
        Pose(x=float("inf"), y=0, z=0, rx=0, ry=0, rz=0)


def test_tool_allowlist_has_no_raw_motion():
    names = {s["name"] for s in tool_specs()}
    assert names == {"get_robot_state", "get_scene", "identify_object", "pick_object", "move_object",
                     "place_object", "release_object", "move_to_target", "shake_object", "perform_motion",
                     "get_task_status", "cancel_task", "stop_robot"}
    for s in tool_specs():
        assert s["parameters"]["additionalProperties"] is False
        props = set(s["parameters"].get("properties", {}))
        assert not props & {"x", "y", "z", "joints", "pose", "ip", "script", "code"}


def test_event_schema_roundtrip():
    ev = Event(event_id=1, task_id="t", action_id="a", action="pick_object", state=TaskState.LIFTING,
               status="SUCCEEDED", backend="mock", robot_model="UR5e")
    d = json.loads(ev.model_dump_json())
    for k in ("schema_version", "task_id", "action_id", "action", "state", "status", "timestamp_utc",
              "backend", "robot_model", "parameters", "feedback", "error", "safety_checks"):
        assert k in d
    assert d["timestamp_utc"].endswith("Z") and "T" in d["timestamp_utc"]
    with pytest.raises(ValueError):
        Event.model_validate({**d, "surprise": 1})
