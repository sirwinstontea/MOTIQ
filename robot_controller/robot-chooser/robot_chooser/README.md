# robot_chooser

| File | Purpose |
|---|---|
| `core.py` | `Chooser`: loads the catalog and hardware, evaluates readiness, renders hardware YAML and the controller config |
| `components.py` | `TypeRegistry` and `Component`: generic hardware from [`hardware_types/`](../hardware_types) |
| `tooling.py` | Works out what the arm carries (mass, TCP, gripper) and how devices connect. Migrates v1 hardware files. |
| `server.py` | Local web UI and JSON API, standard library only, loopback by default |
| `cli.py`, `__main__.py` | Command line |
| [`static/`](static) | Web UI |

```bash
python -m robot_chooser list --brand Elite --dof 6
python -m robot_chooser status
```
