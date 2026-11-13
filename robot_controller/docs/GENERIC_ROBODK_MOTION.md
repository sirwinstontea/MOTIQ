# Generic RoboDK motion

## Workspace policies

`workspace.mode: kinematic` uses the actual RoboDK model's IK, joint limits and swept collision tests for reach.
The old X/Y box and upper-Z values are retained as cell-layout metadata but are NOT enforced as motion bounds.
There is no additional sphere based on nominal catalogue reach (which is not an exact TCP workspace).
The configured `workspace.z_m[0]` remains the minimum target TCP clearance; the current demo uses 0.015 m.
RoboDK trajectory collision checks against the modeled table, robot and other geometry remain mandatory.
This is not a global planner and does not prove a requested 50 cm straight-line move is feasible.

Select it persistently through robot-chooser:

```powershell
cd robot-chooser
..\robot_controller\.venv\Scripts\python.exe -m robot_chooser set workspace_mode kinematic
```

Restart the controller and Bridge and open a new Voice session afterward. The controller status and Robots screen
report the active policy. `workspace_mode: box` remains available for cells needing an explicit operational box.
Kinematic mode refuses configurations with path checking or collision geometry checking disabled.

MOTIQ exposes generic motion as ordinary `robot_controller` tasks. The Bridge and Grok never call RoboDK directly.
The selected robot must be `READY` in robot-chooser, its generated controller configuration must match the named
RoboDK item, and the backend must be `robodk_sim` in `RUNMODE_SIMULATE` with no robot driver connection.

## Task contract

Cartesian translation preserves TCP orientation and requests a straight `MoveL` path:

```json
{
  "schema_version": "1.0",
  "task_id": "jog_1",
  "speed_percent": 40,
  "actions": [{
    "action_id": "move",
    "action": "move_relative",
    "parameters": {"direction": "right", "distance_m": 0.3, "reference_frame": "base"}
  }]
}
```

`reference_frame` is `base` (default), `tool`, or `world`. Direction vectors are transformed into the active robot
base before IK; `forward/backward`, `left/right`, and `up/down` are opposite pairs on frame X, Y, and Z. Positions
are metres and controller angles are radians. There is no global step-distance cap: the active workspace policy,
IK, joint limits, and the complete RoboDK path decide feasibility.

Joint motion uses a one-based index and exactly one target form:

```json
{"action": "move_joint", "parameters": {"joint_index": 2, "delta_rad": 0.261799}}
```

or `target_rad` for an absolute joint target. The selected model's actual DOF and per-joint limits are authoritative.

## Planning and safety

Task timing and safe API-overhead optimizations are documented in [Motion performance](MOTION_PERFORMANCE.md).

The backend solves IK closest to the current joints to preserve branch continuity. Cartesian paths use
`MoveL_Test`; joint paths use `MoveJ_Test`. RoboDK checks the swept simulated path with collision checking enabled,
then MOTIQ restores the joint snapshot before execution. Missing collision geometry is inconclusive and is rejected
when `require_collision_geometry` is enabled. A blocked direct path is rejected; MOTIQ does not claim global path
planning and does not silently replace a requested straight path with a joint detour.

RoboDK's linear test reports singular/limit crossings along the path as a distinct `NEAR_SINGULAR` refusal. This
build does not claim a robot-normalized Jacobian condition threshold because the configured models do not provide
verified model-specific thresholds. If RoboDK cannot make a conclusive feasibility/collision decision, execution
fails closed.

Underactuated models are supported only for poses their RoboDK model can solve. An unavailable Cartesian DOF or an
IK-infeasible orientation is reported as unsupported/unreachable; it is never approximated silently.

## Velocity

`GET/POST /robot/speed` remains the default source of truth. Percentages 1–100 scale the active robot's verified
TCP speed/acceleration, joint acceleration, and each joint's configured maximum speed. RoboDK receives the slowest
joint scalar because its API uses one joint-speed setting; the result also reports the scaled per-joint limits.

Top-level `speed_percent` is an atomic task override. It is captured when the task is accepted, applied before
preflight/execution, recorded in status/events, and the default is restored afterward. It never mutates the default,
and standalone default changes are refused while a task is active.

## Voice and Bridge

Grok tools include the six Cartesian directions, `move_joint`, `get_speed`, `set_speed`, and `status`. Motion still
requires the existing spoken/typed approval. The Bridge detects `robodk_sim` from controller health, submits one
coherent task (including task speed), waits for terminal feedback, and reports controller failures verbatim. It does
not initialize URSim or the physical jog driver for this backend.

Examples: “Set the robot speed to 30 percent”, “move 20 centimetres right”, “move 40 centimetres up at 60 percent”,
and “rotate joint two by 15 degrees at 20 percent”.

## Compatibility limits

For full-table destinations, explicit-distance pushing, collision tags and checked waypoint/circle tools, see
[Table contact and paths](TABLE_CONTACT_PATHS.md).

Robot independence means there are no manufacturer kinematics or fixed joint counts in this path. Compatibility
still requires a verified robot-chooser entry, matching RoboDK model and frames, collision geometry, workspace,
limits, TCP, and reachable requested path. Changing robots requires applying robot-chooser and restarting the
controller and Bridge. This is simulation validation only and establishes no physical-robot safety.

## Opt-in integration test

With the configured station open in RoboDK, explicitly opt in:

```powershell
$env:RUN_ROBODK_TESTS='1'
robot_controller\.venv\Scripts\python.exe -m pytest robot_controller\tests\test_robodk_smoke.py -m integration -q
```

The suite reads model/state, performs a 1 cm Cartesian move and a 1 degree joint move at 10%, verifies
`RUNMODE_SIMULATE`, checks collision snapshot restoration, and refuses an unreachable request. It moves only the
simulated robot and is intentionally excluded from routine tests.
