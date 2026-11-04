"""Command line: ``python -m hardware_capability_check skill.json``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .engine import check_skill
from .profiles import ProfileError, load_profiles
from .provenance import verify_all
from .skill_model import load_skill

# 0 = YES (executable, possibly with listed conditions), 3 = NO (blocker, or required information missing)
EXIT_CODES = {"yes": 0, "no": 3}


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="hardware-capability-check",
        description="Check whether the robot work cell (default: UR5e; see --robot) can physically execute a skill.json.",
    )
    ap.add_argument("skill", nargs="?", help="path to the skill .json file")
    ap.add_argument("--profiles", help="directory with the profile YAML files (default: ./profiles)")
    ap.add_argument("--robot", default="ur5e.yaml", help="robot profile .yaml (default: the built-in UR5e). Another arm: copy profiles/robot_template.yaml, fill it in, pass its path")
    ap.add_argument("--tool", default="tool_custom_3d_printed.yaml")
    # Two cameras: --camera = computer-vision camera of the execution cell (D415, used for the verdict);
    # --demo-camera = camera that recorded the demonstration (D435, informational). Whether skill.json is created
    # from both cameras or only one is still undecided; use --demo-camera none to leave it out of the report.
    ap.add_argument("--camera", default="camera_realsense_d415.yaml", help="computer-vision camera profile (execution cell)")
    ap.add_argument("--demo-camera", default="camera_realsense_d435_demo.yaml", help="demonstration camera profile, or 'none'")
    ap.add_argument("--cell", default="cell_setup.yaml")
    ap.add_argument("--json", metavar="FILE", help="also write the full report as JSON")
    ap.add_argument("--quiet", action="store_true", help="print only the verdict line")
    ap.add_argument("--verify-profiles", action="store_true",
                    help="check every documented value against the downloaded official documents, then exit")
    args = ap.parse_args(argv)

    if args.verify_profiles:
        results = verify_all(Path(args.profiles) if args.profiles else None)
        bad = [r for r in results if not r.ok]
        for r in results:
            mark = "ok " if r.ok else "BAD"
            page = f" (pdf page {r.page})" if r.page else ""
            print(f"[{mark}] {r.profile}:{r.path} <- {r.doc}{page}: {r.detail}")
        print(f"\n{len(results) - len(bad)}/{len(results)} documented values verified against the source documents")
        return 1 if bad else 0

    if not args.skill:
        ap.error("a skill .json file is required (or use --verify-profiles)")
    try:
        profiles = load_profiles(
            args.profiles, robot=args.robot, tool=args.tool, camera=args.camera, cell=args.cell,
            demo_camera=None if args.demo_camera.lower() == "none" else args.demo_camera,
        )
    except ProfileError as exc:
        print(f"profile error: {exc}", file=sys.stderr)
        return 4
    skill = load_skill(args.skill)
    report = check_skill(skill, profiles)
    if args.quiet:
        print(f"{report.verdict.value}: {report.summary.splitlines()[0]}")
    else:
        print(report.to_text())
    if args.json:
        Path(args.json).write_text(report.to_json(), encoding="utf-8")
    return EXIT_CODES[report.verdict.value]


if __name__ == "__main__":
    raise SystemExit(main())
