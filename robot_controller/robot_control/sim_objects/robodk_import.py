"""Import the demo objects into the CURRENT RoboDK station (simulation only).

    python -m robot_control.sim_objects.robodk_import --dry-run     # no RoboDK connection at all
    python -m robot_control.sim_objects.robodk_import               # add missing objects, skip existing
    python -m robot_control.sim_objects.robodk_import --replace     # replace ONLY same-named objects

Safety: never touches robots, tools, TCPs, targets, programs or the run mode;
never connects to a physical robot; never clears the station. Only these are
created/changed: one reference frame (default "Objects_Frame"), configured object collision pairs, and the four
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


def _configure_object_collisions(RDK, rl, obj, allowed, protected):
    """Only object pairs; never disable an explicitly protected support surface."""
    ok = True
    for other in allowed:
        if other not in protected:
            ok = bool(RDK.setCollisionActivePair(rl.COLLISION_OFF, obj, other)) and ok
    for other in protected:
        ok = bool(RDK.setCollisionActivePair(rl.COLLISION_ON, obj, other)) and ok
    return ok


def _pose_mm_deg(rm, p):
    x, y, z, rx, ry, rz = (list(p) + [0] * 6)[:6]
    return rm.transl(x, y, z) * rm.rotz(math.radians(rz)) * rm.roty(math.radians(ry)) * rm.rotx(math.radians(rx))


def plan(spec_file=SPEC_FILE) -> list[dict]:
    """Resolve configs/STL paths/poses without importing robodk."""
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


def _mesh_extents_in_robodk(rl, item):
    """Read back the imported mesh extents (mm, object coordinates) over all mesh features."""
    pts = []
    try:
        for fid in range(10000):            # RoboDK splits a mesh into several features
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
    rcfg = spec["robodk"]
    items = plan(spec_file)
    missing = [i for i in items if not i["stl_exists"]]
    if missing:
        out("[FAIL] missing STL files (run generate_all first): " + ", ".join(str(i["stl"]) for i in missing))
        return 1
    bad_units = [i["name"] for i in items if i["stl_units"] != "mm"]
    if bad_units:
        out(f"[FAIL] STL units must be 'mm' for RoboDK STL import: {bad_units}")
        return 1

    # Connect only to an already-running RoboDK; never auto-launch.
    from ..backends.robodk_sim import _import_robodk, _port_open
    from ..models import RobotControlError
    try:
        rl, rm = _import_robodk()
    except RobotControlError as e:
        out(f"[NOT CONNECTED] {e}")
        return 2
    if not _port_open("127.0.0.1", port):
        out(f"[NOT CONNECTED] RoboDK is not running on 127.0.0.1:{port}. Start it (~/RoboDK/RoboDK-Start.sh) "
            f"and re-run. Geometry generation/validation do not need RoboDK.")
        return 2
    try:
        RDK = rl.Robolink(port=port)
    except Exception as e:
        out(f"[NOT CONNECTED] cannot connect to RoboDK: {e}")
        return 2

    station = RDK.ActiveStation()
    out(f"RoboDK station: {station.Name()!r}")

    # Parent: first existing candidate frame (e.g. robot base), else the station root.
    parent = station
    for cand in rcfg.get("parent_frame_candidates", []):
        found = _find_exact(RDK, cand, rl.ITEM_TYPE_FRAME)
        if found:
            parent = found[0]
            break
    frames = _find_exact(RDK, rcfg["frame_name"], rl.ITEM_TYPE_FRAME)
    if frames:
        frame = frames[0]
        if len(frames) > 1:
            out(f"[WARN] {len(frames)} frames named {rcfg['frame_name']!r}; using the first")
        if reset_frame_pose:
            frame.setPose(_pose_mm_deg(rm, rcfg["frame_pose_mm_deg_when_created"]))
            out(f"frame {rcfg['frame_name']!r}: pose reset to {rcfg['frame_pose_mm_deg_when_created']}")
        else:
            out(f"frame {rcfg['frame_name']!r}: exists, pose left unchanged")
    else:
        frame = RDK.AddFrame(rcfg["frame_name"], parent)
        frame.setPose(_pose_mm_deg(rm, rcfg["frame_pose_mm_deg_when_created"]))
        frame.setVisible(True, False)  # hide the frame's XYZ axes
        out(f"frame {rcfg['frame_name']!r}: created under {parent.Name()!r} at "
            f"{rcfg['frame_pose_mm_deg_when_created']} (mm, deg)")

    no_coll = [it for n in rcfg.get("no_collision_with", []) for it in RDK.ItemList(rl.ITEM_TYPE_OBJECT)
               if it.Name() == n]
    protected = [it for n in rcfg.get("protected_collision_items", []) for it in RDK.ItemList(rl.ITEM_TYPE_OBJECT)
                 if it.Name() == n]
    if len(protected) != len(rcfg.get("protected_collision_items", [])):
        out('[FAIL] protected collision geometry missing or ambiguous; load the table before importing objects')
        return 2

    rc = 0
    for i in items:
        existing = _find_exact(RDK, i["name"], rl.ITEM_TYPE_OBJECT)
        if existing and not replace:
            for obj in existing:
                if not _configure_object_collisions(RDK, rl, obj, no_coll, protected):
                    out(f"[FAIL] could not protect collision pairs for {i['name']}")
                    rc = 1
            out(f"[SKIP] {i['name']}: already in station (use --replace to re-import this object only)")
            continue
        for it in existing:                      # only exact-name objects
            it.Delete()
        obj = RDK.AddFile(str(i["stl"]), frame)
        if not obj.Valid() or obj.Type() != rl.ITEM_TYPE_OBJECT:
            out(f"[FAIL] {i['name']}: AddFile did not create an object")
            rc = 1
            continue
        obj.setName(i["name"])
        obj.setParent(frame)
        obj.setPose(_pose_mm_deg(rm, i["pose_mm_deg"]))
        obj.setColor(list(i["color_rgba"]))
        if not _configure_object_collisions(RDK, rl, obj, no_coll, protected):
            out(f"[FAIL] could not protect collision pairs for {i['name']}")
            rc = 1
        ext = _mesh_extents_in_robodk(rl, obj)
        if ext is None:
            scale = "  (mesh extents could not be read back; scale check NOT RUN)"
        elif all(abs(a - b) < 0.01 for a, b in zip(ext, i["expected_bounds_mm"])):
            scale = f"  RoboDK extents {ext} mm = expected (scale OK)"
        else:
            scale = f"  RoboDK extents {ext} mm != expected {i['expected_bounds_mm']} (SCALE MISMATCH)"
            rc = 1
        out(f"[{'REPLACED' if existing else 'ADDED'}] {i['name']} at {i['pose_mm_deg'][:3]} mm in "
            f"{rcfg['frame_name']!r}, color {i['color_rgba']}{scale}")
    RDK.Render(True)
    out("Done. No robot, tool, target or program was modified. Save the station in RoboDK (Ctrl+S) to keep it.")
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", default=str(SPEC_FILE))
    ap.add_argument("--dry-run", action="store_true", help="print the plan; do not import robodk or connect")
    ap.add_argument("--replace", action="store_true", help="delete + re-import objects with exactly these names")
    ap.add_argument("--reset-frame-pose", action="store_true", help="move an existing Objects_Frame to the spec pose")
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args(argv)

    if a.dry_run:
        spec = load_spec(a.spec)
        r = spec["robodk"]
        print("DRY RUN: RoboDK is not contacted and the station is not modified.")
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
