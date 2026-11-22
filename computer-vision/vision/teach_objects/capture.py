"""Film an object: grab frames from the running vision server (it owns the camera, so we can't open it here)."""
from __future__ import annotations

import shutil
import time
import urllib.request

from .store import folder, read_meta, say, write_meta


def capture(name: str, s: str, url: str, seconds: float = 20.0, fps: float = 4.0) -> int:
    d = folder(s)
    frames = d / "frames"
    if frames.exists():
        shutil.rmtree(frames)                     # filming again replaces the old take
    for sub in ("previews",):
        shutil.rmtree(d / sub, ignore_errors=True)
    frames.mkdir(parents=True, exist_ok=True)
    meta = read_meta(s)
    meta.update({"name": name.strip(), "status": "filming", "found_ratio": None, "dropped": [], "frames": 0})
    write_meta(s, meta)
    n, end, step = 0, time.time() + seconds, 1.0 / fps
    while time.time() < end:
        t0 = time.time()
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                data = r.read()
            if len(data) > 1000:                   # an empty/placeholder frame is a few bytes
                n += 1
                (frames / f"{n:04d}.jpg").write_bytes(data)
        except OSError:
            pass
        say(stage="filming", progress=round(1 - max(0.0, end - time.time()) / seconds, 3), frames=n)
        time.sleep(max(0.0, step - (time.time() - t0)))
    meta["frames"] = n
    meta["status"] = "filmed" if n else "failed"
    write_meta(s, meta)
    if not n:
        say(stage="error", error="No camera picture came from the vision server.")
        return 1
    return 0
