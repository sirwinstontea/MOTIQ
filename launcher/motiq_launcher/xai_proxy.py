"""Local xAI proxy, so the MOTIQ app never holds the xAI key.

The app (groky-app) sends its Grok calls here instead of to api.x.ai, with the launcher's bridge key in an
X-Motiq-Key header. The proxy checks it, adds `Authorization: Bearer <xAI key>` and forwards the request.
Only the three endpoints the app uses are forwarded. The key stays in this process's memory; it is never written
to groky-app/.env, so it is not inlined into the app's JavaScript bundle either (Expo inlines EXPO_PUBLIC_*)."""
from __future__ import annotations

import hmac
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://api.x.ai"
ALLOWED = {"/v1/responses", "/v1/realtime/client_secrets", "/v1/chat/completions"}
MAX_BODY = 20 * 1024 * 1024        # camera frames are sent as base64 JPEGs


def _handler(xai_key: str, bridge_key: str, origins: set[str]):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):    # never log request bodies or headers
            pass

        def _cors(self) -> None:
            origin = self.headers.get("Origin") or ""
            if origin in origins:                  # only the MOTIQ app's own pages, not any website in the browser
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Motiq-Key")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Vary", "Origin")

        def _reply(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            if self.path == "/health":
                return self._reply(200, b'{"ok": true}')
            return self._reply(404, b'{"error": "not found"}')

        def do_POST(self):
            if self.path not in ALLOWED:
                return self._reply(404, b'{"error": "not found"}')
            if not hmac.compare_digest(self.headers.get("X-Motiq-Key", ""), bridge_key):
                return self._reply(401, b'{"error": "bad MOTIQ key"}')
            if not xai_key:
                return self._reply(400, json.dumps({"error": "No xAI key saved. Add one in MOTIQ Settings."}).encode())
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._reply(413, b'{"error": "request too large"}')
            body = self.rfile.read(n) if n else b"{}"
            req = urllib.request.Request(UPSTREAM + self.path, data=body, method="POST", headers={
                "Authorization": f"Bearer {xai_key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return self._reply(r.status, r.read(), r.headers.get("Content-Type", "application/json"))
            except urllib.error.HTTPError as e:
                return self._reply(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
            except OSError as e:
                return self._reply(502, json.dumps({"error": f"could not reach xAI: {e.__class__.__name__}"}).encode())

    return H


class XaiProxy:
    def __init__(self):
        self.httpd: ThreadingHTTPServer | None = None

    def start(self, bind: str, port: int, xai_key: str, bridge_key: str, origins: set[str]) -> None:
        self.stop()
        self.httpd = ThreadingHTTPServer((bind, port), _handler(xai_key, bridge_key, origins))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def running(self) -> bool:
        return self.httpd is not None
