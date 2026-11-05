"""Reload the demo station in the running RoboDK, so every object is back at its saved start pose.

Used by start_stack.sh in SIM=1 mode BEFORE the controller connects: picked/placed/pushed objects otherwise stay
where the last run left them (RoboDK keeps running between stack restarts), and the taught paths then collide.
Scene targets that are not in the .rdk file are re-added by the controller on connect (_add_missing_targets).

    python scripts/reset_station.py [--port 20500] [--station stations/UR5e_demo.rdk]

Only touches RoboDK (simulation). Never run it while a controller is connected to that RoboDK.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=20500)
    ap.add_argument("--station", default="stations/UR5e_demo.rdk")
    args = ap.parse_args()
    path = (HERE / args.station).resolve()   # absolute: RoboDK-Start.sh cds away, relative paths open nothing
    if not path.is_file():
        print(f"[reset_station] station file not found: {path}", file=sys.stderr)
        return 1
    sys.path.append(os.path.expanduser("~/RoboDK/Python"))
    from robodk import robolink

    rdk = robolink.Robolink(port=args.port)
    old = [s for s in rdk.ItemList(robolink.ITEM_TYPE_STATION)]
    new = rdk.AddFile(str(path))
    if not new.Valid():
        print(f"[reset_station] RoboDK could not load {path}", file=sys.stderr)
        return 1
    # close the previous copies only after the fresh one loaded, so a failed load never leaves RoboDK empty
    for s in old:
        if s.Valid() and s != new:
            rdk.CloseStation() if s == rdk.ActiveStation() else s.Delete()
    rdk.setActiveStation(new)
    print(f"[reset_station] reloaded {path.name}: {len(rdk.ItemList())} items, objects back at their start poses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
