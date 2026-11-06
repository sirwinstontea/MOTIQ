# tests

Offline tests for robot-chooser. `conftest.py` builds a throw-away copy of the repository files that the chooser reads and writes.

| File | Covers |
|---|---|
| `test_chooser.py` | Catalog, filters, readiness, generated files that load in the controller, payload and speed blocks |
| `test_any_robot.py` | Any arm with 1-12 axes can be added, configured, selected and removed |
| `test_components.py` | Hardware as data: payload, end effectors, cycles, new types as files, v1 migration |
| `test_server.py` | HTTP contract and validation errors |

Run from `robot-chooser/`:

```bash
python -m pytest tests
```
