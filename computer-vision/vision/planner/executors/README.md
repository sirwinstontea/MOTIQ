# planner/executors

Run planner primitives through one `Executor` interface. `make_executor("mock" | "robodk")` picks one.

| File | Purpose |
|---|---|
| `base.py` | `Executor` interface and `ExecutorError` |
| `mock_exec.py` | `MockExecutor`: in-memory simulation for tests and work without RoboDK |
| `robodk_exec.py` | `RoboDKExecutor`: needs RoboDK open locally and `pip install robodk`. Forces `RUNMODE_SIMULATE`, so a real robot is never driven. |

Primitives use metres and degrees. RoboDK uses millimetres and degrees, with `robomath` rotations in radians.
