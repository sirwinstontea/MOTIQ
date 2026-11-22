"""Train one small YOLO on all taught objects (+ hands/wrists/arms) and export it as ONNX for the tracker."""
from __future__ import annotations

import json
import random
import shutil

from .store import HAND_LABELS, MODELS, OBJECTS, TRAINED, all_objects, folder, say, write_meta


def build_dataset() -> tuple[str, list[str], int]:
    objs = [o for o in all_objects() if o.get("status") in ("ready", "needs_training", "trained")]
    names = [o["name"] for o in objs] + list(HAND_LABELS)
    ds = OBJECTS / "_dataset"
    shutil.rmtree(ds, ignore_errors=True)
    items = []
    for ci, o in enumerate(objs):
        d = folder(o["slug"])
        try:
            labels = json.loads((d / "labels.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        dropped = set(o.get("dropped") or [])
        for fname, e in labels.items():
            if fname in dropped or not e.get("obj"):
                continue                   # the object is in every filmed frame: a frame without its box would teach "absent"
            rows = [f"{ci} " + " ".join(map(str, e["obj"]))]
            rows += [f"{len(objs) + HAND_LABELS.index(h[0])} " + " ".join(map(str, h[1:])) for h in e.get("hands", [])]
            items.append((d / "frames" / fname, f"{o['slug']}_{fname[:-4]}", rows))
    random.Random(7).shuffle(items)
    n_val = max(1, len(items) // 7)
    for i, (src, stem, rows) in enumerate(items):
        split = "val" if i < n_val else "train"
        (ds / "images" / split).mkdir(parents=True, exist_ok=True)
        (ds / "labels" / split).mkdir(parents=True, exist_ok=True)
        shutil.copy(src, ds / "images" / split / f"{stem}.jpg")
        (ds / "labels" / split / f"{stem}.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    yaml = ds / "data.yaml"
    yaml.write_text(f"path: {ds.as_posix()}\ntrain: images/train\nval: images/val\n"
                    f"names: {json.dumps(dict(enumerate(names)), ensure_ascii=False)}\n", encoding="utf-8")
    return str(yaml), names, len(items)


def train(epochs: int = 40, imgsz: int = 480) -> int:
    from ultralytics import YOLO
    import torch

    yaml, names, n = build_dataset()
    if n < 20:
        say(stage="error", error="Not enough labelled pictures to train (film your objects first).")
        return 1
    device = 0 if torch.cuda.is_available() else "cpu"
    say(stage="training", progress=0.0, images=n, device="gpu" if device == 0 else "cpu")
    model = YOLO("yolov8n.pt")                       # small base model, downloaded once by Ultralytics

    def on_epoch(trainer):
        say(stage="training", progress=round(min(1.0, (trainer.epoch + 1) / trainer.epochs), 3))

    model.add_callback("on_fit_epoch_end", on_epoch)
    model.train(data=yaml, epochs=epochs, imgsz=imgsz, batch=16, device=device, workers=0, patience=10,
                project=str(OBJECTS / "_runs"), name="motiq_objects", exist_ok=True, verbose=False, plots=False)
    say(stage="exporting", progress=1.0)
    best = OBJECTS / "_runs" / "motiq_objects" / "weights" / "best.pt"
    onnx = YOLO(str(best)).export(format="onnx", imgsz=640, opset=12, simplify=False)   # the tracker runs 640x640
    MODELS.mkdir(parents=True, exist_ok=True)
    shutil.copy(onnx, TRAINED)
    for o in all_objects():
        if o.get("status") in ("ready", "needs_training"):
            o2 = {k: v for k, v in o.items() if k != "slug"}
            o2["status"] = "trained"
            write_meta(o["slug"], o2)
    say(stage="trained", model=str(TRAINED), classes=names)
    return 0
