"""The launcher's local web server: serves the wizard UI and a small JSON API. Listens on 127.0.0.1 only, because
it holds API keys and can start processes."""
from __future__ import annotations

import json
import mimetypes
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import config, configure, detect, paths, provision, window
from .stack import Stack

VERSION = "1.0.0"


class Launcher:
    def __init__(self):
        self.stack = Stack()
        self.job: provision.Job | None = None
        self.detected: dict = {}
        self.detected_at = 0.0
        self.last_seen = time.time()
        self.lock = threading.Lock()
        self.quit = threading.Event()

    def detect(self, refresh: bool = False) -> dict:
        if refresh or not self.detected or time.time() - self.detected_at > 60:
            self.detected = detect.summary()
            self.detected_at = time.time()
        return self.detected

    def state(self, refresh: bool = False) -> dict:
        cfg = config.load()
        det = self.detect(refresh)
        plan = config.plan(cfg, bool(det.get("robodk")))
        return {"version": VERSION, "config": cfg, "detected": det, "plan": plan,
                "job": self.job.snapshot() if self.job else None,
                "running": self.stack.running(), "services": self.stack.status(),
                "app_url": self.stack.app_url, "phone_url": self.stack.phone_url,
                "lan_ip": config.lan_ip(), "root": str(paths.ROOT)}

    # -------------------------------------------------------------- setup + start
    def start(self) -> dict:
        with self.lock:
            if self.job and self.job.state == "running":
                return {"ok": True, "already": True}
            cfg = config.load()
            plan = config.plan(cfg, bool(self.detect().get("robodk")))
            job = self.job = provision.Job(provision.steps_for(plan))
        threading.Thread(target=self._run, args=(job, cfg, plan), daemon=True).start()
        return {"ok": True}

    def _run(self, job: provision.Job, cfg: dict, plan: dict) -> None:
        current = "python"
        try:
            node_exe = provision.provision(job, plan)
            current = "configure"
            job.step("configure", "running", "writing the app, vision and controller settings")
            result = configure.write_all(cfg, plan, job.log)
            rep = result.get("chooser") or {}
            if rep.get("status") == "BLOCKED":
                raise RuntimeError("robot-chooser says this arm isn't ready: " +
                                   "; ".join(rep.get("blockers") or rep.get("notes") or ["see the setup log"]))
            fp = configure.fingerprint(cfg, plan)
            done = provision._done()
            job.step("configure", "done")
            current = "start"
            job.step("start", "running", "starting the MOTIQ services")
            self.stack.start(cfg, plan, node_exe, clear_cache=done.get("applied") != fp, log=job.log)
            provision._mark("applied", fp)
            job.step("start", "done", "MOTIQ is running")
            job.state = "done"
            window.open_app(self.stack.app_url)
        except provision.Cancelled:
            job.error, job.state = "Setup was cancelled.", "failed"
            job.step(current, "failed")
        except Exception as e:  # shown in the UI, full detail in .motiq/logs/setup.log
            failed = next((s["id"] for s in job.steps if s["status"] == "running"), current)
            job.step(failed, "failed")
            job.error, job.state = str(e), "failed"
            job.log(f"ERROR: {e}")

    def stop(self) -> None:
        if self.job and self.job.state == "running":
            self.job.cancel.set()
        self.stack.stop()


def browse_file(title: str) -> str:
    """Native Windows file picker (the browser can't hand a real path to the page)."""
    if not paths.WIN:
        return ""
    ps = ("Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.OpenFileDialog; "
          f"$d.Title = '{title}'; $d.Filter = 'Python file (*.py)|*.py|All files (*.*)|*.*'; "
          "if ($d.ShowDialog() -eq 'OK') { $d.FileName }")
    try:
        return subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True,
                              timeout=600, creationflags=detect.NO_WINDOW).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def make_handler(launcher: Launcher):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):   # quiet
            pass

        def _json(self, data, code: int = 200) -> None:
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _local_origin(self) -> bool:
            # refuse writes from other websites open in the same browser (no CORS, but a form POST would still land)
            origin = self.headers.get("Origin") or ""
            return not origin or urlparse(origin).hostname in ("127.0.0.1", "localhost")

        def do_GET(self):
            launcher.last_seen = time.time()
            u = urlparse(self.path)
            if u.path == "/api/ping":
                return self._json({"ok": True, "app": "motiq-launcher"})
            if u.path == "/api/state":
                return self._json(launcher.state(refresh="refresh" in u.query))
            if u.path == "/api/logs":
                name = u.query.split("name=")[-1] if "name=" in u.query else "setup"
                return self._json({"lines": launcher.stack.logs(name)})
            rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            f = (paths.UI / rel).resolve()
            if paths.UI.resolve() not in f.parents or not f.is_file():
                f = paths.UI / "index.html"           # single-page app: every other path is the wizard
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            launcher.last_seen = time.time()
            if not self._local_origin():
                return self._json({"ok": False, "error": "forbidden"}, 403)
            p = urlparse(self.path).path
            try:
                b = self._body()
            except ValueError:
                return self._json({"ok": False, "error": "bad json"}, 400)
            if p == "/api/config":
                cfg = config.save({**config.load(), **(b.get("config") or {})})
                return self._json({"ok": True, "config": cfg})
            if p == "/api/demo":
                config.save(config.demo_config(config.load()))
                return self._json(launcher.start())
            if p == "/api/start":
                return self._json(launcher.start())
            if p == "/api/stop":
                launcher.stop()
                return self._json({"ok": True})
            if p == "/api/open":
                window.open_app(launcher.stack.app_url or f"http://localhost:{config.load()['ports']['expo']}")
                return self._json({"ok": True})
            if p == "/api/browse":
                return self._json({"path": browse_file(b.get("title") or "Choose a file")})
            if p == "/api/quit":
                launcher.stop()
                launcher.quit.set()
                return self._json({"ok": True})
            return self._json({"ok": False, "error": "not found"}, 404)

    return Handler


def serve(port: int) -> tuple[ThreadingHTTPServer, Launcher]:
    launcher = Launcher()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(launcher))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, launcher
