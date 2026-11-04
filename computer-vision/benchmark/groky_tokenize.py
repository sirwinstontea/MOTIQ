"""Metodo B (Groky): tokenizzazione identica a planner/agent.py, con telemetria.

Gira con il venv di Groky:
    set -a; source computer-vision/.env; set +a
    Groky_Planner_Agent/.venv/bin/python computer-vision/benchmark/groky_tokenize.py VIDEO OUT.json

Riproduce esattamente il payload di agent.py (stesso system prompt, stesso testo utente,
frames[:10], temperature 0.1, response_format json_object). L'unica differenza è che la
chiamata HTTP è fatta qui per poter leggere `usage`: GrokClient.chat_json di B la scarta.
Nessuna pianificazione.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

GROKY = Path(__file__).resolve().parents[2] / "Groky_Planner_Agent"
sys.path.insert(0, str(GROKY))

import httpx  # noqa: E402

from planner.prompts import TOKENIZER_SYSTEM_PROMPT  # noqa: E402
from planner.schemas import VideoTokens  # noqa: E402
from planner.tokenizer import sample_video_frames  # noqa: E402


def main(video: str, out: str) -> None:
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        sys.exit("XAI_API_KEY mancante: B andrebbe in mock senza dirlo. Esporta il .env.")
    base = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
    model = os.environ.get("XAI_MODEL", "grok-2-vision-1212")

    t0 = time.time()
    frames = sample_video_frames(video, fps=2.0)
    t_frames = time.time() - t0
    sent = frames[:10]
    messages = [
        {"role": "system", "content": TOKENIZER_SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": f"Analyze these {len(frames)} frames and produce human demonstration action tokens."},
            *[{"type": "image_url", "image_url": {"url": f["data_url"]}} for f in sent],
        ]},
    ]
    payload = {"model": model, "messages": messages,
               "response_format": {"type": "json_object"}, "temperature": 0.1}
    t1 = time.time()
    r = httpx.post(f"{base}/chat/completions", json=payload, timeout=120.0,
                   headers={"Authorization": f"Bearer {key}"})
    t_api = time.time() - t1
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    raw = json.loads(content)
    try:
        tokens = VideoTokens.model_validate(raw).model_dump(mode="json")
        valid, err = True, None
    except Exception as e:  # B non ha retry: qui il suo run terminerebbe
        tokens, valid, err = None, False, str(e)[:1000]

    res = {
        "method": "B_groky", "video": video, "model": model,
        "n_frames_sampled": len(frames),
        "n_images": len(sent),
        "image_times_s": [f["timestamp_s"] for f in sent],
        "timestamps_sent_to_grok": False,
        "payload_kb": round(sum(len(f["base64"]) * 3 / 4 for f in sent) / 1024, 1),
        "image_size": "640 px lato lungo, JPEG q80",
        "n_calls": 1,
        "latency_frames_s": round(t_frames, 2),
        "latency_api_s": round(t_api, 2),
        "latency_s": round(t_frames + t_api, 2),
        "usage": data.get("usage"),
        "coverage_s": max(f["timestamp_s"] for f in sent),
        "schema_valid": valid, "schema_error": err,
        "raw_response": raw, "tokens": tokens,
    }
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"B: {len(sent)} img, {res['payload_kb']} KB, {res['latency_s']} s, usage={res['usage']}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
