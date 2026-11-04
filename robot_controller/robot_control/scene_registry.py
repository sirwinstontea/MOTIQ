"""Allowlisted objects, destinations, named targets, patterns and motions.

Only IDs in this registry can be used by the AI. Detections are recorded here
with timestamp, confidence and provenance; picks require a fresh detection.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from pydantic import BaseModel, ConfigDict

from .config import SceneConfig
from .models import ErrorCode, Pose, RobotControlError, utc_now


class Detection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_id: str
    class_label: str
    confidence: float
    pose: Optional[Pose] = None          # only used if a calibrated transform exists (not in v1)
    frame_id: Optional[str] = None
    timestamp_utc: str
    monotonic_s: float
    uncertainty_m: Optional[float] = None
    source: str                          # "mock" | "vision_eyes" | ...
    is_mock: bool

    def age_s(self) -> float:
        return time.monotonic() - self.monotonic_s


class SceneRegistry:
    def __init__(self, scene: SceneConfig):
        self.cfg = scene
        self._lock = threading.Lock()
        self._detections: dict[str, Detection] = {}

    # --- lookups that raise UNKNOWN_ID ------------------------------------
    def object(self, object_id: str):
        if object_id not in self.cfg.objects:
            raise RobotControlError(ErrorCode.UNKNOWN_ID, f"object '{object_id}' is not registered")
        return self.cfg.objects[object_id]

    def destination(self, destination_id: str):
        if destination_id not in self.cfg.destinations:
            raise RobotControlError(ErrorCode.UNKNOWN_ID, f"destination '{destination_id}' is not registered")
        return self.cfg.destinations[destination_id]

    def target(self, target_id: str):
        if target_id not in self.cfg.targets:
            raise RobotControlError(ErrorCode.UNKNOWN_ID, f"target '{target_id}' is not registered")
        return self.cfg.targets[target_id]

    def pattern(self, pattern_id: str):
        if pattern_id not in self.cfg.patterns:
            raise RobotControlError(ErrorCode.UNKNOWN_ID, f"pattern '{pattern_id}' is not registered")
        return self.cfg.patterns[pattern_id]

    def motion(self, motion_id: str):
        if motion_id not in self.cfg.motions:
            raise RobotControlError(ErrorCode.UNKNOWN_ID, f"motion '{motion_id}' is not registered")
        return self.cfg.motions[motion_id]

    # --- detections -------------------------------------------------------
    def record_detection(self, det: Detection) -> None:
        self.object(det.object_id)  # must be registered
        with self._lock:
            self._detections[det.object_id] = det

    def detection(self, object_id: str) -> Optional[Detection]:
        with self._lock:
            return self._detections.get(object_id)

    def clear_detection(self, object_id: str) -> None:
        with self._lock:
            self._detections.pop(object_id, None)

    def snapshot(self) -> dict:
        with self._lock:
            dets = {k: {"confidence": d.confidence, "source": d.source, "is_mock": d.is_mock,
                        "age_s": round(d.age_s(), 3), "timestamp_utc": d.timestamp_utc}
                    for k, d in self._detections.items()}
        return {
            "timestamp_utc": utc_now(),
            "objects": {oid: {"dimensions_m": list(o.dimensions_m), "mass_kg": o.mass_kg,
                              "class_labels": o.class_labels, "detection": dets.get(oid)}
                        for oid, o in self.cfg.objects.items()},
            "destinations": sorted(self.cfg.destinations),
            "targets": sorted(self.cfg.targets),
            "patterns": {pid: {"max_cycles": p.max_cycles, "requires_grip": p.requires_grip,
                               "description": p.description} for pid, p in self.cfg.patterns.items()},
            "motions": {mid: m.description for mid, m in self.cfg.motions.items()},
            "units": {"length": "m", "angle": "rad", "mass": "kg"},
        }
