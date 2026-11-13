import math

import pytest

from robot_control.config import RobotConfig, SafetyConfig, SceneConfig
from robot_control.controller import PathCheckResult
from robot_control.models import ActionStatus, ErrorCode, Pose
from robot_control.backends.mock import MockController
from robot_control.scene_registry import SceneRegistry
from robot_control.task_executor import TaskExecutor


def task(action, parameters, *, task_id="motion", speed_percent=None):
    out = {"schema_version": "1.0", "task_id": task_id,
           "actions": [{"action_id": "a1", "action": action, "parameters": parameters}]}
    if speed_percent is not None:
        out["speed_percent"] = speed_percent
    return out


def test_long_relative_move_has_no_arbitrary_step_cap(make_executor):
    ex = make_executor()
    st = ex.submit(task("move_relative", {"direction": "up", "distance_m": 0.20,
                                           "reference_frame": "base"}), wait=True)
    assert st.status == ActionStatus.SUCCEEDED
    assert ex.c.get_state().tcp_pose.z == pytest.approx(0.5379)
    assert st.execution["final_tcp_pose"]["z"] == pytest.approx(0.5379)


def test_relative_move_preserves_orientation_and_direction(make_executor):
    ex = make_executor()
    before = ex.c.get_state().tcp_pose
    st = ex.submit(task("move_relative", {"direction": "right", "distance_m": 0.05}), wait=True)
    assert st.status == ActionStatus.SUCCEEDED
    after = ex.c.get_state().tcp_pose
    assert (after.rx, after.ry, after.rz) == pytest.approx((before.rx, before.ry, before.rz))
    assert after.y == pytest.approx(before.y - 0.05)


def test_tool_frame_transform(make_executor):
    ex = make_executor()
    ex.c._pose = Pose.from_list([-0.45, 0.0, 0.25, 0.0, 0.0, math.pi / 2])
    st = ex.submit(task("move_relative", {"direction": "forward", "distance_m": 0.04,
                                           "reference_frame": "tool"}), wait=True)
    assert st.status == ActionStatus.SUCCEEDED
    pose = ex.c.get_state().tcp_pose
    assert pose.x == pytest.approx(-0.45, abs=1e-8)
    assert pose.y == pytest.approx(0.04, abs=1e-8)


def test_workspace_rejects_infeasible_distance(make_executor):
    ex = make_executor()
    st = ex.submit(task("move_relative", {"direction": "up", "distance_m": 0.30}), wait=True)
    assert st.status == ActionStatus.REJECTED
    assert st.error.code == ErrorCode.SAFETY_VETO
    assert "workspace" in st.error.message


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.01, 0.0])
def test_invalid_distance_is_rejected(make_executor, value):
    ex = make_executor()
    with pytest.raises(Exception):
        ex.submit(task("move_relative", {"direction": "up", "distance_m": value}), wait=True)


def test_relative_and_absolute_joint_motion(make_executor):
    ex = make_executor()
    start = ex.c.get_state().joints.values[1]
    first = ex.submit(task("move_joint", {"joint_index": 2, "delta_rad": math.radians(15)},
                           task_id="joint_rel"), wait=True)
    assert first.status == ActionStatus.SUCCEEDED
    assert ex.c.get_state().joints.values[1] == pytest.approx(start + math.radians(15))
    second = ex.submit(task("move_joint", {"joint_index": 2, "target_rad": -1.0},
                            task_id="joint_abs"), wait=True)
    assert second.status == ActionStatus.SUCCEEDED
    assert ex.c.get_state().joints.values[1] == pytest.approx(-1.0)


def test_joint_index_and_joint_limit_rejected(make_executor):
    ex = make_executor()
    bad_index = ex.submit(task("move_joint", {"joint_index": 7, "delta_rad": 0.1}), wait=True)
    assert bad_index.status == ActionStatus.REJECTED
    assert bad_index.error.code == ErrorCode.VALIDATION_ERROR
    ex.reset_fault("test")
    bad_limit = ex.submit(task("move_joint", {"joint_index": 3, "target_rad": 4.0},
                               task_id="bad_limit"), wait=True)
    assert bad_limit.status == ActionStatus.REJECTED
    assert "joint 2" in bad_limit.error.message


class SingularityChecker:
    def check(self, _seg):
        return PathCheckResult(ok=False, conclusive=True, singularity=True,
                               reason="trajectory enters configured near-singular region")


def test_singularity_result_rejects_before_motion(make_executor):
    ex = make_executor(path_checker=SingularityChecker())
    st = ex.submit(task("move_relative", {"direction": "up", "distance_m": 0.01}), wait=True)
    assert st.status == ActionStatus.REJECTED
    assert st.error.code == ErrorCode.NEAR_SINGULAR
    assert ex.c.history == []


def test_task_speed_override_is_atomic_and_default_is_restored(make_executor):
    ex = make_executor()
    fast = ex.submit(task("move_relative", {"direction": "up", "distance_m": 0.01},
                          task_id="fast", speed_percent=70), wait=True)
    assert fast.status == ActionStatus.SUCCEEDED
    assert fast.speed_percent == 70
    assert fast.motion_limits["tcp_speed_m_s"] == pytest.approx(0.7)
    assert ex.speed_percent == 10
    normal = ex.submit(task("move_relative", {"direction": "down", "distance_m": 0.01},
                            task_id="normal"), wait=True)
    assert normal.speed_percent == 10
    assert normal.motion_limits["tcp_speed_m_s"] == pytest.approx(0.1)


@pytest.mark.parametrize("percent", [1, 10, 50, 100])
def test_task_speed_percentages(make_executor, percent):
    ex = make_executor()
    st = ex.submit(task("move_relative", {"direction": "up", "distance_m": 0.001},
                        task_id=f"p{percent}", speed_percent=percent), wait=True)
    assert st.speed_percent == percent
    assert st.motion_limits["joint_speed_limits_rad_s"][0] == pytest.approx(
        ex.robot_cfg.limits.max_joint_speed_rad_s[0] * percent / 100)


def test_different_dof_rejects_nonexistent_joint(robot_dict, safety_dict, scene_dict):
    robot_dict["model"] = "FiveDOFTest"
    robot_dict["dof"] = 5
    for key in ("joint_min_rad", "joint_max_rad", "max_joint_speed_rad_s"):
        robot_dict["limits"][key] = robot_dict["limits"][key][:5]
    for target in scene_dict["targets"].values():
        if target.get("joints_rad"):
            target["joints_rad"] = target["joints_rad"][:5]
    robot = RobotConfig.model_validate(robot_dict)
    safety = SafetyConfig.model_validate(safety_dict)
    scene = SceneConfig.model_validate(scene_dict)
    c = MockController(robot, scene)
    ex = TaskExecutor(c, robot, safety, SceneRegistry(scene))
    ex.start()
    st = ex.submit(task("move_joint", {"joint_index": 6, "delta_rad": 0.1}), wait=True)
    assert st.status == ActionStatus.REJECTED
    assert "1..5" in st.error.message
