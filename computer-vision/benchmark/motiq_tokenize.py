"""Metodo A (MOTIQ): VideoTokenizer senza voce, con telemetria.

    cd computer-vision && GROK_REASONING= .venv/bin/python benchmark/motiq_tokenize.py VIDEO OUT.json

hq_mode="on-demand": con "all" (default per video <= 20 s) l'estrazione HQ di ffmpeg fallisce
sui video WhatsApp a frame rate variabile ("Invalid pts"); è un bug di A, segnalato e non corretto.
La cache dei frame viene disattivata (cartella nuova) per misurare la latenza reale.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vision.planner.grok_client import GrokClient, MockGrok  # noqa: E402
from vision.planner.tokenizer import TokenizerConfig, VideoTokenizer  # noqa: E402


def main(video: str, out: str, hq_mode: str = "on-demand") -> None:
    grok = GrokClient.from_env()
    if isinstance(grok, MockGrok):
        sys.exit("Grok in mock: manca XAI_API_KEY")
    grok.reasoning = None  # parità con B, che non manda reasoning
    cache = Path(tempfile.mkdtemp(prefix="motiq_frames_"))
    cfg = TokenizerConfig(use_voice=False, hq_mode=hq_mode, cache_dir=cache)
    tk = VideoTokenizer(grok, cfg)

    t0 = time.time()
    idx, frames_dir = tk.build_index(video)
    t_frames = time.time() - t0
    t1 = time.time()
    vt = tk.tokenize(video)  # build_index ora è in cache
    t_api = time.time() - t1

    kfs = idx.keyframes
    usage = [c.get("usage") for c in grok.calls]
    tot = {}
    for u in usage:
        for k, v in (u or {}).items():
            if isinstance(v, (int, float)):
                tot[k] = tot.get(k, 0) + v
    res = {
        "method": "A_motiq", "video": video, "model": grok.model, "hq_mode": idx.hq_mode,
        "n_images": len(kfs),
        "image_times_s": [round(k.t, 2) for k in kfs],
        "image_reasons": [k.reason for k in kfs],
        "timestamps_sent_to_grok": True,
        "payload_kb": round(sum((frames_dir / k.hq).stat().st_size for k in kfs) / 1024, 1),
        "image_size": f"{idx.width}x{idx.height} (hq_height {cfg.hq_height}, niente upscale)",
        "n_calls": len(grok.calls),
        "motion_threshold": idx.threshold,
        "latency_frames_s": round(t_frames, 2),
        "latency_api_s": round(t_api, 2),
        "latency_s": round(t_frames + t_api, 2),
        "usage_per_call": usage, "usage": tot,
        "coverage_s": max(k.t for k in kfs) if kfs else 0.0,
        "schema_valid": True,
        "tokens": vt.model_dump(mode="json"),
    }
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"A: {len(kfs)} img, {res['payload_kb']} KB, {res['n_calls']} chiamate, "
          f"{res['latency_s']} s, usage={tot}")


if __name__ == "__main__":
    main(*sys.argv[1:])
