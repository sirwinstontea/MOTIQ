# stations

RoboDK robot models and stations.

| File | Purpose |
|---|---|
| `UR5e.robot` | UR5e model from the RoboDK library, loaded by `setup-station` and looked up by `robot-chooser` |
| `UR5e_demo.rdk` | Generated demo station. Gitignored. |

`robot-chooser` looks for each robot's `library_file` only in this folder. Add a `.robot` file here to use another arm with `robodk_sim`.

Build the station with RoboDK running:

```bash
python -m robot_control.cli setup-station --config config/robot.yaml
```
