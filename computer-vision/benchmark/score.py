"""Punteggio dei run di cross_tokenize rispetto alla ground truth e tabella riassuntiva.

    .venv/bin/python benchmark/score.py benchmark/results/<run>/cross benchmark/videos/<id>.gt.json
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ACCEPT = {"grasp": {"grasp"}, "move": {"move", "insert"}, "release": {"release", "insert"},
          "rotate": {"rotate", "wait"}}
BUCKET_KW = ("bucket", "container", "cyan", "blue", "azzurr", "blu", "secchio", "contenitore", "vaso", "pot")
ORANGE_KW = ("orange", "arancion")


def is_bucket(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in BUCKET_KW) and not any(k in n for k in ORANGE_KW)


def iou(a0, a1, b0, b1) -> float:
    if None in (a0, a1, b0, b1):
        return 0.0
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


def score(run: dict, gt: dict) -> dict:
    toks = run["tokens"]
    used: set[int] = set()
    matched, ious = 0, []
    req = [a for a in gt["actions"] if not a.get("optional")]
    for a in req:
        best, bi = 0.0, None
        for i, t in enumerate(toks):
            if i in used or t["canonical"] not in ACCEPT.get(a["canonical"], {a["canonical"]}):
                continue
            v = iou(a["t_start"], a["t_end"], t["t_start"], t["t_end"])
            if v > best:
                best, bi = v, i
        if bi is not None and best >= 0.3:
            used.add(bi)
            matched += 1
            ious.append(best)
    # falsi positivi: token non abbinati e non coperti da un'azione opzionale accettata
    fp = 0
    for i, t in enumerate(toks):
        if i in used:
            continue
        ok_opt = any(a.get("optional") and t["canonical"] in a.get("accept_canonical", [])
                     and iou(a["t_start"], a["t_end"], t["t_start"], t["t_end"]) > 0 for a in gt["actions"])
        if not ok_opt:
            fp += 1

    def dests(t):
        return [o["name"] for o in t["objects"] if o.get("role") in ("dest", "container")]
    text = lambda t: f"{t.get('verb') or ''} {t.get('notes') or ''}".lower()  # noqa: E731
    place = any(t["canonical"] in ("release", "move", "insert") and
                (any(is_bucket(n) for n in dests(t)) or
                 (any(k in text(t) for k in ("bucket", "secchio", "container", "contenitore", "cyan", "azzurr"))
                  and any(k in text(t) for k in ("into", "inside", "dentro", "nel ", "in the", "drop", "rilasc", "release", "lascia", "deposit")))
                ) and "unused" not in text(t)
                for t in toks)
    wrong_dest = sorted({n for t in toks for n in dests(t) if not is_bucket(n)})
    u = run.get("usage") or {}
    return {
        "recall": matched / len(req), "precision": matched / len(toks) if toks else 0.0,
        "false_pos": fp, "mean_iou": st.mean(ious) if ious else 0.0,
        "place_detected": place, "wrong_dest": wrong_dest, "n_tokens": len(toks),
        "n_images": run["n_images"], "payload_kb": run["payload_kb"], "n_calls": run["n_calls"],
        "latency_s": run["latency_s"], "cost": u.get("cost", 0.0),
        "prompt_tok": u.get("prompt_tokens", 0), "compl_tok": u.get("completion_tokens", 0),
        "reason_tok": u.get("reasoning_tokens", 0),
    }


def ms(xs: list[float], fmt: str = "{:.2f}") -> str:
    if len(xs) == 1:
        return fmt.format(xs[0])
    return f"{fmt.format(st.mean(xs))} ± {fmt.format(st.stdev(xs))}"


def main() -> None:
    d, gt = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())
    cells: dict[str, list[dict]] = defaultdict(list)
    rows = []
    for p in sorted(d.glob("*.json")):
        run = json.loads(p.read_text())
        s = score(run, gt)
        key = f"{run['frameset']} + {run['proto']}"
        cells[key].append(s)
        rows.append({"file": p.name, "cell": key, **s})
    (d / "scores.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    cols = ["recall", "precision", "mean_iou", "false_pos", "n_images", "n_calls", "latency_s",
            "cost", "prompt_tok", "compl_tok", "reason_tok"]
    print("| combinazione | n | rilascio nel secchio | " + " | ".join(cols) + " | destinazioni sbagliate |")
    print("|" + "---|" * (len(cols) + 4))
    for k in sorted(cells):
        v = cells[k]
        place = f"{sum(x['place_detected'] for x in v)}/{len(v)}"
        wd = sorted({n for x in v for n in x["wrong_dest"]})
        vals = [ms([x[c] for x in v], "{:.3f}" if c == "cost" else "{:.0f}" if c.endswith("tok") or c in ("n_images", "n_calls", "false_pos") else "{:.2f}") for c in cols]
        print(f"| {k} | {len(v)} | {place} | " + " | ".join(vals) + f" | {', '.join(wd) or '-'} |")


if __name__ == "__main__":
    main()
