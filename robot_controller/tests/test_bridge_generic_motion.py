import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bridge"))
import motiq_bridge as mb


def test_explicit_reset_is_authenticated_and_forwards_controller_refusal():
    import httpx
    from fastapi.testclient import TestClient
    b = fake_bridge()
    b.controller = 'http://127.0.0.1:8765'
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(409, json={'error': {'message': 'task active'}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        b.http = client
        api = TestClient(mb.create_app(b, 'test-bridge-key'))
        assert api.post('/simulation/reset').status_code == 401
        assert not calls
        response = api.post('/simulation/reset', headers={'X-Bridge-Key': 'test-bridge-key'})
        assert response.status_code == 409
        assert response.json()['error'] == 'task active'
        assert calls == ['/robot/simulation/reset']


def fake_bridge():
    b = object.__new__(mb.Bridge)
    b._physical = mb.NullPhysicalArm(sim=True)
    b.real_arm = None
    b.ursim = None
    b.active_task = None
    b.controller_backend = "robodk_sim"
    b._controller_backend = lambda: "robodk_sim"
    b.calls = []

    def run_task(actions, did, speed_percent=None):
        b.calls.append((actions, did, speed_percent))
        return {"ok": True, "did": did, "speed_percent": speed_percent or 30}

    b.run_task = run_task
    return b


def test_voice_reset_uses_same_controller_endpoint_without_a_task():
    import httpx
    b = fake_bridge()
    b.controller = 'http://127.0.0.1:8765'
    requests = []

    def handle(request):
        requests.append(request.url.path)
        return httpx.Response(200, json={'reset': True, 'executor_state': 'IDLE',
                                         'simulation': True, 'moved': False})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        b.http = client
        result = b.tool('reset_simulation_fault', {})
    assert result['ok'] and result['reset'] and result['moved'] is False
    assert result['executor_state'] == 'IDLE'
    assert requests == ['/robot/simulation/reset']
    assert not b.calls


def test_voice_reset_reports_controller_refusal_and_rejects_real_target():
    import httpx
    import pytest
    b = fake_bridge()
    b.controller = 'http://127.0.0.1:8765'
    requests = []

    def handle(request):
        requests.append(request.url.path)
        return httpx.Response(400, json={'error': {'code': 'MODE_VIOLATION',
                                                   'message': 'not robodk_sim'}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        b.http = client
        with pytest.raises(mb.Refused, match='simulation-only'):
            b.tool('reset_simulation_fault', {}, target='real')
        assert not requests
        result = b.tool('reset_simulation_fault', {})
        assert result == {'ok': False, 'error': 'not robodk_sim', 'code': 'MODE_VIOLATION'}
        assert requests == ['/robot/simulation/reset']


def test_cartesian_tool_routes_to_controller_task_with_frame_and_speed():
    b = fake_bridge()
    result = b.tool("move_right", {"distance_m": 0.30, "reference_frame": "tool", "speed_percent": 40})
    assert result["ok"]
    actions, _did, speed = b.calls[0]
    assert actions == [("move_relative", {"direction": "right", "distance_m": 0.30,
                                           "reference_frame": "tool"})]
    assert speed == 40


def test_joint_tool_converts_degrees_and_routes_to_controller():
    b = fake_bridge()
    b.tool("move_joint", {"joint_index": 2, "delta_deg": 15, "speed_percent": 20})
    actions, _did, speed = b.calls[0]
    assert actions[0][0] == "move_joint"
    assert actions[0][1]["joint_index"] == 2
    assert actions[0][1]["delta_rad"] == math.radians(15)
    assert speed == 20


def test_absolute_joint_target_is_not_mixed_with_delta():
    b = fake_bridge()
    b.tool("move_joint", {"joint_index": 3, "target_deg": 45})
    params = b.calls[0][0][0][1]
    assert params == {"joint_index": 3, "target_rad": math.radians(45)}


def test_model_specific_rotate_yaw_is_refused_for_generic_robodk():
    b = fake_bridge()
    try:
        b.tool("rotate_yaw", {"angle_deg": 15})
    except mb.Refused as e:
        assert "explicit joint_index" in str(e)
    else:
        raise AssertionError("rotate_yaw should be refused")


def test_table_object_move_is_one_atomic_task_with_speed():
    b = fake_bridge()
    b.resolve_object = lambda value: 'blue_bucket_01'
    b.ensure_visible = lambda oid: None
    b.tool('move_object_on_table', {'object': 'blue bucket', 'direction': 'right',
                                   'distance_m': 0.1, 'speed_percent': 20})
    actions, _, speed = b.calls[0]
    assert len(b.calls) == 1 and speed == 20
    assert actions == [('identify_object', {'object_id': 'blue_bucket_01'}),
                       ('pick_object', {'object_id': 'blue_bucket_01'}),
                       ('place_on_table', {'direction': 'right', 'distance_m': 0.1})]


def test_explicit_push_does_not_split_into_fixed_distance_tasks():
    b = fake_bridge()
    b.resolve_object = lambda value: 'blue_bucket_01'
    b.ensure_visible = lambda oid: None
    b.tool('push', {'object': 'blue bucket', 'direction': 'right', 'distance_m': 0.1,
                    'speed_percent': 15})
    assert len(b.calls) == 1
    actions, _, speed = b.calls[0]
    assert actions[-1] == ('push_object', {'object_id': 'blue_bucket_01', 'direction': 'right', 'distance_m': 0.1})
    assert speed == 15


def test_custom_path_keeps_controller_failure():
    b = fake_bridge()
    b.run_task = lambda *args: {'ok': False, 'error': 'predicted collision'}
    assert b.tool('follow_path', {'waypoints': [{'x': 0.1, 'y': 0, 'z': 0.3}]}) == {
        'ok': False, 'error': 'predicted collision'}


def test_legacy_taught_push_keeps_task_speed():
    b = fake_bridge()
    b.scene = {'objects': {'blue_bucket_01': {'push': {'distance_m': 0.05}}}}
    b.ensure_visible = lambda oid: None
    b.push('blue_bucket_01', {'speed_percent': 20})
    assert b.calls[0][2] == 20


def test_simulation_check_placed_does_not_infer_location_from_empty_gripper():
    b = fake_bridge()
    b._get = lambda path: {'ok': False, 'verification_source': 'robodk_station_geometry', 'camera_verified': False}
    result = b.check_placed('blue_bucket_01', 'delivery_zone')
    assert result['ok'] is False
    assert result['camera_verified'] is False


def test_explicit_sim_camera_mode_requires_fresh_frames():
    import time
    import pytest
    b = fake_bridge()
    with pytest.raises(mb.Refused, match='stale'):
        b._validate_sim_frame({'ts': time.time()-10})
    with pytest.raises(mb.Refused, match='safety stop'):
        b._validate_sim_frame({'ts': time.time(), 'safety_stop': True})
    b._validate_sim_frame({'ts': time.time(), 'objects': []})


def test_center_table_request_preserves_semantics_and_task_speed():
    b = fake_bridge()
    b.resolve_object = lambda value: 'blue_bucket_01'
    b.ensure_visible = lambda oid: None
    b.tool('move_object_on_table', {'object': 'blue bucket', 'location': 'center', 'speed_percent': 10})
    assert b.calls[0][0][-1] == ('place_on_table', {'location': 'center'})
    assert b.calls[0][2] == 10


def test_simulated_camera_refusal_never_falls_back_to_mock():
    import pytest
    b = fake_bridge()
    b.sim_vision = True
    def unavailable(need=None):
        raise mb.Refused('simulated camera cannot see object')
    b.sync_vision = unavailable
    with pytest.raises(mb.Refused, match='cannot see'):
        b.ensure_visible('blue_bucket_01')
    assert not b.calls
