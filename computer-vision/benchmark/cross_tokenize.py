"""Test 2: incrocio  insieme di frame  x  protocollo di prompt, stesso modello Grok.

Frame set (preparati prima, JPEG + index.json con idx/t/reason):
  A_motion  : keyframe di A (movimento LQ, 848x480)
  B_trunc   : frame di B a 2 fps, solo i primi 10 (come agent.py)
  B_full    : frame di B a 2 fps, tutti
Protocolli:
  pA : prompt di A (TOKENIZE_SYSTEM + tokenize_user con timestamp), chunk da 8 con contesto,
       merge_tokens, temperature 0.2, GrokClient di A (1 retry JSON)
  pB : prompt di B (TOKENIZER_SYSTEM_PROMPT), 1 sola chiamata con tutte le immagini del set,
       nessun timestamp, temperature 0.1, nessun retry

    cd computer-vision && .venv/bin/python benchmark/cross_tokenize.py FRAMESET_DIR PROTO OUT.json [N_DICHIARATI]
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx  # noqa: E402

from vision.planner.grok_client import GrokClient, MockGrok  # noqa: E402
from vision.planner.prompts import TOKENIZE_SYSTEM, tokenize_user  # noqa: E402
from vision.planner.schemas import TokenChunkResponse  # noqa: E402
from vision.planner.tokenizer import merge_tokens  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "groky_prompts", Path(__file__).resolve().parents[2] / "Groky_Planner_Agent/planner/prompts.py")
_gp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gp)  # type: ignore[union-attr]
GROKY_SYSTEM = _gp.TOKENIZER_SYSTEM_PROMPT


def sum_usage(us: list[dict]) -> dict:
    tot: dict = {}
    for u in us:
        for k, v in (u or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                tot[k] = tot.get(k, 0) + v
        rt = ((u or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
        if rt:
            tot["reasoning_tokens"] = tot.get("reasoning_tokens", 0) + rt
    return tot


def run_pA(frames: list[dict], d: Path) -> tuple[list[dict], dict, int]:
    grok = GrokClient.from_env()
    if isinstance(grok, MockGrok):
        sys.exit("mock")
    grok.reasoning = None
    tokens = []
    for c in range(0, len(frames), 8):
        chunk = frames[c:c + 8]
        fd = [{"idx": f["idx"], "t": f["t"], "reason": f.get("reason", "uniform_2fps")} for f in chunk]
        prev = [t.model_dump(exclude={"frame_idx"}) for t in tokens[-3:]]
        imgs = [(d / f["file"]).read_bytes() for f in chunk]
        resp = grok.chat_json("tokenize", TOKENIZE_SYSTEM, tokenize_user(fd, [], prev, None),
                              TokenChunkResponse, images=imgs)
        tokens.extend(resp.tokens)
    tokens = merge_tokens(tokens)
    norm = [{"t_start": t.t_start, "t_end": t.t_end, "canonical": t.canonical, "verb": t.verb,
             "objects": [{"name": o.name, "role": o.role} for o in t.objects],
             "notes": t.notes} for t in tokens]
    return norm, sum_usage([c.get("usage") for c in grok.calls]), len(grok.calls)


def run_pB(frames: list[dict], d: Path, n_declared: int) -> tuple[list[dict], dict, int]:
    key = os.environ["XAI_API_KEY"].strip()
    base = os.environ["XAI_BASE_URL"].rstrip("/")
    content = [{"type": "text", "text": f"Analyze these {n_declared} frames and produce human demonstration action tokens."}]
    for f in frames:
        b64 = base64.b64encode((d / f["file"]).read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    payload = {"model": os.environ["XAI_MODEL"], "temperature": 0.1,
               "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": GROKY_SYSTEM}, {"role": "user", "content": content}]}
    r = httpx.post(f"{base}/chat/completions", json=payload, timeout=180,
                   headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    data = r.json()
    raw = json.loads(data["choices"][0]["message"]["content"])
    norm = [{"t_start": t.get("t_start_s"), "t_end": t.get("t_end_s"), "canonical": t.get("canonical"),
             "verb": t.get("verb"), "objects": t.get("objects") or [], "notes": t.get("notes")}
            for t in raw.get("tokens", [])]
    return norm, sum_usage([data.get("usage")]), 1


def main() -> None:
    from vision.planner.grok_client import load_env
    load_env()
    d, proto, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    frames = json.loads((d / "index.json").read_text())
    n_decl = int(sys.argv[4]) if len(sys.argv) > 4 else len(frames)
    t0 = time.time()
    tokens, usage, calls = run_pA(frames, d) if proto == "pA" else run_pB(frames, d, n_decl)
    res = {"frameset": d.name, "proto": proto, "n_images": len(frames),
           "image_times_s": [f["t"] for f in frames],
           "payload_kb": round(sum((d / f["file"]).stat().st_size for f in frames) / 1024, 1),
           "n_calls": calls, "latency_s": round(time.time() - t0, 2), "usage": usage, "tokens": tokens}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print(f"{d.name}/{proto}: {len(frames)} img, {calls} call, {res['latency_s']} s, "
          f"${usage.get('cost', 0):.4f}, {len(tokens)} token")


if __name__ == "__main__":
    main()
