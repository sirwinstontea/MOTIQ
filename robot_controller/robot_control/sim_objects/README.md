# sim_objects

Generates, validates and imports the four demo objects. Lengths are in millimetres. Every object is a solid of revolution of a closed `(radius, z)` profile about +Z, with its origin at the centre of the lowest flat face.

| File | Purpose |
|---|---|
| `core.py` | Revolve, config loading, volume and mesh checks |
| `build.py` | Build and validate one object, export its STL |
| `generate.py` | Write all STL files |
| `validate.py` | Check configs, meshes, STLs and cavities. `--json` writes a report. |
| `robodk_import.py` | Add objects to the current RoboDK station. Simulation only. |
| [`shapes/`](shapes) | One profile generator per object |

Install with `pip install -e ".[objects,robodk]"`. Usage and options are in [`objects/`](../../objects).
