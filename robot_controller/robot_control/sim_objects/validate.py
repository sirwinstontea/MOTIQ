"""Validate all demo objects (config consistency, generated mesh, STL on disk, cavities).

    python -m robot_control.sim_objects.validate [--json objects/validation_report.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .build import validate_object
from .core import OBJECTS_DIR, SPEC_FILE, fmt_bool, load_object_config, load_spec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", default=str(SPEC_FILE))
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-stl", action="store_true", help="skip validating the STL files on disk")
    ap.add_argument("--json", default=str(OBJECTS_DIR / "validation_report.json"), help="write report ('' = don't)")
    ap.add_argument("-q", "--quiet", action="store_true", help="only print the summary")
    a = ap.parse_args(argv)
    spec = load_spec(a.spec)
    reports = []
    for entry in spec["objects"]:
        if a.only and entry["id"] not in a.only:
            continue
        cfg = load_object_config(entry, a.spec)
        try:
            rep = validate_object(cfg, check_stl=not a.no_stl)
        except Exception as e:
            from .core import ObjectReport
            rep = ObjectReport(cfg["id"], cfg["name"])
            rep.add("validation ran", False, f"{type(e).__name__}: {e}")
        reports.append(rep)
        if not a.quiet:
            print(f"\n=== {rep.name}  [{fmt_bool(rep.passed)}]")
            for c in rep.checks:
                print(f"  [{fmt_bool(c.passed)}] {c.name}" + (f"  -- {c.detail}" if c.detail else ""))
            if rep.stats:
                print(f"  stats: {json.dumps({k: v for k, v in rep.stats.items() if k != 'stl_file'})}")

    print("\nSUMMARY")
    for r in reports:
        n_ok = sum(c.passed for c in r.checks)
        print(f"  {r.name:<22} {fmt_bool(r.passed)}  {n_ok}/{len(r.checks)} checks"
              f"  bounds {r.stats.get('bounds_mm', 'n/a')}  STL validated: {r.stats.get('stl_validated', 'NOT RUN')}")
    if a.json:
        with open(a.json, "w") as f:
            json.dump([{"id": r.object_id, "name": r.name, "passed": r.passed, "stats": r.stats,
                        "checks": [asdict(c) for c in r.checks]} for r in reports], f, indent=2)
        print(f"  report -> {a.json}")
    return 0 if reports and all(r.passed for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
