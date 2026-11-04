"""Generate the STL files for all demo objects (millimetres).

    python -m robot_control.sim_objects.generate [--only blue_bucket_01 ...]
"""
from __future__ import annotations

import argparse
import sys

from .build import export_stl
from .core import SPEC_FILE, load_object_config, load_spec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", default=str(SPEC_FILE))
    ap.add_argument("--only", nargs="*", help="object ids to generate (default: all)")
    a = ap.parse_args(argv)
    spec = load_spec(a.spec)
    rc = 0
    for entry in spec["objects"]:
        if a.only and entry["id"] not in a.only:
            continue
        cfg = load_object_config(entry, a.spec)
        try:
            p, res = export_stl(cfg)
        except Exception as e:                       # report and continue with the others
            print(f"[FAIL] {cfg['name']}: {e}")
            rc = 1
            continue
        m = res.mesh
        print(f"[ OK ] {cfg['name']:<22} {len(m.faces):>6} faces  extents {m.extents.round(3).tolist()} mm"
              f"  volume {m.volume:,.0f} mm^3{'  (winding corrected)' if res.winding_flipped else ''}\n"
              f"       -> {p}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
