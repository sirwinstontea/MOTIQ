"""Simulated-camera search: ONNX + YOLO-World on the PyBullet camera, no taught look path, pick at the measured point."""
from pathlib import Path

import pytest

from robot_control.backends.pybullet_sim import pose_at_camera_xy
from robot_control.models import PickParams

REPO = Path(__file__).resolve().parents[2]


def test_the_camera_sets_the_table_position_and_the_height_stays_taught():
    approach = [-0.45, -0.20, 0.150, 3.1416, 0.0, 0.0]
    grasp = [-0.45, -0.20, 0.025, 3.1416, 0.0, 0.0]
    surface = (0.41, -0.12, 0.0)   # depth often lands on the table, under the workspace floor
    assert pose_at_camera_xy(grasp, surface) == [0.41, -0.12, 0.025, 3.1416, 0.0, 0.0]
    assert pose_at_camera_xy(approach, surface)[2] == pytest.approx(0.150)


def test_seen_point_must_be_three_finite_metres():
    assert PickParams(object_id="green_ring_02", seen_xyz_m=[0.4, -0.1, 0.02]).seen_xyz_m == [0.4, -0.1, 0.02]
    with pytest.raises(ValueError):
        PickParams(object_id="green_ring_02", seen_xyz_m=[0.4, -0.1])
    with pytest.raises(ValueError):
        PickParams(object_id="green_ring_02", seen_xyz_m=[0.4, float("nan"), 0.02])


def _bridge(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    monkeypatch.setenv("SIM_VISION", "1")
    import robot_control.arm_bridge as mb
    b = mb.Bridge("http://127.0.0.1:9", "http://127.0.0.1:9",
                  REPO / "robot_controller/config/scene.example.yaml", tmp_path / "vision.json", sim=True)
    b.tasks = []
    b.run_task = lambda actions, did: (b.tasks.append(actions), {"ok": True, "did": did})[1]
    b.ursim = None
    b._search_cfg = lambda: {"scan_targets": ["scan_a"], "look_first": "object_approach", "settle_s": 0, "look_s": 0.05}
    return mb, b


def _ring(xyz=(0.41, -0.12, 0.02)):
    obj = {"id": 3, "label": "green ring", "conf": 0.91, "bbox": [0.4, 0.4, 0.2, 0.2]}
    if xyz is not None:
        obj["xyz_base_m"] = list(xyz)
    return {"frame_id": 1, "ts": __import__("time").time(), "objects": [obj]}


def test_find_uses_the_camera_and_does_not_move_to_a_taught_pose(tmp_path, monkeypatch):
    mb, b = _bridge(tmp_path, monkeypatch)
    b.vision_frame = lambda: _ring()
    r = b.find("green_ring_02")
    assert r["ok"] is True
    assert r["models"] == ["onnx", "yolo_world"]
    assert r["searched"] == ["current pose"]
    assert b.tasks == []
    assert "scan_a" not in r["did"]


def test_a_miss_moves_the_camera_across_the_scan_poses(tmp_path, monkeypatch):
    _mb, b = _bridge(tmp_path, monkeypatch)
    b.vision_frame = lambda: {"frame_id": 1, "ts": __import__("time").time(), "objects": []}
    r = b.find("green_ring_02")
    assert r["ok"] is False
    assert b.tasks == [[("move_to_target", {"target_id": "scan_a"})]]
    assert "green_ring_02" not in str(b.tasks)


def test_pick_sends_the_measured_point_and_no_look_target(tmp_path, monkeypatch):
    _mb, b = _bridge(tmp_path, monkeypatch)
    b.vision_frame = lambda: _ring()
    r = b.tool("pick", {"object": "green ring"})
    assert r["ok"] is True
    identify, pick = b.tasks[0]
    assert identify == ("identify_object", {"object_id": "green_ring_02"})
    assert pick[0] == "pick_object"
    assert pick[1]["seen_xyz_m"] == [0.41, -0.12, 0.02]
    assert all(step[0] != "move_to_target" for step in b.tasks[0])


def test_find_reports_an_object_already_in_the_gripper(tmp_path, monkeypatch):
    _mb, b = _bridge(tmp_path, monkeypatch)
    b._gripper_holding = lambda: "green_ring_02"
    b.vision_frame = lambda: {"frame_id": 1, "ts": 0, "objects": []}
    r = b.find("green_ring_02")
    assert r["ok"] is True and r["holding"] is True
    assert "gripper" in r["did"]
    assert b.tasks == []


def test_a_second_pick_and_place_places_what_is_already_held(tmp_path, monkeypatch):
    _mb, b = _bridge(tmp_path, monkeypatch)
    b._gripper_holding = lambda: "orange_screw_03"
    b.vision_frame = lambda: {"frame_id": 1, "ts": __import__("time").time(), "objects": [
        {"id": 1, "label": "blue bucket", "conf": 0.9, "xyz_base_m": [-0.50, -0.20, 0.05]}]}
    r = b.tool("pick_and_place", {"object": "orange screw", "destination": "blue bucket"})
    assert r["ok"] is True
    assert b.tasks == [[("place_object", {"destination_id": "blue_bucket_01_inside",
                                          "seen_xyz_m": [-0.50, -0.20, 0.05]})]]
