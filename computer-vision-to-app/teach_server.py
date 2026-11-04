"""Teach server: the app sends a demonstration video, the planner (computer-vision/vision/planner) turns it into
action tokens (keyframes + Grok vision + Grok speech-to-text), and the app writes a MOTIQ skill from them.

    POST /teach?goal=<optional text>   body = the video file (any format ffmpeg reads)
         -> {"ok": true, "summary": "...", "tokens": VideoTokens}
    POST /teach_frames   JSON {frames: [base64 jpeg], fps, audio?: base64, audio_mime?, goal?}  (same answer)
    GET  /health -> {"ok": true, "mock": false}

Started by `python computer-vision-to-app/run.py teach` (or `all`), with the computer-vision venv Python.
Only standard library plus the planner's own requirements. It never runs a robot: it only reads the video.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
CV = HERE.parent / "computer-vision"
APP_ENV = HERE.parent / "groky-app" / ".env"
sys.path.insert(0, str(CV))

MAX_BYTES = 200 * 1024 * 1024  # 200 MB is plenty for a short demonstration


def use_app_key() -> None:
    """The planner reads XAI_API_KEY from computer-vision/.env. If only the app has a key, reuse it."""
    if os.getenv("XAI_API_KEY"):
        return
    cv_env = CV / ".env"
    if cv_env.exists() and any(l.startswith("XAI_API_KEY=") and l.split("=", 1)[1].strip()
                               for l in cv_env.read_text(encoding="utf-8").splitlines()):
        return
    if APP_ENV.exists():
        for line in APP_ENV.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "EXPO_PUBLIC_XAI_API_KEY" and v.strip():
                os.environ["XAI_API_KEY"] = v.strip()
                return


use_app_key()

from vision.planner.grok_client import GrokClient  # noqa: E402
from vision.planner.schemas import VideoTokens  # noqa: E402
from vision.planner.tokenizer import TokenizerConfig, VideoTokenizer  # noqa: E402

GROK = GrokClient.from_env()  # MockGrok when there is no key (GROK_MOCK=1 forces it)
LOCK = threading.Lock()  # one video at a time: tokenizing is heavy and calls Grok


def summarize(vt: VideoTokens) -> str:
    """Plain-language version of the tokens, the same shape the app's Learn-from-camera summary uses."""
    lines = []
    for t in vt.tokens:
        objs = ", ".join(f"{o.color + ' ' if o.color else ''}{o.name} ({o.role})" for o in t.objects)
        extra = []
        if t.motion and t.motion.direction != "none":
            extra.append(f"moving {t.motion.direction}" + (f" {t.motion.distance_cm:g} cm" if t.motion.distance_cm else ""))
        if t.rotation and t.rotation.angle_deg:
            extra.append(f"rotating {t.rotation.angle_deg:g}° {t.rotation.axis}")
        if t.speech:
            extra.append(f'said "{t.speech}"')
        lines.append(f"- {t.t_start:.1f}-{t.t_end:.1f}s: {t.verb} [{t.canonical}] {objs}"
                     + (f" ({'; '.join(extra)})" if extra else ""))
    objects = ", ".join(f"{o.color + ' ' if o.color else ''}{o.name}" for o in vt.scene_objects) or "none recognised"
    dur = f"{vt.duration_s:.0f} s " if vt.duration_s else ""
    return "\n".join(filter(None, [
        f"A person recorded a {dur}demonstration video of a task for the robot arm.",
        f"Objects in the scene: {objects}.",
        "Actions, in order:\n" + "\n".join(lines) if lines else "No actions were recognised.",
        f'What they said: "{vt.transcript.strip()}"' if vt.transcript.strip() else "",
        "Write a skill that makes the robot arm reproduce this task: detect each object first, pick, place at the "
        "same destination, verify, then go home. Keep the same order.",
    ]))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/health":
            return self._send(200, {"ok": True, "mock": GROK.is_mock})
        self._send(404, {"ok": False, "error": "not found"})

    def _learn(self, path: Path, goal: str | None, tmp: str) -> None:
        if not LOCK.acquire(blocking=False):
            return self._send(429, {"ok": False, "error": "already learning another video, try again shortly"})
        try:
            cfg = TokenizerConfig(cache_dir=Path(tmp) / "cache")
            tok = VideoTokenizer(GROK, cfg, log=lambda k, m: print(f"[{k}] {m}", flush=True))
            vt = tok.tokenize(path, goal_hint=goal)
            vt.video = None  # temp path, meaningless to the app
            self._send(200, {"ok": True, "mock": GROK.is_mock, "summary": summarize(vt),
                             "tokens": json.loads(vt.model_dump_json())})
        except Exception as e:  # report, don't crash the server
            traceback.print_exc()
            self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        finally:
            LOCK.release()

    def _frames(self, n: int) -> None:
        """POST /teach_frames: JSON {frames: [base64 jpeg], fps, audio?: base64, audio_mime?, goal?}.
        For cameras that are jpeg urls (vision server, robot bridge): the app sends the frames it grabbed and the
        tablet's microphone recording, and they are joined into one video (ffmpeg) before the planner reads it."""
        import base64
        import subprocess

        import imageio_ffmpeg

        try:
            body = json.loads(self.rfile.read(n))
            frames = [base64.b64decode(f) for f in body.get("frames") or []]
        except (ValueError, TypeError):
            return self._send(400, {"ok": False, "error": "bad JSON"})
        if len(frames) < 2:
            return self._send(400, {"ok": False, "error": "need at least 2 frames"})
        fps = max(0.5, min(30.0, float(body.get("fps") or 5)))
        goal = (body.get("goal") or "").strip() or None
        with tempfile.TemporaryDirectory(prefix="motiq-teach-") as tmp:
            d = Path(tmp)
            for i, f in enumerate(frames):
                (d / f"f_{i:05d}.jpg").write_bytes(f)
            cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", f"{fps:g}",
                   "-i", str(d / "f_%05d.jpg")]
            if body.get("audio"):
                ext = ".webm" if "webm" in (body.get("audio_mime") or "") else ".m4a"
                (d / f"audio{ext}").write_bytes(base64.b64decode(body["audio"]))
                cmd += ["-i", str(d / f"audio{ext}"), "-c:a", "aac"]
            # even width/height for yuv420p
            cmd += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(d / "demo.mp4")]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                return self._send(500, {"ok": False, "error": f"ffmpeg: {r.stderr.strip()[-300:]}"})
            self._learn(d / "demo.mp4", goal, tmp)

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/teach_frames":
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_BYTES:
                return self._send(400, {"ok": False, "error": "empty or too large"})
            return self._frames(n)
        if url.path != "/teach":
            return self._send(404, {"ok": False, "error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return self._send(400, {"ok": False, "error": "send the video file as the request body"})
        if n > MAX_BYTES:
            return self._send(413, {"ok": False, "error": "video too large (max 200 MB)"})
        goal = (parse_qs(url.query).get("goal") or [""])[0].strip() or None
        ext = {"video/quicktime": ".mov", "video/webm": ".webm"}.get(self.headers.get("Content-Type", ""), ".mp4")
        with tempfile.TemporaryDirectory(prefix="motiq-teach-") as tmp:
            path = Path(tmp) / f"demo{ext}"
            remaining = n
            with path.open("wb") as f:
                while remaining:
                    chunk = self.rfile.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            if remaining:
                return self._send(400, {"ok": False, "error": "upload was cut off"})
            self._learn(path, goal, tmp)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[teach] {self.address_string()} {fmt % args}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8001)
    a = ap.parse_args()
    print(f"Teach server on http://{a.host}:{a.port}  (Grok {'MOCK: no XAI_API_KEY' if GROK.is_mock else 'live'})",
          flush=True)
    try:
        ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
