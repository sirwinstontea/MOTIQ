"""Detectors: how ``identify_object`` obtains a detection.

* ``MockDetector`` – demo only. Every detection is marked ``source="mock"`` and
  ``is_mock=True``; results disclose this. It never claims to be real vision.
* ``NoDetector`` – returns NOT_AVAILABLE (default when vision is absent).
* ``VisionEyesFileDetector`` – adapter for the MOTIQ ``vision/eyes`` frame
  payload / ``vision/grok`` handoff JSON (``logs/selection.json``). It maps the
  vision ``label``/``name`` to a *registered* object ID via the scene's
  ``class_labels``. Camera XYZ (``coords_mm``) is deliberately ignored: there is
  no verified camera->robot calibration in this build, so picks use taught
  named targets only.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional, Protocol

from .models import ErrorCode, RobotControlError, utc_now
from .scene_registry import Detection, SceneRegistry


class Detector(Protocol):
    name: str

    def identify(self, object_id: str, scene: SceneRegistry) -> Detection: ...


class NoDetector:
    name = "none"

    def identify(self, object_id, scene):
        raise RobotControlError(ErrorCode.NOT_AVAILABLE, "no vision detector configured")


class MockDetector:
    """Deterministic demo detector. Clearly labelled as mock in every result."""
    name = "mock"

    def __init__(self, confidence: float = 0.95, missing: Optional[set[str]] = None):
        self.confidence = confidence
        self.missing = missing or set()

    def identify(self, object_id, scene):
        obj = scene.object(object_id)
        if object_id in self.missing:
            raise RobotControlError(ErrorCode.NOT_AVAILABLE, f"mock detector: '{object_id}' not visible")
        return Detection(object_id=object_id, class_label=(obj.class_labels or [object_id])[0],
                         confidence=self.confidence, timestamp_utc=utc_now(), monotonic_s=time.monotonic(),
                         source="mock", is_mock=True, frame_id=None, pose=None)


class VisionEyesFileDetector:
    """Reads the latest vision JSON written by the MOTIQ vision repo.

    Accepts either a FramePayload (``{"source","frame_id","ts","objects":[...]}``)
    or a handoff selection (``{"ts","target":{...}}``). Field names follow
    ``vision/eyes/schemas.py`` and ``vision/grok/handoff.py`` without modifying them.
    """
    name = "vision_eyes"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _candidates(self) -> tuple[float, list[dict]]:
        if not self.path.is_file():
            raise RobotControlError(ErrorCode.NOT_AVAILABLE, f"vision file not found: {self.path}")
        try:
            msg = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RobotControlError(ErrorCode.NOT_AVAILABLE, f"vision file unreadable: {e}") from e
        ts = float(msg.get("ts", 0.0))
        if "objects" in msg:
            return ts, list(msg["objects"])
        if isinstance(msg.get("target"), dict):
            return ts, [msg["target"]]
        raise RobotControlError(ErrorCode.NOT_AVAILABLE, "vision file has no objects/target")

    def identify(self, object_id, scene):
        obj = scene.object(object_id)
        labels = {s.lower() for s in obj.class_labels}
        ts, cands = self._candidates()
        matches = [c for c in cands
                   if str(c.get("label", "")).lower() in labels or str(c.get("name", "")).lower() in labels]
        if not matches:
            raise RobotControlError(ErrorCode.NOT_AVAILABLE, f"vision: no detection matching '{object_id}'")
        best = max(matches, key=lambda c: float(c.get("conf", 0.0)))
        age = max(0.0, time.time() - ts)
        return Detection(object_id=object_id, class_label=str(best.get("label")),
                         confidence=float(best.get("conf", 0.0)), timestamp_utc=utc_now(),
                         monotonic_s=time.monotonic() - age, source="vision_eyes", is_mock=False,
                         frame_id="camera_uncalibrated", pose=None)
