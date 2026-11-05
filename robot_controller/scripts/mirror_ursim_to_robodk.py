#!/usr/bin/env python3
"""Mirror UR joint positions onto the RoboDK UR5e model (one-way, read-only on the UR side).

    python scripts/mirror_ursim_to_robodk.py --once          # copy the current pose once
    python scripts/mirror_ursim_to_robodk.py                 # follow live at 10 Hz (Ctrl+C to stop)

Thin wrapper over robot_control.digital_twin (the full feature, with objects and the app controls, is
`python -m robot_control.digital_twin.cli ...`, see docs/DIGITAL_TWIN_SYNC.md).

UR side: rtde_receive only (no rtde_control), so nothing is sent to the controller. Host is restricted to loopback
(URSim) unless --allow-host is given. RoboDK side: only robot.setJoints() on the simulated robot, after checking
before EVERY write that RoboDK is in simulate mode, the robot item has no driver connection and no program moves it.

The printed comparison is like-for-like: RoboDK flange FK vs nominal UR5e DH flange FK of the same joints (model /
base-convention consistency). The UR's active TCP is not the flange and is not compared to it.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robot_control.digital_twin.config import LOOPBACK, RoboDK  # noqa: E402
from robot_control.digital_twin.frames import joints_rad_to_robodk_deg, pose_delta, ur5e_fk_flange  # noqa: E402
from robot_control.digital_twin.robodk_writer import GuardError, RoboDKWriter  # noqa: E402
from robot_control.digital_twin.telemetry import RTDEJointSource, TelemetryError  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="UR controller (default: local URSim)")
    ap.add_argument("--allow-host", action="store_true", help="permit a non-loopback host (read-only still)")
    ap.add_argument("--robot", default="UR5e", help="RoboDK robot item name")
    ap.add_argument("--port", type=int, default=20500, help="RoboDK API port")
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)

    if a.host not in LOOPBACK and not a.allow_host:
        print(f"refusing non-loopback host {a.host!r} (pass --allow-host to read from it)")
        return 2
    writer = RoboDKWriter(RoboDK(robot_item_name=a.robot, port=a.port))
    try:
        writer.guard()
    except GuardError as e:
        print(f"[{e.status}] {e.reason}")
        return 2
    rec = RTDEJointSource(a.host)
    print("MIRROR ONLY — NO PHYSICAL MOTION")
    try:
        while True:
            try:
                s = rec.read()
                q = joints_rad_to_robodk_deg(s.q_rad)
                T_robodk = writer.write_joints(q)
            except TelemetryError as e:
                print(f"\r[{e.status}] {e.reason}", end="", flush=True)
                if a.once:
                    print()
                    return 1
                time.sleep(1.0)
                continue
            except GuardError as e:
                print(f"\n[{e.status}] {e.reason}: stopped, nothing more written")
                return 2
            dmm, ddeg = pose_delta(ur5e_fk_flange(s.q_rad), T_robodk)
            line = (f"joints deg {[round(v, 2) for v in q]}  RoboDK flange FK vs UR5e nominal-DH flange FK "
                    f"{dmm:.2f} mm / {ddeg:.2f} deg")
            if a.once:
                print(line)
                return 0
            print("\r" + line, end="", flush=True)
            time.sleep(1.0 / a.hz)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        rec.close()


if __name__ == "__main__":
    sys.exit(main())
