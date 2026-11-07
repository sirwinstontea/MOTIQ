# planner/executors

Run planner primitives through one `Executor` interface. `make_executor("mock" | "pybullet")` picks one.

| File | Purpose |
|---|---|
| `base.py` | `Executor` interface and `ExecutorError` |
| `mock_exec.py` | `MockExecutor`. `make_executor("pybullet")` returns this too. Arm motion runs in the controller's PyBullet cell. |

Primitives use metres and degrees.
