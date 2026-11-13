# Motion-validation performance

This extends the existing controller/executor. No collision/path results are cached and no safety check is removed.
It does not connect to hardware or change speed, acceleration, RoboDK playback, collision resolution or default paths.

## Batched, verified collision-map updates

The RoboDK contact policy collects applicable pairs before mutation, then uses `setCollisionActivePairList` for
apply/restore. Item names and types are resolved at most once per item handle within that operation, then discarded.
Edits/renames/tags are reevaluated on the next operation. Table, robot self-collision, unknown geometry and other robots
remain protected, including same-name robot/object ambiguities. Pair comparison retains link indices and tolerates
reversed pair ordering.

After applying the policy, the entire active map must equal its original snapshot minus permitted contact pairs.
After restoring, it must equal the original set. A truthy batch return is NOT assumed to prove complete success.
Missing batch APIs use the individual method with the same verification. Failed/partial apply never enters movement.
ALL candidate pairs are restored after partial failure. Failed batch restoration attempts every individual pair;
an unverifiable restoration raises a backend fault. There is no automatic motion retry.

The policy is deliberately NOT kept active across a whole task: each validation/execution operation still gets its
own verified scope and restoration. Contact permissions are not carried across unchecked scene edits.

## Optional circle geometry tolerance

`circle_object` accepts `max_chord_error_m`, an optional maximum radial deviation of each polygon chord from the
circle. Segment count uses the sagitta bound, retaining 24 minimum/256 maximum segments. A 0.15 m radius with an
explicitly approved 0.0005 m error uses 39 polygon segments rather than the default 95: 40 versus 96 moves including
entry. Closure, vertex radius, orientation and complete path checks remain enforced. This is not blending or MoveC.

Omitting the parameter preserves the old roughly 1 cm arc spacing. Voice must never invent or relax a tolerance for
speed; it must be requested/approved explicitly. This tolerance is NOT collision clearance. RoboDK path tests still
use 2 mm linear/1 degree joint steps, regardless of circle segment count. All initial and pre-execution checks remain.
Requests exceeding numerical/resource limits are refused rather than silently coarsened.

Example action, inside an approved task:

```json
{"action_id":"circle","action":"circle_object","parameters":{"object_id":"blue_bucket_01","radius_m":0.15,"z_m":0.25,"max_chord_error_m":0.0005}}
```

This is a schema example, not a certified feasible trajectory. Bridge forwards tolerance in the same task as its
task-specific speed, and the existing Voice approval applies to the complete request.

## Task-local timing and counters

Accepted tasks report `execution.performance` in existing task status and Bridge results. The controller logs one
`task_performance task_id=...` summary containing only durations, counters and task ID: no environment values,
secrets, token data or robot command payloads. Recorders are task-worker local; successive tasks/independent
controllers do not share accumulated measurements.

```json
{
  "elapsed_ms": 1234.5,
  "stages": {
    "collision_policy.resolve": {"calls": 2, "failures": 0, "total_ms": 50.0, "max_ms": 28.0}
  },
  "counters": {"collision_pair_batch_calls": 4, "collision_map_reads": 6}
}
```

These numbers are illustrative, not measured RoboDK latency. Stages cover preflight, state gates, safety segment
decisions, RoboDK planning/pose IK, backend readiness, state reads, collision-map resolve/apply/restore, path tests
and execution (including API waits and Busy polling). Timings are inclusive and can overlap: do NOT sum them as
disjoint durations. `failures` counts raised exceptions, not every returned safety refusal. Elapsed time starts at
worker execution, excluding speech recognition, approval, Bridge transit and frontend rendering. Requests refused
before acceptance have no worker measurements; accepted failed tasks do report timings.

Counters distinguish map reads, item name/type queries, batch mutation calls and fallback individual calls. They
are instrumented call counts, not complete network-packet counts. Batch operations still send every pair: they save
round trips, not collision computations.

Inspect existing `GET /tasks/{task_id}` or the Bridge result. Compare identical requests at the same speed, scene,
attachments and playback multiplier. Separate validation delay from movement duration. For actual latency, use
repeated explicitly approved simulation trials and report median/p95, not one run. No trial is automatically started.

## Verified scope and deferred work

Synthetic tests compare 1, 10 and 50 permitted pairs. For 50, apply/restore mutation calls fall from 100 individual
calls to 2 batch calls. The optimized operation also reads the map 3 times: snapshot, post-apply verification and
post-restore verification. This deterministic mock call-budget test is NOT a measured end-to-end speedup or proof of
compatibility with the live RoboDK server.

Validation reuse/safety certificates remain deferred: reliable scene/model/frame/attachment revisions are not yet
available, and manual RoboDK edits cannot be assumed absent. Global planning, VAMP/FCL integration, mesh simplification
and trajectory blending are not implemented here. No existing geometry is altered.

## Literature behind the design

- [RoboDK batch collision API](https://robodk.com/doc/en/PythonAPI/robodk.html#robodk.robolink.Robolink.setCollisionActivePairList).
- [OMPL motion validation](https://ompl.kavrakilab.org/stateValidation.html): path waypoint spacing is distinct from
  the resolution needed to validate intervening movement.
- [Bialkowski et al., safety certificates](https://ottelab.com/html_stuff/pdf_files/Bialkowski.Otte.ea.IJRR16.pdf):
  reuse requires justified certificates, not a loose nearby-pose cache; this change does not implement that method.

## Loading the changes

Stop the MOTIQ stack terminal with Ctrl+C and rerun the existing [Windows launcher](../../bridge/start_robodk.ps1)
from the repository root. Leave the separate token server open. Refresh http://localhost:8081 and start a new Voice
session for the circle schema. No RoboDK restart/station rebuild is required solely for these changes. Query status
and speed first; only then request a small approved simulation test if desired. Simulation establishes no physical
safety guarantee.

## Verification (2026-11-13)

- Controller regression: **279 passed, 10 deselected**. Eight moving/opt-in integration tests and two known
  physical-UR-driver tests were deselected. The Linux-only digital-twin module was ignored because Windows lacks
  `fcntl`. These exclusions are not reported as passed.
- Focused performance/contact/path/Bridge/generic-motion suite: **81 passed**.
- Voice approval/hearing tests, TypeScript typecheck, focused ESLint, editor diagnostics and `git diff --check`: passed/clear.
- Tests exercise batch call budgets, partial apply failure, false batch-success responses, map restoration,
  recovery fallback, unrecoverable restoration, same-name robot protection, operation-local metadata,
  task-local timings, refused tasks, unchanged double path validation and bounded circle error.
- No robot movement or live moving RoboDK integration benchmark was performed. Real latency improvement remains
  to be measured after restarting services and explicitly approving simulation trials. No commit or push was made.

Changed implementation files: [RoboDK backend](../robot_control/backends/robodk_sim.py),
[timing recorder](../robot_control/performance.py), [task executor](../robot_control/task_executor.py),
[safety timing](../robot_control/safety/policy.py), [circle geometry](../robot_control/planning.py),
[action contract models](../robot_control/models.py), [Voice schema](../../groky-app/src/devices/tools.ts),
[Voice instructions](../../groky-app/src/voice/prompt.ts).
Tests: [performance](../tests/test_performance.py), [contact policy](../tests/test_contact_policy.py),
[paths](../tests/test_paths_and_table.py). Documentation: this guide plus
[generic motion](GENERIC_ROBODK_MOTION.md) and [table/contact paths](TABLE_CONTACT_PATHS.md).
Other existing local edits were preserved.
