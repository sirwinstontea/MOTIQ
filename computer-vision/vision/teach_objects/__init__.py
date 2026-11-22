"""Teach MOTIQ the user's own objects: film -> auto-label -> (optional) train a small YOLO -> ONNX for the tracker.

Everything lives in data/objects/<slug>/ (meta.json, frames/, previews/, labels.json); the trained model goes to
data/models/motiq_objects.onnx. Run as `python -m vision.teach_objects <command>`; progress is printed as one JSON
object per line so the MOTIQ launcher can show it.
"""
