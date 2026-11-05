"""Local web UI + JSON API (stdlib only, loopback by default).

  GET  /                      UI
  GET  /api/state             catalog (with status) + hardware + requirements report + selection
  POST /api/select            {"robot_id": "ur_ur3e"}
  POST /api/hardware          partial hardware.yaml, e.g. {"tool": {"mass_kg": 0.4}}
  POST /api/components        add any device {"id","type","name","attached_to","driver","status","spec"}
  POST /api/components/<id>   partial update, e.g. {"spec": {"mass_kg": 0.3}}
  POST /api/components/<id>/delete
  POST /api/robots            add a robot {"id","brand","model","dof","nominal_reach_m","nominal_payload_kg","library_url"}
  POST /api/robots/<id>/delete remove a robot (not the active one)
  POST /api/robots/<id>       {"limits": {...}} | {"library_file": "UR3e.robot"} | {"scene_file": ...} | ...
  POST /api/apply             re-check + regenerate after editing the YAML by hand

Aliases matching the MOTIQ multi-robot contract: GET /robots/catalog, GET /robots/active, POST /robots/select.
Every change rewrites hardware.yaml (requirements block) and the controller config files.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import Chooser, ChooserError

MAX_BODY = 64 * 1024
INDEX = Path(__file__).with_name("static") / "index.html"


def make_handler(ch: Chooser):
    class H(BaseHTTPRequestHandler):
        server_version = "robot-chooser/1"

        def log_message(self, fmt, *args):       # quiet
            pass

        def _send(self, code: int, obj=None, body: bytes | None = None, ctype="application/json"):
            data = body if body is not None else json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _err(self, e: ChooserError):
            self._send(e.http, {"ok": False, "error": e.as_dict()})

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ChooserError("VALIDATION_ERROR", "request body too large", 413)
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                raise ChooserError("VALIDATION_ERROR", "body is not valid JSON", 400)
            if not isinstance(data, dict):
                raise ChooserError("VALIDATION_ERROR", "body must be a JSON object", 400)
            return data

        def do_GET(self):
            try:
                p = self.path.split("?", 1)[0]
                if p in ("/", "/index.html"):
                    return self._send(200, body=INDEX.read_bytes(), ctype="text/html")
                if p == "/components.js":
                    return self._send(200, body=INDEX.with_name("components.js").read_bytes(),
                                      ctype="text/javascript")
                if p == "/api/state":
                    return self._send(200, {"ok": True, **ch.state()})
                if p == "/robots/catalog":
                    return self._send(200, {"ok": True, "robots": ch.state()["catalog"]})
                if p == "/robots/active":
                    s = ch.state()
                    return self._send(200, {"ok": True, "selection": s["selection"], "report": s["report"]})
                self._send(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": p}})
            except ChooserError as e:
                self._err(e)

        def do_POST(self):
            try:
                p = self.path.split("?", 1)[0]
                body = self._body()
                if p in ("/api/select", "/robots/select"):
                    s = ch.select(body.get("robot_id"))
                elif p == "/api/hardware":
                    s = ch.update_hardware(body)
                elif p == "/api/components":
                    s = ch.add_component(body)
                elif p.startswith("/api/components/") and p.endswith("/delete"):
                    s = ch.remove_component(p.split("/")[3])
                elif p.startswith("/api/components/"):
                    s = ch.update_component(p.rsplit("/", 1)[1], body)
                elif p == "/api/robots":
                    s = ch.add_robot(body)
                elif p.startswith("/api/robots/") and p.endswith("/delete"):
                    s = ch.remove_robot(p.split("/")[3])
                elif p.startswith("/api/robots/"):
                    s = ch.update_robot(p.rsplit("/", 1)[1], body)
                elif p == "/api/apply":
                    s = ch.apply()
                else:
                    return self._send(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": p}})
                self._send(200, {"ok": True, **s})
            except ChooserError as e:
                self._err(e)

    return H


def serve(ch: Chooser, host: str = "127.0.0.1", port: int = 8770) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: serving on {host} - anyone on that network can change the hardware files (no auth).")
    httpd = ThreadingHTTPServer((host, port), make_handler(ch))
    print(f"robot-chooser UI on http://{host}:{port}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
