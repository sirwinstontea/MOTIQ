# tests

Pytest suite for `robot_control`. By default it runs offline on the mock backend: 144 passed, with 5 integration tests deselected.

```bash
python -m pytest -q
RUN_ROBODK_TESTS=1 python -m pytest -m integration -q                       # RoboDK GUI running
RUN_URSIM_TESTS=1 python -m pytest -m integration tests/test_ursim_sync_smoke.py -q   # plus URSim
```

| File | Covers |
|---|---|
| `conftest.py` | Config fixtures and an executor factory |
| `test_contracts.py` | Task parsing, malformed input, NaN rejection, no raw motion tools |
| `test_safety.py` | Reach, joint limits, DOF, workspace, frames, collisions, inconclusive checks |
| `test_task_executor.py` | Happy path, FSM table, fault latch, no automatic retry, preflight |
| `test_mock.py` | Fault injection, stale data, grip failures, physical refusal |
| `test_api.py` | API flow, dashboard, operator reset, auth, speed |
| `test_object_tasks.py` | Demo object tasks and URSim offline parts |
| `test_sim_objects.py` | Geometry, STL export, dimensions, validators |
| `test_robodk_smoke.py` | Opt-in RoboDK simulator tests |
| `test_ursim_sync_smoke.py` | Opt-in URSim + RoboDK test |

Integration tests never connect to hardware.
