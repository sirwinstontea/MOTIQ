"""Where everything lives. Works from a git checkout (python launcher/motiq.py) and from the installed MOTIQ.exe."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def repo_root() -> Path:
    if getattr(sys, "frozen", False):              # MOTIQ.exe sits in the install folder, next to groky-app/ etc.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]     # launcher/motiq_launcher/paths.py -> repo root


ROOT = repo_root()
UI = ROOT / "launcher" / "ui"
STATE = ROOT / ".motiq"                    # launcher data: config, logs, downloaded tools (gitignored)
CONFIG = STATE / "config.json"
PROVISION_STATE = STATE / "provisioned.json"
LOGS = STATE / "logs"
TOOLS = STATE / "tools"

APP = ROOT / "groky-app"
CV = ROOT / "computer-vision"
RC = ROOT / "robot_controller"
CHOOSER = ROOT / "robot-chooser"
BRIDGE = ROOT / "bridge" / "motiq_bridge.py"
TEACH = ROOT / "computer-vision-to-app" / "teach_server.py"

WIN = os.name == "nt"


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if WIN else "bin/python")


RC_PY = venv_python(RC / ".venv")
CV_PY = venv_python(CV / ".venv")
