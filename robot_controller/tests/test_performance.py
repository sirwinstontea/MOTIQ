import threading
import pytest

from robot_control.performance import increment, measure, record_task, snapshot


def test_measurements_are_task_local_and_count_failures():
    assert snapshot() == {}
    with record_task() as recorder:
        with measure('planning'):
            increment('ik_calls', 2)
        with pytest.raises(ValueError):
            with measure('planning'):
                raise ValueError('unreachable')
        result = recorder.snapshot()
        assert result['stages']['planning']['calls'] == 2
        assert result['stages']['planning']['failures'] == 1
        assert result['stages']['planning']['total_ms'] >= 0
        assert result['counters']['ik_calls'] == 2
        other = []
        thread = threading.Thread(target=lambda: other.append(snapshot()))
        thread.start()
        thread.join()
        assert other == [{}]
    assert snapshot() == {}
    with record_task():
        assert snapshot()['stages'] == {}


def test_nested_scopes_restore_the_outer_recorder():
    with record_task():
        increment('outer')
        with record_task():
            increment('inner')
            assert snapshot()['counters'] == {'inner': 1}
        assert snapshot()['counters'] == {'outer': 1}


def test_task_feedback_contains_timings_and_the_next_task_starts_fresh(make_executor):
    ex = make_executor()
    def request(tid):
        return {'task_id': tid, 'actions': [{'action_id': 'a', 'action': 'move_relative',
                'parameters': {'direction': 'up', 'distance_m': 0.01}}]}
    first = ex.submit(request('first'), wait=True)
    assert first.status.value == 'SUCCEEDED'
    perf = first.execution['performance']
    assert perf['elapsed_ms'] >= 0
    assert perf['stages']['preflight']['calls'] == 1
    second = ex.submit(request('second'), wait=True)
    assert second.execution['performance']['stages']['preflight']['calls'] == 1
    assert first.execution['performance'] == perf


def test_refused_task_still_reports_failed_preflight_timing(make_executor):
    ex = make_executor()
    result = ex.submit({'task_id': 'bad', 'actions': [{'action_id': 'a', 'action': 'move_joint',
                       'parameters': {'joint_index': 999, 'delta_rad': 0.1}}]}, wait=True)
    assert result.status.value == 'REJECTED'
    assert result.execution['performance']['stages']['preflight']['failures'] == 1
