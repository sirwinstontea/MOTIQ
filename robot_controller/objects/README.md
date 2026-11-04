# Demo objects for RoboDK (UR5e simulation)

Four watertight, colour-coded meshes for the RoboDK digital twin. All units are **millimetres**.

| Name | Shape | Bounds (mm) | RGBA |
|---|---|---|---|
| `Blue_Bucket_01` | open cup, OD 139 constant, opening 105, 8 mm floor | 139 × 139 × 115 | 0.00 0.65 0.90 1 |
| `Green_Ring_02` | flat ring, OD 139, through-hole **78**, 7 thick | 139 × 139 × 7 | 0.10 0.85 0.20 1 |
| `Orange_Screw_03` | 8 annular ribs, smooth collar, blind top cavity (**approximate**) | 30 × 30 × 90 | 1.00 0.38 0.02 1 |
| `White_Bucket_Lid_04` | flange + truncated cone, 30 mm through-hole | 139 × 139 × 53 | 0.93 0.93 0.93 1 |

User corrections are applied: the blue outer diameter is **139 constant** (not 115), and the green hole is **78** (not 88).

## Layout

```
objects/
  object_specifications.json       station-level: object list, Objects_Frame, display poses
  <id>/config/<id>.json            editable dimensions + provenance, colour, local frame
  <id>/model/<id>.stl              generated binary STL (mm)
  validation_report.json           written by validate_all
robot_control/sim_objects/
  core.py                          revolve, ray/containment tests, common mesh checks
  shapes/<id>.py                   per-object profile generator + cavity validator
  build.py, generate.py, validate.py, robodk_import.py
scripts/generate_all.py | validate_all.py | import_all.py   (thin wrappers)
```

Local frame (all objects): the origin is the centre of the lowest flat face, the symmetry axis is +Z, and z ≥ 0.
Each mesh is a solid of revolution of a closed (r, z) profile with 192 segments. Vertices lie exactly on the
nominal radius, so no diameter exceeds its specified value.

## Commands (Linux, from `robot_controller/`)

```bash
source .venv/bin/activate                      # or: python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[objects,robodk]'   # numpy, trimesh (+ robodk API for import)

python scripts/generate_all.py                 # == python -m robot_control.sim_objects.generate
python scripts/validate_all.py                 # == python -m robot_control.sim_objects.validate  (-q for summary)
python scripts/import_all.py --dry-run         # prints the plan; never imports robodk or connects
python scripts/import_all.py                   # RoboDK GUI must already be running
python scripts/import_all.py --replace         # re-import ONLY objects with these exact names
python -m pytest tests/test_sim_objects.py -q  # offline tests
```

`validate_all` checks the config, the in-memory mesh and the STL file on disk. The checks are:

* derived fields agree with the measured ones
* the mesh is watertight, its winding is consistent, and it is a volume (outward normals)
* the volume is positive and matches the analytic Pappus volume
* there are no degenerate faces
* the AABB equals the expected bounds
* the ratio to the expected size is 1 (this catches a mm/m scale mistake)
* the STL on disk matches the current config
* cavity checks run by ray casting:
  * Blue: open mouth, sealed floor at z=8, rim not capped
  * Green: clear through-hole
  * Orange: blind cavity floor at z=81, closed base, 8 ridges, radius ≤ 15
  * White: clear through-hole, flange, and the cone radius at mid-height

If the STLs don't exist, it reports `STL validated: NOT RUN`.

## Import behaviour (simulation only)

* The objects go into the **currently active station**, as `ITEM_TYPE_OBJECT` items under the
  `Objects_Frame` frame. That frame is created under `UR5e Base` if it exists, otherwise under the station root.
* Colour is set through `item.setColor()`, because STL files carry no colour.
* After import, the mesh is read back from RoboDK and its extents are compared with the expected bounds to
  confirm the mm scale.
* If a name already exists, that object is **skipped** unless you pass `--replace`. `--replace` deletes only
  objects with exactly the same name.
* An existing `Objects_Frame` is never moved unless you pass `--reset-frame-pose`.
* The importer never touches robots, tools, TCPs, targets, programs, the run mode or other items. It does not
  launch RoboDK, and it never talks to a physical robot.
* Collisions between the objects and `table` are turned off, because the objects sit on the table top.
* It does not save the station. Press Ctrl+S in RoboDK to keep the objects. Note that
  `robot_control.cli setup-station` rebuilds the demo station tab, so run `import_all.py` again afterwards.

**Display layout.** `Objects_Frame` is placed at `[-620, 0, 0, 0, 0, 90]` (mm, deg) relative to the robot base.
The objects sit at x = −240 / −70 / 80 / 240 in that frame, so in base coordinates they form a row at
X = −620, Y = −240…240, beside the demo cube and pad. This layout is for display only and does not prove there
is free space in the real cell. To change it, edit `object_specifications.json` and re-run with
`--reset-frame-pose` (frame) or `--replace` (objects).

**Inspecting in RoboDK.** Expand `UR5e Base → Objects_Frame` in the station tree. Double-click an object to see
its pose. Use *Tools → Measure* to check dimensions.

## Changing dimensions

1. Edit `value` in `objects/<id>/config/<id>.json`. If you change a measured field, also update the `derived`
   fields that depend on it, plus `expected_bounds_mm`. The validator fails and tells you what is inconsistent.
2. Run `generate_all`, then `validate_all`, then `import_all --replace`.

Every field has a `status`:

* `measured`: confirmed by the user or the drawing
* `derived`: computed from other fields
* `interpreted`: a reading of an ambiguous drawing
* `estimated`: a guess from photos

## Pending measurements and limitations

* **Orange_Screw_03 is approximate.** Only the 90 mm length and the 30 mm max diameter are measured. These are
  all estimates from photos:
  * rib count (8)
  * valley diameter (23)
  * collar length (12) and lower-end length (5)
  * lower-end diameter (26)
  * cavity opening (22) and depth (9)
  * rib flank/peak fractions

  The ribs are modelled as annular ridges with flat peaks, not as a helical thread. The model is not exact.
* **White_Bucket_Lid_04:** the drawing's 25 mm is *interpreted* as the exposed flange's radial width. The 89 mm
  cone base diameter is therefore *derived*, not measured. The hole is assumed to run all the way through.
* **Orange (Ø30) vs White hole (Ø30): zero nominal clearance.** This does not guarantee the screw can actually
  be inserted. No grip poses, jaw sizes, insertion tolerances or trajectories are defined here.
* The `references/` photos and `object_specifications.json` from the original brief were not in the repo. The
  geometry comes from the task document's numbers.
* Small holes, fillets, textures and screw holes are not modelled.
