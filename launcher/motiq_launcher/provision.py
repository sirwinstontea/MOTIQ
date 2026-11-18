"""Install what a config needs: Python 3.12 + two venvs (via uv), Node.js, the app's npm packages.

Each step is skipped when its inputs (requirements files, package-lock) haven't changed since it last succeeded.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import detect, paths

UV_VERSION = "0.12.19"
NODE_VERSION = "22.12.0"
PY_VERSION = "3.12"
VISION_REQS = [paths.CV / "vision" / d / "requirements.txt" for d in ("grok", "eyes", "planner")]
NO_WINDOW = detect.NO_WINDOW


class Cancelled(Exception):
    pass


class Job:
    """Progress of one setup run, polled by the UI."""

    def __init__(self, steps: list[tuple[str, str]]):
        self.steps = [{"id": i, "label": l, "status": "pending", "detail": ""} for i, l in steps]
        self.lines: list[str] = []
        self.state = "running"        # running | done | failed
        self.error = ""
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        self.logfile = (paths.LOGS / "setup.log").open("a", encoding="utf-8", errors="replace")
        self.log(f"===== setup {time.strftime('%Y-%m-%d %H:%M:%S')}")

    def log(self, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        with self.lock:
            self.lines.append(line)
            del self.lines[:-400]
        self.logfile.write(line + "\n")
        self.logfile.flush()

    def step(self, sid: str, status: str, detail: str = "") -> None:
        with self.lock:
            for s in self.steps:
                if s["id"] == sid:
                    s["status"] = status
                    if detail:
                        s["detail"] = detail
        if detail:
            self.log(f"[{sid}] {detail}")

    def snapshot(self) -> dict:
        with self.lock:
            return {"steps": [dict(s) for s in self.steps], "lines": self.lines[-60:], "state": self.state,
                    "error": self.error}

    def run(self, cmd: list, cwd: Path | None = None, env: dict | None = None) -> None:
        """Run a command, streaming its output into the log. Raises on a non-zero exit."""
        self.log("$ " + " ".join(str(c) for c in cmd))
        p = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", creationflags=NO_WINDOW)
        assert p.stdout
        for line in p.stdout:
            self.log(line)
            if self.cancel.is_set():
                p.kill()
                raise Cancelled()
        if p.wait() != 0:
            raise RuntimeError(f"{Path(str(cmd[0])).name} exited with code {p.returncode}")


# ------------------------------------------------------------------ bookkeeping
def _hash(*files: Path) -> str:
    h = hashlib.sha256()
    for f in files:
        h.update(f.read_bytes() if f.exists() else b"-")
    return h.hexdigest()[:16]


def _done() -> dict:
    try:
        return json.loads(paths.PROVISION_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _mark(key: str, value: str) -> None:
    d = _done()
    d[key] = value
    paths.STATE.mkdir(parents=True, exist_ok=True)
    paths.PROVISION_STATE.write_text(json.dumps(d, indent=2), encoding="utf-8")


def _download(job: Job, url: str, dest: Path) -> None:
    job.log(f"downloading {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "motiq-launcher"})
    with urllib.request.urlopen(req, timeout=60) as r, dest.open("wb") as f:
        total, got, last = int(r.headers.get("content-length") or 0), 0, 0.0
        while chunk := r.read(1 << 16):
            f.write(chunk)
            got += len(chunk)
            if job.cancel.is_set():
                raise Cancelled()
            if total and time.time() - last > 1:
                last = time.time()
                job.log(f"  {got * 100 // total}% of {total // (1 << 20)} MB")


# ------------------------------------------------------------------ tools
def ensure_uv(job: Job) -> str:
    exe = detect.uv()
    if exe:
        return exe
    mach = platform.machine().lower()
    arch = "aarch64" if mach in ("arm64", "aarch64") else "x86_64"
    target = {"nt": f"{arch}-pc-windows-msvc.zip"}.get(os.name) or (
        f"{arch}-apple-darwin.tar.gz" if platform.system() == "Darwin" else f"{arch}-unknown-linux-gnu.tar.gz")
    archive = paths.TOOLS / f"uv-{target}"
    _download(job, f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-{target}", archive)
    name = "uv.exe" if paths.WIN else "uv"
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extract(name, paths.TOOLS)
    else:
        with tarfile.open(archive) as t:
            member = next(m for m in t.getmembers() if m.name.endswith("/uv"))
            member.name = name
            t.extract(member, paths.TOOLS)
        (paths.TOOLS / name).chmod(0o755)
    archive.unlink(missing_ok=True)
    return str(paths.TOOLS / name)


def ensure_node(job: Job) -> str:
    exe = detect.node()
    if exe:
        job.log(f"Node.js: {exe}")
        return exe
    if not paths.WIN:
        raise RuntimeError("Node.js 20+ is needed for the app. Install it from https://nodejs.org and run MOTIQ again.")
    folder = f"node-v{NODE_VERSION}-win-x64"
    archive = paths.TOOLS / f"{folder}.zip"
    _download(job, f"https://nodejs.org/dist/v{NODE_VERSION}/{folder}.zip", archive)
    job.log("unpacking Node.js")
    with zipfile.ZipFile(archive) as z:
        z.extractall(paths.TOOLS)
    shutil.rmtree(paths.TOOLS / "node", ignore_errors=True)
    (paths.TOOLS / folder).rename(paths.TOOLS / "node")
    archive.unlink(missing_ok=True)
    return str(paths.TOOLS / "node" / "node.exe")


def node_env(node_exe: str) -> dict:
    env = dict(os.environ)
    env["PATH"] = str(Path(node_exe).parent) + os.pathsep + env.get("PATH", "")
    return env


def npm_cmd(node_exe: str) -> list[str]:
    """npm via its JS entry point, so it works the same for a PATH install and the launcher's own Node copy."""
    base = Path(node_exe).parent
    for cli in (base / "node_modules/npm/bin/npm-cli.js", base.parent / "lib/node_modules/npm/bin/npm-cli.js"):
        if cli.exists():
            return [node_exe, str(cli)]
    return [shutil.which("npm", path=str(base)) or shutil.which("npm") or "npm"]


# ------------------------------------------------------------------ steps
def _good_venv(py: Path) -> bool:
    if not py.exists():
        return False
    return subprocess.call([str(py), "-c", "import sys; sys.exit(not (3, 10) <= sys.version_info[:2] <= (3, 12))"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=NO_WINDOW) == 0


def make_venv(job: Job, uv: str, venv: Path) -> Path:
    py = paths.venv_python(venv)
    if not _good_venv(py):
        job.run([uv, "venv", "--python", PY_VERSION, "--seed", "--clear", venv])
    return py


def install_controller(job: Job, uv: str) -> None:
    key = _hash(paths.RC / "pyproject.toml")
    py = paths.venv_python(paths.RC / ".venv")
    if _done().get("controller") == key and _good_venv(py):
        job.step("controller", "done", "already installed")
        return
    py = make_venv(job, uv, paths.RC / ".venv")
    job.run([uv, "pip", "install", "--python", py, "-e", f"{paths.RC}[dev,robodk]"])
    _mark("controller", key)


def install_vision(job: Job, uv: str) -> None:
    key = _hash(*VISION_REQS)
    py = paths.venv_python(paths.CV / ".venv")
    if _done().get("vision") == key and _good_venv(py):
        job.step("vision", "done", "already installed")
        return
    py = make_venv(job, uv, paths.CV / ".venv")
    args: list = []
    for r in VISION_REQS:
        args += ["-r", r]
    try:
        job.run([uv, "pip", "install", "--python", py, *args])
    except RuntimeError:
        # the RealSense wheel only exists for some platforms / Python versions; everything else works without it
        job.log("retrying without the optional RealSense driver")
        args = []
        for i, r in enumerate(VISION_REQS):
            lines = [l for l in r.read_text(encoding="utf-8").splitlines() if "pyrealsense2" not in l]
            tmp = paths.STATE / f"vision-req-{i}.txt"
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            args += ["-r", tmp]
        job.run([uv, "pip", "install", "--python", py, *args])
    _mark("vision", key)


def install_app(job: Job, node_exe: str) -> None:
    key = _hash(paths.APP / "package.json", paths.APP / "package-lock.json")
    if _done().get("app") == key and (paths.APP / "node_modules" / "expo").exists():
        job.step("app", "done", "already installed")
        return
    job.run([*npm_cmd(node_exe), "install", "--no-audit", "--no-fund", "--loglevel=error"], cwd=paths.APP,
            env=node_env(node_exe))
    _mark("app", key)


def steps_for(plan: dict) -> list[tuple[str, str]]:
    s = []
    if plan["python"]:
        s += [("python", "Python"), ("controller", "Controller"), ("vision", "Vision")]
    s += [("node", "Node.js"), ("app", "App"), ("configure", "Configure"), ("start", "Start")]
    return s


def provision(job: Job, plan: dict) -> str:
    """Runs every install step for this plan. Returns the Node executable for the stack."""
    if plan["python"]:
        job.step("python", "running", "getting uv and Python 3.12")
        uv = ensure_uv(job)
        job.run([uv, "python", "install", PY_VERSION])
        job.step("python", "done", f"Python {PY_VERSION} ready")
        job.step("controller", "running", "robot controller, bridge and robot-chooser packages")
        install_controller(job, uv)
        job.step("controller", "done")
        job.step("vision", "running", "vision server packages (the first time this is a large download)")
        install_vision(job, uv)
        job.step("vision", "done")
    job.step("node", "running", "looking for Node.js 20+")
    node_exe = ensure_node(job)
    job.step("node", "done", Path(node_exe).name + " ready")
    job.step("app", "running", "app packages (npm install)")
    install_app(job, node_exe)
    job.step("app", "done")
    return node_exe
