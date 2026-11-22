"""Where taught objects live and how they are described."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]                 # computer-vision/
OBJECTS = ROOT / "data" / "objects"
MODELS = ROOT / "data" / "models"
TRAINED = MODELS / "motiq_objects.onnx"
HAND_MODEL = MODELS / "oggetti_mani.onnx"
HAND_LABELS = ("braccio", "mano", "polso")                  # kept in every trained model: the hand safety stop needs them
WORLD_WEIGHTS = "yolov8s-worldv2.pt"
READY_RATIO = 0.8          # found without training in at least 80% of the filmed frames -> no training needed


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return s[:40] or "object"


def folder(s: str) -> Path:
    if not re.fullmatch(r"[a-z0-9_]{1,40}", s):
        raise ValueError("bad object id")
    return OBJECTS / s


def read_meta(s: str) -> dict:
    try:
        return json.loads((folder(s) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_meta(s: str, meta: dict) -> None:
    d = folder(s)
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def all_objects() -> list[dict]:
    out = []
    if OBJECTS.is_dir():
        for d in sorted(OBJECTS.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and (d / "meta.json").exists():
                out.append({"slug": d.name, **read_meta(d.name)})
    return out


def say(**kw) -> None:
    print(json.dumps(kw, ensure_ascii=False), flush=True)
