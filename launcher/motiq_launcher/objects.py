"""Setup step "Teach your objects": runs computer-vision's `python -m vision.teach_objects` (in the vision venv) and
reports its progress to the setup page. The objects themselves live in computer-vision/data/objects/."""
from __future__ import annotations

import json
import re
import subprocess
import threading
from pathlib import Path

from . import detect, paths

OBJECTS = paths.CV / "data" / "objects"
TRAINED = paths.CV / "data" / "models" / "motiq_objects.onnx"
SLUG = re.compile(r"[a-z0-9_]{1,40}")


def list_objects() -> list[dict]:
    out = []
    if OBJECTS.is_dir():
        for d in sorted(OBJECTS.iterdir()):
            m = d / "meta.json"
            if d.is_dir() and not d.name.startswith("_") and m.exists():
                try:
                    meta = json.loads(m.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                previews = sorted(p.name for p in (d / "previews").glob("*.jpg")) if (d / "previews").is_dir() else []
                out.append({"slug": d.name, **meta, "previews": previews})
    return out


def vision_env() -> dict[str, str]:
    """What the vision server needs to see the taught objects: the trained model, and the names YOLO-World finds."""
    env: dict[str, str] = {}
    if TRAINED.exists():
        env["YOLO_MODEL"] = str(TRAINED)
    ready = [o["name"] for o in list_objects() if o.get("status") == "ready" and o.get("name")]
    if ready:
        env["MOTIQ_OBJECTS"] = ",".join(n.replace(",", " ") for n in ready)
    return env


def set_dropped(slug: str, frame: str, dropped: bool) -> dict:
    """Leave a badly-boxed picture out of training (or put it back)."""
    if not SLUG.fullmatch(slug) or not re.fullmatch(r"\d{4}\.jpg", frame):
        raise ValueError("bad request")
    m = OBJECTS / slug / "meta.json"
    meta = json.loads(m.read_text(encoding="utf-8"))
    s = set(meta.get("dropped") or [])
    (s.add if dropped else s.discard)(frame)
    meta["dropped"] = sorted(s)
    m.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def preview_file(slug: str, frame: str) -> Path | None:
    if not SLUG.fullmatch(slug) or not re.fullmatch(r"\d{4}\.jpg", frame):
        return None
    f = OBJECTS / slug / "previews" / frame
    return f if f.is_file() else None


class ObjectsJob:
    """One teaching command at a time (filming + checking, or training)."""

    def __init__(self):
        self.state: dict = {"cmd": "", "stage": "idle", "progress": 0.0, "error": "", "slug": ""}
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def snapshot(self) -> dict:
        return dict(self.state)

    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def run(self, cmd: str, args: list[str]) -> dict:
        with self.lock:
            if self.busy():
                return {"ok": False, "error": "Something is already running."}
            self.state = {"cmd": cmd, "stage": "starting", "progress": 0.0, "error": "", "slug": ""}
            self.proc = subprocess.Popen([str(paths.CV_PY), "-m", "vision.teach_objects", cmd, *args], cwd=str(paths.CV),
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                         errors="replace", creationflags=detect.NO_WINDOW)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        return {"ok": True}

    def _read(self, proc: subprocess.Popen) -> None:
        log = paths.LOGS / "objects.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        last = ""
        with log.open("a", encoding="utf-8") as fh:
            for line in proc.stdout:              # type: ignore[union-attr]
                fh.write(line)
                line = line.strip()
                if line.startswith("{"):
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if "stage" in msg:
                        self.state.update({k: v for k, v in msg.items() if k in ("stage", "progress", "error", "slug",
                                                                                 "found_ratio", "status", "device")})
                elif line:
                    last = line
        rc = proc.wait()
        if rc and self.state.get("stage") != "error":
            self.state.update(stage="error", error=last[-200:] or f"stopped (code {rc})")
        elif not rc:
            self.state["stage"] = "done"

    def stop(self) -> None:
        if self.busy():
            self.proc.terminate()                 # type: ignore[union-attr]
