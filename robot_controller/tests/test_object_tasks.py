"""Tasks for the 4 demo objects + the URSim-synchronized backend (offline parts)."""
import json
import math
from pathlib import Path

import pytest

from robot_control.action_registry import validate_task
from robot_control.backends import create_controller
from robot_control.backends.ursim_sync import URSimSyncController, is_loopback
from robot_control.models import ActionStatus, ErrorCode, RobotControlError
from robot_control.vision import VisionEyesFileDetector

ROOT = Path(__file__).resolve().parents[1]
TASKS = sorted((ROOT / "examples/objects").glob("*.json"))
OBJ_IDS = ["blue_bucket_01", "green_ring_02", "orange_screw_03", "white_bucket_lid_04"]


def test_object_task_files_exist_and_validate():
    names = {p.name for p in TASKS}
    assert {f"{o}_pick_place.json" for o in OBJ_IDS} | {"all_objects_pick_place.json"} <= names
    for p in TASKS:
        validate_task(p.read_text())


@pytest.mark.parametrize("path", TASKS, ids=lambda p: p.stem)
def test_object_tasks_succeed_on_mock(make_executor, path):
    ex = make_executor()
    st = ex.submit(json.loads(path.read_text()), wait=True)
    assert st.status == ActionStatus.SUCCEEDED, st.error


def test_scene_slots_match_imported_object_positions(scene_cfg):
    """Grasp targets must sit on the objects as placed by scripts/import_all.py."""
    spec = json.loads((ROOT / "objects/object_specifications.json").read_text())
    fx, fy, fz, _, _, frz = spec["sim"]["frame_pose_mm_deg_when_created"]
    c, s = math.cos(math.radians(frz)), math.sin(math.radians(frz))
    for e in spec["objects"]:
        x, y = e["display_pose_mm_deg"][:2]
        bx, by = fx + c * x - s * y, fy + s * x + c * y          # Objects_Frame -> robot base (mm)
        g = scene_cfg.targets[scene_cfg.objects[e["id"]].grasp_target].pose_m_rad
        assert g[0] * 1000 == pytest.approx(bx, abs=0.5) and g[1] * 1000 == pytest.approx(by, abs=0.5), e["id"]
        cfg = json.loads((ROOT / "objects" / e["config"]).read_text())
        assert scene_cfg.objects[e["id"]].station_item == cfg["name"]


def test_symbolic_grasp_heights_within_attach_rule(scene_cfg):
    """PyBullet attaches only if the TCP is within 15 mm of the object's mid-height on its axis."""
    for oid in OBJ_IDS:
        o = scene_cfg.objects[oid]
        gz = scene_cfg.targets[o.grasp_target].pose_m_rad[2]
        assert abs(gz - o.dimensions_m[2] / 2) < 0.015, oid


def test_vision_labels_map_to_new_objects(tmp_path, scene_cfg):
    from robot_control.scene_registry import SceneRegistry
    reg = SceneRegistry(scene_cfg)
    for label, oid in [("secchio_blu", "blue_bucket_01"), ("anello_verde", "green_ring_02"),
                       ("vite_arancione", "orange_screw_03"), ("tappo_bianco", "white_bucket_lid_04")]:
        f = tmp_path / f"{label}.json"
        f.write_text(json.dumps({"ts": 0, "target": {"label": label, "confidence": 0.9}}))
        det = VisionEyesFileDetector(str(f)).identify(oid, reg)
        assert det.object_id == oid
    assert "red_cube" not in scene_cfg.objects


def test_object_shake_refused_without_mass(make_executor):
    task = {"schema_version": "1.0", "task_id": "s", "actions": [
        {"action_id": "a1", "action": "identify_object", "parameters": {"object_id": "orange_screw_03"}},
        {"action_id": "a2", "action": "pick_object", "parameters": {"object_id": "orange_screw_03"}},
        {"action_id": "a3", "action": "shake_object", "parameters": {"pattern_id": "gentle_shake", "cycles": 1}}]}
    st = make_executor().submit(task, wait=True)
    assert st.status != ActionStatus.SUCCEEDED
    assert "mass" in json.dumps(st.model_dump(mode="json")).lower()


@pytest.mark.parametrize("host,ok", [("127.0.0.1", True), ("localhost", True), ("::1", True), ("127.0.0.2", True),
                                     ("192.168.1.10", False), ("10.0.0.5", False), ("ur5e.local", False)])
def test_loopback_detection(host, ok):
    assert is_loopback(host) is ok


def test_ursim_sync_refuses_non_loopback_host(robot_cfg, scene_cfg):
    with pytest.raises(RobotControlError) as e:
        URSimSyncController(robot_cfg, scene_cfg, ursim_host="192.168.1.10")
    assert e.value.code == ErrorCode.MODE_VIOLATION
    cfg = robot_cfg.model_copy(update={"ursim_host": "10.0.0.5", "backend": "ursim_sync"})
    with pytest.raises(RobotControlError):
        create_controller(cfg, scene_cfg)


def test_ursim_sync_reports_simulation(robot_cfg, scene_cfg):
    c = URSimSyncController(robot_cfg, scene_cfg)
    caps = c.capabilities()
    assert caps.backend == "ursim_sync" and caps.simulation is True and caps.motion_enabled is False
    assert any("localhost only" in n for n in caps.notes)
