#!/usr/bin/env python3
"""Connect the computer-vision server to the MOTIQ app's Cameras tab.

Works on Windows, macOS and Linux. Needs only Python 3.10+ (standard library); it builds the vision environment
for you. Run from anywhere:

    python computer-vision-to-app/run.py setup     # one time: create computer-vision/.venv and install packages
    python computer-vision-to-app/run.py serve     # start the vision server (camera picked automatically)
    python computer-vision-to-app/run.py env       # point groky-app/.env at the server
    python computer-vision-to-app/run.py check     # is the server up, and is it producing frames?
    python computer-vision-to-app/run.py all       # setup if needed + env + serve

See README.md in this folder.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CV = ROOT / "computer-vision"
APP = ROOT / "groky-app"
REQS = [CV / "vision" / "grok" / "requirements.txt", CV / "vision" / "eyes" / "requirements.txt"]
VENV = CV / ".venv"
ENV_KEY = "EXPO_PUBLIC_VISION_SERVER"
ENV_SOURCES = "EXPO_PUBLIC_VISION_SOURCES"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def lan_ip() -> str:
    """The address other devices on the same network can reach this machine on (no traffic is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def run(cmd: list[str], **kw) -> int:
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.call([str(c) for c in cmd], **kw)


# ---------------------------------------------------------------- setup
def ready() -> bool:
    py = venv_python()
    if not py.exists():
        return False
    return subprocess.call([str(py), "-c", "import cv2, fastapi, uvicorn, numpy, pydantic, openai"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def cmd_setup(_: argparse.Namespace) -> int:
    missing = [r for r in REQS if not r.exists()]
    if missing:
        print(f"Can't find {missing[0]}. Run this from a full MOTIQ checkout.")
        return 1
    if not venv_python().exists():
        print(f"Creating {VENV} ...")
        venv.EnvBuilder(with_pip=True).create(VENV)
    py = venv_python()
    run([py, "-m", "pip", "install", "--upgrade", "pip"])
    args: list = []
    for r in REQS:
        args += ["-r", r]
    if run([py, "-m", "pip", "install", *args]) != 0:
        # the RealSense wheel only exists on some platforms/Python versions; the rest works without it
        print("\nSome package failed. Retrying without the optional RealSense driver ...")
        tmps = []
        try:
            args = []
            for i, r in enumerate(REQS):
                lines = [l for l in r.read_text(encoding="utf-8").splitlines() if "pyrealsense2" not in l]
                tmp = HERE / f".requirements-{i}.txt"
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                tmps.append(tmp)
                args += ["-r", tmp]
            if run([py, "-m", "pip", "install", *args]) != 0:
                print("\nInstall failed. Check the error above (Python 3.10-3.12 is the safest choice).")
                return 1
        finally:
            for t in tmps:
                t.unlink(missing_ok=True)
    print("\nSetup done.")
    return 0


# ---------------------------------------------------------------- env
def read_env(path: Path) -> list[str]:
    if path.exists():
        return path.read_text(encoding="utf-8").splitlines()
    example = APP / ".env.example"
    return example.read_text(encoding="utf-8").splitlines() if example.exists() else []


def set_key(lines: list[str], key: str, value: str) -> list[str]:
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    return out


def cmd_env(a: argparse.Namespace) -> int:
    url = a.url or f"http://{lan_ip()}:{a.port}"
    path = APP / ".env"
    if not APP.exists():
        print(f"Can't find {APP}. Add this line to your app's .env yourself:\n  {ENV_KEY}={url}")
        return 1
    lines = set_key(read_env(path), ENV_KEY, url)
    if a.sources:
        lines = set_key(lines, ENV_SOURCES, a.sources)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {ENV_KEY}={url} to {path}")
    print("Restart the app's dev server afterwards (Expo reads .env only at start).")
    return 0


# ---------------------------------------------------------------- check
def get(url: str, timeout: float = 4.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def cmd_check(a: argparse.Namespace) -> int:
    base = (a.url or f"http://127.0.0.1:{a.port}").rstrip("/")
    try:
        health = json.loads(get(f"{base}/health"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"Vision server not reachable at {base} ({e}).\nStart it with:  python computer-vision-to-app/run.py serve")
        return 1
    print(f"Server up: {health}")
    sources = health.get("sources") or ["arm"]
    bad = 0
    for src in sources:
        try:
            jpg = get(f"{base}/frame/{src}.jpg?raw=false", timeout=8)
            ok = jpg[:2] == b"\xff\xd8"
            print(f"  {src}: annotated frame {'OK' if ok else 'NOT A JPEG'} ({len(jpg)//1024} KB)")
            bad += 0 if ok else 1
            st = json.loads(get(f"{base}/state/{src}"))
            print(f"  {src}: {len(st.get('objects', []))} object(s) tracked")
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"  {src}: no frame yet ({e})")
            bad += 1
    if not bad:
        print(f"\nIn the app set {ENV_KEY}={base if a.url else 'http://' + lan_ip() + ':' + str(a.port)} (or run: env)")
    return 1 if bad else 0


# ---------------------------------------------------------------- serve
def cmd_serve(a: argparse.Namespace) -> int:
    if not ready():
        print("The vision environment isn't set up yet, running setup first.\n")
        if cmd_setup(a) != 0:
            return 1
    args = [venv_python(), "-m", "vision.eyes.cli", "run", "--port", str(a.port), "--host", a.host, "--mode", a.mode]
    if a.device is not None:
        args += ["--device", a.device]
    if a.dog_target:
        args += ["--dog-target", a.dog_target]
    args += a.extra
    ip = lan_ip()
    print(
        "\nVision server starting.\n"
        f"  on this computer : http://127.0.0.1:{a.port}\n"
        f"  from other devices: http://{ip}:{a.port}   (same network; allow Python through the firewall if blocked)\n"
        f"  app setting      : {ENV_KEY}=http://{ip}:{a.port}\n"
        f"  live preview     : http://127.0.0.1:{a.port}/stream/arm\n"
        "  Ctrl+C to stop. Mode 'auto' uses a RealSense camera if present, else the first webcam;\n"
        "  use --mode synthetic to try it with no camera at all.\n",
        flush=True,
    )
    try:
        return subprocess.call([str(x) for x in args], cwd=CV)
    except KeyboardInterrupt:
        return 0


def cmd_all(a: argparse.Namespace) -> int:
    if not ready() and cmd_setup(a) != 0:
        return 1
    cmd_env(argparse.Namespace(url=None, port=a.port, sources=None))
    return cmd_serve(a)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def serve_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--port", type=int, default=8000)
        sp.add_argument("--host", default="0.0.0.0")
        sp.add_argument("--mode", default="auto", choices=["auto", "realsense", "webcam", "synthetic", "replay", "stream"])
        sp.add_argument("--device", default=None, help="webcam index (default 0)")
        sp.add_argument("--dog-target", default=None, help="second camera (index or stream URL) to also publish as 'dog'")
        sp.add_argument("extra", nargs="*", help="anything else is passed to the vision server")

    sub.add_parser("setup", help="create computer-vision/.venv and install packages").set_defaults(func=cmd_setup)
    s = sub.add_parser("serve", help="start the vision server")
    serve_opts(s)
    s.set_defaults(func=cmd_serve)
    al = sub.add_parser("all", help="setup if needed, update groky-app/.env, start the server")
    serve_opts(al)
    al.set_defaults(func=cmd_all)
    e = sub.add_parser("env", help=f"write {ENV_KEY} into groky-app/.env")
    e.add_argument("--url", help="server address (default: this computer's network address)")
    e.add_argument("--port", type=int, default=8000)
    e.add_argument("--sources", help="cameras to list in the app, e.g. arm,dog (default: arm)")
    e.set_defaults(func=cmd_env)
    c = sub.add_parser("check", help="test a running server")
    c.add_argument("--url")
    c.add_argument("--port", type=int, default=8000)
    c.set_defaults(func=cmd_check)

    a = p.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
