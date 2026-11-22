"""The launcher's answers (.motiq/config.json) and what they mean for the stack."""
from __future__ import annotations

import copy
import json
import secrets
import socket

from . import paths, secret

DEFAULTS: dict = {
    "version": 1,
    "configured": False,
    "demo": False,
    "robot": "ur_ur5e",                 # robot-chooser catalog id
    "mode": "robodk",                   # app (demo) | robodk | real (= RoboDK, plus the UR arm whenever it is reachable)
    "robot_ip": "",                     # real mode: the UR's IP (URScript port 30002)
    "ur_jog": "",                       # real mode: path to ur_jog.py (the physical UR driver, not in the repo)
    "real_ack": False,                  # real mode: operator confirmed E-stop / safety config
    "gripper": {"kind": "rigid", "mass_kg": None, "tcp_mm": None},
    "camera": {"source": "", "type": "auto", "device": "0", "mount": "cell"},   # source: external | local
    "ai": {"provider": "xai", "key": ""},   # xai | openrouter | none
    "language": "",                     # BCP-47 hint for speech recognition, "" = auto-detect
    "client": "pc",                     # pc | phone | both
    "speed_percent": 10,
    "bridge_key": "",
    "ports": {"vision": 8000, "teach": 8001, "bridge": 8080, "controller": 8765, "chooser": 8771,
              "expo": 8081, "launcher": 8790, "xai_proxy": 8791},
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
    """The config with the xAI key decrypted into cfg['ai']['key'] (memory only; on disk it is ai.key_enc)."""
    try:
        data = json.loads(paths.CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    cfg = _merge(DEFAULTS, data)
    ai = cfg["ai"]
    if ai.get("key_enc"):
        ai["key"] = secret.unprotect(ai["key_enc"])
    ai.pop("key_enc", None)
    if ai.get("provider") not in ("xai", "none"):     # OpenRouter is no longer offered
        ai["provider"] = "xai" if ai.get("key") else "none"
    if len(cfg.get("bridge_key") or "") < 8:
        cfg["bridge_key"] = secrets.token_urlsafe(18)
    if data and "key" in (data.get("ai") or {}) and data["ai"]["key"]:
        save(cfg)                                     # migrate a plain-text key from an older launcher
    return cfg


def save(cfg: dict) -> dict:
    cfg = _merge(DEFAULTS, cfg)
    if len(cfg.get("bridge_key") or "") < 8:
        cfg["bridge_key"] = secrets.token_urlsafe(18)
    disk = copy.deepcopy(cfg)
    disk["ai"]["key_enc"] = secret.protect(disk["ai"].pop("key", "") or "")
    paths.STATE.mkdir(parents=True, exist_ok=True)
    tmp = paths.CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(disk, indent=2), encoding="utf-8")
    secret.restrict(tmp)
    tmp.replace(paths.CONFIG)
    return cfg


def public(cfg: dict) -> dict:
    """What the setup page may see: never the key itself, only whether one is saved and its last 4 characters."""
    out = copy.deepcopy(cfg)
    key = out["ai"].pop("key", "") or ""
    out["ai"]["has_key"] = bool(key)
    out["ai"]["key_hint"] = key[-4:] if len(key) > 10 else ""
    out["ai"]["key"] = ""
    return out


def merge_from_page(current: dict, incoming: dict) -> dict:
    """Apply answers posted by the setup page. An empty key means 'keep the saved one'; clearing it happens only by
    choosing 'No key yet'."""
    new = _merge(current, incoming)
    sent = ((incoming.get("ai") or {}).get("key") or "").strip()
    new["ai"]["key"] = sent or current["ai"].get("key", "")
    if new["ai"].get("provider") == "none":
        new["ai"]["key"] = ""
    for k in ("has_key", "key_hint"):
        new["ai"].pop(k, None)
    return new


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


def arm_reachable(ip: str, port: int = 30002, timeout: float = 1.5) -> bool:
    """True when the UR controller answers on its URScript port, i.e. the real arm is switched on and connected."""
    if not ip:
        return False
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def plan(cfg: dict, robodk_found: bool = False, probe: bool = True) -> dict:
    """What a config turns into: which services run, on which backend, reachable at which address.
    Every non-demo setup runs on RoboDK; a configured real arm is added on top whenever it is reachable."""
    mode = cfg["mode"]
    phone = cfg["client"] in ("phone", "both")
    host = lan_ip() if phone else "127.0.0.1"
    backend = "mock" if mode == "app" else "robodk_sim"
    real_arm = (mode == "real" and bool(cfg.get("real_ack")) and bool(cfg.get("ur_jog"))
                and probe and arm_reachable(cfg.get("robot_ip") or ""))
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
        "real_arm": real_arm,
        "robodk": backend == "robodk_sim",
    }
