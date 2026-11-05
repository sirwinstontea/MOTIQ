# scripts

Small entry points. Run them from `robot_controller/`.

| Script | Same as | Purpose |
|---|---|---|
| `generate_all.py` | `python -m robot_control.sim_objects.generate` | Generate the demo object STLs |
| `validate_all.py` | `python -m robot_control.sim_objects.validate` | Validate configs and meshes |
| `import_all.py` | `python -m robot_control.sim_objects.robodk_import` | Add objects to the open RoboDK station. `--dry-run` never connects. |
| `mirror_ursim_to_robodk.py` | | One-way, read-only copy of URSim joints into RoboDK. `--once` copies one pose, otherwise it follows at 10 Hz. |

See [`../objects`](../objects) for object options.
