# Safety boundaries

**This is software risk reduction, not a certified safety system.** A real robot's functional safety comes from
its certified safety functions (UR safety configuration, protective stop, E-stop), protective devices, a risk
assessment, and a responsible, trained operator. Python code, RoboDK collision checks and an LLM cannot replace
physical interlocks, a protective stop or an emergency stop. `cancel_task` and `stop_robot` are controlled
software requests, **not** E-stops.

**A digital twin proves nothing about the physical cell.** A "0 collisions" result in RoboDK only means the
*modelled* geometry didn't intersect. The demo station's table and cube are stand-ins.

## Central gate (`safety/policy.py`), fail closed

Runs before every action and before every motion segment.

1. **Backend/mode**: backend must be in `allowed_backends`. `physical_disabled` is always refused. Capabilities
   must say simulation with motion enabled. RoboDK must be in `RUNMODE_SIMULATE` with no driver connection
   (checked again inside every move), and the tool and reference frame must not have changed.
2. **State**: connected, no fault, joint feedback with the right DOF, data age ≤ `max_state_age_ms`. One task
   owns the robot at a time.
3. **Allowlist**: only registered objects, targets, destinations, patterns and motions are accepted, and nothing
   arrives from the AI as a coordinate.
4. **Limits**: each segment is checked against the model's joint limits, the workspace box in a matching frame,
   and the model's reach. Speeds and accelerations are validated against model limits at startup. Payload and
   shake cycles are checked against the pattern, safety and model limits. Units are m/rad at the boundaries,
   converted explicitly to mm/deg for RoboDK.
5. **Path check**: RoboDK `MoveJ_Test` / `MoveL_Test` with collision checking ON. The simulated joints are
   snapshotted and restored, and a restore failure counts as inconclusive. A check is rejected if it's
   inconclusive, if a collision is found, or (`require_collision_geometry`) if the station has no geometry.
   The mock has no geometry by design and labels its checks "feasibility only".
6. **Vision**: confidence ≥ minimum, detection age ≤ maximum, mock detections only if allowed and always
   labelled. Any camera pose is rejected because there's no calibration.
7. **Execution**: per-segment and per-action timeouts. Cancel/stop are polled during motion. After each move the
   final joints must match the plan within 0.5°. The grip must be verified before lift, shake or transport.
8. **No fallback**: a denial gives `REJECTED` with reasons and latches FAULT. The controller never switches to a
   weaker backend or turns checks off.
9. **Logging and network**: decisions are logged without credentials or IPs. The API refuses to bind outside
   loopback.

## Plan for separate, supervised physical commissioning (not part of this build)

1. Risk assessment for the cell. Configure and verify the UR safety settings (limits, planes, tool orientation,
   reduced mode) on the teach pendant. Test the E-stop and protective stop.
2. Fit the real gripper. Measure TCP, payload and CoG and enter them on the controller. Write a gripper adapter
   with real object detection (e.g. Robotiq `rq_is_object_detected`).
3. Re-teach every named target on the pendant. Rebuild the RoboDK station from measured cell geometry.
4. Implement a UR RTDE adapter behind the `physical_disabled` interface in a separate module, keeping UR5e and
   UR3e as separate configs. Test it against URSim first (the Windows prototype already ran moveJ/moveL there).
5. Dry runs at reduced speed (≤ 10 %) with the operator holding the enable device. Run single segments first,
   then whole tasks. Add a hardware heartbeat watchdog.
6. Only after sign-off: enable a physical backend through an explicit, reviewed configuration change. It must
   never be selectable by the AI.
