# backends

Implementations of `RobotController`. `create_controller()` in `__init__.py` picks one from config or `--backend`.

| File | Backend | Notes |
|---|---|---|
| `mock.py` | `mock` | Deterministic, in memory, with fault injection. No real kinematics: Cartesian targets get pseudo-joints so limit checks have input. |
| `pybullet_sim.py` | `pybullet` | UR5e cell, four demo parts, symbolic gripper. No robot driver. |
| `ursim_sync.py` | `ursim_sync` | Runs on URSim and PyBullet in lock-step. Loopback hosts only. |
| `physical_disabled.py` | `physical_disabled` | Diagnostics work. Every motion and gripper call raises `PHYSICAL_BACKEND_DISABLED`. |

To add a backend, subclass `RobotController`, register it in `create_controller`, add it to the CLI choices and to `allowed_backends`. Feedback must state its source honestly: `simulated`, `unverified` or `hardware_confirmed`.

A physical adapter is deliberately not implemented. See [`SAFETY_BOUNDARIES.md`](../../docs/SAFETY_BOUNDARIES.md).
