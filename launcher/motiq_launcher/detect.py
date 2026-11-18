"""What's on this PC: robots in the catalog, cameras, RoboDK, Node, uv. Standard library only."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import paths

NO_WINDOW = 0x08000000 if paths.WIN else 0


def robots() -> list[dict]:
    """robot-chooser/catalog.yaml, read without PyYAML (the launcher runs before any package is installed)."""
    try:
        text = (paths.CHOOSER / "catalog.yaml").read_text(encoding="utf-8")
    except OSError:
        return []
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^- id:\s*(\S+)", line)
        if m:
            cur = {"id": m.group(1)}
            out.append(cur)
            continue
        m = re.match(r"^  ([a-z_]+):\s*(.*)$", line)
        if cur is not None and m and m.group(2).strip():
            cur[m.group(1)] = m.group(2).strip().strip("'\"")
        m = re.match(r"^    verified:\s*(\S+)", line)
        if cur is not None and m:
            cur["verified"] = m.group(1) == "true"
    for r in out:
        scene = r.get("scene_file", "")
        r["has_scene"] = bool(scene) and any((paths.RC / "config" / d / scene).exists() for d in ("scenes", "."))
        r["has_model"] = bool(r.get("library_file")) and (paths.RC / "stations" / r["library_file"]).exists()
        r["ready"] = bool(r.get("verified")) and r["has_scene"] and r["has_model"]
        r["name"] = f"{r.get('brand', '')} {r.get('model', r['id'])}".strip()
    return out


def cameras() -> list[dict]:
    """Camera-class devices Windows knows about (name only). RealSense shows up as several nodes; keep one."""
    if not paths.WIN:
        return [{"name": p.name, "realsense": False} for p in sorted(Path("/dev").glob("video*"))][:6]
    ps = ("Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPClass -in 'Camera','Image' -and $_.Status -eq 'OK' }"
          " | Select-Object -ExpandProperty Name | ConvertTo-Json")
    try:
        raw = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=15, creationflags=NO_WINDOW).stdout.strip()
        names = json.loads(raw) if raw else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    names = [names] if isinstance(names, str) else names
    out, seen_rs = [], False
    for n in names:
        low = n.lower()
        if re.search(r"printer|scanner|mfp|laserjet|officejet|\bir camera\b|infrared", low):
            continue                       # scanners register as Image devices; IR cams are Windows Hello only
        rs = "realsense" in low
        if rs and seen_rs:
            continue
        seen_rs |= rs
        out.append({"name": re.sub(r"\s+(RGB|Depth)$", "", n) if rs else n, "realsense": rs})
    return out


def robodk() -> str:
    for p in (Path("C:/RoboDK/bin/RoboDK.exe"), Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "RoboDK/bin/RoboDK.exe",
              Path.home() / "RoboDK" / "RoboDK-Start.sh", Path("/Applications/RoboDK.app")):
        if p.exists():
            return str(p)
    return ""


def _version(exe: str) -> str:
    try:
        return subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15,
                              creationflags=NO_WINDOW).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def node() -> str:
    """A Node.js 20+ executable: the launcher's own copy first, then PATH."""
    own = paths.TOOLS / "node" / ("node.exe" if paths.WIN else "bin/node")
    for exe in (str(own) if own.exists() else "", shutil.which("node") or ""):
        if exe:
            m = re.match(r"v(\d+)", _version(exe))
            if m and int(m.group(1)) >= 20:
                return exe
    return ""


def uv() -> str:
    """uv (Python + package installer): bundled by the installer, downloaded by the launcher, or on PATH."""
    name = "uv.exe" if paths.WIN else "uv"
    for p in (paths.ROOT / "tools" / name, paths.TOOLS / name):
        if p.exists():
            return str(p)
    return shutil.which("uv") or ""


def ur_jog_default() -> str:
    p = paths.ROOT.parent / "ur_manual" / "ur_jog.py"
    return str(p) if p.exists() else ""


def summary() -> dict:
    return {"robots": robots(), "cameras": cameras(), "robodk": robodk(), "docker": bool(shutil.which("docker")),
            "node": node(), "uv": uv(), "ur_jog": ur_jog_default(), "windows": paths.WIN}
