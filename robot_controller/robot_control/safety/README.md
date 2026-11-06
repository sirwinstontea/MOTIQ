# safety

The software gate. It runs before every action and every motion segment, and any doubt means not approved. This is risk reduction, **not** a certified safety system.

| File | Purpose |
|---|---|
| `policy.py` | `SafetyPolicy`: `check_backend`, `check_state`, `check_segment`, `check_detection`, `check_shake` |
| `limits.py` | Joint limit and workspace checks |
| `trajectory.py` | Path check adapters. `BackendPathChecker` delegates to RoboDK `MoveJ_Test` / `MoveL_Test`. |
| `watchdog.py` | Data freshness and deadlines |

A denial gives `REJECTED` with reasons and latches FAULT. The controller never switches to a weaker backend or turns checks off. Full rules are in [`SAFETY_BOUNDARIES.md`](../../docs/SAFETY_BOUNDARIES.md).
