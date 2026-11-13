from fastapi.testclient import TestClient

from robot_control.api.app import create_app
from robot_control.models import TaskState


def test_reset_unblocks_rejected_simulation_without_motion(make_executor):
    ex = make_executor()
    ex.c.backend_name = 'robodk_sim'  # offline simulation double; no hardware or RoboDK required
    ex.policy.backend_name = 'mock'  # mock path checker has no geometry
    rejected = ex.submit({'task_id': 'outside', 'actions': [
        {'action_id': 'a', 'action': 'move_relative',
         'parameters': {'direction': 'up', 'distance_m': 5.0}}
    ]}, wait=True)
    assert rejected.status.value == 'REJECTED'
    assert ex.state == TaskState.FAULT
    before = ex.c.get_state()
    result = TestClient(create_app(ex)).post('/robot/simulation/reset')
    assert result.status_code == 200, result.text
    assert result.json()['moved'] is False
    assert ex.state == TaskState.IDLE and ex.latched is None
    assert ex.c.get_state().joints == before.joints
    assert ex.c.history == []
    assert rejected.status.value == 'REJECTED'  # historical refusal remains recorded
    followup = ex.submit({'task_id': 'valid_move', 'actions': [
        {'action_id': 's', 'action': 'move_relative',
         'parameters': {'direction': 'up', 'distance_m': 0.01}}
    ]}, wait=True)
    assert followup.status.value == 'SUCCEEDED'


def test_reset_excludes_other_backends(make_executor):
    ex = make_executor()
    ex.state = TaskState.FAULT
    r = TestClient(create_app(ex)).post('/robot/simulation/reset')
    assert r.status_code == 400
    assert ex.state == TaskState.FAULT


def test_state_exposes_runtime_workspace_not_selection_on_disk(make_executor):
    ex = make_executor()
    state = TestClient(create_app(ex)).get('/robot/state').json()
    assert state['workspace'] == ex.safety_cfg.workspace.model_dump(mode='json')
    assert state['reach_m'] == ex.robot_cfg.limits.reach_m


def test_reset_refuses_busy_or_invalid_state(make_executor):
    ex = make_executor()
    ex.c.backend_name = 'robodk_sim'
    ex.state = TaskState.FAULT
    client = TestClient(create_app(ex))
    ex._active = 'active'
    assert client.post('/robot/simulation/reset').status_code == 409
    ex._active = None
    ex.c._joints[2] = 100.0
    assert client.post('/robot/simulation/reset').status_code == 400
    assert ex.state == TaskState.FAULT


def test_reset_refuses_changed_simulation_mode(make_executor):
    ex = make_executor()
    ex.c.backend_name = 'robodk_sim'
    ex.state = TaskState.FAULT
    ex.c.check_ready = lambda: ['RoboDK mode changed or hardware driver detected']
    assert TestClient(create_app(ex)).post('/robot/simulation/reset').status_code == 400
    assert ex.state == TaskState.FAULT
