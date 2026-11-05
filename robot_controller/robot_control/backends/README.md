# backends

Implementations of `RobotController`. `create_controller()` in `__init__.py` picks one from config or `--backend`.

| File | Backend | Notes |
|---|---|---|
| `mock.py` | `mock` | Deterministic, in memory, with fault injection. No real kinematics: Cartesian targets get pseudo-joints so limit checks have input. |
| `robodk_sim.py` | `robodk_sim` | Never connects a robot driver, generates programs, or leaves `RUNMODE_SIMULATE` |
| `robodk_station.py` | | Builds the demo station in a new tab |
| `ursim_sync.py` | `ursim_sync` | Runs on URSim and RoboDK in lock-step. Loopback hosts only. |
| `physical_disabled.py` | `physical_disabled` | Diagnostics work. Every motion and gripper call raises `PHYSICAL_BACKEND_DISABLED`. |

To add a backend, subclass `RobotController`, register it in `create_controller`, add it to the CLI choices and to `allowed_backends`. Feedback must state its source honestly: `simulated`, `unverified` or `hardware_confirmed`.

A physical adapter is deliberately not implemented. See [`SAFETY_BOUNDARIES.md`](../../docs/SAFETY_BOUNDARIES.md).
