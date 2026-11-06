"""Simulated-camera search: ONNX + YOLO-World on the RoboDK camera, no taught look path, pick at the measured point."""
import sys
from pathlib import Path

import pytest

from robot_control.backends.robodk_sim import pick_pose_from_vision
from robot_control.models import PickParams

REPO = Path(__file__).resolve().parents[2]


def test_pick_pose_keeps_the_taught_height_above_the_measured_surface():
    grasp_z = 0.025
    approach = [-0.45, -0.20, 0.150, 3.1416, 0.0, 0.0]
    grasp = [-0.45, -0.20, grasp_z, 3.1416, 0.0, 0.0]
    surface = (0.41, -0.12, 0.03)
    assert pick_pose_from_vision(grasp, grasp_z, surface) == [0.41, -0.12, 0.03, 3.1416, 0.0, 0.0]
    assert pick_pose_from_vision(approach, grasp_z, surface)[2] == pytest.approx(0.155)


def test_seen_point_must_be_three_finite_metres():
    assert PickParams(object_id="green_ring_02", seen_xyz_m=[0.4, -0.1, 0.02]).seen_xyz_m == [0.4, -0.1, 0.02]
    with pytest.raises(ValueError):
        PickParams(object_id="green_ring_02", seen_xyz_m=[0.4, -0.1])
    with pytest.raises(ValueError):
        PickParams(object_id="green_ring_02", seen_xyz_m=[0.4, float("nan"), 0.02])


def _bridge(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    monkeypatch.setenv("SIM_VISION", "1")
    sys.path.insert(0, str(REPO / "robot_controller" / "bridge"))
    import motiq_bridge as mb
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


def test_a_miss_steps_on_a_generated_grid_not_a_named_target(tmp_path, monkeypatch):
    mb, b = _bridge(tmp_path, monkeypatch)
    monkeypatch.setattr(mb, "sim_search_offsets_m", lambda: [(0.08, 0.0)])
    b.vision_frame = lambda: {"frame_id": 1, "ts": __import__("time").time(), "objects": []}
    moves = []

    class Arm:
        sim = True

        def shift_xy(self, x, y):
            moves.append((round(x, 3), round(y, 3)))
            return {"ok": True}

        def tcp_now(self):
            return [0.30, 0.0, 0.40, 0, 0, 0]

    b.physical = Arm()
    r = b.find("green_ring_02")
    assert r["ok"] is False
    assert moves == [(0.38, 0.0), (0.30, 0.0)]
    assert b.tasks == []


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
