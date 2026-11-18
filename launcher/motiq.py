#!/usr/bin/env python3
"""MOTIQ launcher: setup wizard, settings and service manager for the whole MOTIQ stack.

    python launcher/motiq.py            first run: setup wizard; afterwards: start MOTIQ and open the app
    python launcher/motiq.py --setup    open the settings / setup screens
    python launcher/motiq.py --stop     stop a running MOTIQ (all services)

Needs only Python 3.10+ (standard library). Everything else (Python 3.12 venvs, Node.js, npm packages) is
installed by the wizard. The Windows installer ships this same launcher as MOTIQ.exe.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from motiq_launcher import config, paths, server, window  # noqa: E402


def _call(port: int, path: str, method: str = "GET") -> dict | None:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"{}" if method == "POST" else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="MOTIQ launcher")
    ap.add_argument("--setup", action="store_true", help="open the settings / setup screens")
    ap.add_argument("--stop", action="store_true", help="stop a running MOTIQ")
    ap.add_argument("--no-window", action="store_true", help="don't open a window, just print the address")
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args()
    if a.no_window:
        os.environ["MOTIQ_NO_WINDOW"] = "1"
    port = a.port or int(config.load()["ports"]["launcher"])

    running = (_call(port, "/api/ping") or {}).get("app") == "motiq-launcher"
    if a.stop:
        if running:
            _call(port, "/api/quit", "POST")
            print("MOTIQ stopped.")
        else:
            print("MOTIQ isn't running.")
        return 0
    if running:                                # one launcher at a time: bring up its window instead
        window.open_launcher(port, "#settings" if a.setup else "")
        return 0

    try:
        httpd, launcher = server.serve(port)
    except OSError as e:
        print(f"Port {port} is in use ({e}). Close the other program or start with --port.")
        return 1
    cfg = config.load()
    print(f"MOTIQ launcher on http://127.0.0.1:{port}  (repo: {paths.ROOT})")
    if cfg.get("configured") and not a.setup:
        launcher.start()                       # configured before: start the stack straight away
    window.open_launcher(port, "#settings" if a.setup else "")

    try:
        while not launcher.quit.wait(2):
            idle = time.time() - launcher.last_seen
            busy = launcher.stack.running() or (launcher.job and launcher.job.state == "running")
            if idle > 120 and not busy:        # window closed and nothing running: done
                break
    except KeyboardInterrupt:
        print("\nStopping MOTIQ ...")
    launcher.stop()
    httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
