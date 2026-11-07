"""One process for the robot PC: vision, teach, the controller, and the app bridge.

    python -m robot_control.cli stack

The bridge listens on BRIDGE_PORT (default 8080) in this process. The controller API stays on
127.0.0.1. Vision and the teach server use the computer-vision environment, because that is a
separate install. SIM=1 follows the arm selected in the app and restarts the cell when it changes.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

RC = Path(__file__).resolve().parents[1]
MOTIQ = RC.parent
CV = MOTIQ / "computer-vision"
VISION_FILE = RC / "runs" / "vision_bridge.json"
SEL = RC / "config" / "selection.yaml"


def _say(msg: str) -> None:
    print(f"[stack] {msg}", flush=True)


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def _sel_field(key: str) -> str:
    if not SEL.is_file():
        return ""
    prefix = f"{key}:"
    for line in SEL.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if parts and parts[0] == prefix:
            return parts[1]
    return ""


def _sel_key() -> str:
    return f"{_sel_field('robot_id')}/{_sel_field('status')}"


def _wait_http(url: str, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def _flags(pairs: list[str]) -> dict:
    out: dict = {}
    it = iter(pairs)
    for tok in it:
        name = tok.lstrip("-").replace("-", "_")
        out[name] = next(it)
    return out


def _controller_ns(flags: list[str], port: int) -> argparse.Namespace:
    got = _flags(flags)
    return argparse.Namespace(
        config=got.get("config"), scene=got.get("scene"), safety=got.get("safety"),
        backend=got.get("backend"), detector=got.get("detector", "mock"),
        vision_file=got.get("vision_file"), speed_percent=None,
        host="127.0.0.1", port=port,
    )


def _pick_arm(sim: bool) -> tuple[list[str], Path, str]:
    """Controller flags, scene path, and the model name shown to the app."""
    if not sim:
        backend = os.environ.get("BACKEND", "")
        return (["--backend", backend] if backend else []), _scene_file(), "UR5e"
    rid, model, status = _sel_field("robot_id"), _sel_field("model"), _sel_field("status")
    scene_yaml = RC / "config" / "scene.yaml"
    if rid and rid != "ur_ur5e" and status == "READY" and scene_yaml.is_file():
        _say(f"SIM=1: {model} ({rid}) cell in PyBullet")
        flags = ["--backend", "pybullet", "--config", "config/robot.yaml",
                 "--safety", "config/safety.yaml", "--scene", "config/scene.yaml"]
        return flags, scene_yaml, model or rid
    if rid and rid != "ur_ur5e":
        print(f"[stack] WARNING: selected arm {rid} is {status or 'unknown'} - showing the UR5e", file=sys.stderr)
    _say("SIM=1: UR5e demo cell in PyBullet (config/robot.ursim_demo.yaml)")
    flags = ["--backend", "pybullet", "--config", "config/robot.ursim_demo.yaml",
             "--safety", "config/safety.ursim_demo.yaml", "--scene", "config/scene.example.yaml"]
    return flags, RC / "config" / "scene.example.yaml", "UR5e"


def _scene_file() -> Path:
    chosen = RC / "config" / "scene.yaml"
    return chosen if chosen.is_file() else RC / "config" / "scene.example.yaml"


class _Arm:
    def __init__(self, ex, controller, controller_thread, bridge, bridge_thread):
        self.ex = ex
        self.controller = controller
        self.controller_thread = controller_thread
        self.bridge = bridge
        self.bridge_thread = bridge_thread

    def stop(self) -> None:
        self.controller.should_exit = True
        self.bridge.should_exit = True
        self.controller_thread.join(timeout=8)
        self.bridge_thread.join(timeout=8)
        try:
            self.ex.c.disconnect()
        except Exception:
            pass


def run_stack() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    os.chdir(RC)
    sim = os.environ.get("SIM", "0") == "1"
    cv_py = CV / ".venv" / "bin" / "python"
    vision_on = os.environ.get("VISION", "1" if cv_py.is_file() else "0") == "1" and cv_py.is_file()
    vision_mode = os.environ.get("VISION_MODE", "")
    if sim and vision_on and not vision_mode:
        vision_mode = "pybullet"
    if sim and vision_mode == "pybullet":
        os.environ["SIM_VISION"] = os.environ.get("SIM_VISION", "1")
    else:
        os.environ["SIM_VISION"] = os.environ.get("SIM_VISION", "0")

    bridge_port = int(os.environ.get("BRIDGE_PORT", "8080"))
    ctrl_port = int(os.environ.get("CTRL_PORT", "8765"))
    teach_port = int(os.environ.get("TEACH_PORT", "8001"))
    if _port_busy(ctrl_port):
        print(f"[stack] port {ctrl_port} is taken. Stop the other controller or set CTRL_PORT.", file=sys.stderr)
        return 1

    vision_url = "http://127.0.0.1:8000" if vision_on else ""
    if vision_on and sim and os.environ["SIM_VISION"] != "1":
        detector = ["--detector", "mock"]
    elif vision_on:
        detector = ["--detector", "vision_file", "--vision-file", str(VISION_FILE)]
    else:
        detector = ["--detector", "mock"]
        _say("no vision (run computer-vision/setup.sh to enable); controller uses the MOCK detector")

    children: list[subprocess.Popen] = []
    arm: _Arm | None = None
    stop = threading.Event()

    def _sig(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    def spawn(cmd: list[str], cwd: Path) -> subprocess.Popen:
        proc = subprocess.Popen(cmd, cwd=cwd)
        children.append(proc)
        return proc

    try:
        if vision_on:
            _say(f"teach server on :{teach_port}")
            spawn([str(cv_py), "-u", str(CV / "to-app" / "teach_server.py"), "--port", str(teach_port)], CV)
            args = ["-m", "vision.eyes.cli", "run", "--port", "8000"]
            if vision_mode:
                args += ["--mode", vision_mode]
            robot_ip = os.environ.get("VISION_ROBOT_IP", "")
            if robot_ip:
                args += ["--robot-ip", robot_ip]
            label = "PyBullet camera, color + depth, ONNX detector + YOLO-World" if vision_mode == "pybullet" else ""
            _say("vision server on :8000" + (f" ({label})" if label else ""))
            spawn([str(cv_py), "-u", *args], CV)
        twin_cfg = os.environ.get("DIGITAL_TWIN_CONFIG", "")
        if twin_cfg:
            _say(f"digital twin (MIRROR ONLY, no physical motion) on 127.0.0.1:8770, config {twin_cfg}")
            spawn([sys.executable, "-m", "robot_control.digital_twin.cli", "serve", "--config", twin_cfg], RC)
        if os.environ.get("CHOOSER", "1") == "1":
            _say("robot-chooser (arm catalog, config files only) on 127.0.0.1:8771")
            spawn([sys.executable, "-m", "robot_chooser", "serve", "--port", "8771"], RC / "robot-chooser")

        def start_arm() -> _Arm:
            import uvicorn
            from .api.app import create_app
            from .arm_bridge import bridge_server
            from .cli import build_executor

            flags, scene, sim_arm = _pick_arm(sim)
            _say(f"robot controller on 127.0.0.1:{ctrl_port} (dashboard http://127.0.0.1:{ctrl_port}/)")
            ns = _controller_ns(flags + detector, ctrl_port)
            ex = build_executor(ns)
            ex.start()
            config = uvicorn.Config(create_app(ex), host="127.0.0.1", port=ctrl_port, log_level="info")
            server = uvicorn.Server(config)
            server.install_signal_handlers = False
            thread = threading.Thread(target=server.run, name="controller", daemon=True)
            thread.start()
            if not _wait_http(f"http://127.0.0.1:{ctrl_port}/health", 60):
                server.should_exit = True
                thread.join(timeout=5)
                try:
                    ex.c.disconnect()
                except Exception:
                    pass
                raise RuntimeError("the robot controller did not start")
            bns = argparse.Namespace(
                host="0.0.0.0", port=bridge_port, controller=f"http://127.0.0.1:{ctrl_port}",
                vision=vision_url, scene=scene, vision_file=VISION_FILE, task_timeout=180.0, min_conf=0.6,
                digital_twin=os.environ.get("DIGITAL_TWIN_URL", "http://127.0.0.1:8770"),
                robot_chooser=os.environ.get("ROBOT_CHOOSER_URL", "http://127.0.0.1:8771"),
                sim=sim, sim_arm=sim_arm,
            )
            bridge = bridge_server(bns)
            bthread = threading.Thread(target=bridge.run, name="bridge", daemon=True)
            bthread.start()
            return _Arm(ex, server, thread, bridge, bthread)

        arm = start_arm()
        ip = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
        host = ip[0] if ip else "<this-pc-ip>"
        _say(f"app bridge on :{bridge_port}  ->  groky-app/.env:")
        print(f"          EXPO_PUBLIC_ARM_URL=http://{host}:{bridge_port}", flush=True)
        if vision_url:
            print(f"          EXPO_PUBLIC_VISION_SERVER=http://{host}:8000", flush=True)
        _say("operator reset after a stop/fault:  "
             f"curl -X POST localhost:{ctrl_port}/operator/reset -d '{{\"operator\":\"you\"}}'")

        current = _sel_key()
        while not stop.is_set():
            time.sleep(2)
            for proc in children:
                if proc.poll() is not None:
                    print(f"[stack] a part of the stack exited (pid {proc.pid})", file=sys.stderr)
                    return 1
            if arm.controller_thread.is_alive() and arm.bridge_thread.is_alive():
                pass
            else:
                print("[stack] the controller stopped", file=sys.stderr)
                return 1
            if not sim:
                continue
            new = _sel_key()
            if new == current:
                continue
            _say(f"arm selection changed ({current} -> {new}): restarting the cell")
            current = new
            arm.stop()
            arm = None
            for _ in range(20):
                if not _port_busy(ctrl_port) and not _port_busy(bridge_port):
                    break
                time.sleep(0.25)
            arm = start_arm()
        return 0
    except RuntimeError as exc:
        print(f"[stack] ERROR: {exc}", file=sys.stderr)
        print("        If robot-chooser selected a BLOCKED arm, pick a Ready arm in the app", file=sys.stderr)
        print("        (Robots -> Arm catalog) or run", file=sys.stderr)
        print("        (cd robot_controller/robot-chooser && ../.venv/bin/python -m robot_chooser select ur_ur5e)",
              file=sys.stderr)
        return 1
    finally:
        if arm is not None:
            arm.stop()
        for proc in children:
            if proc.poll() is None:
                proc.terminate()
        for proc in children:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
