#!/usr/bin/env python3
"""Cross-platform MOTIQ stack launcher for the desktop app (simulation / RoboDK default).

Starts: vision :8000, teach :8001, chooser :8771, controller :8765, bridge :8080.
Does NOT run computer-vision/training. Stops all children on SIGINT/SIGTERM/exit.

    BRIDGE_KEY=secret python desktop/scripts/start_stack.py
    # or with MOTIQ_ROOT pointing at the installed resource tree
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("MOTIQ_ROOT") or Path(__file__).resolve().parents[2]).resolve()
RC = ROOT / "robot_controller"
CV = ROOT / "computer-vision"
CHOOSER = ROOT / "robot-chooser"
BRIDGE = ROOT / "bridge"
CV_APP = ROOT / "computer-vision-to-app"

BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "8080"))
CTRL_PORT = int(os.environ.get("CTRL_PORT", "8765"))
TEACH_PORT = int(os.environ.get("TEACH_PORT", "8001"))
VISION_MODE = os.environ.get("VISION_MODE", "synthetic")
BACKEND = os.environ.get("BACKEND", "robodk_sim")  # robodk_sim | mock


def venv_python(base: Path) -> Path:
    if sys.platform == "win32":
        return base / ".venv" / "Scripts" / "python.exe"
    return base / ".venv" / "bin" / "python"


def pick_python(*candidates: Path) -> str:
    for c in candidates:
        if c.is_file():
            return str(c)
    return sys.executable


children: list[subprocess.Popen] = []


def spawn(name: str, argv: list[str], cwd: Path, env: dict | None = None) -> subprocess.Popen:
    e = os.environ.copy()
    if env:
        e.update(env)
    print(f"[stack] {name}: {' '.join(argv)}", flush=True)
    kw: dict = {"cwd": str(cwd), "env": e}
    if sys.platform != "win32":
        kw["start_new_session"] = True
    p = subprocess.Popen(argv, **kw)
    children.append(p)
    return p


def cleanup(*_args):
    for p in children:
        try:
            p.terminate()
        except Exception:
            pass
    t0 = time.time()
    while time.time() - t0 < 5:
        if all(p.poll() is not None for p in children):
            break
        time.sleep(0.2)
    for p in children:
        if p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
    sys.exit(0)


def main() -> int:
    key = os.environ.get("BRIDGE_KEY", "").strip()
    if len(key) < 8:
        print("error: set BRIDGE_KEY to 8+ characters", file=sys.stderr)
        return 2

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    py_rc = pick_python(venv_python(RC))
    py_cv = pick_python(venv_python(CV), Path(py_rc))

    vision_url = ""
    if os.environ.get("VISION", "1") != "0" and (CV / "vision").is_dir():
        spawn(
            "vision",
            [py_cv, "-m", "vision.eyes.cli", "run", "--port", "8000", "--mode", VISION_MODE],
            CV,
        )
        vision_url = "http://127.0.0.1:8000"
        teach = CV_APP / "teach_server.py"
        if teach.is_file():
            spawn("teach", [py_cv, str(teach), "--port", str(TEACH_PORT)], CV)

    if os.environ.get("CHOOSER", "1") != "0":
        spawn("chooser", [py_rc, "-m", "robot_chooser", "serve", "--host", "127.0.0.1", "--port", "8771"], CHOOSER)

    ctrl_argv = [
        py_rc, "-m", "robot_control.cli", "serve",
        "--host", "127.0.0.1", "--port", str(CTRL_PORT),
        "--backend", BACKEND if BACKEND in ("mock", "robodk_sim") else "robodk_sim",
    ]
    vision_file = RC / "runs" / "vision_bridge.json"
    vision_file.parent.mkdir(parents=True, exist_ok=True)
    if vision_url:
        ctrl_argv += ["--detector", "vision_file", "--vision-file", str(vision_file)]
    else:
        ctrl_argv += ["--detector", "mock"]
    spawn("controller", ctrl_argv, RC)

    # wait briefly for controller
    time.sleep(1.5)

    bridge_argv = [
        py_rc, str(BRIDGE / "motiq_bridge.py"),
        "--host", "127.0.0.1", "--port", str(BRIDGE_PORT),
        "--controller", f"http://127.0.0.1:{CTRL_PORT}",
        "--robot-chooser", "http://127.0.0.1:8771",
    ]
    if vision_url:
        bridge_argv += ["--vision", vision_url]
    spawn("bridge", bridge_argv, BRIDGE, env={"BRIDGE_KEY": key})

    print(f"[stack] ready  bridge=http://127.0.0.1:{BRIDGE_PORT}  key set  backend={BACKEND}", flush=True)
    # wait on children
    while True:
        for p in children:
            code = p.poll()
            if code is not None:
                print(f"[stack] child exited code={code}; shutting down", flush=True)
                cleanup()
        time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
