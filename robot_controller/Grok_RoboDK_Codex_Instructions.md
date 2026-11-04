# CODEX TASK — Create 4 simulation objects in RoboDK (Linux)

You are working in an **existing Grok robotics repository** with **RoboDK running on Linux** and a **simulated Universal Robots UR5e**. Implement the four visually distinctive 3D objects described below, using the supplied photographs and sketches in `references/`.

**IMPORTANT: This is an implementation task.** Generate actual mesh files, reusable Python source code, configuration, validation checks, and a safe import workflow, not merely a conceptual explanation. Inspect the repository before creating or changing files. Reuse existing objects/import utilities if present. Do not break unrelated project code.

**Source of truth:** `object_specifications.json` and this task document. The user explicitly corrected the blue body's outside diameter to **139 mm constant**, and the green inner diameter to **78 mm**. Never use the old blue body diameter (115 mm) or old green hole (88 mm).

## Desired deliverables

1. For every object create a watertight `.stl` file, a JSON config, a Python geometry generator and a Python geometry validator.
2. Implement a shared RoboDK importer or thin per-object wrappers. Import all four into the current station as **four separate `ITEM_TYPE_OBJECT` objects**, parented to a reference frame such as `Objects_Frame`.
3. Set each model's visual color through RoboDK API (STL does not reliably preserve colors).
4. Include an `import_all.py` script with a `--dry-run` or equivalent option that does not connect to RoboDK or modify the station, and a normal import mode that safely detects duplicate names.
5. Add a concise README with Linux Python environment setup, model generation, validation and import commands. Use `python -m` commands, a project venv or the repo's established package manager as appropriate.
6. Validation must include mesh watertightness, consistently oriented normals, positive volume, expected bounds, correct opening/cavity arrangement, and no millimetre-to-metre import scale mistake.
7. Do not connect to a physical UR robot, perform any robot movement, generate unsafe URScript motion, replace a real gripper tool, clear the station or delete other station items.
8. Store all dimension fields with **millimetre units**; record which are measured, derived, interpreted or estimated. Keep estimated parameters easy to change.

## Coordinate-system convention

Use the same convention for every mesh:
- Object local origin = centre of its lowest flat face, at `(x,y,z)=(0,0,0)` mm.
- Symmetry axis is local `+Z`.
- Mesh geometry has `z >= 0` and the top surface at the specified overall height.
- Objects are independent. The importer sets each object's pose relative to `Objects_Frame` and must not alter the robot base, TCP, tools, or existing programs.
- For an initial **display-only layout**, suggest non-overlapping translations in `Objects_Frame`, such as Blue at `(-240,0,0)`, Green at `(-70,0,0)`, Orange at `(80,0,0)`, and White at `(240,0,0)` mm. Make positions configurable; do not assume the real station has free space there.

## Object 01: Blue_Bucket_01

Read reference sketches/photos in `references/blue_bucket/`.

**Confirmed geometry (mm):**
- Outer diameter = **139**, **constant along full height** (no projecting flange/rim).
- Inner opening diameter = **105**.
- Total height = **115**.
- Bottom material thickness = **8**.
- Derived radial wall thickness = `(139-105)/2 = 17`.
- Derived inner depth = `115-8 = 107`.
- Open top at `z=115`, sealed flat bottom exterior at `z=0`; inner floor at `z=8`.
- Bright cyan-blue `RGBA=[0.0,0.65,0.90,1.0]`.
- Ignore all small holes, cosmetic fillets and minor surface features.

Suggested closed `(radius,z)` profile (mm), suitable for a solid of revolution:

```python
[(0,0), (69.5,0), (69.5,115), (52.5,115), (52.5,8), (0,8), (0,0)]
```

The opening must be geometrically open; the cup should not be accidentally capped at the top. `Blue_Bucket_01` bounding dimensions: **139 x 139 x 115 mm**.

## Object 02: Green_Ring_02

Read `references/green_ring/`.

**Confirmed geometry (mm):**
- Outer diameter = **139**.
- Central **through-hole diameter = 78** (the user corrected this from 88).
- Thickness = **7**.
- Derived radial width = `(139-78)/2 = 30.5`.
- Flat annular disk, bottom face `z=0`, top face `z=7`.
- Bright green `RGBA=[0.10,0.85,0.20,1.0]`.
- Ignore small screw holes and backside pattern/texture.

Suggested closed `(radius,z)` profile (mm):

```python
[(39,0), (69.5,0), (69.5,7), (39,7), (39,0)]
```

The centre must stay open all the way through. Bounding dimensions: **139 x 139 x 7 mm**.

## Object 03: Orange_Screw_03

Read `references/orange_screw/`.

**Confirmed (mm):**
- Total axial length = **90**.
- Maximum outside diameter = **30**.
- Vivid orange `RGBA=[1.0,0.38,0.02,1.0]`.

**The following are approximations estimated from photos — NOT measured; keep each as editable JSON config:**
- Around **8 circumferential ribs**.
- Minimum valley diameter around **23 mm** (valley radius 11.5).
- Smooth upper collar length around **12 mm**.
- Smooth lower end length around **5 mm**.
- Upper cavity opening diameter around **22 mm**.
- Blind cavity depth around **9 mm**.
- Bottom initially modeled as closed.

Use a single **solid of revolution** with repeated annular ridges — **NOT a helical screw thread** unless later measurements require it. Outer `radius <= 15 mm` everywhere; ribs should be clearly visible, with sloping sides and softly flat/rounded peaks where practicable. Build a smooth collar at the top around the blind hole. Ensure total height remains 90 mm. Profile should form a top blind cup-shaped cavity with visible mouth, no hole extending through the base by default. Make all uncertain details configurable. Bounding dimensions: **30 x 30 x 90 mm**.

Do not claim that the unmeasured upper cavity or rib shape is exact. Label it as approximate in the README and JSON.

## Object 04: White_Bucket_Lid_04

Read `references/white_lid/`.

**Confirmed/recorded drawing dimensions (mm):**
- Flat circular base flange outer diameter = **139**.
- Base flange thickness = **8**.
- Total height, including cone = **53**.
- Top outside diameter of cone frustum = **50**.
- Centre hole diameter = **30**.
- White `RGBA=[0.93,0.93,0.93,1.0]`.

**Interpretation / derived geometry:**
- Drawing's **25 mm** dimension is interpreted as the exposed flange's **radial width** beyond the bottom of the cone. Confirm this interpretation if a later measurement contradicts it.
- Consequently, cone lower outside diameter = `139 - 2*25 = 89` mm (**derived, not separately measured**).
- Cone height above flange = `53 - 8 = 45` mm.
- Centre hole is assumed to run through the whole object from z=0 to z=53, consistent with photos.
- Ignore all little mounting holes and surface texture for this first model.

Suggested closed `(radius,z)` profile (mm):

```python
[(15,0), (69.5,0), (69.5,8), (44.5,8), (25,53), (15,53), (15,0)]
```

This generates a flat flange, sloping truncated-cone exterior, annular top face and an unblocked central through-hole. Bounding dimensions: **139 x 139 x 53 mm**.

## Robust implementation guidance

- Use Python + `numpy` + `trimesh` (and other dependencies only if truly needed). Prefer a **revolved closed 2D radial profile** with 128–256 angular segments to avoid hard-to-maintain CAD Boolean operations. Validate `trimesh.creation.revolve` profile expectations and mesh orientation instead of assuming every provided sample profile is automatically oriented correctly.
- Avoid approximated cylinder diameters drifting beyond the requested maximum. Use parameter-driven profiles.
- For all meshes, check `mesh.is_watertight`, `mesh.is_winding_consistent`, `mesh.volume > 0`, and AABB bounds with a small float tolerance. Also implement explicit cavity checks: blue open mouth / sealed bottom, green through-hole, orange blind top opening, white through-hole. Don't silently auto-fill holes during repair.
- If mesh winding is inverted, correct it **without changing dimensions** and re-run tests.
- Export **STL in millimetres** and write units to JSON (STL has no guaranteed unit metadata).
- Import using the RoboDK Python API; for example `from robodk.robolink import Robolink, ITEM_TYPE_OBJECT, ITEM_TYPE_FRAME` and `from robodk.robomath import transl`. Reuse the correct station / frame and `RDK.AddFile(absolute_path, parent_frame)`. Apply `item.setColor(rgba)` to the resulting object.
- Make import idempotent. If same object name is found, update/replace **only that named object** if the user explicitly opts in (e.g. `--replace`). Otherwise skip and report; never delete unrelated items.
- Perform dry-run tests without RoboDK and optional integration tests only when RoboDK is running. Do **not** automatically launch or command a physical robot.
- Output a validation summary for each of the four objects and make clear when a validation has not been run.
- The orange maximum external diameter (30 mm) equals the white object's nominal bore (30 mm): **zero nominal clearance**, not a guaranteed feasible real insertion. Do not invent grip poses, gripper jaw sizes, insertion tolerances or collision-free trajectories.

## Expected project folder tree (adapt to existing repo if needed)

```text
objects/
  blue_bucket_01/
    model/blue_bucket_01.stl
    config/blue_bucket_01.json
  green_ring_02/
    model/green_ring_02.stl
    config/green_ring_02.json
  orange_screw_03/
    model/orange_screw_03.stl
    config/orange_screw_03.json
  white_bucket_lid_04/
    model/white_bucket_lid_04.stl
    config/white_bucket_lid_04.json
scripts/
  generate_all.py
  validate_all.py
  import_all.py
README.md
```

You may create shared utilities and per-object Python generators if it improves maintainability. Avoid unnecessary duplication. Read the existing project before deciding locations.

## Definition of done

1. `generate_all` creates four named STL assets reproducibly.
2. `validate_all` prints validated bounds and cavity/opening checks for all four models.
3. Each object has confirmed/derived/estimated metadata, including its local frame, in JSON.
4. Running import mode with RoboDK shows all **four separate, colored objects** without moving the UR5e or altering unrelated station content.
5. Re-running import does not create duplicates.
6. README explains exact Linux commands, how to update dimensions, how to perform a dry run, and how to inspect the imported models.
7. If RoboDK is absent, geometry creation/validation still succeeds and RoboDK import gracefully reports the missing connection.

**Complete and test the code in this turn.** Show created file paths, test results and any assumptions/limitations still pending measurement.
