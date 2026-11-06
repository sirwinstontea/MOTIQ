"""Teach server: the app sends a demonstration video, YOLO-World (cv_teach.py, computer-vision/yolov8s-worldv2.pt)
turns it into action tokens (what moved where, + Grok speech-to-text of the commentary), Grok rewrites that YOLO
report into a clear task (text only, it never sees the video), and the app writes a MOTIQ skill from it.

    POST /teach?goal=<optional text>   body = the video file (any format ffmpeg reads)
         -> {"ok": true, "summary": "...", "tokens": VideoTokens}
    POST /teach_frames   JSON {frames: [base64 jpeg], fps, audio?: base64, audio_mime?, goal?}  (same answer)
    GET  /health -> {"ok": true, "mock": false}
    GET  /skills          -> {"ok": true, "skills": [SkillDoc]}   learned skills kept on this PC
    POST /skills          body = SkillDoc -> saved as computer-vision/data/skills/learned/<skill id>.json
    DELETE /skills/<id>   -> removes it

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
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
CV = HERE.parent / "computer-vision"
APP_ENV = HERE.parent / "groky-app" / ".env"
sys.path.insert(0, str(CV))

MAX_BYTES = 200 * 1024 * 1024  # 200 MB is plenty for a short demonstration
SKILLS_DIR = CV / "data" / "skills" / "learned"   # every skill the robot learned from a demonstration
MAX_SKILL_BYTES = 2 * 1024 * 1024


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

import cv_teach  # noqa: E402  (computer-vision-to-app/cv_teach.py)

GROK = GrokClient.from_env()  # MockGrok when there is no key (GROK_MOCK=1 forces it)
cv_teach.warm_up(lambda k, m: print(f"[{k}] {m}", flush=True))
LOCK = threading.Lock()  # one video at a time: tokenizing is heavy and calls Grok


def skill_file(sid: str) -> Path | None:
    """File for a skill id; None for anything that isn't a plain id (no paths)."""
    import re
    return SKILLS_DIR / f"{sid}.json" if re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", sid) else None


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
        "same destination, verify, then go home. Keep the same order. The arm's camera is on its head and it knows "
        "four objects: orange screw, blue bucket, green ring, white bucket lid; map what the person handled to the "
        "closest of these. Putting something in a cup/bucket/container means into the blue bucket; putting it to one "
        "side or on a pad means the delivery zone.",
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            return self._send(200, {"ok": True, "mock": GROK.is_mock})
        if path == "/skills":
            skills = []
            for f in sorted(SKILLS_DIR.glob("*.json")) if SKILLS_DIR.exists() else []:
                try:
                    skills.append(json.loads(f.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    print(f"[skills] unreadable {f.name}, skipped", flush=True)
            return self._send(200, {"ok": True, "skills": skills})
        self._send(404, {"ok": False, "error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not path.startswith("/skills/"):
            return self._send(404, {"ok": False, "error": "not found"})
        f = skill_file(unquote(path.removeprefix("/skills/")))
        if f is None:
            return self._send(400, {"ok": False, "error": "bad skill id"})
        existed = f.exists()
        f.unlink(missing_ok=True)
        self._send(200, {"ok": True, "deleted": existed})

    def _save_skill(self, n: int) -> None:
        if not 0 < n <= MAX_SKILL_BYTES:
            return self._send(400, {"ok": False, "error": "empty or too large"})
        try:
            doc = json.loads(self.rfile.read(n))
            sid = doc["skill"]["id"]
            assert isinstance(doc.get("steps"), list)
        except (ValueError, KeyError, TypeError, AssertionError):
            return self._send(400, {"ok": False, "error": "body must be a skill document (skill.id, steps)"})
        f = skill_file(str(sid))
        if f is None:
            return self._send(400, {"ok": False, "error": "bad skill id"})
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
        print(f"[skills] saved {f.name}: {doc['skill'].get('name')}", flush=True)
        self._send(200, {"ok": True, "path": str(f.relative_to(CV))})

    def _as_mp4(self, path: Path, tmp: Path) -> Path:
        """Remux/transcode browser webm / phone mov into a planner-friendly mp4.

        MediaRecorder webm and some phone containers occasionally fail the planner's
        ffmpeg probe ("video non leggibile") even when the bytes arrived intact.
        """
        import subprocess

        from vision.planner.frames import ffmpeg_exe

        if path.suffix.lower() == ".mp4" and path.stat().st_size > 0:
            # still normalise odd pixel sizes / missing audio so probe is stable
            pass
        out = tmp / "demo_norm.mp4"
        cmd = [
            ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(path),
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(out),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
        # retry without audio (silent demos / broken opus tracks)
        cmd_no_a = [
            ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(path),
            "-an", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out),
        ]
        r2 = subprocess.run(cmd_no_a, capture_output=True, text=True)
        if r2.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
        err = (r2.stderr or r.stderr or "").strip()
        # keep the useful ffmpeg lines, drop dynamic-linker noise
        lines = [ln for ln in err.splitlines() if ln.strip() and "libcaca" not in ln and "lib" != ln[:3]]
        tip = " | ".join(lines[-4:])[:280] if lines else "ffmpeg failed"
        raise RuntimeError(f"could not normalise video for learning: {tip}")

    def _learn(self, path: Path, goal: str | None, tmp: str) -> None:
        if not LOCK.acquire(blocking=False):
            return self._send(429, {"ok": False, "error": "already learning another video, try again shortly"})
        try:
            work = Path(tmp)
            if path.stat().st_size < 500:
                return self._send(400, {"ok": False, "error": "video file is empty or too small"})
            try:
                path = self._as_mp4(path, work)
            except Exception as e:
                traceback.print_exc()
                return self._send(400, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            # YOLO-World reads the video; Grok only transcribes the speech and rewrites the YOLO report
            log = lambda k, m: print(f"[{k}] {m}", flush=True)  # noqa: E731
            vt = cv_teach.analyze(path, GROK, goal, log=log)
            vt.video = None  # temp path, meaningless to the app
            report = summarize(vt)
            task = cv_teach.rewrite(GROK, report, log=log)
            summary = f"The task (Grok's reading of the YOLO report):\n{task}\n\nYOLO report:\n{report}" if task else report
            self._send(200, {"ok": True, "mock": GROK.is_mock, "summary": summary,
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

        from vision.planner.frames import ffmpeg_exe  # system ffmpeg, else imageio-ffmpeg

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
            cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", f"{fps:g}",
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
        if url.path == "/skills":
            return self._save_skill(int(self.headers.get("Content-Length") or 0))
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
        # browsers often send "video/webm;codecs=vp9,opus" — only the type matters for the extension
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        ext = {"video/quicktime": ".mov", "video/webm": ".webm", "video/x-matroska": ".webm",
               "video/mp4": ".mp4", "application/mp4": ".mp4"}.get(ctype, ".mp4")
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
            print(f"[teach] received {path.name} ({path.stat().st_size} bytes, {ctype or 'no type'})", flush=True)
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
