import math
import pytest

from robot_control.controller import PathCheckResult
from robot_control.models import Pose, RobotControlError
from robot_control.planning import circle_waypoints, table_surface


def task(action, parameters, task_id='custom'):
    return {'task_id': task_id, 'actions': [{'action_id': 'a', 'action': action, 'parameters': parameters}]}


def test_path_preserves_orientation_and_executes_all_waypoints(make_executor):
    ex = make_executor()
    before = ex.c.get_tcp_pose()
    points = [{'x': -0.45, 'y': 0.0, 'z': 0.3}, {'x': -0.45, 'y': 0.05, 'z': 0.3}]
    result = ex.submit(task('follow_path', {'waypoints': points}), wait=True)
    assert result.status.value == 'SUCCEEDED', result.error
    assert ex.c.history == ['linear:waypoint'] * 2
    after = ex.c.get_tcp_pose()
    assert after.y == pytest.approx(0.05)
    assert [after.rx, after.ry, after.rz] == [before.rx, before.ry, before.rz]
    # Performance work must NOT remove the initial validation or the pre-execution check.
    assert result.execution['performance']['stages']['safety.segment']['calls'] == 4


def test_late_collision_rejects_entire_path_before_motion(make_executor):
    class Blocked:
        def check(self, seg):
            blocked = seg.end_pose.y > 0.1
            return PathCheckResult(ok=not blocked, conclusive=True, collisions=int(blocked), reason='obstacle')

    ex = make_executor(path_checker=Blocked())
    result = ex.submit(task('follow_path', {'waypoints': [
        {'x': -0.45, 'y': 0.0, 'z': 0.3}, {'x': -0.45, 'y': 0.2, 'z': 0.3}]}), wait=True)
    assert result.error.code.value == 'COLLISION_PREDICTED'
    assert ex.c.history == []


def test_circle_is_closed_and_clockwise_is_explicit():
    current = Pose.from_list([0.2, 0, 0.3, math.pi, 0, 0])
    points = circle_waypoints([0, 0, 0], 0.1, 0.3, True, current)
    assert points[1].y < 0
    assert points[0].as_list() == pytest.approx(points[-1].as_list())
    assert all(math.hypot(p.x, p.y) == pytest.approx(0.1) for p in points)
    assert all(p.rx == math.pi and p.z == 0.3 for p in points)


def test_explicit_circle_tolerance_reduces_waypoints_without_changing_radius_or_closure():
    current = Pose.from_list([0.2, 0, 0.3, math.pi, 0, 0])
    legacy = circle_waypoints([0, 0, 0], 0.15, 0.3, False, current)
    tolerant = circle_waypoints([0, 0, 0], 0.15, 0.3, False, current, 0.0005)
    assert len(legacy) == 96
    assert len(tolerant) == 40
    assert tolerant[0].as_list() == pytest.approx(tolerant[-1].as_list())
    for a, b in zip(tolerant, tolerant[1:]):
        middle_radius = math.hypot((a.x + b.x) / 2, (a.y + b.y) / 2)
        assert 0 <= 0.15 - middle_radius <= 0.0005
    assert all(math.hypot(p.x, p.y) == pytest.approx(0.15) for p in tolerant)


@pytest.mark.parametrize('tolerance', [0, -0.001, float('nan'), float('inf')])
def test_circle_rejects_invalid_tolerance_at_contract(make_executor, tolerance):
    with pytest.raises(RobotControlError):
        make_executor().submit(task('circle_object', {'object_id': 'blue_bucket_01', 'radius_m': 0.15,
            'z_m': 0.3, 'max_chord_error_m': tolerance}))


def test_table_is_full_surface_not_delivery_pad(scene_cfg):
    assert table_surface(scene_cfg, -0.45, -0.25, [0.03, 0.03, 0.05]) == pytest.approx(0.0)
    with pytest.raises(RobotControlError, match='beyond the table'):
        table_surface(scene_cfg, -0.45, 0.399, [0.03, 0.03, 0.05])


def test_pick_then_place_at_arbitrary_table_point(make_executor, safety_cfg):
    from robot_control.config import SafetyConfig
    safety = SafetyConfig.model_validate({**safety_cfg.model_dump(), 'workspace': {
        **safety_cfg.workspace.model_dump(), 'mode': 'kinematic', 'z_m': [0.0, 0.55]}})
    ex = make_executor(safety=safety)
    result = ex.submit({'task_id': 'table_move', 'actions': [
        {'action_id': 'i', 'action': 'identify_object', 'parameters': {'object_id': 'test_cube'}},
        {'action_id': 'p', 'action': 'pick_object', 'parameters': {'object_id': 'test_cube'}},
        {'action_id': 't', 'action': 'place_on_table', 'parameters': {'x_m': -0.4, 'y_m': -0.1}}
    ]}, wait=True)
    assert result.status.value == 'SUCCEEDED', result.error
    assert ex.c.get_gripper_state().holding_object_id is None
    assert ex.c.get_tcp_pose().x == pytest.approx(-0.4)
    assert ex.c.get_tcp_pose().y == pytest.approx(-0.1)


def test_center_location_is_resolved_by_controller(make_executor):
    ex = make_executor()
    result = ex.submit({'task_id': 'center', 'actions': [
        {'action_id': 'i', 'action': 'identify_object', 'parameters': {'object_id': 'test_cube'}},
        {'action_id': 'p', 'action': 'pick_object', 'parameters': {'object_id': 'test_cube'}},
        {'action_id': 't', 'action': 'place_on_table', 'parameters': {'location': 'center'}}]}, wait=True)
    assert result.status.value == 'SUCCEEDED', result.error
    assert ex.c.get_tcp_pose().x == pytest.approx(-0.45)
    assert ex.c.get_tcp_pose().y == pytest.approx(0)


@pytest.mark.parametrize('parameters', [
    {'waypoints': [{'x': float('nan'), 'y': 0, 'z': 0.3}]},
    {'waypoints': []},
])
def test_invalid_paths_rejected_at_contract(make_executor, parameters):
    with pytest.raises(RobotControlError):
        make_executor().submit(task('follow_path', parameters))


def test_new_paths_cannot_route_to_ursim(make_executor):
    ex = make_executor()
    ex.c.backend_name = 'ursim_sync'
    result = ex.submit(task('follow_path', {'waypoints': [{'x': -0.45, 'y': 0, 'z': 0.3}]}), wait=True)
    assert result.status.value == 'REJECTED'
    assert ex.c.history == []


def test_push_checks_late_stroke_collision_before_approach(make_executor, scene_cfg):
    end = scene_cfg.objects['blue_bucket_01'].push.end_target
    class Blocked:
        def check(self, seg):
            return PathCheckResult(ok=seg.target_id != end, conclusive=True,
                                   collisions=int(seg.target_id == end), reason='blocked stroke')
    ex = make_executor(path_checker=Blocked())
    result = ex.submit({'task_id': 'blocked_push', 'actions': [
        {'action_id': 'i', 'action': 'identify_object', 'parameters': {'object_id': 'blue_bucket_01'}},
        {'action_id': 'p', 'action': 'push_object', 'parameters': {'object_id': 'blue_bucket_01'}}]}, wait=True)
    assert result.error.code.value == 'COLLISION_PREDICTED'
    assert ex.c.history == []


def test_tagged_object_cannot_be_pushed(make_executor, scene_cfg):
    scene_cfg.objects['blue_bucket_01'].avoid_collision = True
    ex = make_executor()
    result = ex.submit(task('push_object', {'object_id': 'blue_bucket_01'}), wait=True)
    assert 'avoid_collision' in result.error.message
    assert ex.c.history == []
