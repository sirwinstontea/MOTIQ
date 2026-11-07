import math
import time

import pytest

from robot_control.backends.mock import MockController
from robot_control.backends.physical_disabled import PhysicalDisabledController
from robot_control.config import RobotConfig, SafetyConfig, cross_validate
from robot_control.controller import PathCheckResult, PlannedSegment
from robot_control.models import ErrorCode, Pose, RobotControlError
from robot_control.safety.policy import SafetyPolicy
from robot_control.safety.trajectory import BackendPathChecker
from robot_control.scene_registry import Detection
from robot_control.models import utc_now

GOOD_POSE = Pose.from_list([-0.45, -0.2, 0.15, math.pi, 0, 0])
GOOD_J = [0.1, -1.5, 2.0, -2.4, -1.57, 1.7]


class FixedChecker:
    def __init__(self, res):
        self.res = res

    def check(self, seg):
        return self.res


def policy(robot_cfg, safety_cfg, res=None, backend="mock"):
    p = SafetyPolicy(robot_cfg, safety_cfg,
                     FixedChecker(res or PathCheckResult(ok=True, conclusive=True, geometry_checked=True)))
    p.backend_name = backend
    return p


def seg(joints=GOOD_J, pose=GOOD_POSE):
    return PlannedSegment("t", "joint", [0.0] * 6, joints, pose)


def test_segment_ok(robot_cfg, safety_cfg):
    assert policy(robot_cfg, safety_cfg).check_segment(seg()).approved


def test_unreachable(robot_cfg, safety_cfg):
    d = policy(robot_cfg, safety_cfg).check_segment(seg(joints=None, pose=None))
    assert not d.approved and d.code == ErrorCode.UNREACHABLE


def test_joint_limit_violation(robot_cfg, safety_cfg):
    d = policy(robot_cfg, safety_cfg).check_segment(seg(joints=[0, 0, 3.5, 0, 0, 0]))   # elbow > pi
    assert not d.approved and d.code == ErrorCode.SAFETY_VETO and "joint 2" in d.reasons[0]


def test_wrong_dof_rejected(robot_cfg, safety_cfg):
    d = policy(robot_cfg, safety_cfg).check_segment(seg(joints=[0, 0, 0]))
    assert not d.approved


def test_workspace_violation(robot_cfg, safety_cfg):
    d = policy(robot_cfg, safety_cfg).check_segment(seg(pose=Pose.from_list([-0.45, -0.2, -0.05, math.pi, 0, 0])))
    assert not d.approved and any("z" in r for r in d.reasons)


def test_wrong_frame_rejected(robot_cfg, safety_cfg):
    p = Pose.from_list([-0.45, -0.2, 0.15, math.pi, 0, 0], frame_id="camera")
    assert not policy(robot_cfg, safety_cfg).check_segment(seg(pose=p)).approved


def test_collision_veto(robot_cfg, safety_cfg):
    d = policy(robot_cfg, safety_cfg, PathCheckResult(ok=False, conclusive=True, collisions=2, reason="hit")).check_segment(seg())
    assert not d.approved and d.code == ErrorCode.COLLISION_PREDICTED


def test_inconclusive_path_check(robot_cfg, safety_cfg):
    d = policy(robot_cfg, safety_cfg, PathCheckResult(ok=False, conclusive=False, reason="?")).check_segment(seg())
    assert not d.approved and d.code == ErrorCode.PATH_CHECK_INCONCLUSIVE


def test_checker_exception_is_inconclusive(robot_cfg, safety_cfg, scene_cfg):
    class Boom(MockController):
        def test_segment(self, s):
            raise RuntimeError("api down")
    p = SafetyPolicy(robot_cfg, safety_cfg, BackendPathChecker(Boom(robot_cfg, scene_cfg)))
    d = p.check_segment(seg())
    assert not d.approved and d.code == ErrorCode.PATH_CHECK_INCONCLUSIVE


def test_pybullet_requires_collision_geometry(robot_cfg, safety_cfg):
    empty_station = PathCheckResult(ok=True, conclusive=True, geometry_checked=False, reason="0 collisions")
    d = policy(robot_cfg, safety_cfg, empty_station, backend="pybullet").check_segment(seg())
    assert not d.approved and d.code == ErrorCode.PATH_CHECK_INCONCLUSIVE


def test_physical_backend_denied(robot_cfg, safety_cfg):
    d = policy(robot_cfg, safety_cfg).check_backend(PhysicalDisabledController(robot_cfg))
    assert not d.approved and d.code in (ErrorCode.MODE_VIOLATION, ErrorCode.PHYSICAL_BACKEND_DISABLED)


def test_physical_backend_denied_even_if_allowed(robot_cfg, safety_dict):
    safety_dict["allowed_backends"] = ["mock", "pybullet", "physical_disabled"]
    d = policy(robot_cfg, SafetyConfig.model_validate(safety_dict)).check_backend(PhysicalDisabledController(robot_cfg))
    assert not d.approved and d.code == ErrorCode.PHYSICAL_BACKEND_DISABLED


def test_stale_state(robot_cfg, safety_cfg, scene_cfg):
    m = MockController(robot_cfg, scene_cfg, stale_state=True)
    m.connect()
    d = policy(robot_cfg, safety_cfg).check_state(m.get_state())
    assert not d.approved and d.code == ErrorCode.STALE_DATA


def test_disconnected_state(robot_cfg, safety_cfg, scene_cfg):
    m = MockController(robot_cfg, scene_cfg)
    d = policy(robot_cfg, safety_cfg).check_state(m.get_state())
    assert not d.approved and d.code == ErrorCode.BACKEND_NOT_CONNECTED


def _det(**kw):
    base = dict(object_id="test_cube", class_label="cube", confidence=0.9, timestamp_utc=utc_now(),
                monotonic_s=time.monotonic(), source="mock", is_mock=True)
    base.update(kw)
    return Detection(**base)


@pytest.mark.parametrize("det,code", [
    (None, ErrorCode.PRECONDITION_FAILED),
    (_det(confidence=0.3), ErrorCode.SAFETY_VETO),
    (_det(monotonic_s=time.monotonic() - 3600), ErrorCode.STALE_DATA),
    (_det(pose=Pose.from_list([0.1, 0.1, 0.5, 0, 0, 0], frame_id="camera")), ErrorCode.SAFETY_VETO),  # raw camera XYZ
])
def test_detection_rejections(robot_cfg, safety_cfg, det, code):
    d = policy(robot_cfg, safety_cfg).check_detection(det)
    assert not d.approved and d.code == code


def test_mock_detection_disallowed_by_config(robot_cfg, safety_dict):
    safety_dict["allow_mock_detector"] = False
    d = policy(robot_cfg, SafetyConfig.model_validate(safety_dict)).check_detection(_det())
    assert not d.approved


@pytest.mark.parametrize("cycles,grip,mass,ok", [
    (2, True, 0.1, True),
    (4, True, 0.1, False),      # > pattern max 3
    (2, False, 0.1, False),     # no verified grip
    (2, True, None, False),     # unknown payload
    (2, True, 2.0, False),      # payload above pattern max
])
def test_shake_bounds(robot_cfg, safety_cfg, scene_cfg, cycles, grip, mass, ok):
    pat = scene_cfg.patterns["gentle_shake"]
    assert policy(robot_cfg, safety_cfg).check_shake("gentle_shake", pat, cycles, grip, mass).approved is ok


# ---- config validation (invalid model config => reject) ------------------------

def test_invalid_model_config_dof_mismatch(robot_dict):
    robot_dict["dof"] = 7
    with pytest.raises(ValueError):
        RobotConfig.model_validate(robot_dict)


def test_invalid_model_config_nan(robot_dict):
    robot_dict["limits"]["joint_max_rad"][0] = float("nan")
    with pytest.raises(ValueError):
        RobotConfig.model_validate(robot_dict)


def test_safety_speed_above_model_limit(robot_cfg, safety_dict, scene_cfg):
    safety_dict.pop("speed_percent_of_model_max")
    safety_dict.update(tcp_speed_m_s=1.5, tcp_accel_m_s2=0.5, joint_speed_rad_s=1.0, joint_accel_rad_s2=2.0)  # UR5e max 1.0 m/s
    with pytest.raises(RobotControlError) as ei:
        cross_validate(robot_cfg, SafetyConfig.model_validate(safety_dict), scene_cfg)
    assert ei.value.code == ErrorCode.CONFIG_ERROR


def test_pattern_cycles_above_safety_max(robot_cfg, safety_dict, scene_cfg):
    safety_dict["max_shake_cycles"] = 2     # pattern allows 3
    with pytest.raises(RobotControlError):
        cross_validate(robot_cfg, SafetyConfig.model_validate(safety_dict), scene_cfg)


def test_scene_unknown_target_reference(scene_dict):
    from robot_control.config import SceneConfig
    scene_dict["objects"]["test_cube"]["grasp_target"] = "nowhere"
    with pytest.raises(ValueError):
        SceneConfig.model_validate(scene_dict)


def test_missing_config_file():
    from robot_control.config import load_robot_config
    with pytest.raises(RobotControlError) as ei:
        load_robot_config("/nonexistent/robot.yaml")
    assert ei.value.code == ErrorCode.CONFIG_ERROR


# ---- speed as a percentage of model maximums -------------------------------------

def test_speed_percent_resolves_from_model(robot_cfg, safety_dict):
    from robot_control.config import motion_limits
    safety_dict["speed_percent_of_model_max"] = 25
    for k in ("tcp_speed_m_s", "tcp_accel_m_s2", "joint_speed_rad_s", "joint_accel_rad_s2"):
        safety_dict.pop(k, None)
    ml = motion_limits(robot_cfg, SafetyConfig.model_validate(safety_dict))
    assert ml.tcp_speed_m_s == pytest.approx(0.25)            # 25% of 1.0 m/s
    assert ml.tcp_accel_m_s2 == pytest.approx(0.5)            # 25% of 2.0 m/s^2
    assert ml.joint_speed_rad_s == pytest.approx(math.pi / 4, abs=1e-4)  # 25% of 180 deg/s = 45 deg/s
    assert ml.joint_accel_rad_s2 == pytest.approx(1.0)        # 25% of 4.0 rad/s^2


def test_speed_percent_uses_slowest_joint(safety_dict):
    import yaml
    from tests.conftest import CFG
    from robot_control.config import motion_limits
    ur3 = RobotConfig.model_validate(yaml.safe_load((CFG / "robot.ur3e.example.yaml").read_text()))
    safety_dict["speed_percent_of_model_max"] = 25
    ml = motion_limits(ur3, SafetyConfig.model_validate(safety_dict))
    assert ml.joint_speed_rad_s == pytest.approx(0.25 * math.pi, abs=1e-4)   # wrists are faster; base joints bound it


@pytest.mark.parametrize("pct", [0, -10, 150])
def test_speed_percent_out_of_range(safety_dict, pct):
    safety_dict["speed_percent_of_model_max"] = pct
    with pytest.raises(ValueError):
        SafetyConfig.model_validate(safety_dict)


def test_speed_percent_and_absolute_conflict(safety_dict):
    safety_dict["tcp_speed_m_s"] = 0.1          # example file already sets a percentage
    with pytest.raises(ValueError):
        SafetyConfig.model_validate(safety_dict)


def test_no_speed_source_rejected(safety_dict):
    safety_dict.pop("speed_percent_of_model_max")
    with pytest.raises(ValueError):
        SafetyConfig.model_validate(safety_dict)
