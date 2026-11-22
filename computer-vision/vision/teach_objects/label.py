"""Auto-label a filmed object: YOLO-World looks for it by name in every frame, the existing hand model marks hands,
wrists and arms in the same frames (so a trained model keeps the hand safety stop). Also decides whether the object
is already found well enough without training."""
from __future__ import annotations

import json

import cv2

from .store import HAND_LABELS, HAND_MODEL, READY_RATIO, ROOT, WORLD_WEIGHTS, folder, read_meta, say, write_meta


def _world():
    from ultralytics import YOLO
    local = ROOT / WORLD_WEIGHTS
    return YOLO(str(local) if local.exists() else WORLD_WEIGHTS)


def _hands():
    from ultralytics import YOLO
    return YOLO(str(HAND_MODEL), task="detect") if HAND_MODEL.exists() else None


def _xywhn(box, w, h):
    x1, y1, x2, y2 = (float(v) for v in box)
    return [round((x1 + x2) / 2 / w, 5), round((y1 + y2) / 2 / h, 5), round((x2 - x1) / w, 5), round((y2 - y1) / h, 5)]


def label(s: str, conf: float = 0.15) -> int:
    d = folder(s)
    meta = read_meta(s)
    name = meta.get("name") or s
    frames = sorted((d / "frames").glob("*.jpg"))
    if not frames:
        say(stage="error", error="Nothing filmed yet.")
        return 1
    say(stage="labeling", progress=0.0)
    world = _world()
    world.set_classes([name])
    hands = _hands()
    prev = d / "previews"
    prev.mkdir(exist_ok=True)
    labels: dict = {}
    found = 0
    for i, f in enumerate(frames):
        img = cv2.imread(str(f))
        if img is None:
            continue
        h, w = img.shape[:2]
        entry: dict = {"obj": None, "conf": 0.0, "hands": []}
        r = world.predict(img, conf=conf, imgsz=480, verbose=False)[0]
        if r.boxes is not None and len(r.boxes):
            k = int(r.boxes.conf.argmax())
            box = r.boxes.xyxy[k].tolist()
            if (box[2] - box[0]) * (box[3] - box[1]) < 0.6 * w * h:      # a box over the whole frame is not the object
                entry["obj"] = _xywhn(box, w, h)
                entry["conf"] = round(float(r.boxes.conf[k]), 3)
                found += 1
        if hands is not None:
            hr = hands.predict(img, conf=0.4, imgsz=640, verbose=False)[0]   # the ONNX hand model is fixed at 640
            for b, c in zip(hr.boxes.xyxy.tolist(), hr.boxes.cls.tolist()):
                lab = hr.names[int(c)]
                if lab in HAND_LABELS:
                    entry["hands"].append([lab, *_xywhn(b, w, h)])
        labels[f.name] = entry
        small = cv2.resize(img, (240, int(240 * h / w)))
        if entry["obj"]:
            cx, cy, bw, bh = entry["obj"]
            sw, sh = small.shape[1], small.shape[0]
            cv2.rectangle(small, (int((cx - bw / 2) * sw), int((cy - bh / 2) * sh)),
                          (int((cx + bw / 2) * sw), int((cy + bh / 2) * sh)), (106, 132, 232), 2)
        cv2.imwrite(str(prev / f.name), small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        say(stage="labeling", progress=round((i + 1) / len(frames), 3))
    (d / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    ratio = round(found / max(1, len(frames)), 3)
    meta.update({"found_ratio": ratio, "status": "ready" if ratio >= READY_RATIO else "needs_training"})
    write_meta(s, meta)
    say(stage="labeled", found_ratio=ratio, status=meta["status"])
    return 0
