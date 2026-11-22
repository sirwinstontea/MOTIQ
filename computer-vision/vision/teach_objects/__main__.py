"""python -m vision.teach_objects capture|label|train|list|remove ..."""
from __future__ import annotations

import argparse
import shutil
import sys

from . import store


def main() -> int:
    ap = argparse.ArgumentParser(prog="vision.teach_objects")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="film an object from the running vision server")
    c.add_argument("--name", required=True)
    c.add_argument("--url", required=True, help="vision server frame URL, e.g. http://127.0.0.1:8000/frame/webcam.jpg?raw=true")
    c.add_argument("--seconds", type=float, default=20.0)
    c.add_argument("--fps", type=float, default=4.0)
    lb = sub.add_parser("label", help="auto-label a filmed object and check if it needs training")
    lb.add_argument("--slug", required=True)
    t = sub.add_parser("train", help="train one model on every taught object")
    t.add_argument("--epochs", type=int, default=40)
    sub.add_parser("list")
    rm = sub.add_parser("remove")
    rm.add_argument("--slug", required=True)
    a = ap.parse_args()

    if a.cmd == "capture":
        from .capture import capture
        s = store.slug(a.name)
        store.say(stage="start", slug=s)
        rc = capture(a.name, s, a.url, a.seconds, a.fps)
        if rc:
            return rc
        from .label import label
        return label(s)
    if a.cmd == "label":
        from .label import label
        return label(a.slug)
    if a.cmd == "train":
        from .train import train
        return train(a.epochs)
    if a.cmd == "list":
        store.say(objects=store.all_objects(), trained_model=store.TRAINED.exists())
        return 0
    if a.cmd == "remove":
        shutil.rmtree(store.folder(a.slug), ignore_errors=True)
        store.say(removed=a.slug)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
