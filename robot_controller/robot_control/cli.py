"""Linux CLI.

  python -m robot_control.cli demo --backend mock --task examples/pick_shake_place.json
  python -m robot_control.cli setup-station --config config/robot.yaml     # RoboDK demo station
  python -m robot_control.cli demo --backend robodk_sim --task examples/pick_shake_place.json --config config/robot.yaml
  python -m robot_control.cli demo --backend ursim_sync --task examples/objects/all_objects_pick_place.json   # URSim + RoboDK in sync
  python -m robot_control.cli validate --task examples/invalid_action.json
  python -m robot_control.cli tools          # AI tool list (JSON)
  python -m robot_control.cli serve          # local FastAPI on 127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from pathlib import Path

from .action_registry import tool_specs, validate_task
from .backends import create_controller
from .config import SafetyConfig, load_robot_config, load_safety_config, load_scene_config
from .models import ErrorCode, RobotControlError
from .scene_registry import SceneRegistry
from .task_executor import TaskExecutor
from .vision import MockDetector, NoDetector, VisionEyesFileDetector


def _default_config(name: str) -> str:
    p = Path("config") / f"{name}.yaml"
    return str(p if p.is_file() else Path("config") / f"{name}.example.yaml")


def _add_common(ap):
    ap.add_argument("--config", default=None, help="robot config (default config/robot.yaml or robot.example.yaml)")
    ap.add_argument("--scene", default=None)
    ap.add_argument("--safety", default=None)
    ap.add_argument("--backend", choices=["mock", "robodk_sim", "ursim_sync", "physical_disabled"], default=None)
    ap.add_argument("--detector", choices=["mock", "none", "vision_file"], default="mock")
    ap.add_argument("--vision-file", default=None, help="MOTIQ vision JSON (FramePayload or logs/selection.json)")
    ap.add_argument("--start-robodk", action="store_true", help="launch RoboDK if it is not running")
    ap.add_argument("--speed-percent", type=float, default=None,
                    help="override: speed/accel as %% of the robot model's maximums (0 < p <= 100)")


def build_executor(args, on_event=None, on_step=None) -> TaskExecutor:
    robot = load_robot_config(args.config or _default_config("robot"))
    scene_cfg = load_scene_config(args.scene or _default_config("scene"))
    safety = load_safety_config(args.safety or _default_config("safety"))
    if getattr(args, "speed_percent", None) is not None:
        try:
            safety = SafetyConfig.model_validate({**safety.model_dump(), "speed_percent_of_model_max": args.speed_percent,
                                                  "tcp_speed_m_s": None, "tcp_accel_m_s2": None,
                                                  "joint_speed_rad_s": None, "joint_accel_rad_s2": None})
        except ValueError as e:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, f"invalid --speed-percent: {e}") from e
    backend = args.backend or robot.backend
    kwargs = {"start_robodk": args.start_robodk} if backend in ("robodk_sim", "ursim_sync") else {}
    controller = create_controller(robot, scene_cfg, backend, **kwargs)
    if args.detector == "mock":
        detector = MockDetector()
    elif args.detector == "vision_file":
        if not args.vision_file:
            raise RobotControlError(ErrorCode.CONFIG_ERROR, "--vision-file is required with --detector vision_file")
        detector = VisionEyesFileDetector(args.vision_file)
    else:
        detector = NoDetector()
    return TaskExecutor(controller, robot, safety, SceneRegistry(scene_cfg), detector, on_event=on_event,
                        on_step=on_step)


def _print_event(ev):
    d = ev.model_dump(mode="json", exclude_none=True)
    if d["status"] == "INFO" and str(d.get("message", "")).startswith("segment"):
        print(f"    . {d['message']}  [{(d.get('safety_checks') or {}).get('reasons', [''])[-1][:90]}]", flush=True)
        return
    fb = d.get("feedback", {})
    tcp = fb.get("tcp_pose")
    line = f"[{d['timestamp_utc']}] {d['state']:<15} {d['status']:<9} {d.get('action') or '-':<16}"
    if tcp:
        line += f" tcp=({tcp['x']:+.3f},{tcp['y']:+.3f},{tcp['z']:+.3f}) m"
    if fb.get("holding_object_id"):
        line += f" holding={fb['holding_object_id']}"
    if d.get("message"):
        line += f" | {d['message']}"
    if d.get("error"):
        line += f" | ERROR {d['error']['code']}: {d['error']['message']}"
    print(line, flush=True)


def cmd_demo(args) -> int:
    from .live import LiveView, Telemetry
    task = json.loads(Path(args.task).read_text(encoding="utf-8"))
    view = LiveView(live_file=args.live_file)

    def on_event(ev):                      # step lines replace the per-segment event lines
        if ev.status in ("SUCCEEDED", "FAILED", "REJECTED", "CANCELLED") and ev.action:
            sv = getattr(ev.status, 'value', ev.status)
            msg = f"  {'✓' if sv == 'SUCCEEDED' else '✗'} {ev.action} {sv}"
            if ev.result and ev.result.get("object_state"):
                msg += f" -> {ev.result['object_state']}"
            if ev.error:
                msg += f" | {ev.error.code.value}: {ev.error.message}"
            view.log(msg)

    ex = build_executor(args, on_event=on_event, on_step=view.on_step)
    ex.start()
    ml = ex.motion_limits
    view.log(f"motion limits: {ml.source} -> TCP {ml.tcp_speed_m_s:.3f} m/s, {ml.tcp_accel_m_s2:.2f} m/s^2; "
             f"joints {ml.joint_speed_rad_s:.3f} rad/s ({math.degrees(ml.joint_speed_rad_s):.0f} deg/s), "
             f"{ml.joint_accel_rad_s2:.2f} rad/s^2")
    view.log(f"backend={ex.c.backend_name} model={ex.robot_cfg.model} detector={ex.detector.name}"
             + ("  (MOCK detector: demo only, not real vision)" if ex.detector.name == "mock" else ""))
    if args.live_file:
        view.log(f"live status file: {args.live_file}")
    if args.dashboard:
        _start_dashboard(ex, args.dashboard_port)
        view.log(f"dashboard: http://127.0.0.1:{args.dashboard_port}/  (Ctrl+C to quit after the run)")
    tele = Telemetry(ex.live_status, view.on_telemetry, period=0.25).start()
    try:
        status = ex.submit(task, wait=True)
    finally:
        tele.stop()
        view.write_file(ex.live_status())
        view.finish()
    out = status.model_dump(mode="json", exclude={"events", "plan"})
    print(json.dumps(out, indent=2))
    if args.events_out:
        Path(args.events_out).write_text(status.model_dump_json(indent=2), encoding="utf-8")
    if args.dashboard:
        try:
            print("run finished; dashboard still up. Ctrl+C to exit.")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    return 0 if status.status.value == "SUCCEEDED" else 1


def _start_dashboard(ex, port: int) -> None:
    import threading
    import uvicorn
    from .api.app import create_app
    server = uvicorn.Server(uvicorn.Config(create_app(ex), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="dashboard").start()
    for _ in range(50):                     # wait until it is listening
        if server.started:
            break
        time.sleep(0.1)


def cmd_validate(args) -> int:
    try:
        t = validate_task(Path(args.task).read_text(encoding="utf-8"))
        print(json.dumps({"valid": True, "task_id": t.task_id, "actions": len(t.actions)}))
        return 0
    except RobotControlError as e:
        print(json.dumps({"valid": False, "error": {"code": e.code.value, "message": e.message}}, indent=2))
        return 1


def cmd_setup_station(args) -> int:
    from .backends.robodk_station import build_demo_station
    robot = load_robot_config(args.config or _default_config("robot"))
    scene_cfg = load_scene_config(args.scene or _default_config("scene"))
    path = build_demo_station(robot, scene_cfg)
    print(f"RoboDK demo station built and saved to {path}")
    if not args.no_objects:
        from .sim_objects.robodk_import import run_import
        print("importing demo objects (objects/object_specifications.json):")
        if run_import(replace=True, out=lambda m: print("  " + m)) == 0:
            from .backends.robodk_sim import _import_robodk
            rl, _ = _import_robodk()
            rl.Robolink().Save(path)
            print(f"station with objects saved to {path}")
        else:
            print("  object import failed (run `python scripts/generate_all.py` first); station has no demo objects")
    print("NOTE: demo geometry is a stand-in; collision checks are a digital-twin aid only.")
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    from .api.app import create_app
    password = os.environ.get("ROBOT_API_PASSWORD", "")
    user = os.environ.get("ROBOT_API_USER", "operator")
    if args.host not in ("127.0.0.1", "localhost", "::1") and len(password) < 12:
        print("Refusing to bind a robot-control API to a non-loopback address without authentication. "
              "Set ROBOT_API_PASSWORD (12+ chars) to enable HTTP Basic auth.", file=sys.stderr)
        return 2
    ex = build_executor(args)
    ex.start()
    auth = (user, password) if password else None
    uvicorn.run(create_app(ex, auth=auth), host=args.host, port=args.port, log_level="info",
                proxy_headers=False)
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="robot_control")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="run a task JSON")
    _add_common(d)
    d.add_argument("--task", required=True)
    d.add_argument("--events-out", default=None, help="write full task status + events JSON here")
    d.add_argument("--live-file", default="runs/live_status.json", help="live status JSON ('' to disable)")
    d.add_argument("--dashboard", action="store_true", help="serve a live dashboard on 127.0.0.1")
    d.add_argument("--dashboard-port", type=int, default=8765)
    d.set_defaults(fn=cmd_demo)

    v = sub.add_parser("validate", help="validate a task JSON without running it")
    v.add_argument("--task", required=True)
    v.set_defaults(fn=cmd_validate)

    s = sub.add_parser("setup-station", help="build the RoboDK demo station (simulation only)")
    s.add_argument("--config", default=None)
    s.add_argument("--scene", default=None)
    s.add_argument("--no-objects", action="store_true", help="don't import the 4 demo objects (objects/)")
    s.set_defaults(fn=cmd_setup_station)

    t = sub.add_parser("tools", help="print the AI tool allowlist")
    t.set_defaults(fn=lambda a: (print(json.dumps(tool_specs(), indent=2)), 0)[1])

    sv = sub.add_parser("serve", help="FastAPI server + dashboard (loopback unless ROBOT_API_PASSWORD is set)")
    _add_common(sv)
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except RobotControlError as e:
        print(json.dumps({"error": {"code": e.code.value, "message": e.message}}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
