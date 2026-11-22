"""Turn the launcher's answers into the files MOTIQ already reads: groky-app/.env, computer-vision/.env and the
robot-chooser selection (which regenerates robot_controller/config/*.yaml)."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from . import detect, paths

AI = {"XAI_BASE_URL": "https://api.x.ai/v1", "XAI_MODEL": "grok-4.7"}

# The xAI key is never written into these .env files: groky-app/.env would put it inside the app's JavaScript
# bundle (EXPO_PUBLIC_* are inlined), and computer-vision/.env would keep it in plain text. The Python services get
# it as an environment variable of their own process (secret_env), the app goes through the local xAI proxy.
KEY_VARS = ("EXPO_PUBLIC_XAI_API_KEY", "XAI_API_KEY", "XAI_STT_API_KEY")


def set_env_file(path: Path, example: Path, values: dict[str, str]) -> None:
    """Update KEY=value lines in place; keeps comments and any key the launcher doesn't manage."""
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    elif example.exists():
        lines = example.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    todo = dict(values)
    out = []
    for line in lines:
        k = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and k in todo:
            out.append(f"{k}={todo.pop(k)}")
        else:
            out.append(line)
    if todo:
        out += ["", "# set by the MOTIQ launcher"] + [f"{k}={v}" for k, v in todo.items()]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def app_env(cfg: dict, plan: dict) -> dict[str, str]:
    h, p = plan["host"], cfg["ports"]
    stack = plan["python"]
    ai = cfg["ai"]
    has_key = ai["provider"] == "xai" and bool(ai.get("key"))
    return {
        "EXPO_PUBLIC_XAI_API_KEY": "",
        "EXPO_PUBLIC_XAI_PROXY": f"http://{h}:{p['xai_proxy']}" if has_key else "",
        "EXPO_PUBLIC_GROK_LANGUAGE_HINT": cfg.get("language", ""),
        "EXPO_PUBLIC_ARM_URL": f"http://{h}:{p['bridge']}" if stack else "",
        "EXPO_PUBLIC_BRIDGE_KEY": cfg["bridge_key"] if stack else "",
        "EXPO_PUBLIC_VISION_SERVER": f"http://{h}:{p['vision']}" if stack else "",
        "EXPO_PUBLIC_TEACH_SERVER": f"http://{h}:{p['teach']}" if stack else "",
        "EXPO_PUBLIC_CHOOSER_URL": f"http://{h}:{p['chooser']}" if stack else "",
        "EXPO_PUBLIC_LAUNCHER_URL": f"http://127.0.0.1:{p['launcher']}",
    }


def vision_env(cfg: dict) -> dict[str, str]:
    """computer-vision/.env: everything except the key (also blanks a key an older launcher wrote there)."""
    ai = cfg["ai"]
    blank = {"XAI_API_KEY": "", "XAI_STT_API_KEY": ""}
    if ai["provider"] == "none" or not ai.get("key"):
        return {"GROK_MOCK": "1", **blank}
    return {"GROK_MOCK": "0", **AI, **blank}


def secret_env(cfg: dict) -> dict[str, str]:
    """Environment for the vision and teach server processes only: the key lives in their memory, not on disk."""
    ai = cfg["ai"]
    if ai["provider"] != "xai" or not ai.get("key"):
        return {}
    return {"XAI_API_KEY": ai["key"], "XAI_STT_API_KEY": ai["key"]}   # STT: the planner's demonstration videos


CHOOSER_SNIPPET = r"""
import json, sys
from robot_chooser.core import Chooser, ChooserError
want = json.loads(sys.argv[1])
ch = Chooser()
notes = []
def attempt(label, fn, *a):
    try:
        return fn(*a)
    except ChooserError as e:
        notes.append(f"{label}: {e.message}")
attempt("backend", ch.update_hardware, {"backend": want["backend"], "speed_percent": want["speed_percent"]})
if want.get("tool"):
    attempt("tool", ch.update_component, "main_tool", want["tool"])
attempt("robot", ch.select, want["robot"])
rep = ch.state().get("report") or {}
msg = lambda b: b.get("message", str(b)) if isinstance(b, dict) else str(b)
print(json.dumps({"status": rep.get("status"), "label": rep.get("label"), "notes": notes,
                  "blockers": [msg(b) for b in rep.get("blockers", [])]}))
"""


def tool_patch(cfg: dict) -> dict:
    g = cfg["gripper"]
    kind = g.get("kind") or "rigid"
    spec: dict = {"kind": kind, "actuated": False, "sim_gripper": "symbolic"}   # no real gripper driver exists yet
    if g.get("mass_kg") not in (None, ""):
        spec["mass_kg"] = float(g["mass_kg"])
    if g.get("tcp_mm") not in (None, ""):
        spec["tcp_offset_m_rad"] = [0.0, 0.0, round(float(g["tcp_mm"]) / 1000.0, 4), 0.0, 0.0, 0.0]
    measured = "mass_kg" in spec and "tcp_offset_m_rad" in spec
    names = {"rigid": "Fixed tool (non-actuated)", "parallel": "Parallel gripper", "vacuum": "Vacuum gripper",
             "magnetic": "Magnetic gripper", "angular": "Angular gripper", "other": "Custom tool"}
    return {"name": names.get(kind, "Tool"), "spec": spec, "status": "measured" if measured else "placeholder"}


def chooser(cfg: dict, plan: dict) -> dict:
    want = {"robot": cfg["robot"], "backend": plan["backend"], "speed_percent": float(cfg["speed_percent"]),
            "tool": tool_patch(cfg)}
    r = subprocess.run([str(paths.RC_PY), "-c", CHOOSER_SNIPPET, json.dumps(want)], cwd=str(paths.CHOOSER),
                       capture_output=True, text=True, timeout=120, creationflags=detect.NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("robot-chooser failed: " + (r.stderr.strip().splitlines() or ["?"])[-1])
    return json.loads(r.stdout.strip().splitlines()[-1])


def fingerprint(cfg: dict, plan: dict) -> str:
    return hashlib.sha256(json.dumps([cfg, plan], sort_keys=True, default=str).encode()).hexdigest()[:16]


def write_all(cfg: dict, plan: dict, log=print) -> dict:
    set_env_file(paths.APP / ".env", paths.APP / ".env.example", app_env(cfg, plan))
    log("wrote groky-app/.env")
    result: dict = {"chooser": None}
    if plan["python"]:
        set_env_file(paths.CV / ".env", paths.CV / ".env.example", vision_env(cfg))
        log("wrote computer-vision/.env")
        result["chooser"] = rep = chooser(cfg, plan)
        log(f"robot-chooser: {cfg['robot']} on {plan['backend']} -> {rep.get('label') or rep.get('status')}")
        for n in rep.get("notes", []) + rep.get("blockers", []):
            log(f"  {n}")
    return result
