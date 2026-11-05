"""MOTIQ arm bridge: groky-app  <->  robot_controller  <->  computer-vision.

The app (EXPO_PUBLIC_ARM_URL) speaks the "bridge" protocol:
    GET  /health          -> status of the bridge, the controller and the vision server
    GET  /camera.jpg      -> latest annotated frame from the vision server (arm camera)
    POST /tools/stop      -> cancel the active task + controlled stop
    GET  /speed           -> current arm speed (% of the active robot model's maximums)
    POST /speed           -> {"percent": 25}: change it (refused while a task runs)
    POST /tools/{name}    -> run one tool, wait for it, return {ok, did, error, ...}
    POST /tools/ur_down_up -> {"mm": 50}: PHYSICAL UR motion (down + up, <= 50 mm). /tools/stop stops it too.
    POST /tools/move_up|down|left|right|forward|backward {"distance_m": 0.05}, /tools/move_joint, /tools/rotate_yaw
                          -> small PHYSICAL UR steps (<= 10 cm, <= 30 deg, 50 mm/s), one at a time, stoppable.
    POST /tools/ur_visit {"object": "green ring" | "green", "hold_s": 2, "come_back": true}
                          -> PHYSICAL: the vision server guides the arm camera over the object (sideways only, same
                             height, <= 35 cm away), holds, then goes back to the start pose. /tools/stop stops it.
    POST /tools/find {"object"}       -> camera on the arm head: look for one object from the scene's look poses
    POST /tools/scan                  -> look at the whole table: which of the known objects are there
    POST /tools/check_placed {"object","destination"} -> look at the destination after a place
    POST /tools/overview              -> the arm camera looks from where the arm is now (NO motion): which objects
    POST /tools/push {"object"}       -> SIM=1: controller task (identify -> push_object on the taught push path, RoboDK /
                                         URSim). Real arm: camera hover, beside the object, force-guarded down to the
                                         table, push 5 cm along the scene's push direction, lift, back to the start.
    POST /tools/touch {"object", "max_down_mm": 250, "force_n": 15}
                          -> PHYSICAL: camera hover (as ur_visit), taught touching point over the object, down at
                             10 mm/s until the UR5e wrist force sensor reads > force_n (checked on the robot), lift,
                             back to the start pose. /tools/stop stops it.
    POST /tools/touch_teach_start {"object"} -> centre the camera over the object and stay there
    POST /tools/touch_teach_save      -> save the touching point's offset (operator jogged it over the object)
    GET  /digital-twin/status, POST /digital-twin/check|snapshot|start|stop {"objects": bool}
                          -> READ-ONLY digital twin (real UR5e + RealSense -> RoboDK simulation), proxied to the local
                             twin service (robot_control.digital_twin, 127.0.0.1:8770). MIRROR ONLY: these routes never
                             reach the controller, PhysicalArm or /tools/stop; Stop ends mirroring, not the robot.
    GET  /robots/catalog, GET /robots/active, POST /robots/select {"robot_id"}, POST /robots/add {catalog entry}
                          -> arm catalog, proxied to the local robot-chooser (python -m robot_chooser serve,
                             127.0.0.1:8771). Adding/selecting an arm only rewrites config files (simulation only);
                             it takes effect after the controller + bridge restart. Never reaches the controller.

The controller only accepts allowlisted high-level tasks (AI_ACTION_CONTRACT.md) and only on
127.0.0.1, so this bridge runs on the same PC, translates tool calls into task JSON and is the
only thing exposed on the LAN (protected by X-Bridge-Key).

Vision: before every task that needs an object, the bridge reads GET {vision}/state/arm, maps the
tracked objects to the controller's *registered* object IDs (scene `class_labels`) and writes them
to --vision-file, which the controller reads with `--detector vision_file`. If the object isn't
visible the bridge refuses up front, so a missing object never latches a controller FAULT.
Camera coordinates (coords_mm) are not forwarded: picks use the taught named targets.

Camera on the arm head (scene `search.camera: arm_mounted`): when the object isn't in view, a pick first searches
for it: straight down at the object's taught spot, then the scene's scan poses, stopping as soon as it is seen.
Every look pose is a named target moved to with an ordinary controller task (same safety checks).
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

log = logging.getLogger("motiq_bridge")

HERE = Path(__file__).resolve().parent
_CFG = HERE.parent / "robot_controller" / "config"
# scene.yaml is generated by robot-chooser for the selected robot; same lookup order as the controller CLI
DEFAULT_SCENE = _CFG / "scene.yaml" if (_CFG / "scene.yaml").is_file() else _CFG / "scene.example.yaml"
DEFAULT_VISION_FILE = HERE.parent / "robot_controller" / "runs" / "vision_bridge.json"

TERMINAL = {"SUCCEEDED", "FAILED", "REJECTED", "CANCELLED"}

# Italian colour names the vision server reports -> English, so "orange screw" matches "vite" + "arancione"
COLORS_IT_EN = {"rosso": "red", "blu": "blue", "verde": "green", "giallo": "yellow", "nero": "black",
                "bianco": "white", "arancione": "orange", "viola": "purple", "grigio": "gray", "rosa": "pink",
                "marrone": "brown", "azzurro": "light blue"}

# The app uses the controller's scene ids directly (blue_bucket_01, ..., delivery_zone); these are spoken names.
APP_ALIASES = {"delivery": "delivery_zone", "delivery pad": "delivery_zone", "pad": "delivery_zone",
               "drop zone": "delivery_zone", "box": "delivery_zone"}

# app primitives with no safe equivalent (absolute poses could go anywhere; roll/pitch tilt the tool)
REFUSED_PRIMITIVES = {"rotate_roll", "rotate_pitch", "move_joints", "move_to_pose", "close_gripper"}

# small relative step moves the app sends ("move the arm 5 cm up"): run on the PHYSICAL arm via ur_jog
STEP_MOVES = {"move_left", "move_right", "move_forward", "move_backward", "move_up", "move_down"}
UR_JOINTS = ["shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"]
TEMPLATE = HERE.parent / "groky-app" / "src" / "skills" / "templates" / "pick_place_ur5e.json"


def load_direction_mapping() -> dict[str, tuple[int, int]]:
    """direction -> (base axis index, sign). Same table the app uses (skill template robot.direction_mapping)."""
    axes = {"x": 0, "y": 1, "z": 2}
    out = {"forward": (0, 1), "backward": (0, -1), "left": (1, 1), "right": (1, -1), "up": (2, 1), "down": (2, -1)}
    try:
        m = json.loads(TEMPLATE.read_text(encoding="utf-8"))["robot"]["direction_mapping"]
        for d in out:
            if isinstance(m.get(d), dict):
                out[d] = (axes[m[d]["axis"]], int(m[d]["sign"]))
    except Exception as e:  # keep the defaults, they match the template
        log.warning("direction mapping not read from %s (%s); using defaults", TEMPLATE, e)
    return out


class Refused(Exception):
    """A request the bridge won't forward. Nothing was sent to the robot."""


# ---------------------------------------------------------------------- physical UR arm (one fixed motion)
# The ONLY thing the bridge does on real hardware: TCP straight down N mm (<= 50) and back up, then stop.
# Uses ../../ur_manual/ur_jog.py (URScript on the robot's port 30002). No homing, no other motion.
PHYSICAL_MAX_MM = 50.0
PHYSICAL_STEP_MAX_MM = 100.0   # one voice step move ("5 cm up"); same cap as ur_jog.MAX_STEP_MM
PHYSICAL_STEP_TIMEOUT_S = 15.0
# ur_visit: hover the arm camera over an object the vision server sees (no hand-eye calibration needed)
VISIT_NUDGE_MM = 20.0          # two test nudges to learn which way the camera faces
VISIT_STEP_MM = 80.0           # largest sideways move per correction
VISIT_MAX_TRAVEL_MM = 350.0    # never wander farther than this from the start pose
VISIT_TOL = 0.03               # centred when within 3 % of the image size
VISIT_MAX_ITERS = 8
VISIT_MIN_CONF = 0.4           # hovering touches nothing, so a lower bar than picks
# touch / push on the PHYSICAL arm: hover (as ur_visit), shift by the taught camera->touching-point offset, then a
# force-guarded move down (UR5e wrist F/T sensor, checked on the robot every cycle)
TOUCH_SPEED_MPS = 0.010        # 10 mm/s going down
TOUCH_FORCE_N = 15.0           # contact = more than this on the (zeroed) wrist sensor
TOUCH_FORCE_RANGE_N = (5.0, 40.0)
TOUCH_MAX_DOWN_MM = 250.0      # default: give up after this much descent without contact
TOUCH_MAX_DOWN_CAP_MM = 400.0
TOUCH_MAX_OFFSET_M = 0.20      # a taught camera->tip offset bigger than this is a mistake
PUSH_STANDOFF_MM = 25.0        # start this far from the object's side
PUSH_HEIGHT_MM = 20.0          # push this high above the table (at most half the object's height)
PUSH_SPEED_MPS = 0.03
TOUCH_OFFSET_FILE = HERE.parent / "computer-vision" / "data" / "calibration" / "touch_offset.json"
_STOP_SCRIPT = b"def stop_jog():\n  stopl(1.0)\nend\nstop_jog()\n"


URSIM_HOST = "127.0.0.1"          # URSim's ports are published on loopback only
URSIM_UI = "http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale"


def is_loopback_host(host: str) -> bool:
    import ipaddress
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class URSimLauncher:
    """SIM=1: before any motion, make sure URSim (the local docker container) is up, powered on, brakes released
    and not protective-stopped, so tapping Run on a skill "just works" even if URSim was closed. Also opens the
    URSim pendant (noVNC) in the PC's browser once, so the demo shows URSim next to RoboDK.
    Only ever talks to 127.0.0.1: it can't reach the physical arm."""

    def __init__(self, container: str = "ursim-ur5e", open_ui: bool = True, boot_timeout_s: float = 120.0):
        import threading
        self.container = container
        self.open_ui = open_ui
        self.boot_timeout_s = boot_timeout_s
        self.lock = threading.Lock()
        self.ui_opened = False
        self.last_ok = 0.0

    @staticmethod
    def _dash(cmd: str, timeout: float = 2.0) -> str:
        import socket
        with socket.create_connection((URSIM_HOST, 29999), timeout=timeout) as s:
            s.settimeout(timeout)
            s.recv(1024)
            s.sendall((cmd + "\n").encode())
            return s.recv(1024).decode().strip()

    def _docker(self, *args: str) -> tuple[int, str]:
        import subprocess
        try:
            p = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60)
            return p.returncode, (p.stderr or p.stdout).strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            return 1, str(e)

    def _start_container(self) -> None:
        log.info("URSim: not answering, starting docker container %s", self.container)
        rc, out = self._docker("start", self.container)
        if rc == 0:
            return
        if "No such container" not in out:
            raise Refused(f"could not start URSim (docker start {self.container}: {out[:200]})")
        progs = Path.home() / "ursim-ur5e" / "programs"
        progs.mkdir(parents=True, exist_ok=True)
        rc, out = self._docker("run", "-d", "--name", self.container, "-e", "ROBOT_MODEL=UR5",
                               "-p", "127.0.0.1:5900:5900", "-p", "127.0.0.1:6080:6080",
                               "-p", "127.0.0.1:29999:29999", "-p", "127.0.0.1:30001-30004:30001-30004",
                               "-v", f"{progs}:/ursim/programs", "universalrobots/ursim_e-series")
        if rc != 0:
            raise Refused(f"could not create the URSim container: {out[:200]}")

    def _open_ui(self) -> None:
        if self.ui_opened or not self.open_ui:
            return
        self.ui_opened = True
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return
        import subprocess
        try:
            subprocess.Popen(["xdg-open", URSIM_UI], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            log.info("URSim: opened the pendant at %s", URSIM_UI)
        except OSError:
            pass

    def ensure(self) -> None:
        """Bring URSim to RUNNING + NORMAL or raise Refused. Cheap when it already is."""
        with self.lock:
            if time.monotonic() - self.last_ok < 2.0:
                return
            deadline = time.monotonic() + self.boot_timeout_s
            started = False
            mode = "unreachable"
            while time.monotonic() < deadline:
                try:
                    mode = self._dash("robotmode")
                    safety = self._dash("safetystatus")
                except OSError:
                    if not started:
                        self._start_container()
                        started = True
                    time.sleep(1.0)
                    continue
                if "PROTECTIVE_STOP" in safety:
                    self._dash("unlock protective stop")
                elif "RUNNING" in mode and "NORMAL" in safety:
                    self.last_ok = time.monotonic()
                    self._open_ui()
                    if started:
                        log.info("URSim: started and ready")
                    return
                elif "POWER_OFF" in mode or "DISCONNECTED" in mode or "CONFIDENTIAL" in mode:
                    self._dash("close safety popup")
                    self._dash("power on")
                elif "IDLE" in mode:
                    self._dash("brake release")
                time.sleep(1.0)
            raise Refused(f"URSim did not become ready within {self.boot_timeout_s:.0f}s (robot mode: {mode}). "
                          "Open http://127.0.0.1:6080/vnc.html to check it.")


import contextvars

_ARM: "contextvars.ContextVar[Optional[PhysicalArm]]" = contextvars.ContextVar("motiq_arm", default=None)


class PhysicalArm:
    """Small step moves on a UR. sim=True (SIM=1 / --sim) sends them to URSim on 127.0.0.1 instead of the real arm;
    the controller's ursim_sync backend mirrors URSim into RoboDK, so they show up in the RoboDK simulation."""

    def __init__(self, sim: bool = False, sim_arm: str = "UR5e"):
        import importlib.util
        import threading
        # a private copy of ur_jog per arm: its robot IP is a module global (ur_jog.ROBOT_IP) that send_script /
        # move_rel / stop read, so the URSim arm and the physical arm (SIM=1 + real-arm runs) must never share it
        spec = importlib.util.spec_from_file_location(f"ur_jog_{'sim' if sim else 'real'}_{id(self):x}",
                                                      HERE.parent.parent / "ur_manual" / "ur_jog.py")
        ur_jog = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ur_jog)
        self.sim = sim
        # SIM=1 with another arm selected (robot-chooser): RoboDK runs that arm via the controller, URSim is not
        # mirrored, so free step/joint moves on URSim would move nothing visible -> refused (named moves still work)
        self.jog_refusal = (f"step and joint moves only exist for the UR5e simulation (URSim); the simulated "
                            f"{sim_arm} does named moves only: home, pick, place, push, look at an object"
                            if sim and sim_arm != "UR5e" else "")
        if sim:
            ur_jog.ROBOT_IP = URSIM_HOST      # URSim (docker, loopback only): never the physical arm
            if not is_loopback_host(ur_jog.ROBOT_IP):
                raise SystemExit(f"SIM=1 but the arm IP is {ur_jog.ROBOT_IP}, not loopback: refusing to start")
        self.jog, self.ip = ur_jog, ur_jog.ROBOT_IP
        self.where = "in the RoboDK simulation" if sim else "on the physical arm"
        self.name = "the simulated arm (URSim)" if sim else "the physical arm"
        self.lock = threading.Lock()
        self.cancel = threading.Event()

    def status(self) -> dict:
        """Quick probe for /health: is the UR reachable (ethernet plugged in) and ready? Never raises."""
        import socket
        try:
            with socket.create_connection((self.ip, 29999), timeout=0.5) as s:
                s.settimeout(0.5)
                s.recv(1024)
                s.sendall(b"robotmode\n")
                mode = s.recv(1024).decode().strip().removeprefix("Robotmode: ")
                s.sendall(b"safetystatus\n")
                safety = s.recv(1024).decode().strip().removeprefix("Safetystatus: ")
        except OSError:
            return {"reachable": False, "ready": False, "sim": self.sim,
                    "note": f"no answer from {self.ip}: " + ("URSim container down?" if self.sim else "ethernet unplugged?")}
        return {"reachable": True, "sim": self.sim, "ip": self.ip, "ready": "RUNNING" in mode and "NORMAL" in safety,
                "mode": mode, "safety": safety, "busy": self.lock.locked()}

    def stop(self) -> bool:
        """Best effort, quick: stopl on the physical arm. Never raises."""
        self.cancel.set()
        import socket
        try:
            with socket.create_connection((self.ip, 30002), timeout=0.5) as s:
                s.sendall(_STOP_SCRIPT)
            return True
        except OSError:
            return False

    def _preflight(self) -> list:
        """Refuse unless the arm is reachable, RUNNING and NORMAL. Returns the start TCP pose."""
        if self.jog_refusal:
            raise Refused(self.jog_refusal)
        try:
            mode = self.jog.dashboard("robotmode", self.ip)
            safety = self.jog.dashboard("safetystatus", self.ip)
            start, _ = self.jog.read_state(self.ip)
        except OSError as e:
            raise Refused(f"{self.name} not reachable at {self.ip} ({e.__class__.__name__}); " + ("is the URSim container running?" if self.sim else "check the ethernet cable"))
        if "RUNNING" not in mode:
            raise Refused(f"{self.name} not ready ({mode}): power on + brake release on the pendant")
        if "NORMAL" not in safety:
            raise Refused(f"{self.name} safety is not NORMAL ({safety})")
        return start

    def _wait(self, reached) -> Optional[dict]:
        """Poll until reached(tcp, joints) is true. None = reached, else an error result."""
        t0 = time.monotonic()
        while True:
            if self.cancel.is_set():
                return {"ok": False, "error": "stopped"}
            tcp, q = self.jog.read_state(self.ip)
            if reached(tcp, q):
                return None
            if time.monotonic() - t0 > PHYSICAL_STEP_TIMEOUT_S:
                return {"ok": False, "error": "did not reach the target; arm stopped. Check the pendant ("
                        + self.jog.dashboard("safetystatus", self.ip) + ")"}
            time.sleep(0.1)

    def _locked(self, fn) -> dict:
        # calls from one voice turn arrive back to back ("up 5 cm, then down"): wait for the previous one
        if not self.lock.acquire(timeout=PHYSICAL_STEP_TIMEOUT_S * 2):
            raise Refused(f"{self.name} is still busy with the previous move")
        try:
            self.cancel.clear()
            self._preflight()
            return fn()
        except OSError as e:
            return {"ok": False, "error": f"lost connection to {self.name} ({e.__class__.__name__}); arm stopped"}
        finally:
            self.stop()
            self.lock.release()

    def _result(self, did: str) -> dict:
        tcp, q = self.jog.read_state(self.ip)
        # joints_deg lets the app sync its arm model to the real pose
        return {"ok": True, "did": did, "physical": not self.sim, "simulation": self.sim, "joints_deg": [round(math.degrees(v), 2) for v in q],
                "tcp_mm": [round(v * 1000, 1) for v in tcp[:3]]}

    def step(self, direction: str, distance_m: Any, axis_sign: tuple[int, int]) -> dict:
        """Straight-line TCP move in the base frame, e.g. 5 cm up. ur_jog speed (50 mm/s)."""
        try:
            mm = float(distance_m if distance_m is not None else 0.05) * 1000
        except (TypeError, ValueError):
            raise Refused("distance_m must be a number")
        if not 1 <= mm <= PHYSICAL_STEP_MAX_MM:
            raise Refused(f"on {self.name} a step must be 1..{PHYSICAL_STEP_MAX_MM / 10:g} cm")
        axis, sign = axis_sign
        d = [0.0, 0.0, 0.0]
        d[axis] = sign * mm

        def run():
            log.info("physical step %s %.0f mm", direction, mm)
            target = self.jog.move_rel(*d)
            err = self._wait(lambda tcp, q: all(abs(tcp[i] - target[i]) < 0.0005 for i in range(3)))
            return err or self._result(f"moved {mm / 10:g} cm {direction} {self.where}")
        return self._locked(run)

    def joint(self, index: int, label: str, delta_deg: float) -> dict:
        """Rotate one joint by delta_deg (capped by ur_jog.MAX_JOINT_DEG), slowly."""
        if not 0.5 <= abs(delta_deg) <= self.jog.MAX_JOINT_DEG:
            raise Refused(f"on {self.name} a joint step must be 0.5..{self.jog.MAX_JOINT_DEG} degrees")

        def run():
            log.info("physical joint %s %+.1f deg", label, delta_deg)
            target = self.jog.move_joint_rel(index + 1, delta_deg)
            err = self._wait(lambda tcp, q: all(abs(q[i] - target[i]) < 0.002 for i in range(6)))
            return err or self._result(f"turned the {label} {delta_deg:+g}° {self.where}")
        return self._locked(run)

    # ---- camera-guided hover (shared by visit, touch, push and touch-offset teaching) -------------------------
    def _centre_over(self, locate, label: str) -> Optional[dict]:
        """Lock held + preflight done. Centre the arm camera over the object, sideways only at the same height.
        None = centred, else an error result.

        `locate(after_ts)` -> (u, v) bbox centre normalised 0-1 from a vision frame newer than `after_ts`, or None.
        No hand-eye calibration: two VISIT_NUDGE_MM nudges (base X, base Y) measure how the object moves in the
        image, then sideways moves centre it. Every move is a ur_jog movel at 50 mm/s, <= VISIT_STEP_MM, and the
        whole hover stays within VISIT_MAX_TRAVEL_MM of the start. STOP cancels it."""
        import numpy as np

        def look():
            p = locate(time.time() + 0.3)       # only frames taken after the arm settled
            return None if p is None else np.array(p, dtype=float)

        def go(dx, dy):
            target = self.jog.move_rel(dx, dy, 0)
            return self._wait(lambda tcp, q: all(abs(tcp[i] - target[i]) < 0.0005 for i in range(3)))

        p0 = look()
        if p0 is None:
            return {"ok": False, "error": f"the camera can't see the {label} from here"}
        start, _ = self.jog.read_state(self.ip)
        log.info("physical hover %s: seen at (%.3f, %.3f), start %s mm", label, *p0,
                 [round(v * 1000, 1) for v in start[:3]])
        moved = np.zeros(2)
        err = go(VISIT_NUDGE_MM, 0) or None
        p1 = None if err else look()
        err = err or (go(0, VISIT_NUDGE_MM) if p1 is not None else None)
        p2 = None if err or p1 is None else look()
        moved += [VISIT_NUDGE_MM, VISIT_NUDGE_MM]
        if err:
            return err
        if p1 is None or p2 is None:
            return {"ok": False, "error": f"lost sight of the {label} while testing the camera direction"}
        J = np.column_stack([(p1 - p0) / VISIT_NUDGE_MM, (p2 - p1) / VISIT_NUDGE_MM])  # image units per mm
        if abs(np.linalg.det(J)) < 1e-8:
            return {"ok": False, "error": "the object didn't move in the image during the test nudges"}
        p = p2
        for _ in range(VISIT_MAX_ITERS):
            if self.cancel.is_set():
                return {"ok": False, "error": "stopped"}
            e = np.array([0.5, 0.5]) - p
            if np.linalg.norm(e) < VISIT_TOL:
                break
            d = np.linalg.solve(J, e)
            if np.abs(d).max() > VISIT_STEP_MM:
                d *= VISIT_STEP_MM / np.abs(d).max()
            if np.abs(moved + d).max() > VISIT_MAX_TRAVEL_MM:
                return {"ok": False, "error": f"the {label} is more than {VISIT_MAX_TRAVEL_MM / 10:g} cm away; "
                        "not going that far"}
            err = go(float(d[0]), float(d[1]))
            if err:
                return err
            moved += d
            p = look()
            if p is None:
                return {"ok": False, "error": f"lost sight of the {label}"}
        else:
            return {"ok": False, "error": f"couldn't centre the {label} under the camera"}
        over, _ = self.jog.read_state(self.ip)
        log.info("physical hover %s: over it at %s mm", label, [round(v * 1000, 1) for v in over[:3]])
        return None

    def _movel(self, pose: list, speed: Optional[float] = None) -> Optional[dict]:
        """Straight line to an absolute TCP pose (base frame), wait until there. None = reached."""
        p = "p[%s]" % ",".join("%.6f" % v for v in pose)
        self.jog.send_script("def motiq_l():\n  movel(%s, a=%s, v=%s)\nend\nmotiq_l()"
                             % (p, self.jog.ACCEL, speed or self.jog.SPEED))
        return self._wait(lambda tcp, q: all(abs(tcp[i] - pose[i]) < 0.0005 for i in range(3)))

    def _back_to(self, start: list) -> None:
        """Return to `start`: first straight UP to its height if the arm is lower (never drag sideways near the
        table), then a straight line to the exact start pose. Best effort, never raises."""
        try:
            for target in ("up", "start"):
                tcp, _ = self.jog.read_state(self.ip)
                if target == "up":
                    if tcp[2] >= start[2] - 0.002:
                        continue
                    pose = tcp[:2] + [start[2]] + tcp[3:]
                else:
                    pose = list(start)
                p = "p[%s]" % ",".join("%.6f" % v for v in pose)
                self.jog.send_script("def back():\n  movel(%s, a=%s, v=%s)\nend\nback()"
                                     % (p, self.jog.ACCEL, self.jog.SPEED))
                t0 = time.monotonic()
                while time.monotonic() - t0 < 30 and not self.cancel.is_set():
                    tcp, _ = self.jog.read_state(self.ip)
                    if all(abs(tcp[i] - pose[i]) < 0.0005 for i in range(3)):
                        break
                    time.sleep(0.1)
                if self.cancel.is_set():
                    return
        except OSError:
            pass

    def _hover_session(self, locate, label: str, then, come_back: bool) -> dict:
        """Lock, preflight, centre over the object, run then(start, over) -> result, and (by default) go back."""
        if not self.lock.acquire(timeout=PHYSICAL_STEP_TIMEOUT_S * 2):
            raise Refused(f"{self.name} is still busy with the previous move")
        start = None
        try:
            self.cancel.clear()
            start = self._preflight()
            err = self._centre_over(locate, label)
            if err:
                return err
            over, _ = self.jog.read_state(self.ip)
            return then(start, over)
        except OSError as e:
            return {"ok": False, "error": f"lost connection to {self.name} ({e.__class__.__name__}); arm stopped"}
        finally:
            if come_back and start is not None and not self.cancel.is_set():
                self._back_to(start)
            self.stop()
            self.lock.release()

    def visit(self, locate, label: str, hold_s: float = 2.0, come_back: bool = True) -> dict:
        """Hover the arm camera over an object the vision server sees, then (by default) go back to the start pose."""
        def then(start, over):
            t_end = time.monotonic() + max(0.0, min(10.0, hold_s))
            while time.monotonic() < t_end:
                if self.cancel.is_set():
                    return {"ok": False, "error": "stopped"}
                time.sleep(0.1)
            res = self._result(f"went over the {label}" + (" and came back" if come_back else ""))
            res["over_tcp_mm"] = [round(v * 1000, 1) for v in over[:3]]
            return res
        return self._hover_session(locate, label, then, come_back)

    def tcp_now(self) -> list:
        tcp, _ = self.jog.read_state(self.ip)
        return tcp

    # ---- contact moves: force-guarded touch and push (physical UR5e, uses its built-in wrist F/T sensor) -------
    @staticmethod
    def rotvec_to_mat(rv):
        import numpy as np
        rv = np.asarray(rv, dtype=float)
        th = float(np.linalg.norm(rv))
        if th < 1e-12:
            return np.eye(3)
        k = rv / th
        K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
        return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * K @ K

    def _tip_over(self, over: list, offset_tool_m: list) -> list:
        """TCP pose that puts the taught touching point where the camera centre was (base XY only, same height)."""
        import numpy as np
        d = self.rotvec_to_mat(over[3:6]) @ np.asarray(offset_tool_m, dtype=float)
        d[2] = 0.0
        if float(np.linalg.norm(d)) > TOUCH_MAX_OFFSET_M:
            raise Refused("the taught touch offset is larger than "
                          f"{TOUCH_MAX_OFFSET_M * 1000:.0f} mm; teach it again")
        return [over[0] + float(d[0]), over[1] + float(d[1]), over[2]] + list(over[3:6])

    def _guarded_down(self, max_mm: float, force_n: float) -> tuple[Optional[dict], float, bool]:
        """Go straight down at TOUCH_SPEED_MPS until the wrist force sensor reads > force_n (contact) or max_mm.
        Runs on the robot (URScript loop at controller rate), so the stop doesn't depend on the network.
        Returns (error or None, mm descended, contact?)."""
        z_start = self.tcp_now()[2]
        script = (
            "def motiq_touch():\n"
            "  zero_ftsensor()\n"
            "  sleep(0.3)\n"
            "  z0 = get_actual_tcp_pose()[2]\n"
            "  going = True\n"
            "  while going:\n"
            f"    if force() > {force_n:.2f} or z0 - get_actual_tcp_pose()[2] > {max_mm / 1000:.4f}:\n"
            "      going = False\n"
            "    else:\n"
            f"      speedl([0, 0, -{TOUCH_SPEED_MPS:.4f}, 0, 0, 0], 0.5, 0.02)\n"
            "    end\n"
            "  end\n"
            "  stopl(2.0)\n"
            "end\n"
            "motiq_touch()")
        self.jog.send_script(script)
        time.sleep(0.8)                      # zero the sensor + start moving
        deadline = time.monotonic() + max_mm / 1000 / TOUCH_SPEED_MPS + 8
        last_z, still_since = self.tcp_now()[2], time.monotonic()
        while True:
            if self.cancel.is_set():
                return {"ok": False, "error": "stopped"}, (z_start - last_z) * 1000, False
            time.sleep(0.1)
            z = self.tcp_now()[2]
            if abs(z - last_z) > 0.0001:
                last_z, still_since = z, time.monotonic()
            elif time.monotonic() - still_since > 0.5:
                break
            if time.monotonic() > deadline:
                return ({"ok": False, "error": "the guarded move down took too long; arm stopped"},
                        (z_start - z) * 1000, False)
        safety = self.jog.dashboard("safetystatus", self.ip)
        down = (z_start - last_z) * 1000
        if "NORMAL" not in safety:
            return ({"ok": False, "error": f"the arm stopped itself on contact ({safety}); clear it on the pendant"},
                    down, True)
        return None, down, down < max_mm - 2

    def touch(self, locate, label: str, offset_tool_m: list, max_down_mm: float, force_n: float,
              come_back: bool = True) -> dict:
        """Hover over the object, put the taught touching point above it, go down slowly until contact (force),
        lift back to the hover height, then go back to the start pose."""
        def then(start, over):
            tip = self._tip_over(over, offset_tool_m)
            err = self._movel(tip)
            if err:
                return err
            log.info("physical touch %s: guarded down <= %.0f mm, stop at %.0f N", label, max_down_mm, force_n)
            err, down, contact = self._guarded_down(max_down_mm, force_n)
            if err:
                return err
            if not contact:
                self._movel(tip)
                return {"ok": False, "error": f"went down {down:.0f} mm and touched nothing; came back up. "
                        "The object may be lower than that (raise max_down_mm) or the touch offset is off"}
            touched_at = self.tcp_now()
            err = self._movel(tip)       # straight back up
            if err:
                return err
            res = self._result(f"touched the {label} ({down:.0f} mm below the hover height)"
                               + (" and came back" if come_back else ""))
            res["touch_tcp_mm"] = [round(v * 1000, 1) for v in touched_at[:3]]
            return res
        return self._hover_session(locate, label, then, come_back)

    def push(self, locate, label: str, offset_tool_m: list, dir_xy: tuple[float, float], footprint_mm: float,
             height_mm: float, distance_mm: float, max_down_mm: float, force_n: float) -> dict:
        """Hover over the object, move beside it (opposite the push direction), find the table with a guarded move
        down, lift a little, push `distance_mm` sideways, lift back to the hover height, go back to the start."""
        ux, uy = dir_xy

        def then(start, over):
            tip = self._tip_over(over, offset_tool_m)
            back = footprint_mm / 2 + PUSH_STANDOFF_MM
            beside = [tip[0] - ux * back / 1000, tip[1] - uy * back / 1000] + tip[2:]
            err = self._movel(beside)
            if err:
                return err
            log.info("physical push %s: beside it at %s mm, finding the table", label,
                     [round(v * 1000, 1) for v in beside[:3]])
            err, down, contact = self._guarded_down(max_down_mm, force_n)
            if err:
                return err
            if not contact:
                self._movel(beside)
                return {"ok": False, "error": f"went down {down:.0f} mm beside the {label} and found no table; "
                        "came back up (raise max_down_mm?)"}
            low = self.tcp_now()
            lift = min(PUSH_HEIGHT_MM, height_mm / 2)
            at = [low[0], low[1], low[2] + lift / 1000] + beside[3:]
            err = self._movel(at)
            if err:
                return err
            travel = PUSH_STANDOFF_MM + distance_mm
            end = [at[0] + ux * travel / 1000, at[1] + uy * travel / 1000, at[2]] + at[3:]
            err = self._movel(end, speed=PUSH_SPEED_MPS)
            if err:
                return err
            err = self._movel(end[:2] + [beside[2]] + end[3:])     # straight up to the hover height
            if err:
                return err
            return self._result(f"pushed the {label} about {distance_mm:.0f} mm and came back")
        return self._hover_session(locate, label, then, True)

    def joints_now_deg(self) -> list[float]:
        _, q = self.jog.read_state(self.ip)
        return [math.degrees(v) for v in q]

    def down_up(self, mm: Any) -> dict:
        try:
            mm = float(mm if mm is not None else PHYSICAL_MAX_MM)
        except (TypeError, ValueError):
            raise Refused("mm must be a number")
        if not 1 <= mm <= PHYSICAL_MAX_MM:
            raise Refused(f"down/up distance must be 1..{PHYSICAL_MAX_MM:.0f} mm")
        if not self.lock.acquire(blocking=False):
            raise Refused(f"{self.name} is already moving")
        try:
            self.cancel.clear()
            start = self._preflight()
            log.info("physical down_up %.0f mm, start z=%.1f mm", mm, start[2] * 1000)
            for dz in (-mm, +mm):
                if self.cancel.is_set():
                    return {"ok": False, "error": "stopped"}
                target = self.jog.move_rel(0, 0, dz)
                t0 = time.monotonic()
                while True:
                    if self.cancel.is_set():
                        return {"ok": False, "error": "stopped"}
                    tcp, _ = self.jog.read_state(self.ip)
                    if all(abs(tcp[i] - target[i]) < 0.0005 for i in range(3)):
                        break
                    if time.monotonic() - t0 > PHYSICAL_STEP_TIMEOUT_S:
                        return {"ok": False, "error": "did not reach the target; arm stopped. Check the pendant ("
                                + self.jog.dashboard("safetystatus", self.ip) + ")"}
                    time.sleep(0.1)
            end, _ = self.jog.read_state(self.ip)
            return {"ok": True, "did": f"moved {mm / 10:g} cm down and back up {self.where}, then stopped",
                    "start_z_mm": round(start[2] * 1000, 1), "end_z_mm": round(end[2] * 1000, 1)}
        except OSError as e:
            return {"ok": False, "error": f"lost connection to {self.name} ({e.__class__.__name__}); arm stopped"}
        finally:
            self.stop()
            self.lock.release()


class Bridge:
    def __init__(self, controller: str, vision: str, scene_path: Path, vision_file: Path,
                 task_timeout_s: float = 180.0, min_conf: float = 0.6, sim: bool = False, sim_arm: str = "UR5e"):
        self.controller = controller.rstrip("/")
        self.vision = vision.rstrip("/")
        self.vision_file = vision_file
        self.task_timeout_s = task_timeout_s
        self.min_conf = min_conf      # same as the controller's safety min_detection_confidence
        self.scene = yaml.safe_load(scene_path.read_text(encoding="utf-8"))
        self.http = httpx.Client(timeout=10.0)
        self.active_task: Optional[str] = None
        self._physical = PhysicalArm(sim, sim_arm)
        # SIM=1 on the UR5e: the physical UR5e as well, for "Run on real arm" (requests with X-Arm-Target: real).
        # Only camera/force moves (locate, touch, push, small steps): the controller drives the simulation only.
        self.real_arm = (PhysicalArm(False) if sim and sim_arm == "UR5e" and os.environ.get("REAL_ARM", "1") == "1"
                         else None)
        # SIM=1 on the UR5e: URSim is started / powered on on demand before every motion (see tool())
        self.ursim = (URSimLauncher(os.environ.get("URSIM_CONTAINER", "ursim-ur5e"),
                                    open_ui=os.environ.get("URSIM_OPEN_UI", "1") == "1")
                      if sim and sim_arm == "UR5e" else None)
        self.directions = load_direction_mapping()
        import threading
        self.search_cancel = threading.Event()   # set by stop: ends an arm-camera search between poses
        self.seen: dict[str, dict] = {}          # last confident sighting per object id (arm camera search)

    # the arm the current request drives: the default one, or the physical arm while a real-target tool runs
    @property
    def physical(self) -> "PhysicalArm":
        return _ARM.get() or self._physical

    @physical.setter
    def physical(self, arm: "PhysicalArm") -> None:
        self._physical = arm

    # ------------------------------------------------------------------ scene helpers
    @property
    def objects(self) -> dict[str, dict]:
        return self.scene.get("objects") or {}

    @property
    def destinations(self) -> dict[str, dict]:
        return self.scene.get("destinations") or {}

    def _labels(self, oid: str) -> set[str]:
        return {oid.lower(), oid.lower().replace("_", " ")} | {
            str(s).lower() for s in self.objects[oid].get("class_labels") or []}

    def resolve_object(self, q: Any) -> str:
        """Registered object id from an id, a spoken name ("orange screw") or a vision track number."""
        s = str(q or "").strip().lower()
        if not s:
            raise Refused("which object? say e.g. 'the orange screw'")
        s = s.removeprefix("the ").strip()
        s = APP_ALIASES.get(s, s)
        if s in self.objects:
            return s
        if s.lstrip("#").isdigit():                       # vision tracker id, e.g. from vision_select_object
            return self._object_from_track(int(s.lstrip("#")))
        for oid in self.objects:
            if s in self._labels(oid):
                return oid
        # loose: "the orange screw please". Longest label wins, so "white bucket lid" -> the lid, not the bucket
        hits = [(len(lbl), oid) for oid in self.objects for lbl in self._labels(oid) if len(lbl) > 2 and lbl in s]
        if hits:
            return max(hits)[1]
        raise Refused(f"'{q}' isn't an object the arm knows. Known: {', '.join(self.objects)}")

    def resolve_destination(self, q: Any) -> str:
        s = str(q or "").strip().lower().removeprefix("the ").strip()
        if not s:
            if len(self.destinations) == 1:
                return next(iter(self.destinations))
            raise Refused(f"where to? Known destinations: {', '.join(self.destinations)}")
        s = APP_ALIASES.get(s, s)
        if s in self.destinations:
            return s
        s = s.removeprefix("into ").removeprefix("in ").removeprefix("the ").strip()
        try:                                   # "blue bucket" / "blue_bucket_01" -> into the bucket
            inside = f"{self.resolve_object(s)}_inside"
            if inside in self.destinations:
                return inside
        except Refused:
            pass
        for d in self.destinations:
            if s in (d, d.replace("_", " ")) or d.replace("_", " ") in s:
                return d
        raise Refused(f"'{q}' isn't a destination the arm knows. Known: {', '.join(self.destinations)}")

    def approach_target(self, name: str) -> str:
        if name in self.objects:
            return self.objects[name]["approach_target"]
        return self.destinations[name]["approach_target"]

    # ------------------------------------------------------------------ vision
    def _vision_names(self, v: dict) -> set[str]:
        label = str(v.get("label") or "").lower()
        name = str(v.get("name") or "").lower()
        color_it = str(v.get("color") or "").lower()
        color_en = COLORS_IT_EN.get(color_it, color_it)
        out = {label, name}
        for c in {color_it, color_en} - {""}:
            for base in {label, name} - {""}:
                out |= {f"{c} {base}", f"{base} {c}", f"{c}_{base}"}
        return out - {""}

    def match_vision(self, v: dict) -> Optional[str]:
        names = self._vision_names(v)
        # prefer an object whose labels include the colour-qualified name, then any label match
        hits = [oid for oid in self.objects if names & self._labels(oid)]
        return hits[0] if hits else None

    def vision_frame(self) -> dict:
        r = self.http.get(f"{self.vision}/state/arm", timeout=3.0)
        r.raise_for_status()
        return r.json()

    def _object_from_track(self, track: int) -> str:
        if not self.vision:
            raise Refused("vision server is not configured, so object numbers mean nothing to the arm")
        frame = self.vision_frame()
        v = next((o for o in frame.get("objects") or [] if o.get("id") == track), None)
        if v is None:
            raise Refused(f"object #{track} isn't visible right now")
        oid = self.match_vision(v)
        if oid is None:
            color = COLORS_IT_EN.get(str(v.get('color') or '').lower(), v.get('color') or '')
            raise Refused(f"#{track} looks like a {color} {v.get('label')}, which the arm "
                          f"has no taught pick for. Known: {', '.join(self.objects)}")
        return oid

    # ------------------------------------------------------------------ ur_visit: vision-guided hover (physical arm)
    def _visit_matcher(self, q: Any):
        """(match(vision_obj) -> bool, readable label) for a registered object ('green ring', 'green_ring_02', a
        track number) or just a colour ('green', 'verde')."""
        if not self.vision:
            raise Refused("visiting an object needs the vision server (the arm camera)")
        s = str(q or "").strip().lower().removeprefix("the ").strip()
        if not s:
            raise Refused("which object? e.g. 'green ring'")
        try:
            oid = self.resolve_object(s)
            return (lambda v: self.match_vision(v) == oid), oid.replace("_", " ")
        except Refused:
            pass
        en_it = {en: it for it, en in COLORS_IT_EN.items()}
        word = s.split()[0]
        it = word if word in COLORS_IT_EN else en_it.get(word)
        if it:
            return (lambda v: str(v.get("color") or "").lower() == it), f"{COLORS_IT_EN[it]} object"
        raise Refused(f"don't know '{q}'. Known: {', '.join(self.objects)} or a colour (green, blue, ...)")

    def _locate(self, match, after_ts: float, timeout_s: float = 4.0) -> Optional[tuple[float, float]]:
        """Bbox centre (normalised) of the best matching vision object, averaged over 2 frames newer than after_ts."""
        while time.time() < after_ts:
            time.sleep(0.05)
        pts, last_id, t0 = [], None, time.time()
        while len(pts) < 2 and time.time() - t0 < timeout_s:
            try:
                f = self.vision_frame()
            except Exception:
                time.sleep(0.2)
                continue
            if f.get("frame_id") == last_id or float(f.get("ts") or 0) < after_ts:
                time.sleep(0.05)
                continue
            last_id = f.get("frame_id")
            hits = [v for v in f.get("objects") or [] if float(v.get("conf") or 0) >= VISIT_MIN_CONF and match(v)]
            if hits:
                x, y, w, h = max(hits, key=lambda v: v.get("conf", 0))["bbox"]
                pts.append((x + w / 2, y + h / 2))
            elif time.time() - t0 > timeout_s / 2:
                break
        if not pts:
            return None
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    def sync_vision(self, need: Optional[str] = None) -> Optional[dict]:
        """Write the current frame (in controller ids) for the controller's vision_file detector.
        Returns the matching vision object for `need`, or raises Refused if it isn't visible."""
        if not self.vision:
            return None
        try:
            frame = self.vision_frame()
        except Exception as e:
            raise Refused(f"can't reach the vision server ({e.__class__.__name__}); not moving without it")
        objs, seen, weak = [], None, None
        for v in frame.get("objects") or []:
            oid = self.match_vision(v)
            if not oid:
                continue
            if float(v.get("conf") or 0) < self.min_conf:      # the controller would reject it and latch a FAULT
                if oid == need and (weak is None or v.get("conf", 0) > weak.get("conf", 0)):
                    weak = v
                continue
            labels = self.objects[oid].get("class_labels") or [oid]
            objs.append({"label": labels[0], "name": oid, "conf": v.get("conf", 0.0), "vision_id": v.get("id"),
                         "color": v.get("color"), "bbox": v.get("bbox")})
            if oid == need and (seen is None or v.get("conf", 0) > seen.get("conf", 0)):
                seen = v
        payload = {"source": "arm", "frame_id": frame.get("frame_id"), "ts": frame.get("ts", time.time()),
                   "objects": objs, "written_by": "motiq_bridge"}
        self.vision_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self.vision_file.parent, delete=False, suffix=".tmp") as f:
            json.dump(payload, f)
        os.replace(f.name, self.vision_file)              # atomic: the controller never reads half a file
        if frame.get("safety_stop"):
            raise Refused(f"vision safety stop: {frame.get('safety_reason') or 'something in the workspace'}")
        if need and seen is None and weak is not None:
            raise Refused(f"the camera thinks it sees the {need.replace('_', ' ')} but is only "
                          f"{float(weak.get('conf') or 0):.0%} sure (needs {self.min_conf:.0%}); "
                          "move it into clear view and try again")
        if need and seen is None:
            raise Refused(f"the camera can't see the {need.replace('_', ' ')} right now")
        return seen

    # ------------------------------------------------------------------ camera on the arm head: search
    # The camera moves with the arm, so "not in view" usually means "not in view from here". Before giving up on a
    # pick the bridge moves the camera through named look poses (scene `search`), all as ordinary controller
    # move_to_target tasks, so every pose goes through the same safety checks as any other move.
    def _search_cfg(self) -> dict:
        s = self.scene.get("search") or {}
        return {"scan_targets": list(s.get("scan_targets") or []), "look_first": s.get("look_first", "object_approach"),
                "settle_s": float(s.get("settle_s", 0.6)), "look_s": float(s.get("look_s", 1.5))}

    @property
    def eye_in_hand(self) -> bool:
        s = self.scene.get("search") or {}
        return (bool(self.vision) or self.physical.sim) and s.get("camera") == "arm_mounted" and bool(s.get("scan_targets"))

    def _look(self, target: Optional[str], seconds: float) -> dict[str, dict]:
        """Move the camera to `target` (None = stay where it is), let it settle, then watch for `seconds`.
        Returns the best confident detection per registered object id, from frames taken after arriving."""
        cfg = self._search_cfg()
        if target:
            r = self.run_task([("move_to_target", {"target_id": target})], f"camera at {target}")
            if not r.get("ok"):
                raise Refused(f"couldn't move the camera to {target.replace('_', ' ')}: {r.get('error')}")
            if self.search_cancel.wait(cfg["settle_s"]):
                raise Refused("stopped")
        if self.physical.sim:
            return self._sim_seen(target)
        arrived = time.time()
        best: dict[str, dict] = {}
        last_fid = None
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.search_cancel.is_set():
                raise Refused("stopped")
            try:
                frame = self.vision_frame()
            except Exception as e:
                raise Refused(f"can't reach the vision server ({e.__class__.__name__}); not moving without it")
            if frame.get("safety_stop"):
                raise Refused(f"vision safety stop: {frame.get('safety_reason') or 'something in the workspace'}")
            fid = frame.get("frame_id")
            if fid != last_fid and float(frame.get("ts") or 0) >= arrived - 0.05:
                last_fid = fid
                for v in frame.get("objects") or []:
                    oid = self.match_vision(v)
                    conf = float(v.get("conf") or 0)
                    if oid and conf >= self.min_conf and conf > best.get(oid, {}).get("conf", 0):
                        best[oid] = {"conf": round(conf, 3), "vision_id": v.get("id"), "bbox": v.get("bbox"),
                                     "color": v.get("color"), "label": v.get("label"),
                                     "seen_from": target or "current pose", "ts": time.time()}
            time.sleep(0.08)
        self.seen.update(best)
        return best

    def _sim_seen(self, target: Optional[str]) -> dict[str, dict]:
        """SIM=1: the objects are RoboDK station items the real camera can't see. The simulated camera reports every
        registered object that isn't in the gripper, clearly marked simulated (the arm still moves to the look pose)."""
        try:
            holding = (self._get("/status").get("robot") or {}).get("holding")
        except Exception:
            holding = None
        best = {oid: {"conf": 1.0, "simulated": True, "label": oid.replace("_", " "),
                      "seen_from": target or "current pose", "ts": time.time()}
                for oid in self.objects if oid != holding}
        self.seen.update(best)
        return best

    def _sim_visit(self, q: Any, hold_s: float, come_back: bool) -> dict:
        """SIM=1 VISIT: hover over the object's taught approach pose in RoboDK, wait, then go home (the controller
        moves between taught targets, so 'back' is home rather than the exact start pose)."""
        oid = self.resolve_object(q)
        name = oid.replace("_", " ")
        r = self.run_task([("move_to_target", {"target_id": self.approach_target(oid)})], f"over the {name}")
        if not r.get("ok"):
            return r
        if self.search_cancel.wait(max(0.0, min(hold_s, 10.0))):
            return {"ok": False, "error": "stopped"}
        if come_back:
            r = self.run_task([("perform_motion", {"motion_id": "go_home"})], "home")
            if not r.get("ok"):
                return r
        return {**r, "ok": True, "simulation": True,
                "did": f"hovered over the {name} in the RoboDK simulation" + (", then went home" if come_back else "")}

    def _look_plan(self, oid: Optional[str]) -> list[str]:
        cfg = self._search_cfg()
        plan = []
        if oid and cfg["look_first"] == "object_approach" and oid in self.objects:
            plan.append(self.objects[oid]["approach_target"])      # straight down at the object's taught spot
        return plan + [t for t in cfg["scan_targets"] if t not in plan]

    def find(self, oid: str) -> dict:
        """Search for one object with the arm camera. Stops at the first pose where it's seen."""
        if not self.vision and not self.physical.sim:
            raise Refused("no vision server configured, so the arm can't look for anything")
        self.search_cancel.clear()
        name = oid.replace("_", " ")
        # (in SIM=1 skip "already in view" so RoboDK shows the arm going to look)
        if not self.physical.sim and not self.active_task and (hit := self._look(None, 0.5).get(oid)):
            return {"ok": True, "did": f"the camera already sees the {name}", "object": oid, "found": hit}
        if not self.eye_in_hand:
            return {"ok": False, "object": oid, "error": f"the camera can't see the {name}, and it isn't on the arm "
                    "(scene search.camera), so the arm can't look around for it"}
        tried = []
        for t in self._look_plan(oid):
            tried.append(t)
            hit = self._look(t, self._search_cfg()["look_s"]).get(oid)
            if hit:
                log.info("found %s from %s (conf %.2f)", oid, t, hit["conf"])
                return {"ok": True, "did": f"found the {name} (looked from {len(tried)} pose(s))", "object": oid,
                        "found": hit, "searched": tried}
        return {"ok": False, "object": oid, "searched": tried,
                "error": f"searched the table from {len(tried)} poses and the camera never saw the {name} "
                         f"(needs {self.min_conf:.0%} confidence). Put it in view and try again."}

    def scan(self) -> dict:
        """Look at the whole table from every scan pose: which of the known objects are there."""
        if not self.vision and not self.physical.sim:
            raise Refused("no vision server configured")
        self.search_cancel.clear()
        found: dict[str, dict] = {}
        plan = self._look_plan(None) if self.eye_in_hand else [None]
        for t in plan:
            for oid, d in self._look(t, self._search_cfg()["look_s"]).items():
                if d["conf"] > found.get(oid, {}).get("conf", 0):
                    found[oid] = d
        missing = [o for o in self.objects if o not in found]
        seen_txt = ", ".join(o.replace("_", " ") for o in found) or "none of the known objects"
        did = f"scanned the table from {len(plan)} pose(s): saw {seen_txt}"
        if missing:
            did += f"; didn't see {', '.join(o.replace('_', ' ') for o in missing)}"
        return {"ok": True, "did": did, "objects": found, "missing": missing}

    def overview(self) -> dict:
        """What the arm camera sees from where the arm is right now. Never moves anything."""
        if not self.vision and not self.physical.sim:
            raise Refused("no vision server configured")
        self.search_cancel.clear()
        found = self._look(None, self._search_cfg()["look_s"])
        missing = [o for o in self.objects if o not in found]
        seen_txt = ", ".join(o.replace("_", " ") for o in found) or "none of the known objects"
        did = f"the camera sees {seen_txt} from here"
        if missing:
            did += f"; not in view: {', '.join(o.replace('_', ' ') for o in missing)}"
        return {"ok": True, "did": did, "objects": found, "missing": missing, "moved": False}

    # ------------------------------------------------------------------ touch / push on the physical arm
    def _touch_offset(self) -> list:
        try:
            return list(json.loads(TOUCH_OFFSET_FILE.read_text())["offset_tool_m"])
        except (OSError, ValueError, KeyError):
            raise Refused("the touch offset isn't taught yet: run touch_teach_start on an object, jog the touching "
                          "part right above it, then touch_teach_save")

    def _contact_args(self, a: dict) -> tuple[float, float]:
        try:
            down = float(a.get("max_down_mm") or TOUCH_MAX_DOWN_MM)
            force = float(a.get("force_n") or TOUCH_FORCE_N)
        except (TypeError, ValueError):
            raise Refused("max_down_mm and force_n must be numbers")
        if not 10 <= down <= TOUCH_MAX_DOWN_CAP_MM:
            raise Refused(f"max_down_mm must be 10..{TOUCH_MAX_DOWN_CAP_MM:.0f}")
        lo, hi = TOUCH_FORCE_RANGE_N
        if not lo <= force <= hi:
            raise Refused(f"force_n must be {lo:g}..{hi:g}")
        return down, force

    def touch_teach_start(self, q: Any) -> dict:
        if self.physical.sim:
            raise Refused("teaching the touch offset needs the real arm and camera (not SIM=1)")
        match, label = self._visit_matcher(q)
        r = self.physical.visit(lambda after: self._locate(match, after), label, hold_s=0, come_back=False)
        if not r.get("ok"):
            return r
        self.touch_teach = {"tcp": self.physical.tcp_now(), "label": label, "ts": time.time()}
        r["did"] = (f"the camera is centred over the {label}. Now use small steps (left/right/forward/back, 1-2 cm) "
                    f"until the part of the arm that should touch is right above the {label} - don't go down onto it "
                    "- then save the touch offset")
        return r

    def touch_teach_save(self) -> dict:
        import numpy as np
        t = getattr(self, "touch_teach", None)
        if not t or time.time() - t["ts"] > 1800:
            raise Refused("run touch_teach_start first (it centres the camera over an object)")
        a, b = t["tcp"], self.physical.tcp_now()
        d_base = np.array([b[0] - a[0], b[1] - a[1], 0.0])
        if np.linalg.norm(d_base) > TOUCH_MAX_OFFSET_M:
            raise Refused(f"the arm moved {np.linalg.norm(d_base) * 1000:.0f} mm sideways since the camera was "
                          f"centred; more than {TOUCH_MAX_OFFSET_M * 1000:.0f} mm is not a camera offset")
        if np.linalg.norm(d_base) < 0.005:
            raise Refused("the arm hasn't moved since the camera was centred; jog the touching part over the "
                          "object first")
        d_tool = PhysicalArm.rotvec_to_mat(a[3:6]).T @ d_base
        TOUCH_OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOUCH_OFFSET_FILE.write_text(json.dumps({
            "offset_tool_m": [round(float(v), 5) for v in d_tool],
            "offset_base_mm_when_taught": [round(float(v) * 1000, 1) for v in d_base[:2]],
            "camera_centred_tcp": a, "tip_over_tcp": b, "object": t["label"],
            "taught_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "note": "TCP-frame XY offset from 'camera centred over the object' to 'touching part over it'"}, indent=2))
        self.touch_teach = None
        return {"ok": True, "did": f"saved the touch offset ({d_base[0] * 1000:+.0f}, {d_base[1] * 1000:+.0f}) mm",
                "moved": False}

    def _sim_touch(self, q: Any, come_back: bool) -> dict:
        """SIM=1 TOUCH: no camera or force sensor needed. Over the object's taught approach pose, down to its grasp
        pose (where the tool meets it), back up, then home - the same taught targets the controller picks with."""
        oid = self.resolve_object(q)
        name = oid.replace("_", " ")
        obj = self.objects[oid]
        steps = [("move_to_target", {"target_id": obj["approach_target"]}),
                 ("move_to_target", {"target_id": obj["grasp_target"]}),
                 ("move_to_target", {"target_id": obj["approach_target"]})]
        if come_back:
            steps.append(("perform_motion", {"motion_id": "go_home"}))
        r = self.run_task(steps, f"touched the {name}")
        if r.get("ok"):
            r.update(simulation=True, did=f"touched the {name} in the RoboDK simulation (down to it and back up)"
                                          + (", then went home" if come_back else ""))
        return r

    def touch(self, q: Any, a: dict) -> dict:
        if self.physical.sim:
            # RoboDK: the camera and the wrist force sensor are only needed on the real arm
            return self._sim_touch(q, a.get("come_back", True) is not False)
        down, force = self._contact_args(a)
        offset = self._touch_offset()
        match, label = self._visit_matcher(q)
        return self.physical.touch(lambda after: self._locate(match, after), label, offset, down, force,
                                   come_back=a.get("come_back", True) is not False)

    def _physical_push(self, oid: str, push: dict, a: dict) -> dict:
        import numpy as np
        down, force = self._contact_args(a)
        offset = self._touch_offset()
        targets = self.scene.get("targets") or {}
        try:
            s = np.array(targets[push["start_target"]]["pose_m_rad"][:2], dtype=float)
            e = np.array(targets[push["end_target"]]["pose_m_rad"][:2], dtype=float)
        except (KeyError, TypeError):
            raise Refused(f"the {oid.replace('_', ' ')} push path has no start/end target to take the direction from")
        u = e - s
        if np.linalg.norm(u) < 1e-6:
            raise Refused("the push path has no direction")
        u /= np.linalg.norm(u)
        dims = (self.objects[oid].get("dimensions_m") or [0.05, 0.05, 0.05])
        match = lambda v: self.match_vision(v) == oid  # noqa: E731
        return self.physical.push(lambda after: self._locate(match, after), oid.replace("_", " "), offset,
                                  (float(u[0]), float(u[1])), max(dims[0], dims[1]) * 1000, dims[2] * 1000,
                                  float(push.get("distance_m", 0.05)) * 1000, down, force)

    def push(self, oid: str, a: Optional[dict] = None) -> dict:
        push = (self.objects.get(oid) or {}).get("push")
        if not push:
            raise Refused(f"the {oid.replace('_', ' ')} can't be pushed (no push path taught for it)")
        if not self.physical.sim:
            # the controller can't move the real UR: push with the camera + force-guarded contact instead
            return self._physical_push(oid, push, a or {})
        self.ensure_visible(oid)
        how = push.get("description") or f"{float(push.get('distance_m', 0)) * 100:g} cm"
        return self.run_task([("identify_object", {"object_id": oid}), ("push_object", {"object_id": oid})],
                             f"pushed the {oid.replace('_', ' ')} {how}")

    def ensure_visible(self, oid: str) -> Optional[dict]:
        """sync_vision for a pick, but with the camera on the arm: if it's not in view, go look for it first."""
        if self.physical.sim:
            # SIM=1: the objects are RoboDK's station items, which the real camera can't see; the controller runs
            # with its (labelled) mock detector in this mode, see start_stack.sh
            return None
        try:
            return self.sync_vision(need=oid)
        except Refused as e:
            msg = str(e)
            if not self.eye_in_hand or "safety stop" in msg or "can't reach the vision" in msg:
                raise
        r = self.find(oid)
        if not r["ok"]:
            raise Refused(r["error"])
        return self.sync_vision(need=oid)   # the arm is now where the camera sees it: write it for the controller

    def check_placed(self, oid: str, dest: str) -> dict:
        """After a place: look at the destination with the arm camera. Inside a container the object can be hidden,
        so there it also counts if the object is gone from its own spot and the gripper is empty."""
        if not self.vision and not self.physical.sim:
            raise Refused("no vision server configured, so the arm can't check")
        self.search_cancel.clear()
        name, where = oid.replace("_", " "), dest.replace("_", " ")
        look_at = "scan_delivery" if dest == "delivery_zone" and "scan_delivery" in (self._search_cfg()["scan_targets"]) \
            else self.destinations[dest]["approach_target"]
        if self.physical.sim:
            self._look(look_at, 0)
            ok = not (self._get("/status").get("robot") or {}).get("holding")
            return ({"ok": True, "simulation": True, "did": f"simulated check: the {name} was released at the {where}"}
                    if ok else {"ok": False, "error": f"simulated check: the {name} is still in the gripper"})
        if self._look(look_at if self.eye_in_hand else None, self._search_cfg()["look_s"]).get(oid):
            return {"ok": True, "did": f"checked with the camera: the {name} is at the {where}"}
        if dest.endswith("_inside") and self.eye_in_hand:
            robot = (self._get("/status").get("robot") or {})
            gone = not self._look(self.objects[oid]["approach_target"], self._search_cfg()["look_s"]).get(oid)
            if gone and not robot.get("holding"):
                return {"ok": True, "did": f"the {name} isn't visible inside the {where.removesuffix(' inside')}, but "
                        "it's gone from its spot and the gripper is empty"}
        return {"ok": False, "error": f"the camera doesn't see the {name} at the {where}"}

    # ------------------------------------------------------------------ controller
    def _get(self, path: str) -> dict:
        r = self.http.get(f"{self.controller}{path}")
        r.raise_for_status()
        return r.json()

    def run_task(self, actions: list[tuple[str, dict]], did: str) -> dict:
        task_id = f"app_{int(time.time() * 1000)}_{secrets.token_hex(2)}"
        task = {"schema_version": "1.0", "task_id": task_id,
                "actions": [{"action_id": f"a{i + 1}", "action": a, "parameters": p}
                            for i, (a, p) in enumerate(actions)]}
        r = self.http.post(f"{self.controller}/tasks", content=json.dumps(task))
        if r.status_code >= 400:
            err = (r.json().get("error") or {}) if r.headers.get("content-type", "").startswith("application/json") else {}
            code = err.get("code", str(r.status_code))
            msg = err.get("message", r.text[:200])
            if code == "FAULT_LATCHED":
                msg += (" An operator must reset it on the robot PC: "
                        "curl -X POST localhost:8765/operator/reset -d '{\"operator\":\"<name>\"}'")
            return {"ok": False, "error": msg, "code": code, "task_id": task_id}

        self.active_task = task_id
        deadline = time.monotonic() + self.task_timeout_s
        try:
            while True:
                st = self._get(f"/tasks/{task_id}")
                if st["status"] in TERMINAL:
                    break
                if time.monotonic() > deadline:
                    self.http.post(f"{self.controller}/tasks/{task_id}/cancel")
                    return {"ok": False, "error": f"timed out after {self.task_timeout_s:.0f}s, task cancelled",
                            "task_id": task_id}
                time.sleep(0.25)
        finally:
            self.active_task = None

        out: dict[str, Any] = {"ok": st["status"] == "SUCCEEDED", "task_id": task_id, "status": st["status"],
                               "state": st["state"], "speed_percent": st.get("speed_percent")}
        if out["ok"]:
            out["did"] = did
            # the controller can't drive the physical UR in this build (mock / RoboDK / URSim backends only):
            # with the bridge on the real arm, never let a controller task read as a real-arm move
            if not self.physical.sim:
                try:
                    h = self._get("/health")
                except Exception:
                    h = {}
                if h.get("simulation", True):
                    out["simulation"], out["physical"] = True, False
                    out["did"] = (f"{did} - SIMULATED ONLY (controller backend {h.get('backend', '?')}); "
                                  "the real arm did not move")
        else:
            e = st.get("error") or {}
            out["error"] = e.get("message") or st["status"].lower()
            out["code"] = e.get("code")
        try:
            robot = self._get("/status").get("robot") or {}
            if robot.get("joints_deg"):
                out["joints_deg"] = robot["joints_deg"]        # app syncs its arm model from this
            out["gripper"] = robot.get("gripper")
            out["holding"] = robot.get("holding")
            if "pick_object" in (a for a, _ in actions) and not out["ok"] and st["state"] == "VERIFYING_GRIP":
                out["object_detected"] = False
        except Exception:
            pass
        return out

    def speed(self, percent: Any = None) -> tuple[int, dict]:
        """Read (percent=None) or set the controller's speed. Returns (http status, body)."""
        if percent is None:
            r = self.http.get(f"{self.controller}/robot/speed")
        else:
            r = self.http.post(f"{self.controller}/robot/speed", json={"percent": percent})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400:
            err = body.get("error") or {}
            return r.status_code, {"ok": False, "error": err.get("message", r.text[:200]), "code": err.get("code")}
        return 200, {"ok": True, **body}

    def stop(self) -> dict:
        res: dict[str, Any] = {"ok": True}
        self.search_cancel.set()
        res["physical_stop_sent"] = self._physical.stop()
        if self.real_arm:
            res["real_arm_stop_sent"] = self.real_arm.stop()
        if self.active_task:
            try:
                self.http.post(f"{self.controller}/tasks/{self.active_task}/cancel", timeout=2.0)
                res["cancelled"] = self.active_task
            except Exception as e:
                res["cancel_error"] = str(e)
        try:
            self.http.post(f"{self.controller}/robot/stop", timeout=2.0).raise_for_status()
        except Exception as e:
            res.update(ok=False, error=f"controller stop failed: {e}")
        return res

    # ------------------------------------------------------------------ tools
    def tool(self, name: str, a: dict, target: str = "") -> dict:
        name = name.lower()
        if target == "real" and self._physical.sim:
            return self._real_tool(name, a)
        if self.ursim and name != "status" and _ARM.get() is None:   # (not for a physical-arm tool)
            self.ursim.ensure()           # SIM=1: start / power on URSim if it isn't ready (raises Refused)
        if name in ("pick", "pick_object"):
            oid = self.resolve_object(a.get("object") or a.get("target"))
            self.ensure_visible(oid)
            return self.run_task([("identify_object", {"object_id": oid}), ("pick_object", {"object_id": oid})],
                                 f"picked up the {oid.replace('_', ' ')}")
        if name in ("place", "place_object"):
            dest = self.resolve_destination(a.get("destination"))
            return self.run_task([("place_object", {"destination_id": dest})], f"placed it at {dest.replace('_', ' ')}")
        if name in ("shake", "shake_object"):
            return self.run_task([("shake_object", self._shake(a))], "shook it")
        if name == "pick_and_place":
            oid = self.resolve_object(a.get("object") or a.get("target"))
            dest = self.resolve_destination(a.get("destination"))
            self.ensure_visible(oid)
            steps = [("identify_object", {"object_id": oid}), ("pick_object", {"object_id": oid})]
            if int(a.get("shake_cycles") or 0) > 0:
                steps.append(("shake_object", self._shake({"cycles": a["shake_cycles"]})))
            steps.append(("place_object", {"destination_id": dest}))
            return self.run_task(steps, f"moved the {oid.replace('_', ' ')} to {dest.replace('_', ' ')}")
        if name in ("find", "find_object", "detect"):
            return self.find(self.resolve_object(a.get("object") or a.get("target")))
        if name in ("scan", "look_around"):
            return self.scan()
        if name in ("overview", "look"):
            return self.overview()
        if name in ("push", "push_object"):
            if self.active_task:
                raise Refused("a controller task is running; wait for it to finish")
            return self.push(self.resolve_object(a.get("object") or a.get("target")), a)
        if name in ("touch", "ur_touch", "touch_teach_start", "touch_teach_save"):
            if self.active_task:
                raise Refused("a controller task is running; wait for it to finish")
            if name == "touch_teach_save":
                return self.touch_teach_save()
            q = a.get("object") or a.get("target")
            return self.touch_teach_start(q) if name == "touch_teach_start" else self.touch(q, a)
        if name == "check_placed":
            oid = self.resolve_object(a.get("object") or a.get("target"))
            return self.check_placed(oid, self.resolve_destination(a.get("destination")))
        if name == "home":
            return self.run_task([("perform_motion", {"motion_id": "go_home"})], "went home")
        if name in ("open_gripper", "release"):
            return self.run_task([("release_object", {})], "opened the gripper")
        if name == "move_above":
            tgt = str(a.get("target") or "")
            try:
                t = self.resolve_object(tgt)
            except Refused:
                t = self.resolve_destination(tgt)
            return self.run_task([("move_to_target", {"target_id": self.approach_target(t)})],
                                 f"moved above the {t.replace('_', ' ')}")
        if name == "ur_down_up":
            if self.active_task:
                raise Refused("a controller task is running; wait for it to finish")
            return self.physical.down_up(a.get("mm"))
        if name in ("ur_visit", "visit"):
            if self.active_task:
                raise Refused("a controller task is running; wait for it to finish")
            if self.physical.sim:
                return self._sim_visit(a.get("object") or a.get("target"), float(a.get("hold_s") or 2.0),
                                       a.get("come_back", True) is not False)
            match, label = self._visit_matcher(a.get("object") or a.get("target"))
            return self.physical.visit(lambda after: self._locate(match, after), label,
                                       hold_s=float(a.get("hold_s") or 2.0),
                                       come_back=a.get("come_back", True) is not False)
        if name in STEP_MOVES or name in ("move_joint", "rotate_yaw"):
            if self.active_task:
                raise Refused("a controller task is running; wait for it to finish")
            if name in STEP_MOVES:
                d = name.removeprefix("move_")
                return self.physical.step(d, a.get("distance_m"), self.directions[d])
            if name == "rotate_yaw":      # spin the gripper = wrist 3 (it turns about the tool flange Z)
                return self.physical.joint(5, "gripper (wrist 3)", float(a.get("angle_deg") or 15))
            j = str(a.get("joint") or "")
            if j not in UR_JOINTS:
                raise Refused(f"unknown joint '{j}'. Joints: {', '.join(UR_JOINTS)}")
            i = UR_JOINTS.index(j)
            if a.get("target_deg") is not None:
                delta = float(a["target_deg"]) - self.physical.joints_now_deg()[i]
            else:
                delta = float(a.get("delta_deg") or 10)
            return self.physical.joint(i, j.replace("_", " "), round(delta, 2))
        if name == "status":
            return {"ok": True, **self._get("/status")}
        if name in REFUSED_PRIMITIVES:
            raise Refused(f"'{name}' isn't available on the real arm. Small steps (move_up/down/left/right/"
                          "forward/backward), move_joint, rotate_yaw, pick, place, shake, pick_and_place, move_above "
                          "and home are.")
        raise Refused(f"unknown tool '{name}'")

    def _real_tool(self, name: str, a: dict) -> dict:
        """SIM=1 stack, "Run on real arm": the tool runs on the PHYSICAL UR5e. Only what the physical arm does on its
        own (camera + force): locate, touch, push, small steps, joints, down-up, camera look. Pick/place/home need the
        controller, which only drives the simulation here (and the real arm has no gripper)."""
        if not self.real_arm:
            raise Refused("no physical arm in this stack (REAL_ARM=0, or the simulated arm isn't the UR5e)")
        if name in ("pick", "pick_object", "place", "place_object", "pick_and_place", "shake", "shake_object", "home",
                    "move_above", "open_gripper", "release", "scan", "look_around", "check_placed"):
            raise Refused(f"'{name}' runs in RoboDK only: the real arm has no gripper and the controller drives the "
                          "simulation. On the real arm: locate, touch, push, small steps and joint moves.")
        token = _ARM.set(self.real_arm)
        try:
            log.info("REAL ARM %s: tool %s %s", self.real_arm.ip, name, a)
            if name in ("find", "find_object", "detect"):
                # no controller look poses on the real arm: look with the camera from where the arm is now
                oid = self.resolve_object(a.get("object") or a.get("target"))
                self.search_cancel.clear()
                hit = self._look(None, self._search_cfg()["look_s"]).get(oid)
                nm = oid.replace("_", " ")
                return ({"ok": True, "did": f"the camera sees the {nm}", "object": oid, "found": hit, "moved": False}
                        if hit else {"ok": False, "object": oid, "error": f"the camera can't see the {nm} from here"})
            return self.tool(name, a)
        finally:
            _ARM.reset(token)

    def _shake(self, a: dict) -> dict:
        patterns = self.scene.get("patterns") or {}
        pid = str(a.get("pattern") or next(iter(patterns), "gentle_shake"))
        return {"pattern_id": pid, "cycles": max(1, min(10, int(a.get("cycles") or 2)))}

    def health(self) -> dict:
        out: dict[str, Any] = {"ok": True, "bridge": "motiq", "model": "unknown (controller unreachable)",
                               "physical_arm": self._physical.status()}
        if self.real_arm:
            out["real_arm"] = self.real_arm.status()
        if self.ursim:
            out["ursim"] = {"auto_start": True, "container": self.ursim.container, "ui": URSIM_UI}
        try:
            h = self._get("/health")
            # model/dof/simulation come from the controller (robot-chooser selection), not a hard-coded UR5e label
            out["model"] = f"{h.get('model', 'unknown')} (robot_controller)"
            out["dof"] = h.get("dof")
            out["simulation"] = h.get("simulation")
            st = self._get("/robot/state")
            out["controller"] = {"backend": h.get("backend"), "state": st.get("executor_state"),
                                 "mode": st.get("mode"), "gripper": st.get("gripper_state"),
                                 "fault": (st.get("latched_error") or {}).get("message")}
            out["speed_percent"] = st.get("speed_percent")
        except Exception as e:
            out.update(ok=False, controller={"error": f"unreachable: {e.__class__.__name__}"})
        if self.vision:
            try:
                r = self.http.get(f"{self.vision}/health", timeout=2.0)
                out["vision"] = {"ok": r.status_code == 200}
            except Exception as e:
                out["vision"] = {"ok": False, "error": e.__class__.__name__}
        else:
            out["vision"] = "not configured"
        out["eye_in_hand"] = self.eye_in_hand
        now = time.time()
        out["seen"] = {oid: {"from": d["seen_from"], "conf": d["conf"], "age_s": round(now - d["ts"], 1)}
                       for oid, d in self.seen.items()}
        return out


class DigitalTwinProxy:
    """Forwards /digital-twin/* to the local twin service. Deliberately holds NO reference to the controller,
    PhysicalArm or any motion path: the only thing it can reach is the twin service's own five routes."""

    ACTIONS = {"check", "snapshot", "start", "stop"}

    def __init__(self, url: str, key: str = "", client: Optional[httpx.Client] = None):
        url = (url or "").rstrip("/")
        if url and not url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("--digital-twin must be a local http://127.0.0.1:<port> URL")
        self.url, self.key = url, key
        self.http = client or httpx.Client(timeout=15.0)

    def call(self, method: str, action: str, body: Optional[dict] = None) -> tuple[int, dict]:
        if action != "status" and action not in self.ACTIONS:
            return 404, {"ok": False, "status": "unknown_action", "reason": f"no digital-twin action {action!r}"}
        if not self.url:
            return 503, {"ok": False, "status": "not_configured", "reason": "bridge started without --digital-twin"}
        try:
            r = self.http.request(method, f"{self.url}/digital-twin/{action}", json=body,
                                  headers={"X-Bridge-Key": self.key})
            try:
                data = r.json()
            except ValueError:
                data = {"ok": False, "status": "bad_reply", "reason": f"HTTP {r.status_code}"}
            return r.status_code, data
        except httpx.HTTPError as e:
            return 503, {"ok": False, "status": "service_unavailable", "state": "DISCONNECTED",
                         "reason": "digital twin service not running (robot_controller: python -m "
                                   f"robot_control.digital_twin.cli serve --config ...): {e.__class__.__name__}"}


def parse_twin_body(body: Any) -> dict:
    """Only {"objects": bool} is accepted; anything else is a 400."""
    if body is None:
        return {}
    if not isinstance(body, dict) or set(body) - {"objects"}:
        raise ValueError('body must be {} or {"objects": true|false}')
    if "objects" in body and not isinstance(body["objects"], bool):
        raise ValueError('"objects" must be true or false')
    return body


class RobotChooserProxy:
    """Forwards the arm catalog routes to the local robot-chooser. Holds NO reference to the controller or any
    motion path: it can only reach these four chooser routes, which edit catalog.yaml / hardware.yaml and
    regenerate the (simulation-only) controller config files."""

    ROUTES = {("GET", "catalog"): "/robots/catalog", ("GET", "active"): "/robots/active",
              ("POST", "select"): "/robots/select", ("POST", "add"): "/api/robots"}
    # fields the app may set when adding an arm; limits / scene / workspace stay in the chooser UI or catalog.yaml
    ADD_FIELDS = {"id", "brand", "model", "dof", "nominal_reach_m", "nominal_payload_kg", "library_url",
                  "library_file", "robodk_item_name", "notes"}

    def __init__(self, url: str, client: Optional[httpx.Client] = None):
        url = (url or "").rstrip("/")
        if url and not url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("--robot-chooser must be a local http://127.0.0.1:<port> URL")
        self.url = url
        self.http = client or httpx.Client(timeout=15.0)

    @classmethod
    def clean_body(cls, action: str, body: Any) -> dict:
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        if action == "select":
            if set(body) != {"robot_id"} or not isinstance(body["robot_id"], str):
                raise ValueError('body must be {"robot_id": "<catalog id>"}')
        elif action == "add":
            extra = set(body) - cls.ADD_FIELDS
            if extra:
                raise ValueError(f"unknown field(s) {sorted(extra)}; allowed: {sorted(cls.ADD_FIELDS)}")
        return body

    def call(self, method: str, action: str, body: Optional[dict] = None) -> tuple[int, dict]:
        path = self.ROUTES.get((method, action))
        if path is None:
            return 404, {"ok": False, "error": {"code": "NOT_FOUND", "message": f"no robots action {action!r}"}}
        if not self.url:
            return 503, {"ok": False, "error": {"code": "NOT_CONFIGURED",
                                                 "message": "bridge started without --robot-chooser"}}
        try:
            r = self.http.request(method, f"{self.url}{path}", json=body)
            try:
                data = r.json()
            except ValueError:
                data = {"ok": False, "error": {"code": "BAD_REPLY", "message": f"HTTP {r.status_code}"}}
        except httpx.HTTPError as e:
            return 503, {"ok": False, "error": {"code": "SERVICE_UNAVAILABLE", "message":
                         "robot-chooser not running (cd robot-chooser && ../robot_controller/.venv/bin/python "
                         f"-m robot_chooser serve): {e.__class__.__name__}"}}
        if r.status_code == 200 and action in ("select", "add"):
            # a mutation returns the chooser's full state; the app only needs the catalog + active report
            data = {"ok": True, "robots": data.get("catalog", []), "report": data.get("report"),
                    "selection": data.get("selection"), "restart_required": True}
        return r.status_code, data


def create_app(bridge: Bridge, key: str, twin: Optional[DigitalTwinProxy] = None,
               chooser: Optional[RobotChooserProxy] = None) -> FastAPI:
    app = FastAPI(title="MOTIQ arm bridge")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if key and request.method != "OPTIONS" and not secrets.compare_digest(
                request.headers.get("x-bridge-key", ""), key):
            return JSONResponse({"ok": False, "error": "bad or missing X-Bridge-Key"}, status_code=401)
        # the app sends X-Require-Sim when it thinks this bridge is SIM=1 (it then skips the real-robot approval):
        # if the bridge is actually on the physical arm, refuse every motion request instead of moving it
        if (request.headers.get("x-require-sim") == "1" and not bridge.physical.sim and request.method == "POST"
                and request.url.path.startswith("/tools/") and request.url.path != "/tools/stop"):
            return JSONResponse({"ok": False, "refused": True,
                                 "error": "this bridge drives the PHYSICAL arm, not the simulation; nothing was "
                                          "sent. Restart the PC stack with SIM=1 for the RoboDK demo."},
                                status_code=409)
        return await call_next(request)

    @app.get("/health")
    def health():
        h = bridge.health()
        return JSONResponse(h, status_code=200 if h["ok"] else 503)

    @app.get("/camera.jpg")
    def camera(raw: bool = False):
        if not bridge.vision:
            return Response(status_code=404)
        try:
            r = bridge.http.get(f"{bridge.vision}/frame/arm.jpg", params={"raw": str(raw).lower()}, timeout=3.0)
            return Response(r.content, media_type="image/jpeg", status_code=r.status_code,
                            headers={"Cache-Control": "no-store"})
        except Exception:
            return Response(status_code=502)

    # sync handlers run in FastAPI's threadpool, so /tools/stop is served while a task is running
    @app.post("/tools/stop")
    def stop():
        return bridge.stop()

    @app.get("/speed")
    def get_speed():
        try:
            code, body = bridge.speed()
        except httpx.HTTPError as e:
            return JSONResponse({"ok": False, "error": f"controller unreachable: {e.__class__.__name__}"}, status_code=502)
        return JSONResponse(body, status_code=code)

    @app.post("/speed")
    async def set_speed(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict) or body.get("percent") is None:
            return JSONResponse({"ok": False, "error": 'body must be {"percent": <number>}'}, status_code=400)
        from starlette.concurrency import run_in_threadpool
        try:
            code, res = await run_in_threadpool(bridge.speed, body["percent"])
        except httpx.HTTPError as e:
            return JSONResponse({"ok": False, "error": f"controller unreachable: {e.__class__.__name__}"}, status_code=502)
        log.info("speed -> %s", res.get("speed_percent", res.get("error")))
        return JSONResponse(res, status_code=code)

    @app.post("/tools/{name}")
    async def tool(name: str, request: Request):
        try:
            args = await request.json()
        except Exception:
            args = {}
        args = args if isinstance(args, dict) else {}
        from starlette.concurrency import run_in_threadpool
        try:
            res = await run_in_threadpool(bridge.tool, name, args, request.headers.get("x-arm-target", ""))
        except Refused as e:
            return JSONResponse({"ok": False, "refused": True, "error": str(e)}, status_code=400)
        except (ValueError, TypeError) as e:
            return JSONResponse({"ok": False, "refused": True, "error": f"bad arguments: {e}"}, status_code=400)
        except httpx.HTTPError as e:
            return JSONResponse({"ok": False, "error": f"controller unreachable: {e.__class__.__name__}"},
                                status_code=502)
        log.info("tool %s %s -> %s", name, args, {k: res.get(k) for k in ("ok", "did", "error")})
        return JSONResponse(res, status_code=200 if res.get("ok") else 409)

    # ---- read-only digital twin (mirror only): separate proxy, never the controller / PhysicalArm / stop
    twin = twin or DigitalTwinProxy("")

    @app.get("/digital-twin/status")
    def twin_status():
        code, res = twin.call("GET", "status")
        return JSONResponse(res, status_code=code)

    @app.post("/digital-twin/{action}")
    async def twin_action(action: str, request: Request):
        raw = await request.body()
        try:
            body = parse_twin_body(json.loads(raw) if raw.strip() else None)
        except ValueError as e:
            return JSONResponse({"ok": False, "status": "bad_request", "reason": str(e)}, status_code=400)
        from starlette.concurrency import run_in_threadpool
        code, res = await run_in_threadpool(twin.call, "POST", action, body)
        log.info("digital-twin %s %s -> %s", action, body, {k: res.get(k) for k in ("ok", "status", "state")})
        return JSONResponse(res, status_code=code)

    # ---- arm catalog (robot-chooser): config files only, never the controller or a motion path
    chooser = chooser or RobotChooserProxy("")

    @app.get("/robots/{action}")
    def robots_get(action: str):
        code, res = chooser.call("GET", action)
        return JSONResponse(res, status_code=code)

    @app.post("/robots/{action}")
    async def robots_post(action: str, request: Request):
        raw = await request.body()
        try:
            body = chooser.clean_body(action, json.loads(raw) if raw.strip() else {})
        except ValueError as e:
            return JSONResponse({"ok": False, "error": {"code": "VALIDATION_ERROR", "message": str(e)}},
                                status_code=400)
        from starlette.concurrency import run_in_threadpool
        code, res = await run_in_threadpool(chooser.call, "POST", action, body)
        log.info("robots %s %s -> %s", action, body.get("robot_id") or body.get("id"), code)
        return JSONResponse(res, status_code=code)

    return app


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--controller", default=os.environ.get("CONTROLLER_URL", "http://127.0.0.1:8765"))
    ap.add_argument("--vision", default=os.environ.get("VISION_URL", ""),
                    help="computer-vision server, e.g. http://127.0.0.1:8000 ('' = no vision)")
    ap.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    ap.add_argument("--vision-file", type=Path, default=DEFAULT_VISION_FILE,
                    help="same path as the controller's --vision-file")
    ap.add_argument("--task-timeout", type=float, default=180.0)
    ap.add_argument("--min-conf", type=float, default=0.6, help="match the controller's min_detection_confidence")
    ap.add_argument("--digital-twin", default=os.environ.get("DIGITAL_TWIN_URL", "http://127.0.0.1:8770"),
                    help="local read-only digital twin service ('' = disabled)")
    ap.add_argument("--robot-chooser", default=os.environ.get("ROBOT_CHOOSER_URL", "http://127.0.0.1:8771"),
                    help="local robot-chooser (arm catalog) service ('' = disabled)")
    ap.add_argument("--sim", action="store_true", default=os.environ.get("SIM", "0") == "1",
                    help="simulation only: step/joint/down-up moves go to URSim (127.0.0.1, mirrored into RoboDK) "
                         "instead of the physical UR (env SIM=1)")
    ap.add_argument("--sim-arm", default=os.environ.get("SIM_ARM", "UR5e"),
                    help="with --sim: model of the simulated arm (robot-chooser selection). Only UR5e runs on URSim; "
                         "any other arm gets named moves only (set by start_stack.sh)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    key = os.environ.get("BRIDGE_KEY", "")
    if args.host not in ("127.0.0.1", "localhost", "::1") and len(key) < 8:
        ap.error("set BRIDGE_KEY (8+ chars) before exposing the bridge on the network; "
                 "the app sends it as EXPO_PUBLIC_BRIDGE_KEY")
    bridge = Bridge(args.controller, args.vision, args.scene, args.vision_file, args.task_timeout, args.min_conf,
                    sim=args.sim, sim_arm=args.sim_arm)
    log.info("controller=%s vision=%s vision_file=%s arm=%s", bridge.controller, bridge.vision or "-",
             bridge.vision_file, (("SIMULATION (URSim -> RoboDK)" if args.sim_arm == "UR5e" else f"SIMULATION ({args.sim_arm} in RoboDK)")
                                     if args.sim else f"physical {bridge.physical.ip}"))
    twin = DigitalTwinProxy(args.digital_twin, key)
    chooser = RobotChooserProxy(args.robot_chooser)
    uvicorn.run(create_app(bridge, key, twin, chooser), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
