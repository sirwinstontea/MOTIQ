"""The launcher's answers (.motiq/config.json) and what they mean for the stack."""
from __future__ import annotations

import copy
import json
import secrets
import socket

from . import paths

DEFAULTS: dict = {
    "version": 1,
    "configured": False,
    "demo": False,
    "robot": "ur_ur5e",                 # robot-chooser catalog id
    "mode": "sim",                      # app | sim | robodk | real
    "robot_ip": "",                     # real mode: the UR's IP (URScript port 30002)
    "ur_jog": "",                       # real mode: path to ur_jog.py (the physical UR driver, not in the repo)
    "real_ack": False,                  # real mode: operator confirmed E-stop / safety config
    "gripper": {"kind": "rigid", "mass_kg": None, "tcp_mm": None},
    "camera": {"type": "auto", "device": "0", "mount": "cell"},
    "ai": {"provider": "xai", "key": ""},   # xai | openrouter | none
    "language": "",                     # BCP-47 hint for speech recognition, "" = auto-detect
    "client": "pc",                     # pc | phone | both
    "speed_percent": 10,
    "bridge_key": "",
    "ports": {"vision": 8000, "teach": 8001, "bridge": 8080, "controller": 8765, "chooser": 8771,
              "expo": 8081, "launcher": 8790},
}

# "I don't know yet": the app with its built-in simulated arm. No Python services, no keys, nothing to plug in.
DEMO = {"configured": True, "demo": True, "mode": "app", "client": "pc", "ai": {"provider": "none", "key": ""}}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    try:
        data = json.loads(paths.CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    cfg = _merge(DEFAULTS, data)
    if len(cfg.get("bridge_key") or "") < 8:
        cfg["bridge_key"] = secrets.token_urlsafe(18)
    return cfg


def save(cfg: dict) -> dict:
    cfg = _merge(DEFAULTS, cfg)
    if len(cfg.get("bridge_key") or "") < 8:
        cfg["bridge_key"] = secrets.token_urlsafe(18)
    paths.STATE.mkdir(parents=True, exist_ok=True)
    tmp = paths.CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(paths.CONFIG)
    return cfg


def demo_config(current: dict | None = None) -> dict:
    return _merge(current or DEFAULTS, DEMO)


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))     # no packet is sent; this just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def plan(cfg: dict, robodk_found: bool = False) -> dict:
    """What a config turns into: which services run, on which backend, reachable at which address."""
    mode = cfg["mode"]
    phone = cfg["client"] in ("phone", "both")
    host = lan_ip() if phone else "127.0.0.1"
    if mode == "robodk":
        backend = "robodk_sim"
    elif mode == "real":
        backend = "robodk_sim" if robodk_found else "mock"
    else:
        backend = "mock"
    services = ["expo"]
    if mode != "app":
        services = ["chooser", "vision", "teach", "controller", "bridge", "expo"]
    cam = cfg["camera"]["type"]
    return {
        "mode": mode,
        "backend": backend,
        "python": mode != "app",
        "services": services,
        "host": host,                                  # what the app uses to reach the services
        "bind": "0.0.0.0" if phone else "127.0.0.1",   # where the services listen
        "vision_mode": {"none": "synthetic"}.get(cam, cam),
        "real_arm": mode == "real",
        "robodk": backend == "robodk_sim",
    }
