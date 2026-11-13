import pytest

from robot_control.config import SafetyConfig
from robot_control.models import Pose
from robot_control.safety.limits import pose_within_workspace


def test_no_table_footprint_or_nominal_reach_cap(robot_cfg, safety_cfg):
    workspace = safety_cfg.workspace.model_copy(update={'mode': 'kinematic'})
    # This checker only applies table clearance; IK/path validation remains mandatory in the policy.
    target = Pose.from_list([-0.45, 0.50, 0.8, 0, 0, 0])
    assert pose_within_workspace(target, workspace, robot_cfg) == []
    assert pose_within_workspace(target, safety_cfg.workspace, robot_cfg)


def test_table_clearance_and_frame_are_still_required(robot_cfg, safety_cfg):
    workspace = safety_cfg.workspace.model_copy(update={'mode': 'kinematic'})
    target = Pose.from_list([-0.45, 0.5, workspace.z_m[0] - 0.001, 0, 0, 0])
    assert 'table clearance' in pose_within_workspace(target, workspace, robot_cfg)[0]
    target.frame_id = 'world'
    assert 'frame' in pose_within_workspace(target, workspace, robot_cfg)[0]


@pytest.mark.parametrize('flag', ['require_path_check', 'require_collision_geometry'])
def test_kinematic_mode_cannot_disable_collision_validation(safety_dict, flag):
    safety_dict['workspace']['mode'] = 'kinematic'
    safety_dict[flag] = False
    with pytest.raises(ValueError, match='collision geometry'):
        SafetyConfig.model_validate(safety_dict)


def test_target_outside_old_box_still_gets_collision_veto(make_executor, safety_cfg):
    from robot_control.controller import PathCheckResult

    class Blocked:
        calls = 0

        def check(self, _segment):
            self.calls += 1
            return PathCheckResult(ok=False, conclusive=True, collisions=1, reason='table collision')

    safety = SafetyConfig.model_validate({**safety_cfg.model_dump(),
        'workspace': {**safety_cfg.workspace.model_dump(), 'mode': 'kinematic'}})
    checker = Blocked()
    ex = make_executor(safety=safety, path_checker=checker)
    result = ex.submit({'task_id': 'lateral_50cm', 'actions': [
        {'action_id': 'a', 'action': 'move_relative', 'parameters': {'direction': 'left', 'distance_m': 0.5}}
    ]}, wait=True)
    assert result.status.value == 'REJECTED'
    assert result.error.code.value == 'COLLISION_PREDICTED'
    assert checker.calls > 0
    assert not ex.c.history
