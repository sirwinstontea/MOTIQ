# States and feedback

## Task FSM (explicit table in `task_executor.py: TRANSITIONS`)

```
IDLE -> VALIDATING -> (per action)
  identify_object : IDENTIFYING -> IDENTIFIED
  pick_object     : APPROACH_PICK -> AT_PICK -> GRIPPING -> VERIFYING_GRIP -> LIFTING
  move_object     : TRANSPORTING
  shake_object    : SHAKING
  place_object    : APPROACH_PLACE -> AT_PLACE -> RELEASING -> RETREATING
  release_object  : RELEASING
  push_object     : PUSHING
  move_to_target / perform_motion : MOVING
-> COMPLETED

any active state -> FAULT      (failure, timeout, stale data, safety veto)
any active state -> CANCELLED  (cancel_task / stop_robot)
FAULT, CANCELLED -> IDLE       only via reset_fault(operator="name"); never AI-triggered
```

You can't reach SHAKING, LIFTING or TRANSPORTING without passing VERIFYING_GRIP, and the executor also
re-checks the grip during the shake and before release.

Task status and action status are tracked separately:
`PENDING | RUNNING | SUCCEEDED | FAILED | CANCELLED | REJECTED`.

## Event format (every action result and executor event)

```json
{
  "schema_version": "1.0", "event_id": 7, "task_id": "demo_001", "action_id": "a2",
  "action": "pick_object", "state": "LIFTING", "status": "SUCCEEDED",
  "timestamp_utc": "2026-11-04T16:14:35.788Z", "backend": "robodk_sim", "robot_model": "UR5e",
  "parameters": {"object_id": "orange_screw_03"},
  "feedback": {
    "tcp_pose": {"x": -0.45, "y": -0.2, "z": 0.25, "rx": 3.1416, "ry": 0.0, "rz": 0.0,
                 "frame_id": "base", "units": "m_rad_rotvec"},
    "joints": {"values": [0.14, -1.59, 1.82, -1.80, -1.57, 1.72], "units": "rad"},
    "holding_object_id": "orange_screw_03", "grip_confirmed": true, "gripper_state": "closed",
    "feedback_source": "simulated", "data_age_ms": 3.1, "detector": "mock"
  },
  "error": null,
  "safety_checks": {"approved": true, "reasons": ["..."]},
  "result": {"object_id": "orange_screw_03", "object_state": "held", "grip_confirmed": true, "grip_source": "simulated"}
}
```

* `feedback_source` is `simulated` (mock / RoboDK) or `unverified`. This build never emits `hardware_confirmed`.
* Every motion segment emits an `INFO` event carrying its safety decision (limits, workspace, path-check result).
* `error` = `{code, message, recoverable, auto_retry: false}`. Motion is never retried automatically.
* `--events-out file.json` on the CLI writes the full audit trail.
