"""Esporta i frame di B (sample_video_frames, 2 fps, 640 px) come JPEG + index.json.

    Groky_Planner_Agent/.venv/bin/python computer-vision/benchmark/export_groky_frames.py VIDEO OUT_DIR
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Groky_Planner_Agent"))
from planner.tokenizer import sample_video_frames  # noqa: E402

video, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
frames = sample_video_frames(video, fps=2.0)
idx = []
for f in frames:
    name = f"{f['frame_idx']:06d}.jpg"
    (out / name).write_bytes(base64.b64decode(f["base64"]))
    idx.append({"idx": f["frame_idx"], "t": f["timestamp_s"], "file": name})
(out / "index.json").write_text(json.dumps(idx, indent=1))
print(f"{len(idx)} frame in {out}")
