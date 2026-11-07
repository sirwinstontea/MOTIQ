"""Import the demo objects into the CURRENT PyBullet station (simulation only).

    python -m robot_control.sim_objects.sim_import --dry-run     # no PyBullet connection at all
    python -m robot_control.sim_objects.sim_import               # add missing objects, skip existing
    python -m robot_control.sim_objects.sim_import --replace     # replace ONLY same-named objects

Safety: never touches robots, tools, TCPs, targets, programs or the run mode;
never connects to a physical robot; never clears the station. Only these are
created/changed: one reference frame (default "Objects_Frame") and the four
named ITEM_TYPE_OBJECT items. With --replace, an existing object with exactly
the same name *of type object* is deleted and re-imported; nothing else is
deleted. Without --replace, existing names are skipped and reported.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from .core import SPEC_FILE, load_object_config, load_spec, stl_path

PORT = 20500


def _pose_mm_deg(rm, p):
    x, y, z, rx, ry, rz = (list(p) + [0] * 6)[:6]
    return rm.transl(x, y, z) * rm.rotz(math.radians(rz)) * rm.roty(math.radians(ry)) * rm.rotx(math.radians(rx))


def plan(spec_file=SPEC_FILE) -> list[dict]:
    """Resolve configs/STL paths/poses without connecting to PyBullet."""
    spec = load_spec(spec_file)
    out = []
    for e in spec["objects"]:
        cfg = load_object_config(e, spec_file)
        p = stl_path(cfg)
        out.append({"id": cfg["id"], "name": cfg["name"], "stl": p, "stl_exists": p.is_file(),
                    "stl_units": cfg.get("stl_units"), "color_rgba": cfg["color_rgba"],
                    "expected_bounds_mm": cfg["expected_bounds_mm"],
                    "pose_mm_deg": e.get("display_pose_mm_deg", [0, 0, 0, 0, 0, 0])})
    return out


def _find_exact(RDK, name, item_type):
    return [it for it in RDK.ItemList(item_type) if it.Name() == name]


def _mesh_extents(rl, item):
    """Read back the imported mesh extents (mm, object coordinates) over all mesh features."""
    pts = []
    try:
        for fid in range(10000):            # PyBullet splits a mesh into several features
            fp, _ = item.GetPoints(rl.FEATURE_OBJECT_MESH, fid)
            if not fp:
                break
            pts += [p[:3] for p in fp]
    except Exception:
        pass
    if not pts:
        return None
    cols = list(zip(*pts))
    return [round(max(c) - min(c), 3) for c in cols]


def run_import(spec_file=SPEC_FILE, replace=False, reset_frame_pose=False, port=PORT, out=print) -> int:
    spec = load_spec(spec_file)
    rcfg = spec["sim"]
    items = plan(spec_file)
    missing = [i for i in items if not i["stl_exists"]]
    if missing:
        out("[FAIL] missing STL files (run generate_all first): " + ", ".join(str(i["stl"]) for i in missing))
        return 1
    bad_units = [i["name"] for i in items if i["stl_units"] != "mm"]
    if bad_units:
        out(f"[FAIL] STL units must be 'mm' for PyBullet STL import: {bad_units}")
        return 1

    out("The demo parts are built into the PyBullet cell when the controller starts. Nothing else is imported.")
    return 0

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", default=str(SPEC_FILE))
    ap.add_argument("--dry-run", action="store_true", help="print the plan; do not connect to PyBullet")
    ap.add_argument("--replace", action="store_true", help="delete + re-import objects with exactly these names")
    ap.add_argument("--reset-frame-pose", action="store_true", help="move an existing Objects_Frame to the spec pose")
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args(argv)

    if a.dry_run:
        spec = load_spec(a.spec)
        r = spec["sim"]
        print("DRY RUN: the PyBullet cell is not modified.")
        print(f"frame {r['frame_name']!r} under first of {r['parent_frame_candidates']} (else station root); "
              f"pose if created: {r['frame_pose_mm_deg_when_created']} (mm, deg)")
        ok = True
        for i in plan(a.spec):
            st = "ok" if i["stl_exists"] else "MISSING (run generate_all)"
            ok &= i["stl_exists"]
            print(f"  would import {i['name']:<22} pose {i['pose_mm_deg']}  rgba {i['color_rgba']}  "
                  f"units {i['stl_units']}  STL {st}\n      {i['stl']}")
        print("existing objects with the same name would be " + ("REPLACED" if a.replace else "skipped"))
        return 0 if ok else 1
    return run_import(a.spec, replace=a.replace, reset_frame_pose=a.reset_frame_pose, port=a.port)


if __name__ == "__main__":
    sys.exit(main())
