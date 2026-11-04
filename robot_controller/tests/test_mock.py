import json

import pytest

from robot_control.backends import create_controller
from robot_control.backends.physical_disabled import PhysicalDisabledController
from robot_control.config import RobotConfig, SceneConfig
from robot_control.controller import MotionContext
from robot_control.models import ActionStatus, ErrorCode, RobotControlError
from robot_control.safety.trajectory import BackendPathChecker, VetoPathChecker
from robot_control.task_executor import TaskExecutor
from robot_control.vision import MockDetector, NoDetector, VisionEyesFileDetector
from robot_control.scene_registry import SceneRegistry
from tests.conftest import CFG, demo_task


def test_gripper_fault_injection(make_executor):
    ex = make_executor(faults={"close_gripper": ErrorCode.GRIPPER_FAULT})
    st = ex.submit(demo_task(), wait=True)
    assert st.error.code == ErrorCode.GRIPPER_FAULT
    assert "joint:shake_center" not in ex.c.history      # never shook without a grip


def test_grip_not_confirmed(make_executor):
    ex = make_executor(faults={"verify_grip": ErrorCode.GRIP_NOT_CONFIRMED})
    st = ex.submit(demo_task(), wait=True)
    assert st.status == ActionStatus.FAILED
    assert "linear:pick_lift" not in ex.c.history         # no lift without verified grip


def test_stale_sensor_data(make_executor):
    ex = make_executor(stale_state=True)
    st = ex.submit(demo_task(), wait=True)
    assert st.status == ActionStatus.REJECTED and st.error.code == ErrorCode.STALE_DATA
    assert ex.c.history == []


def test_missing_target_in_station(make_executor):
    ex = make_executor(missing_targets={"delivery_place"})
    st = ex.submit(demo_task(), wait=True)
    assert st.status == ActionStatus.REJECTED and st.error.code == ErrorCode.UNKNOWN_ID
    assert ex.c.history == []                             # caught in preflight


def test_unreachable_target(make_executor):
    ex = make_executor(unreachable_targets={"shake_left"})
    st = ex.submit(demo_task(), wait=True)
    assert st.status == ActionStatus.REJECTED and st.error.code == ErrorCode.UNREACHABLE
    assert ex.c.history == []


def test_collision_veto_adapter(make_executor, robot_cfg, scene_cfg):
    ex = make_executor()
    ex.policy.path_checker = VetoPathChecker(BackendPathChecker(ex.c), {"delivery_place"})
    st = ex.submit(demo_task(), wait=True)
    assert st.status == ActionStatus.REJECTED and st.error.code == ErrorCode.COLLISION_PREDICTED
    assert "linear:delivery_place" not in ex.c.history
    veto = st.events[-1]
    assert veto.safety_checks and not veto.safety_checks.approved


def test_excess_shaking_rejected(make_executor):
    ex = make_executor()
    st = ex.submit(demo_task(cycles=5), wait=True)        # contract allows <=10, pattern max is 3
    assert st.status == ActionStatus.REJECTED and st.error.code == ErrorCode.SAFETY_VETO
    assert ex.c.history == []


def test_identify_not_available_without_vision(make_executor):
    ex = make_executor(detector=NoDetector())
    st = ex.submit(demo_task(), wait=True)
    assert st.status == ActionStatus.REJECTED and st.error.code == ErrorCode.NOT_AVAILABLE


def test_mock_detector_missing_object(make_executor):
    ex = make_executor(detector=MockDetector(missing={"red_cube"}))
    assert ex.submit(demo_task(), wait=True).status == ActionStatus.REJECTED


# ---- physical backend must refuse everything -------------------------------------

@pytest.mark.parametrize("method,args", [
    ("plan_segment", ("home", "joint")), ("test_segment", (None,)), ("move_to_named_target", (None, MotionContext())),
    ("move_joints", ([0] * 6, MotionContext())), ("move_linear", (None, MotionContext())),
    ("open_gripper", ()), ("close_gripper", ("red_cube",)), ("verify_grip", ()), ("get_gripper_state", ()),
])
def test_physical_backend_refuses_motion_and_gripper(robot_cfg, method, args):
    c = PhysicalDisabledController(robot_cfg)
    with pytest.raises(RobotControlError) as ei:
        getattr(c, method)(*args)
    assert ei.value.code == ErrorCode.PHYSICAL_BACKEND_DISABLED


def test_physical_backend_task_rejected(robot_cfg, safety_cfg, scene_cfg):
    c = create_controller(robot_cfg, scene_cfg, "physical_disabled")
    ex = TaskExecutor(c, robot_cfg, safety_cfg, SceneRegistry(scene_cfg), MockDetector())
    ex.start()
    st = ex.submit(demo_task(), wait=True)
    assert st.status == ActionStatus.REJECTED
    assert st.error.code in (ErrorCode.MODE_VIOLATION, ErrorCode.PHYSICAL_BACKEND_DISABLED)
    caps = c.capabilities()
    assert caps.motion_enabled is False and caps.simulation is False


def test_robodk_backend_has_no_real_robot_calls():
    """Static guard: the RoboDK backend never uses driver/program-generation APIs."""
    from tests.conftest import ROOT
    import ast
    tree = ast.parse((ROOT / "robot_control/backends/robodk_sim.py").read_text())
    for node in ast.walk(tree):                      # drop docstrings; comments vanish in unparse
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(tree)
    for forbidden in (".Connect(", "ConnectSafe(", "RUNMODE_RUN_ROBOT", "RUNMODE_MAKE_ROBOTPROG",
                      "ProgramStart(", "MakeProgram(", "RunOnRobot", "setConnectionParams"):
        assert forbidden not in code, forbidden
    assert "RUNMODE_SIMULATE" in code


def test_generic_modules_do_not_import_rtde():
    from tests.conftest import ROOT
    import re
    pat = re.compile(r"^\s*(import|from)\s+(rtde|ur_rtde)", re.M)
    for p in (ROOT / "robot_control").rglob("*.py"):
        assert not pat.search(p.read_text()), p


# ---- model switching via config only -----------------------------------------------

def test_switch_to_ur3e_by_config(scene_dict, safety_cfg):
    import yaml
    ur3 = RobotConfig.model_validate(yaml.safe_load((CFG / "robot.ur3e.example.yaml").read_text()))
    assert ur3.model == "UR3e" and ur3.limits.max_payload_kg == 3.0 and ur3.limits.reach_m == 0.5
    # A UR3e-sized scene (targets within 0.5 m reach); same executor/AI code.
    for t in scene_dict["targets"].values():
        if t.get("pose_m_rad"):
            t["pose_m_rad"][0] = -0.30
    scene = SceneConfig.model_validate(scene_dict)
    safety = safety_cfg.model_copy(update={"workspace": safety_cfg.workspace.model_copy(update={"x_m": (-0.45, -0.15)})})
    c = create_controller(ur3, scene, "mock")
    ex = TaskExecutor(c, ur3, safety, SceneRegistry(scene), MockDetector())
    ex.start()
    st = ex.submit(demo_task(), wait=True)
    assert st.status == ActionStatus.SUCCEEDED
    assert all(e.robot_model == "UR3e" for e in st.events)


def test_ur5e_scene_out_of_reach_for_ur3e(scene_cfg, safety_cfg):
    import yaml
    ur3 = RobotConfig.model_validate(yaml.safe_load((CFG / "robot.ur3e.example.yaml").read_text()))
    ex = TaskExecutor(create_controller(ur3, scene_cfg, "mock"), ur3, safety_cfg, SceneRegistry(scene_cfg), MockDetector())
    ex.start()
    st = ex.submit(demo_task(), wait=True)
    assert st.status == ActionStatus.REJECTED          # UR5e targets exceed the UR3e reach


# ---- vision adapter -------------------------------------------------------------------

def test_vision_eyes_adapter_maps_labels(tmp_path, scene_cfg):
    import time
    f = tmp_path / "frame.json"
    f.write_text(json.dumps({"source": "arm", "frame_id": 1, "ts": time.time(), "objects": [
        {"id": 3, "label": "cube", "conf": 0.88, "bbox": [0.1, 0.1, 0.2, 0.2], "color": "red",
         "coords_mm": {"x": 999, "y": 999, "angle_deg": 0}}]}))
    det = VisionEyesFileDetector(f).identify("red_cube", SceneRegistry(scene_cfg))
    assert det.source == "vision_eyes" and det.is_mock is False and det.confidence == 0.88
    assert det.pose is None                                # camera XYZ ignored without calibration


def test_vision_adapter_no_match(tmp_path, scene_cfg):
    f = tmp_path / "sel.json"
    f.write_text(json.dumps({"ts": 0, "target": {"label": "cup", "conf": 0.9}}))
    with pytest.raises(RobotControlError) as ei:
        VisionEyesFileDetector(f).identify("red_cube", SceneRegistry(scene_cfg))
    assert ei.value.code == ErrorCode.NOT_AVAILABLE
