# config

Example configuration for the controller. Invalid or missing configuration raises `CONFIG_ERROR`, so the controller fails closed. Units are metres and radians.

| File | Purpose |
|---|---|
| `robot.example.yaml` | UR5e model: DOF, limits, backend, gripper kind, TCP |
| `robot.ur3e.example.yaml` | UR3e variant |
| `scene.example.yaml` | Allowlisted targets, objects, destinations, patterns, motions and search poses |
| `safety.example.yaml` | Allowed backends, speed percentage, workspace box, timeouts, detection rules |

`robot.yaml`, `scene.yaml`, `safety.yaml`, `selection.yaml` and `robots/` are local files and gitignored. Copy them from the examples, or let [`robot-chooser`](../../robot-chooser) generate them. The CLI prefers them over the examples.

Scene targets are simulation placeholders. Re-teach them with the pendant on a real cell. The scene frame is the robot base, and the work area sits at negative X because this UR5e model faces −X at home.

Each object lists `class_labels`, which map vision classes to its ID. Vision model classes come first. Without a `mass_kg`, `shake_object` is refused for that object.
