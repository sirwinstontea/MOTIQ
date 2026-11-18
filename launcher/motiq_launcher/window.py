"""Open pages as app windows (Edge or Chrome with --app, no tabs or address bar); a normal browser tab otherwise."""
from __future__ import annotations

import os
import shutil
import subprocess
import webbrowser
from pathlib import Path

from . import detect, paths


def _browser() -> str:
    pf = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    candidates = [Path(b) / rel for b in pf if b for rel in (
        "Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe")]
    for c in candidates:
        if c.exists():
            return str(c)
    for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name) or ""
    return ""


def open_window(url: str, profile: str, size: tuple[int, int] = (1120, 780)) -> None:
    exe = _browser()
    if exe and os.environ.get("MOTIQ_NO_WINDOW") != "1":
        data = paths.STATE / "browser" / profile      # own profile: remembers mic/camera permission, no tabs
        data.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={data}", f"--window-size={size[0]},{size[1]}",
                          "--no-first-run", "--no-default-browser-check"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=detect.NO_WINDOW)
    elif os.environ.get("MOTIQ_NO_WINDOW") != "1":
        webbrowser.open(url)


def open_launcher(port: int, hash_: str = "") -> None:
    open_window(f"http://127.0.0.1:{port}/{hash_}", "launcher", (1080, 760))


def open_app(url: str) -> None:
    if url:
        open_window(url, "app", (1360, 900))
