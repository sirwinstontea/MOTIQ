"""Opt-in RoboDK SIMULATOR integration test. Never connects to hardware.

Run:  RUN_ROBODK_TESTS=1 python -m pytest -m integration -q
Needs: RoboDK GUI running locally and the demo station
(`python -m robot_control.cli setup-station --config config/robot.yaml`).
"""
import math
import os
import socket
import time

import pytest

from robot_control.backends.robodk_sim import RoboDKSimController
from robot_control.models import ActionStatus, ErrorCode
from robot_control.scene_registry import SceneRegistry
from robot_control.task_executor import TaskExecutor
from robot_control.vision import MockDetector

pytestmark = pytest.mark.integration


def _robodk_up():
    try:
        with socket.create_connection(("127.0.0.1", 20500), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture
def rdk_executor(robot_cfg, safety_cfg, scene_cfg):
    if os.environ.get("RUN_ROBODK_TESTS") != "1":
        pytest.skip("set RUN_ROBODK_TESTS=1 to opt in")
    if not _robodk_up():
        pytest.skip("RoboDK GUI not running on 127.0.0.1:20500")
    cfg = robot_cfg.model_copy(update={"backend": "robodk_sim"})
    c = RoboDKSimController(cfg, scene_cfg)
    ex = TaskExecutor(c, cfg, safety_cfg, SceneRegistry(scene_cfg), MockDetector())
    ex.start()
    return ex


def test_reads_selected_robot_in_simulate_mode(rdk_executor):
    c = rdk_executor.c
    assert c.robot.Name() == rdk_executor.robot_cfg.robot_item_name
    assert c.RDK.RunMode() == c.rl.RUNMODE_SIMULATE
    assert c.check_ready() == []
    st = c.get_state()
    assert len(st.joints.values) == 6 and st.feedback_source.value == "simulated"


def test_move_between_named_targets(rdk_executor):
    task = {"schema_version": "1.0", "task_id": "it_move", "actions": [
        {"action_id": "m1", "action": "move_to_target", "parameters": {"target_id": "orange_screw_03_approach"}},
        {"action_id": "m2", "action": "move_to_target", "parameters": {"target_id": "home"}}]}
    st = rdk_executor.submit(task, wait=True)
    assert st.status == ActionStatus.SUCCEEDED, st.error
    seg_events = [e for e in st.events if e.message and e.message.startswith("segment")]
    assert seg_events and all("0 collisions" in e.safety_checks.reasons[-1] for e in seg_events)


def test_one_centimetre_cartesian_and_joint_jog(rdk_executor):
    """Explicit opt-in motion: 1 cm up, then a 1 degree joint move."""
    before = rdk_executor.c.get_state()
    cart = {"schema_version": "1.0", "task_id": "it_cart_1cm", "speed_percent": 10,
            "actions": [{"action_id": "m1", "action": "move_relative",
                         "parameters": {"direction": "up", "distance_m": 0.01,
                                        "reference_frame": "base"}}]}
    st = rdk_executor.submit(cart, wait=True)
    assert st.status == ActionStatus.SUCCEEDED, st.error
    assert st.execution["final_tcp_pose"]["z"] == pytest.approx(before.tcp_pose.z + 0.01, abs=0.002)
    joint = {"schema_version": "1.0", "task_id": "it_joint_1deg", "speed_percent": 10,
             "actions": [{"action_id": "j1", "action": "move_joint",
                          "parameters": {"joint_index": 1, "delta_rad": math.radians(1)}}]}
    js = rdk_executor.submit(joint, wait=True)
    assert js.status == ActionStatus.SUCCEEDED, js.error
    assert js.execution["final_joints_rad"][0] == pytest.approx(
        st.execution["final_joints_rad"][0] + math.radians(1), abs=math.radians(0.5))
    assert rdk_executor.c.RDK.RunMode() == rdk_executor.c.rl.RUNMODE_SIMULATE
    restore = {"schema_version": "1.0", "task_id": "it_restore_home",
               "actions": [{"action_id": "h1", "action": "move_to_target",
                            "parameters": {"target_id": rdk_executor.scene.cfg.home_target}}]}
    home = rdk_executor.submit(restore, wait=True)
    assert home.status == ActionStatus.SUCCEEDED, home.error


def test_unreachable_relative_target_is_refused_without_motion(rdk_executor):
    before = rdk_executor.c.robot.Joints().list()
    task = {"schema_version": "1.0", "task_id": "it_unreachable",
            "actions": [{"action_id": "m1", "action": "move_relative",
                         "parameters": {"direction": "up", "distance_m": 10.0}}]}
    st = rdk_executor.submit(task, wait=True)
    assert st.status == ActionStatus.REJECTED
    assert st.error.code in (ErrorCode.UNREACHABLE, ErrorCode.SAFETY_VETO)
    assert rdk_executor.c.robot.Joints().list() == pytest.approx(before, abs=1e-3)


def test_commanded_speed_changes_simulated_motion_duration(rdk_executor):
    """Playback multiplier must be 1x for a meaningful duration comparison."""
    durations = {}
    for percent in (10, 100):
        move = {"schema_version": "1.0", "task_id": f"it_speed_{percent}", "speed_percent": percent,
                "actions": [{"action_id": "m1", "action": "move_relative",
                             "parameters": {"direction": "up", "distance_m": 0.05}}]}
        started = time.monotonic()
        st = rdk_executor.submit(move, wait=True)
        durations[percent] = time.monotonic() - started
        assert st.status == ActionStatus.SUCCEEDED, st.error
        assert st.speed_percent == percent
        assert st.motion_limits["tcp_speed_m_s"] == pytest.approx(percent / 100)
        home = {"schema_version": "1.0", "task_id": f"it_speed_home_{percent}",
                "actions": [{"action_id": "h1", "action": "move_to_target",
                             "parameters": {"target_id": rdk_executor.scene.cfg.home_target}}]}
        assert rdk_executor.submit(home, wait=True).status == ActionStatus.SUCCEEDED
    assert durations[100] < durations[10], durations


def test_collision_is_detected_and_robot_restored(rdk_executor):
    """A segment driving the flange into the table must be flagged, and the
    path test must leave the simulated robot where it was."""
    c = rdk_executor.c
    rm = c.rm
    before = c.robot.Joints().list()
    pose = rm.UR_2_Pose([-450, 0, -200, math.pi, 0, 0])        # TCP 200 mm under the table top
    sol = c.robot.SolveIK(pose, before, c.tool.PoseTool()).list()
    assert len(sol) == 6
    from robot_control.controller import PlannedSegment
    seg = PlannedSegment("probe", "joint", [math.radians(v) for v in before], [math.radians(v) for v in sol],
                         c._pose_from_mat(pose))
    res = c.test_segment(seg)
    assert res.conclusive and not res.ok and res.collisions > 0
    after = c.robot.Joints().list()
    assert max(abs(a - b) for a, b in zip(before, after)) < 1e-3
    # and the policy refuses it (workspace and/or collision)
    assert not rdk_executor.policy.check_segment(seg).approved


def test_non_simulate_mode_is_refused(rdk_executor):
    c = rdk_executor.c
    c.RDK.setRunMode(c.rl.RUNMODE_QUICKVALIDATE)
    try:
        assert any("RUNMODE_SIMULATE" in p for p in c.check_ready())
        assert not rdk_executor.policy.check_backend(c).approved
    finally:
        c.RDK.setRunMode(c.rl.RUNMODE_SIMULATE)
