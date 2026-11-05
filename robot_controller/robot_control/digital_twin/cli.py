"""Digital twin CLI (run from robot_controller/). MIRROR ONLY — NO PHYSICAL MOTION.

    python -m robot_control.digital_twin.cli check    --config config/digital_twin.sim.json
    python -m robot_control.digital_twin.cli snapshot --config config/digital_twin.sim.json [--robot-only]
    python -m robot_control.digital_twin.cli run      --config config/digital_twin.sim.json [--robot-only]
    python -m robot_control.digital_twin.cli serve    --config config/digital_twin.local.json

`run` mirrors live in the foreground (Ctrl+C stops mirroring; nothing is sent to the robot).
`serve` starts the local API on 127.0.0.1 for the bridge / app (Skills -> Sync Digital Twin).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

from .config import load_config
from .service import LABEL, TwinService


def _print(d):
    print(json.dumps(d, indent=2, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "snapshot", "run", "serve"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--robot-only", action="store_true", help="explicit partial mode: joints only, no objects")
    ap.add_argument("--port", type=int, default=None, help="serve: override service.port")
    a = ap.parse_args(argv)

    try:
        cfg = load_config(a.config)
    except Exception as e:  # noqa: BLE001 - show a readable config error
        print(f"[config] {a.config}: {e}", file=sys.stderr)
        return 2
    print(f"*** {LABEL} *** target={cfg.target} telemetry={cfg.physical_robot.telemetry} "
          f"vision={cfg.vision.source}", file=sys.stderr)
    key = os.environ.get(cfg.vision.api_key_env, "")
    svc = TwinService(cfg, api_key=key)
    objects = not a.robot_only

    if a.command == "check":
        res = svc.check()
        _print(res)
        svc.joints.close()
        return 0 if res["ok"] else 1
    if a.command == "snapshot":
        res = svc.snapshot(objects)
        _print(res)
        svc.joints.close()
        return 0 if res.get("ok") else 1
    if a.command == "run":
        res = svc.start(objects)
        if not res.get("ok"):
            _print(res)
            return 1
        stop = {"flag": False}
        signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
        signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
        try:
            while not stop["flag"] and svc.state in ("RUNNING", "PAUSED_STALE"):
                st = svc.status()
                r, o = st["robot"], st["objects"]
                print(f"\r{st['state']:<13} {r['hz']:5.1f} Hz  q_deg {r['q_deg']}  objects upd {o['updated']} "
                      f"stale {o['stale']} skip {o['skipped']}   ", end="", flush=True)
                time.sleep(0.5)
        finally:
            print()
            svc.close()
            _print({"state": svc.state, "last_error": svc.last_error, "robot_command_sent": False})
        return 0 if svc.state != "ERROR" else 1

    import uvicorn

    from .api import create_app
    app = create_app(svc)
    port = a.port or cfg.service["port"]
    if cfg.sync.auto_start:
        print(f"[digital twin] auto_start: {svc.start(objects=not svc.objects_blockers())}", file=sys.stderr)
    print(f"[digital twin] http://{cfg.service['host']}:{port}/digital-twin/status", file=sys.stderr)
    uvicorn.run(app, host=cfg.service["host"], port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
