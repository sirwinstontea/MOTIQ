# hardware_types

One YAML file per hardware type. A new type needs a new file and no new code.

| File | Type |
|---|---|
| `gripper.yaml` | End effector: parallel, angular, vacuum, magnetic or rigid. Only one may be carried by the arm. |
| `camera.yaml` | Camera |
| `force_torque_sensor.yaml` | Force/torque sensor |
| `tool_changer.yaml` | Tool changer |
| `mobile_base.yaml` | Mobile base |
| `generic.yaml` | Anything else |

Each file sets `type`, `label`, `description`, `attach_to`, `roles`, `payload_field` and `fields`, and may define drivers. Field entries give a type, limits, unit and whether the field is required. `components.py` loads and validates these files.
