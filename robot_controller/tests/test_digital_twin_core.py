"""Digital twin, pure parts: units, frames, config gates, calibration/quality gates, label mapping."""
import copy
import json
import math
import time
from pathlib import Path

import numpy as np
import pytest

from robot_control.digital_twin.config import TwinConfig, load_config
from robot_control.digital_twin.frames import (
    joints_rad_to_robodk_deg, marker_T_R, point_B_to_R, pose_delta, pose_in_parent, rigid_inverse, rotz,
    tabletop_contact_B, transl, ur5e_fk_flange)
from robot_control.digital_twin.scene_mapper import map_frame
from robot_control.digital_twin.vision_client import ReplaySource, parse_frame

CFG = Path(__file__).resolve().parents[1] / "config"


def sim_cfg(**over) -> TwinConfig:
    d = json.loads((CFG / "digital_twin.sim.example.json").read_text())
    d.pop("_comment")
    for k, v in over.items():
        d[k] = {**d[k], **v} if isinstance(v, dict) else v
    d["vision"]["replay_file"] = str(CFG / "digital_twin_replay.example.json")
    return TwinConfig.model_validate(d)


def phys_dict():
    d = json.loads((CFG / "digital_twin.example.json").read_text())
    d.pop("_comment")
    d["physical_robot"]["host"] = "192.0.2.10"
    return d


def phys_cfg(**vision) -> TwinConfig:
    d = phys_dict()
    d["vision"].update(vision)
    d["robodk"]["base_alignment"].update(validated=True, validated_for="physical", validated_by="test")
    return TwinConfig.model_validate(d)


Q = (0.1, -1.5, 1.4, -1.2, -1.57, 0.3)


def live_frame(**over):
    d = {"ts": time.time(), "source_frame_id": "camera_color_7", "coordinate_frame": "ur5e_base",
         "point_semantics": "visible_surface_centroid_3d", "calibration_id": "hef-abc", "simulation": False,
         "calibration_reference_frame": "flange", "robot_model": "UR5e", "robot_serial": "20235500001",
         "robot_stationary": True, "pose_frame_synchronized": True,
         "T_base_flange": ur5e_fk_flange(Q).tolist(),
         "objects": [{"track_id": 3, "object_class": "vite_arancione", "object_label": "orange_screw_03",
                      "confidence": 0.94, "position_base_m": [-0.45, -0.12, 0.09], "depth_valid_ratio": 0.88,
                      "depth_points": 410, "status": "ok"}]}
    d.update(over)
    return parse_frame(d, "vision_server")


# ---- 1. joints ------------------------------------------------------------------------------------------------
def test_joint_conversion_rad_to_deg_keeps_order():
    q = [0.0, -math.pi / 2, math.pi / 2, -math.pi / 2, -math.pi / 2, math.pi]
    assert joints_rad_to_robodk_deg(q) == pytest.approx([0, -90, 90, -90, -90, 180])
    with pytest.raises(ValueError):
        joints_rad_to_robodk_deg(q[:5])
    with pytest.raises(ValueError):
        joints_rad_to_robodk_deg([float("nan")] * 6)


def test_nominal_fk_zero_pose():
    T = ur5e_fk_flange([0] * 6)                     # UR5e zero pose: arm stretched along -X
    assert T[:3, 3] == pytest.approx([-0.8172, -0.2329, 0.0628], abs=1e-4)


# ---- 2. frames ------------------------------------------------------------------------------------------------
def test_point_identity_and_nonidentity_T_R_B():
    p = [-0.45, -0.12, 0.09]
    assert point_B_to_R(np.eye(4), p) == pytest.approx(p)
    T = transl(0.1, 0.2, 0.0) @ rotz(180)
    assert point_B_to_R(T, p) == pytest.approx([0.55, 0.32, 0.09])


def test_marker_pose_in_rotated_objects_frame():
    """Objects_Frame = transl(-620 mm) * rotz(90) under the robot base; the base itself offset in the station."""
    T_S_R = transl(1.0, 2.0, 0.5) @ rotz(30)
    T_S_P = T_S_R @ transl(-0.62, 0, 0) @ rotz(90)
    p_R = [-0.62, -0.24, 0.115]
    T_P_M = pose_in_parent(T_S_P, T_S_R, marker_T_R(p_R))
    assert (T_S_P @ T_P_M)[:3, 3] == pytest.approx((T_S_R @ [*p_R, 1])[:3])
    # local coordinates in the rotated frame: base -Y becomes frame -X
    assert T_P_M[:3, 3] == pytest.approx([-0.24, 0.0, 0.115], abs=1e-9)
    # the WRONG shortcut (send base XYZ as the local pose) lands elsewhere
    assert np.linalg.norm((T_S_P @ marker_T_R(p_R))[:3, 3] - (T_S_R @ [*p_R, 1])[:3]) > 0.3


def test_rigid_inverse_and_delta():
    T = transl(0.1, -0.2, 0.3) @ rotz(37)
    assert rigid_inverse(T) @ T == pytest.approx(np.eye(4))
    assert pose_delta(T, T @ transl(0.003, 0, 0)) == pytest.approx((3.0, 0.0), abs=1e-6)


def test_T_R_B_must_be_rigid():
    d = phys_dict()
    d["robodk"]["base_alignment"]["T_R_B"] = [[2, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    with pytest.raises(ValueError, match="orthonormal"):
        TwinConfig.model_validate(d)


# ---- config gates ---------------------------------------------------------------------------------------------
def test_config_target_rules():
    d = phys_dict()
    d["physical_robot"]["allow_non_loopback"] = False
    with pytest.raises(ValueError, match="allow_non_loopback"):
        TwinConfig.model_validate(d)
    d = phys_dict()
    d["vision"].update(source="replay", replay_file="x.json")
    with pytest.raises(ValueError, match="replay"):
        TwinConfig.model_validate(d)
    d = phys_dict()
    d["target"] = "simulation"
    with pytest.raises(ValueError, match="non-loopback"):
        TwinConfig.model_validate(d)
    d = phys_dict()
    d["robodk"]["base_alignment"]["validated"] = True          # no validated_for/by
    with pytest.raises(ValueError, match="validated_for"):
        TwinConfig.model_validate(d)
    d = phys_dict()
    d["objects"]["blue_bucket_01"]["marker_name"] = "Blue_Bucket_01"   # would hijack the CAD item
    with pytest.raises(ValueError, match="MOTIQ_Detected_"):
        TwinConfig.model_validate(d)
    d = phys_dict()
    d["vision"]["base_url"] = "http://10.0.0.5:8000"
    with pytest.raises(ValueError, match="local"):
        TwinConfig.model_validate(d)


def test_placeholder_identity_alignment_is_not_accepted():
    cfg = TwinConfig.model_validate(phys_dict())
    ok, why = cfg.alignment_ok()
    assert not ok and "not validated" in why
    d = phys_dict()
    d["robodk"]["base_alignment"].update(validated=True, validated_for="simulation", validated_by="sim")
    ok, why = TwinConfig.model_validate(d).alignment_ok()
    assert not ok and "simulation" in why             # a sim sign-off never counts for the physical robot


def test_env_placeholder_for_host(tmp_path, monkeypatch):
    p = tmp_path / "c.json"
    p.write_text((CFG / "digital_twin.example.json").read_text())
    monkeypatch.delenv("MOTIQ_UR_HOST", raising=False)
    with pytest.raises(ValueError, match="MOTIQ_UR_HOST"):
        load_config(p)
    monkeypatch.setenv("MOTIQ_UR_HOST", "192.0.2.7")
    assert load_config(p).physical_robot.host == "192.0.2.7"


# ---- 3. calibration / identity gates -------------------------------------------------------------------------
def test_valid_live_frame_maps_to_registered_object():
    r = map_frame(live_frame(), phys_cfg(), Q)
    assert r.frame_ok, r.frame_reason
    d = r.decisions["orange_screw_03"]
    assert d.state == "valid" and d.position_base_m == pytest.approx((-0.45, -0.12, 0.09))
    assert r.decisions["blue_bucket_01"].state == "not_visible"
    assert r.flange_check["vision_flange_vs_twin_joints_mm"] < 0.01


@pytest.mark.parametrize("over,reason", [
    ({"calibration_id": None}, "invalid_calibration"),
    ({"calibration_reference_frame": "tcp"}, "invalid_calibration"),
    ({"calibration_reference_frame": "fixed"}, "invalid_calibration"),       # fixed look-pose: not for a moving arm
    ({"calibration_reference_frame": "replay_fixture"}, "invalid_calibration"),
    ({"robot_model": "UR3e"}, "robot_identity_mismatch"),
    ({"simulation": True}, "simulation_mismatch"),
    ({"simulation": None}, "simulation_mismatch"),
    ({"coordinate_frame": "camera"}, "frame_mismatch"),
    ({"point_semantics": "grasp_pose"}, "unknown_point_semantics"),
    ({"robot_stationary": False}, "moving"),
    ({"pose_frame_synchronized": None}, "frame_mismatch"),
    ({"ts": None}, "stale_frame"),
    ({"T_base_flange": None}, "frame_mismatch"),
])
def test_frame_gates_reject(over, reason):
    r = map_frame(live_frame(**over), phys_cfg(), Q)
    assert not r.frame_ok and r.frame_reason == reason and r.decisions == {}


def test_stale_frame_and_expected_ids():
    assert map_frame(live_frame(ts=time.time() - 2.0), phys_cfg(), Q).frame_reason == "stale_frame"
    assert map_frame(live_frame(), phys_cfg(expected_calibration_id="hef-other"), Q).frame_reason == \
        "calibration_changed"
    assert map_frame(live_frame(), phys_cfg(expected_robot_serial="999"), Q).frame_reason == \
        "robot_identity_mismatch"


def test_robot_moved_since_frame_or_other_robot_is_frame_mismatch():
    q_moved = list(Q)
    q_moved[0] += math.radians(2.0)
    r = map_frame(live_frame(), phys_cfg(), q_moved)
    assert r.frame_reason == "frame_mismatch" and r.flange_check["vision_flange_vs_twin_joints_mm"] > 10
    assert map_frame(live_frame(), phys_cfg(), None).frame_reason == "robot_offline"


# ---- 5. quality / mapping -------------------------------------------------------------------------------------
def det(**over):
    d = {"track_id": 3, "object_class": "vite_arancione", "object_label": "orange_screw_03", "confidence": 0.94,
         "position_base_m": [-0.45, -0.12, 0.09], "depth_valid_ratio": 0.88, "depth_points": 410, "status": "ok"}
    d.update(over)
    return d


@pytest.mark.parametrize("over,reason", [
    ({"confidence": 0.3}, "low_confidence"),
    ({"confidence": None}, "low_confidence"),
    ({"depth_valid_ratio": 0.2}, "no_depth"),
    ({"depth_points": 5}, "no_depth"),
    ({"position_base_m": None}, "no_depth"),
    ({"status": "invalid_depth", "position_base_m": None}, "no_depth"),
    ({"status": "object_partially_visible", "position_base_m": None}, "truncated"),
    ({"position_base_m": [5.0, 0, 0]}, "out_of_range"),
])
def test_object_quality_gates(over, reason):
    r = map_frame(live_frame(objects=[det(**over)]), phys_cfg(), Q)
    d = r.decisions["orange_screw_03"]
    assert d.state == "invalid" and d.reason == reason and d.position_base_m is None


def test_duplicates_ambiguous_and_unknown_labels():
    two = [det(), det(track_id=9, position_base_m=[-0.3, 0.1, 0.09])]
    r = map_frame(live_frame(objects=two), phys_cfg(), Q)
    assert r.decisions["orange_screw_03"].state == "ambiguous"
    same_track = [det(), det()]
    assert map_frame(live_frame(objects=same_track), phys_cfg(), Q).decisions["orange_screw_03"].reason == \
        "duplicate_detection"
    r = map_frame(live_frame(objects=[det(object_class="Robot_Base", object_label="UR5e")]), phys_cfg(), Q)
    assert r.unknown_labels == ["Robot_Base"] and all(d.state == "not_visible" for d in r.decisions.values())


def test_label_aliases_case_insensitive():
    r = map_frame(live_frame(objects=[det(object_class="Orange Screw", object_label="x")]), phys_cfg(), Q)
    assert r.decisions["orange_screw_03"].state == "valid"


def test_duplicate_class_label_across_objects_rejected():
    d = phys_dict()
    d["objects"]["green_ring_02"]["class_labels"].append("vite")
    with pytest.raises(ValueError, match="mapped to both"):
        TwinConfig.model_validate(d)


# ---- replay fixture: simulation only --------------------------------------------------------------------------
def test_replay_fixture_passes_only_in_simulation():
    cfg = sim_cfg()
    obs = ReplaySource(cfg.vision.replay_file).snapshot()
    r = map_frame(obs, cfg, None)
    assert r.frame_ok
    states = {k: v.state for k, v in r.decisions.items()}
    assert states == {"blue_bucket_01": "valid", "green_ring_02": "valid", "orange_screw_03": "valid",
                      "white_bucket_lid_04": "invalid"}                     # 0.4 confidence in the fixture
    assert r.unknown_labels == ["tazza"]
    assert map_frame(obs, phys_cfg(), Q).frame_reason == "invalid_calibration"


# ---- 4. surface point vs CAD origin -------------------------------------------------------------------------
def test_top_surface_centroid_is_not_the_bucket_cad_origin():
    p_top = [-0.62, -0.24, 0.115]                   # visible top of the 115 mm bucket
    origin = tabletop_contact_B(p_top, table_z_B_m=0.0)
    assert origin[2] == 0.0 and origin[:2] == pytest.approx(p_top[:2])
    d = copy.deepcopy(phys_dict())
    d["objects"]["blue_bucket_01"]["pose_policy"] = "tabletop_contact_preserve_orientation"
    with pytest.raises(ValueError, match="table_z_base_m"):
        TwinConfig.model_validate(d)
