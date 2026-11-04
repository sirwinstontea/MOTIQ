# RoboDK on Linux: setup for the `robodk_sim` backend

1. **Start the RoboDK GUI with the launcher** (it sets `LD_LIBRARY_PATH`; starting `~/RoboDK/bin/RoboDK`
   directly fails with `libTKG2d.so.7.9: cannot open shared object file`):
   ```bash
   ~/RoboDK/RoboDK-Start.sh &
   ```
   Or pass `--start-robodk` to the CLI. Without it, the backend only connects to a GUI that's already running
   and otherwise gives a clear `BACKEND_NOT_CONNECTED` error.
2. **Python API:** the backend imports `robodk` and, if that isn't installed, falls back to the copy bundled at
   `~/RoboDK/Python`. You don't need to reinstall RoboDK. A license is only needed for whatever RoboDK itself
   enforces, and this code doesn't try to get around license limits.
3. **Station:**
   * *Demo:* `python -m robot_control.cli setup-station --config config/robot.yaml` opens a new tab called
     **UR5e Controller Demo** (your other stations are untouched). The tab contains:
     * the UR5e from `stations/UR5e.robot` (https://robodk.com/robot/Universal-Robots/UR5e)
     * tool `Gripper TCP` (+150 mm Z)
     * base frame `UR5e Base`
     * named targets from `config/scene.example.yaml`
     * boxes for the table, delivery pad and `red_cube`

     It's saved to `stations/UR5e_demo.rdk`. Run it again to reset the cube.
   * *Your own station:* set these in `config/robot.yaml`:
     * `station_name` / `station_path`
     * `robot_item_name` (must be unique)
     * `base_frame` (the robot's parent frame)
     * `tool_frame` (a tool on that robot)

     Teach targets in RoboDK with **the same names** as in your scene YAML. Add collision geometry, or the path
     check will be rejected (`require_collision_geometry: true`).
4. **What the backend checks at connect and before every move:**
   * `RUNMODE_SIMULATE` is set and read back
   * the robot is selected by its configured name and its item type is checked
   * the tool and reference frame are still the active ones
   * `ConnectedState()` shows no driver connection
   * the DOF matches the config
5. **What it never does:** call `Connect`, generate or upload programs, or use any run-on-robot mode. A static test
   (`tests/test_mock.py::test_robodk_backend_has_no_real_robot_calls`) guards this.
6. **Over SSH / headless:** the integration tests skip unless `RUN_ROBODK_TESTS=1` is set *and* RoboDK is
   listening on `127.0.0.1:20500`. A display isn't assumed.

## UR3e

Download `UR3e.robot` into `stations/` and use `config/robot.ur3e.example.yaml` with a scene whose targets lie
within the UR3e's 0.5 m reach. The UR5e scene is (correctly) rejected as out of reach for a UR3e, and a test
covers this.
