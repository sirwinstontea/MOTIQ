# hardware-capability-check

**Question answered:** can *this* robot cell (UR5e + the project's tool + camera) **physically** execute a given
`skill.json`? Not "is it the right task", not a safety approval - only hardware capability.

```
python -m hardware_capability_check path/to/skill.json            # readable report, exit code 0 / 3
python -m hardware_capability_check skill.json --json report.json # + machine-readable report
python -m hardware_capability_check skill.json --tool tool_example_parallel_gripper.yaml   # what-if: actuated gripper
python -m hardware_capability_check --verify-profiles             # prove every documented number against the PDFs
python -m pytest -q                                               # tests
```
(set `PYTHONPATH=src`, or `pip install -e .`). Python 3.11+, deps: numpy, pyyaml (tests: pytest, pymupdf, beautifulsoup4).
One check takes a few seconds.

## Verdict: only YES or NO

| verdict | meaning | exit code |
|---|---|---|
| `yes` | no hard blocker and nothing undecidable; unknown values (tool length, camera mount, ...) are listed as conditions | 0 |
| `no` | a hard blocker exists, OR required information is missing (then "Information needed" lists what) | 3 |

The report's `detail` field keeps the reason (`feasible`, `feasible_with_conditions`, `needs_validation`, `infeasible`).

Unknown values are **never invented**: unknown tool length / container height / object size are *scanned* over a plausible
range and the answer says for which values it holds; anything else becomes a condition or a missing-information item.

## What is checked (for any skill)

1. **Skill file** - tolerant reader (units m/mm/cm/in, many verbs and field spellings, objects with/without size or mass). Problems are reported, never crash.
2. **Reach and path** - each step is expanded into tool waypoints (hover, grasp, lift, carry, place, retreat; push; tilt; moves).
   Closed-form UR5e inverse kinematics (all 8 branches) + a search over branches along every straight-line segment:
   joint limits (incl. **elbow +-160 deg** from the current manual), stay above the table, distance to the kinematic limit, singularities,
   no branch flips (the tool orientation is held constant on carry moves when the skill says "keep upright").
   Also reports the reachable radius band and margin around each pick/place point.
3. **Tool capability** - grasp/hold and release need an actuated tool (or a declared passive mode such as scoop). Grip opening vs object width, grip force vs weight.
4. **Payload** - manual's payload-vs-CoG-offset curve + static joint-torque budget along the path (arm link masses from UR's DH article, joint torque ratings). Gives the allowed *tool + object* mass.
5. **Vision** - camera range/FOV/pixels-on-target for DETECT/VERIFY objects; full coverage test when a camera mount is given (fixed pose or eye-in-hand).
6. **Skill-stated requirements** - force vs F/T sensor, tolerance vs repeatability (+-0.03 mm), speed vs max TCP speed, max duration vs joint-speed lower bound, object vs container size.

Not covered: task correctness, safety certification (stopping distances, risk assessment, controller safety config), collision-free planning
around unknown obstacles, perception accuracy, accelerations (UR does not publish them -> timing is a lower bound only).

## Using another robot (default: UR5e)

The built-in arm is the **UR5e** (`profiles/ur5e.yaml`, values quoted from UR's official manuals). To check against a different arm:

1. copy `profiles/robot_template.yaml` to e.g. `my_robot.yaml` and fill in every value from your robot's manual/datasheet
   (DH table, link masses, joint limits and speeds, payload curve, reach, repeatability, torque ratings, tool I/O);
2. run `python -m hardware_capability_check skill.json --robot my_robot.yaml` (path, or a bare name that lives in `profiles/`).

Everything else (reach/path search, payload, sequence checks, report) is driven by that file; nothing is hard-coded to the UR5e.
**Limit:** the inverse kinematics is closed-form for UR-style arms (6 rotary joints, spherical wrist: UR3e/5e/10e/16e/20/30 and look-alikes).
A profile with another kinematic structure is rejected with a clear error (`kinematics.family` must be `ur`); supporting it means adding a
kinematics class with the same interface (`fk`, `ik`, `jacobian`, `gravity_torques`). Tool, camera and cell profiles are separate files (`--tool`, `--camera`, `--cell`).

## Example skills

* `examples/skill_pick_place_red_cube.json` - the first, simple example.
* `examples/template_skill_v1_1.json` - **TEMPORARY template skill (schema 1.1)**: includes `robot`, `action_primitives` and `primitive_sequence`. It is the current working
  template and will be replaced when the skill format is final. The checker evaluates every field it understands and lists the rest.

Validation (accuracy tests against a MuJoCo simulation) was done during development and is intentionally not part of this repository.

## Hardware data (`profiles/`)

| file | content |
|---|---|
| `robot_template.yaml` | blank-ish template for a different robot (see above) |
| `ur5e.yaml` | UR5e: DH kinematics, link masses/CoM, joint limits/speeds/torques, payload curve, F/T sensor, tool I/O. **Every value has a verbatim quote from an official UR document** (checked by tests) |
| `tool_custom_3d_printed.yaml` | the project's gripper: 3D-printed, rigid, **not actuated**; mass/length unknown (`null`) |
| `camera_realsense_d415.yaml` | **computer-vision camera** of the execution cell (used for the verdict); mounting unknown |
| `camera_realsense_d435_demo.yaml` | camera that **recorded the demonstration** that becomes skill.json (informational) |
| `cell_setup.yaml` | robot base pose in the skill's world frame, table height, motion defaults (all marked `assumed` until confirmed) |
| `tool_example_parallel_gripper.yaml` | illustrative actuated gripper for what-if runs (placeholder numbers) |

**Two cameras:** the D415 is the computer-vision camera and the only camera the verdict uses; the D435 was used to make the skill.json.
It is still undecided whether skill.json files will be created from both cameras or from only one - nothing in the checker depends on it
(`--demo-camera none` leaves the D435 out of the report; `--camera` selects the vision camera).

Documents: `docs/SOURCES.md` lists every downloaded file (official UR manuals PolyScope 5 / 5.25.1 / X, datasheets Oct 2026 and Dec 2024,
UR DH and joint-torque articles, Intel D415 brief) with SHA-256, date, what it was used for, conflicts between documents
(notably elbow range +-160 deg vs +-360 deg) and what is *not* published. Originals: `docs/sources/`, text extracts: `docs/text/`.

## Adapting later

* another UR arm (UR3e/UR10e/...): copy `ur5e.yaml`, change DH / masses / limits / payload curve (same code);
* another tool/camera/cell: edit or add a YAML and pass `--tool/--camera/--cell`;
* another robot family: add a kinematics class with the same interface as `URKinematics` (`fk`, `ik`, `jacobian`, `gravity_torques`).

## Layout

`src/hardware_capability_check/`: `skill_model.py` (reader) - `actions.py` (verb meanings) - `motion.py` (waypoints + reach/path search) -
`engine.py` (all checks, verdict) - `kinematics.py` (UR FK/IK/Jacobian/torques) - `profiles.py` - `provenance.py` - `report.py` - `cli.py`.
`tests/`: kinematics round-trip (300 random poses), provenance of every documented number, parser, ~20 adversarial skills (out of reach, too heavy, unknown verbs, units, cameras, CLI).
