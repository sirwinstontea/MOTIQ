"""Calibrated observation -> per-registered-object decision (spec sections 8 and 9.3). Pure functions, no I/O.

Frame-level gates (any failure: no object is updated from this frame):
    UR5e base coordinates, visible-surface semantics, flange eye-in-hand calibration (replay fixture only in
    simulation), calibration id present (+ equal to `expected_calibration_id` when set), robot identity, simulation
    flag matching the target, stationary robot + synchronized pose/frame, frame age, and the flange pose used by
    vision agreeing with the twin's own joints (catches a different robot or a robot that moved since the frame).
Object-level gates: vision status ok, known class label, a single detection per registered object (two -> ambiguous,
none updated), confidence, depth-valid ratio and depth points.

Outcome per registered object: `valid` (update marker), `invalid` (keep last marker, mark STALE with a reason),
`not_visible` (not in this view; never interpreted as "gone from the workspace"), `ambiguous`.
Unknown labels are reported separately and never turned into RoboDK item names.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .frames import pose_delta, ur5e_fk_flange
from .vision_client import FrameObservation, map_status


@dataclass(frozen=True)
class ObjectDecision:
    object_id: str
    state: str                        # valid | invalid | not_visible | ambiguous
    reason: str | None = None
    position_base_m: tuple[float, float, float] | None = None
    confidence: float | None = None
    track_id: int | None = None


@dataclass
class MappingResult:
    frame_ok: bool
    frame_reason: str | None
    decisions: dict[str, ObjectDecision] = field(default_factory=dict)
    unknown_labels: list[str] = field(default_factory=list)
    flange_check: dict | None = None


def frame_problem(obs: FrameObservation, cfg, q_now_rad, now_wall: float | None = None) -> tuple[str | None, dict | None]:
    """(reason code or None, flange cross-check details)."""
    v = cfg.vision
    sim_target = cfg.target == "simulation"
    if obs.coordinate_frame != "ur5e_base":
        return "frame_mismatch", None
    if obs.point_semantics != "visible_surface_centroid_3d":
        return "unknown_point_semantics", None
    ref = obs.calibration_reference_frame
    if ref == "replay_fixture":
        if not sim_target or obs.source != "replay":
            return "invalid_calibration", None
    elif ref != v.require_pose_reference:          # "flange"; a TCP or fixed-look-pose calibration is refused
        return "invalid_calibration", None
    if not obs.calibration_id:
        return "invalid_calibration", None
    if v.expected_calibration_id and obs.calibration_id != v.expected_calibration_id:
        return "calibration_changed", None
    if obs.simulation is None or obs.simulation != sim_target:
        return "simulation_mismatch", None
    if v.require_matching_robot_identity:
        if obs.robot_model != cfg.physical_robot.model:
            return "robot_identity_mismatch", None
        if v.expected_robot_serial and obs.robot_serial != v.expected_robot_serial:
            return "robot_identity_mismatch", None
    if obs.robot_stationary is not True:
        return "moving", None
    if obs.pose_frame_synchronized is not True:
        return "frame_mismatch", None
    if obs.ts is None:
        return "stale_frame", None
    age_ms = ((time.time() if now_wall is None else now_wall) - obs.ts) * 1000.0
    if age_ms > v.max_detection_age_ms or age_ms < -500:
        return "stale_frame", {"frame_age_ms": round(age_ms, 1)}
    check = None
    if obs.T_base_flange is not None:
        if q_now_rad is None:
            return "robot_offline", None
        dmm, ddeg = pose_delta(ur5e_fk_flange(q_now_rad), np.asarray(obs.T_base_flange, dtype=float))
        check = {"vision_flange_vs_twin_joints_mm": round(dmm, 2), "deg": round(ddeg, 3),
                 "compares": "vision T_base_flange vs nominal-DH flange FK of the twin's current actual joints"}
        if dmm > v.max_flange_mismatch_mm or ddeg > v.max_flange_mismatch_deg:
            return "frame_mismatch", check
    elif obs.source != "replay":
        return "frame_mismatch", None              # live vision must report the flange pose it used
    return None, check


def map_frame(obs: FrameObservation, cfg, q_now_rad=None, now_wall: float | None = None) -> MappingResult:
    labels = cfg.label_map()
    reason, check = frame_problem(obs, cfg, q_now_rad, now_wall)
    if reason:
        return MappingResult(False, reason, {}, [], check)

    by_obj: dict[str, list] = {oid: [] for oid in cfg.objects}
    unknown = []
    for d in obs.detections:
        oid = labels.get(d.object_class.strip().lower()) or labels.get(d.object_label.strip().lower())
        if oid is None:
            unknown.append(d.object_class or d.object_label or "?")
            continue
        by_obj[oid].append(d)

    v = cfg.vision
    out: dict[str, ObjectDecision] = {}
    for oid, dets in by_obj.items():
        if not dets:
            out[oid] = ObjectDecision(oid, "not_visible", "not_visible")
            continue
        tracks = {d.track_id for d in dets}
        if len(dets) > 1:
            why = "duplicate_detection" if len(tracks) < len(dets) else "ambiguous"
            out[oid] = ObjectDecision(oid, "ambiguous", why)
            continue
        d = dets[0]
        why = None
        if d.status != "ok":
            why = map_status(d.status)
        elif d.position_base_m is None:
            why = "no_depth"
        elif d.confidence is None or d.confidence < v.min_confidence:
            why = "low_confidence"
        elif d.depth_valid_ratio is None or d.depth_valid_ratio < v.min_depth_valid_ratio:
            why = "no_depth"
        elif d.depth_points is None or d.depth_points < v.min_depth_points:
            why = "no_depth"
        elif not all(np.isfinite(d.position_base_m)) or max(abs(x) for x in d.position_base_m) > 2.0:
            why = "out_of_range"                   # UR5e reach is 0.85 m: > 2 m is a bad depth/calibration
        if why:
            out[oid] = ObjectDecision(oid, "invalid", why, None, d.confidence, d.track_id)
        else:
            out[oid] = ObjectDecision(oid, "valid", None, d.position_base_m, d.confidence, d.track_id)
    return MappingResult(True, None, out, unknown, check)
