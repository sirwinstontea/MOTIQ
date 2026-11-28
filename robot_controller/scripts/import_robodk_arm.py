#!/usr/bin/env python3
"""Import any RoboDK library arm into Motiq for robodk_sim (simulation only).

Given a .robot file or a library .rdk containing one robot:
  1. copy/extract -> robot_controller/stations/<Model>.robot
  2. read DOF + JointLimits() from RoboDK
  3. auto-fit the MOTIQ demo cell within reach (same probe as build_arm_stations.py)
  4. write config/scenes/<robot_id>.yaml + stations/<Model>_demo.rdk
  5. return a catalog-ready payload (limits verified for sim-only)

    cd robot_controller && .venv/bin/python scripts/import_robodk_arm.py \\
        --source ../UR3e.rdk --robot-id ur_ur3e_custom --brand "Universal Robots" --model UR3e

Needs a running RoboDK GUI (API port 20500 by default). Never enables physical motion.
"""
from __future__ import annotations

import argparse
import math
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

RC = Path(__file__).resolve().parents[1]
MOTIQ = RC.parent
sys.path.insert(0, str(RC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_arm_stations import (  # noqa: E402
    BASE_SCENE,
    Probe,
    build_station,
    extract_robot,
    find_layout,
    workspace_for,
    write_scene,
)
from robot_control.backends.robodk_sim import _import_robodk  # noqa: E402
from robot_control.config import SceneConfig  # noqa: E402

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_MODEL_SAFE = re.compile(r"[^A-Za-z0-9._\-]+")


def suggest_id(brand: str, model: str) -> str:
    raw = f"{brand} {model}".lower()
    return re.sub(r"[^a-z0-9]+", "_", raw).strip("_") or "custom_arm"


def safe_model_name(model: str) -> str:
    name = _MODEL_SAFE.sub("_", model.strip()) or "Arm"
    return name[:64]


def resolve_source(source: Path) -> Path:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source not found: {source}")
    if source.suffix.lower() not in {".robot", ".rdk"}:
        raise ValueError("source must be a .robot or .rdk file")
    return source


def import_arm(
    source: Path,
    *,
    robot_id: str,
    brand: str,
    model: str,
    library_url: str = "",
    port: int = 20500,
    select: bool = False,
    nominal_reach_m: float | None = None,
    nominal_payload_kg: float | None = None,
) -> dict[str, Any]:
    """Build station + scene and return a catalog entry dict (not yet written to catalog.yaml)."""
    if not _ID_RE.match(robot_id):
        raise ValueError("robot_id: lowercase letters, digits and _ only")
    source = resolve_source(source)
    model_name = safe_model_name(model)
    item_name = model_name
    # Never clobber bundled catalog models (e.g. UR5e.robot); namespace imports by robot_id.
    file_stem = f"imported_{robot_id}"
    robot_file = RC / "stations" / f"{file_stem}.robot"
    robot_file.parent.mkdir(parents=True, exist_ok=True)

    rl, rm = _import_robodk()
    RDK = rl.Robolink(port=port)
    RDK.setRunMode(rl.RUNMODE_SIMULATE)

    try:
        if source.suffix.lower() == ".robot":
            shutil.copy2(source, robot_file)
            # open a scratch station to read JointLimits
            keep = RDK.ActiveStation()
            st = RDK.AddStation("MOTIQ import probe limits")
            try:
                robot = RDK.AddFile(str(robot_file), st)
                lo, hi, _ = robot.JointLimits()
                home_lib = robot.Joints().list()
                lo, hi = lo.list(), hi.list()
            finally:
                RDK.setActiveStation(st)
                RDK.CloseStation()
                if keep.Valid():
                    RDK.setActiveStation(keep)
        else:
            home_lib, lo, hi = extract_robot(RDK, rl, source, robot_file)

        dof = len(lo)
        if dof < 1 or dof > 12:
            raise RuntimeError(f"unsupported DOF {dof} (need 1-12)")

        base = yaml.safe_load(BASE_SCENE.read_text(encoding="utf-8"))
        SceneConfig.model_validate(base)

        probe = Probe(RDK, rl, rm, robot_file, home_lib)
        try:
            found = find_layout(probe, base)
            home_pose = probe.fk(found[3]) if found else None
        finally:
            probe.close()

        if not found:
            raise RuntimeError(
                "could not fit the MOTIQ demo cell within this arm's reach (IK/collision probe failed). "
                "Try a longer-reach serial arm, or teach targets manually in RoboDK."
            )

        scene, dx, kz, home = found
        scene["targets"][scene["home_target"]] = {
            "joints_rad": [round(math.radians(q), 5) for q in home],
            "pose_m_rad": home_pose,
            "description": f"safe home posture (tool down above the table, solved by RoboDK IK for the {model_name})",
        }
        scene_path = RC / "config" / "scenes" / f"{robot_id}.yaml"
        write_scene(scene_path, scene, robot_id, model_name, dx, kz)
        SceneConfig.model_validate(yaml.safe_load(scene_path.read_text(encoding="utf-8")))

        # file_stem keeps demo .rdk unique; item_name is the RoboDK robot item inside the station
        station = build_station(RDK, rl, rm, robot_id, file_stem, item_name, dof, robot_file, scene_path, dx)
    except SystemExit as e:
        raise RuntimeError(str(getattr(e, "code", None) or e or "import aborted")) from e

    # conservative sim caps — not datasheet-certified
    reach = float(nominal_reach_m) if nominal_reach_m and nominal_reach_m > 0 else max(
        abs(t["pose_m_rad"][0]) for t in scene["targets"].values() if t.get("pose_m_rad")
    ) + 0.15
    payload = float(nominal_payload_kg) if nominal_payload_kg and nominal_payload_kg > 0 else 3.0
    max_j_speed = [math.pi] * dof
    ws = workspace_for(scene)

    entry = {
        "id": robot_id,
        "brand": brand.strip() or "Custom",
        "model": model_name,
        "dof": dof,
        "nominal_reach_m": round(reach, 3),
        "nominal_payload_kg": round(payload, 3),
        "library_url": library_url.strip() or f"https://robodk.com/robot",
        "robodk_item_name": item_name,
        "station_name": f"{file_stem} Controller Demo",
        "library_file": robot_file.name,
        "scene_file": f"{robot_id}.yaml",
        "default_workspace": ws,
        "limits": {
            "verified": True,
            "source": (
                "RoboDK JointLimits() + Motiq demo caps (SIMULATION ONLY). "
                "Speeds/payload/accel are conservative placeholders, not manufacturer maxima."
            ),
            "joint_min_rad": [round(math.radians(v), 4) for v in lo],
            "joint_max_rad": [round(math.radians(v), 4) for v in hi],
            "max_joint_speed_rad_s": [round(v, 4) for v in max_j_speed],
            "max_joint_accel_rad_s2": 4.0,
            "max_tcp_speed_m_s": 1.0,
            "max_tcp_accel_m_s2": 2.0,
            "max_payload_kg": round(payload, 3),
            "reach_m": round(reach, 3),
        },
        "notes": (
            f"Imported from {source.name} for robodk_sim. Demo cell shift {dx * 1000:+.0f} mm, height x{kz}. "
            "Re-teach targets before any real-world use."
        ),
    }

    meta = {
        "ok": True,
        "robot": entry,
        "station_path": str(station),
        "scene_path": str(scene_path),
        "robot_file": str(robot_file),
        "layout": {"dx_m": dx, "height_scale": kz},
        "select": select,
        "restart_required": True,
        "simulation_only": True,
    }
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True, help=".robot or library .rdk path")
    ap.add_argument("--robot-id", default="", help="catalog id (default: from brand+model)")
    ap.add_argument("--brand", default="Custom")
    ap.add_argument("--model", default="", help="display / RoboDK item name (default: from file stem)")
    ap.add_argument("--library-url", default="")
    ap.add_argument("--reach", type=float, default=0.0)
    ap.add_argument("--payload", type=float, default=0.0)
    ap.add_argument("--port", type=int, default=20500)
    ap.add_argument("--select", action="store_true", help="also select this arm in robot-chooser")
    ap.add_argument("--json", action="store_true", help="print result JSON")
    args = ap.parse_args(argv)

    source = Path(args.source)
    model = args.model.strip() or source.stem
    robot_id = args.robot_id.strip() or suggest_id(args.brand, model)
    try:
        meta = import_arm(
            source,
            robot_id=robot_id,
            brand=args.brand,
            model=model,
            library_url=args.library_url,
            port=args.port,
            select=args.select,
            nominal_reach_m=args.reach or None,
            nominal_payload_kg=args.payload or None,
        )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Register the already-built entry into robot-chooser (heavy RoboDK work already done above).
    sys.path.insert(0, str(MOTIQ / "robot-chooser"))
    from robot_chooser.core import Catalog, Chooser, Hardware, RobotEntry

    ch = Chooser()
    entry = meta["robot"]
    cat = ch.load_catalog()
    new_r = RobotEntry.model_validate({"robodk_item_name": entry.get("robodk_item_name") or entry["model"], **entry})
    if cat.get(entry["id"]) is None:
        new_cat = Catalog.model_validate({"schema": cat.schema_, "robots": [*cat.robots, new_r]})
    else:
        new_cat = Catalog.model_validate({
            "schema": cat.schema_,
            "robots": [new_r if x.id == entry["id"] else x for x in cat.robots],
        })
    ch._save_catalog(new_cat)
    hw = ch.load_hardware()
    if args.select:
        hw = Hardware.model_validate({**hw.model_dump(mode="json"),
                                      "active_robot": entry["id"], "backend": "robodk_sim"})
    state = ch._commit(new_cat, hw)
    meta["chooser_status"] = state.get("report", {}).get("status")
    meta["robots"] = state.get("catalog")

    if args.json:
        import json
        print(json.dumps(meta, indent=2))
    else:
        print(f"imported {meta['robot']['id']} -> {meta['station_path']}")
        print(f"scene {meta['scene_path']}")
        if meta.get("chooser_status"):
            print(f"chooser {meta['chooser_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
