"""Typed adapter for calibrated object observations (spec section 8). Read-only.

Sources:
- `VisionServerSource`: `POST {vision}/localization/snapshot_all` on the local computer-vision server. That endpoint
  uses the ACTIVE flange eye-in-hand calibration (validated + operator-approved, tied to the camera serial and
  intrinsics), brackets one new RGB-D frame between two stationary flange reads, and returns every detection with a
  `position_base_m` (UR5e base, m, visible-surface centroid) or the reason it has none. Nothing here invents a field:
  whatever the server does not report stays None and the gates in scene_mapper reject it.
- `ReplaySource`: a JSON fixture of base-frame points for offline simulation. Marked `simulation: true` and
  `calibration_reference_frame: "replay_fixture"`, so it can never pass the gates of a physical target.

`GET /locate/arm` is intentionally not polled per object: one frame gives all objects at once.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Detection:
    object_class: str
    object_label: str
    track_id: int | None
    confidence: float | None
    position_base_m: tuple[float, float, float] | None
    depth_valid_ratio: float | None
    depth_points: int | None
    status: str                       # "ok" or the vision server's reason code
    reason: str | None = None


@dataclass(frozen=True)
class FrameObservation:
    source: str                       # "vision_server" | "replay"
    received_mono_s: float
    ts: float | None                  # frame wall-clock time (host clock), None if unknown
    frame_id: str | None
    coordinate_frame: str | None
    point_semantics: str | None
    calibration_id: str | None
    calibration_reference_frame: str | None
    robot_model: str | None
    robot_serial: str | None
    simulation: bool | None
    robot_stationary: bool | None
    pose_frame_synchronized: bool | None
    T_base_flange: list[list[float]] | None
    detections: list[Detection] = field(default_factory=list)


class VisionUnavailable(RuntimeError):
    """Frame-level failure (no observation at all): status is a short reason code for the app."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


# vision-server status -> twin reason code (spec 8: invalid_calibration, no_depth, moving, stale_frame, ...)
STATUS_MAP = {
    "missing_calibration": "invalid_calibration",
    "camera_mismatch": "invalid_calibration",
    "invalid_setup": "invalid_calibration",
    "camera_offline": "no_camera",
    "robot_moving": "moving",
    "unsynced_robot_pose": "frame_mismatch",
    "invalid_flange_pose": "frame_mismatch",
    "stale_pose": "frame_mismatch",
    "robot_offline": "robot_offline",
    "invalid_depth": "no_depth",
    "object_partially_visible": "truncated",
    "object_not_detected": "not_visible",
    "unauthorized": "vision_unauthorized",
}


def map_status(s: str | None) -> str:
    return STATUS_MAP.get(s or "", s or "unknown")


class ObservationSource(Protocol):
    kind: str

    def snapshot(self) -> FrameObservation: ...


def _f(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _point(v: Any) -> tuple[float, float, float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    try:
        p = tuple(float(x) for x in v)
    except (TypeError, ValueError):
        return None
    return p  # type: ignore[return-value]


def parse_frame(d: dict, source: str) -> FrameObservation:
    dets = []
    for o in d.get("objects") or []:
        if not isinstance(o, dict):
            continue
        dets.append(Detection(
            object_class=str(o.get("object_class") or ""), object_label=str(o.get("object_label") or ""),
            track_id=o.get("track_id") if isinstance(o.get("track_id"), int) else None,
            confidence=_f(o.get("confidence")), position_base_m=_point(o.get("position_base_m")),
            depth_valid_ratio=_f(o.get("depth_valid_ratio")),
            depth_points=o.get("depth_points") if isinstance(o.get("depth_points"), int) else None,
            status=str(o.get("status") or "unknown"), reason=o.get("reason")))
    T = d.get("T_base_flange")
    return FrameObservation(
        source=source, received_mono_s=time.monotonic(), ts=_f(d.get("ts")),
        frame_id=d.get("source_frame_id") or d.get("frame_id"), coordinate_frame=d.get("coordinate_frame"),
        point_semantics=d.get("point_semantics"), calibration_id=d.get("calibration_id"),
        calibration_reference_frame=d.get("calibration_reference_frame"), robot_model=d.get("robot_model"),
        robot_serial=d.get("robot_serial"),
        simulation=d.get("simulation") if isinstance(d.get("simulation"), bool) else None,
        robot_stationary=d.get("robot_stationary") if isinstance(d.get("robot_stationary"), bool) else None,
        pose_frame_synchronized=d.get("pose_frame_synchronized")
        if isinstance(d.get("pose_frame_synchronized"), bool) else None,
        T_base_flange=T if isinstance(T, list) and len(T) == 4 else None, detections=dets)


class VisionServerSource:
    kind = "vision_server"

    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = 8.0, opener=None):
        self.base_url, self.api_key, self.timeout_s = base_url.rstrip("/"), api_key, timeout_s
        self._open = opener or urllib.request.urlopen

    def _post(self, path: str) -> tuple[int, dict]:
        req = urllib.request.Request(f"{self.base_url}{path}", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "X-Bridge-Key": self.api_key})
        try:
            with self._open(req, timeout=self.timeout_s) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")
            except Exception:  # noqa: BLE001
                return e.code, {}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise VisionUnavailable("vision_offline", f"vision server {self.base_url} unreachable: {e}") from e

    def get_status(self) -> dict:
        req = urllib.request.Request(f"{self.base_url}/calibration/status", headers={"X-Bridge-Key": self.api_key})
        try:
            with self._open(req, timeout=self.timeout_s) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except Exception as e:  # noqa: BLE001
            raise VisionUnavailable("vision_offline", f"vision server {self.base_url} unreachable: {e}") from e

    def snapshot(self) -> FrameObservation:
        code, d = self._post("/localization/snapshot_all")
        if code == 404:
            raise VisionUnavailable("vision_outdated", "vision server has no /localization/snapshot_all: update and "
                                    "restart computer-vision")
        if code != 200 or not d.get("ok"):
            raise VisionUnavailable(map_status(d.get("status")), d.get("reason") or f"HTTP {code}")
        return parse_frame(d, self.kind)


class ReplaySource:
    """Fixture: {"objects": [{"object_class", "position_base_m", "confidence", ...}]}. Simulation only."""

    kind = "replay"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._n = 0

    def snapshot(self) -> FrameObservation:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise VisionUnavailable("replay_unreadable", f"{self.path}: {e}") from e
        self._n += 1
        objs = []
        for i, o in enumerate(d.get("objects") or []):
            objs.append({"track_id": o.get("track_id", 1000 + i), "object_class": o.get("object_class"),
                         "object_label": o.get("object_label") or o.get("object_class"),
                         "confidence": o.get("confidence", 1.0), "position_base_m": o.get("position_base_m"),
                         "depth_valid_ratio": o.get("depth_valid_ratio", 1.0), "depth_points": o.get("depth_points", 500),
                         "status": o.get("status", "ok"), "reason": o.get("reason")})
        frame = {"ts": time.time(), "source_frame_id": f"replay_{self._n}", "coordinate_frame": "ur5e_base",
                 "point_semantics": "visible_surface_centroid_3d",
                 "calibration_id": f"replay:{self.path.name}", "calibration_reference_frame": "replay_fixture",
                 "robot_model": "UR5e", "robot_serial": None, "simulation": True,
                 "robot_stationary": True, "pose_frame_synchronized": True, "T_base_flange": None, "objects": objs}
        return parse_frame(frame, self.kind)


def make_observation_source(cfg, api_key: str = "") -> ObservationSource | None:
    v = cfg.vision
    if v.source == "vision_server":
        return VisionServerSource(v.base_url, api_key, v.request_timeout_s)
    if v.source == "replay":
        return ReplaySource(v.replay_file)
    return None
