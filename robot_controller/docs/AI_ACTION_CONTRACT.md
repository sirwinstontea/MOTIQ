# AI action contract (schema 1.0)

The planner produces a **task**: an ordered list of allowlisted actions. Requests are strictly validated
(Pydantic, `extra="forbid"`). The planner has **no** tool for raw XYZ, joint arrays, moveJ/moveL, IP
addresses, scripts or code. `python -m robot_control.cli tools` prints the machine-readable tool list.

```json
{
  "schema_version": "1.0",
  "task_id": "demo_001",
  "idempotency_key": "optional-client-key",
  "actions": [
    {"action_id": "a1", "action": "identify_object", "parameters": {"object_id": "orange_screw_03"}},
    {"action_id": "a2", "action": "pick_object",     "parameters": {"object_id": "orange_screw_03"}},
    {"action_id": "a3", "action": "shake_object",    "parameters": {"pattern_id": "gentle_shake", "cycles": 2}},
    {"action_id": "a4", "action": "place_object",    "parameters": {"destination_id": "delivery_zone"}}
  ]
}
```

Note: in the current scene this exact task is refused at `shake_object`, because none of the 4 demo objects has a
measured `mass_kg` yet. Drop the shake step (see `examples/pick_place.json`) or add the mass to the scene first.

| Tool | Parameters | Task step? | Semantics |
|---|---|---|---|
| `get_robot_state` | – | yes | state, backend, mode, data age |
| `get_scene` | – | yes | registered IDs, patterns, detections |
| `identify_object` | `object_id` | yes | detection via the configured detector; `NOT_AVAILABLE` if there's no vision |
| `pick_object` | `object_id` | yes | open (if needed) → approach → grasp → close → **verify** → lift |
| `move_object` | `object_id`, `destination_id` | yes | transport a held, verified object to the destination **approach** |
| `place_object` | `destination_id` | yes | approach → place → open → verify → retreat |
| `release_object` | – | yes | open gripper; refused while holding (use `place_object`) |
| `move_to_target` | `target_id` | yes | joint move to a registered target |
| `shake_object` | `pattern_id`, `cycles` (int 1–10, and ≤ pattern/safety max) | yes | approved pattern made of named targets |
| `perform_motion` | `motion_id` | yes | pre-validated macro (e.g. `go_home`) |
| `push_object` | `object_id` | yes | empty gripper, closed (if needed) → above → down beside it → push `distance_m` → lift, on the object's taught `push:` path; needs a fresh detection; objects without a push path (the 7 mm ring) are refused. Simulation: the PyBullet object moves only on the push segment, and only if it is still beside the gripper |
| `get_task_status` | `task_id` | **no** (API) | `GET /tasks/{id}` |
| `cancel_task` | `task_id` | **no** (API) | `POST /tasks/{id}/cancel`; controlled cancel, not an E-stop |
| `stop_robot` | – | **no** (API) | `POST /robot/stop`; controlled stop, not an E-stop |

## When an object counts as held, at the destination, or released

| After | `object_state` | Meaning |
|---|---|---|
| `pick_object` succeeded | `held` | gripper closed **and** `verify_grip` confirmed (simulated) |
| `move_object` succeeded | `held_at_destination_approach` | still held, above the destination |
| `place_object` succeeded | `released_at_destination` | lowered to the place target, gripper opened and reported open, robot retreated |

Composite actions never double-open or double-close: `pick_object` opens the gripper only if it isn't already
open, and `release_object` on an open gripper sends no command.

## Rejections

| Rejected at submit, before a task is created (no state change) | Rejected during preflight / run (task `REJECTED`, FAULT latched) |
|---|---|
| unknown action name, extra fields, wrong types, NaN/Inf, negative or > 10 cycles, bad ID format, duplicate `action_id`, control calls inside a task, wrong `schema_version` | unknown object/target/pattern, target missing in the station, unreachable target, joint-limit / workspace violation, plan inconsistency (place before pick), excess cycles for the pattern, low-confidence or stale detection, collision predicted, path check inconclusive |

Repeating the same `idempotency_key` returns the original task without running it again. Re-using a
`task_id` gives `DUPLICATE_TASK`.
