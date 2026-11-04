#!/usr/bin/env python3
"""Mirror URSim's joint positions onto the RoboDK UR5e model (one-way, read-only on the UR side).

    python scripts/mirror_ursim_to_robodk.py --once          # copy the current pose once
    python scripts/mirror_ursim_to_robodk.py                 # follow live at 10 Hz (Ctrl+C to stop)

UR side: uses only rtde_receive (no rtde_control), so nothing is sent to the
controller. Host is restricted to loopback (URSim) unless --allow-host is given.
RoboDK side: only robot.setJoints() on the simulated robot (display update, no
program, no driver connection); RoboDK run mode is checked to be simulate.
"""
from __future__ import annotations

import argparse
import math
import sys
import time

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


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
    import rtde_receive
    from robodk import robolink as rl, robomath as rm

    RDK = rl.Robolink(port=a.port)
    if RDK.RunMode() != rl.RUNMODE_SIMULATE:
        print(f"RoboDK run mode is {RDK.RunMode()}, not simulate; refusing")
        return 2
    robot = RDK.Item(a.robot, rl.ITEM_TYPE_ROBOT)
    if not robot.Valid():
        print(f"RoboDK robot {a.robot!r} not found in station {RDK.ActiveStation().Name()!r}")
        return 2
    if robot.ConnectedState()[0] == rl.ROBOTCOM_READY:
        print("RoboDK robot is connected to a real driver; refusing to mirror onto it")
        return 2

    rec = rtde_receive.RTDEReceiveInterface(a.host)
    try:
        while True:
            q = [math.degrees(x) for x in rec.getActualQ()]
            tcp = rec.getActualTCPPose()
            robot.setJoints(q)
            fk = rm.Pose_2_UR(robot.SolveFK(q))
            err = math.dist([v * 1000 for v in tcp[:3]], fk[:3])
            line = (f"joints deg {[round(v, 2) for v in q]}  UR TCP mm {[round(v * 1000, 1) for v in tcp[:3]]}"
                    f"  RoboDK flange-vs-UR TCP {err:.2f} mm")
            if a.once:
                print(line)
                return 0
            print("\r" + line, end="", flush=True)
            time.sleep(1.0 / a.hz)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        rec.disconnect()


if __name__ == "__main__":
    sys.exit(main())
