"""The catalog is open: any arm (1-12 axes) can be added, configured, selected and removed."""
import pytest
import yaml

from robot_chooser.core import Chooser, ChooserError

IIWA = {"id": "kuka_lbr_iiwa7", "brand": "KUKA", "model": "LBR iiwa 7 R800", "dof": 7,
        "nominal_reach_m": 0.8, "nominal_payload_kg": 7.0,
        "library_url": "https://robodk.com/robot/KUKA/LBR-iiwa-7-R800", "library_file": "iiwa7.robot"}
TEST_LIMITS = {"verified": True, "source": "TEST ONLY - not real iiwa data",
               "joint_min_rad": [-1.0] * 7, "joint_max_rad": [1.0] * 7, "max_joint_speed_rad_s": [1.0] * 7,
               "max_joint_accel_rad_s2": 1.0, "max_tcp_speed_m_s": 0.5, "max_tcp_accel_m_s2": 1.0,
               "max_payload_kg": 7.0, "reach_m": 0.8}


def _y(p):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def test_any_robot_can_be_added_configured_and_selected(ch, repo):
    s = ch.add_robot(IIWA)
    row = next(r for r in s["catalog"] if r["id"] == "kuka_lbr_iiwa7")
    assert row["status"] == "Config incomplete" and row["robodk_item_name"] == "LBR iiwa 7 R800"
    assert ch.filter_catalog(brand="KUKA") == ["kuka_lbr_iiwa7"] and ch.filter_catalog(dof=7) == ["kuka_lbr_iiwa7"]
    scenes = repo / "robot_controller" / "config" / "scenes"
    scenes.mkdir()
    scene = {"home_target": "home", "targets": {"home": {"joints_rad": [0.0] * 7},
                                                "above": {"pose_m_rad": [0.4, 0.0, 0.3, 3.14159, 0, 0]}},
             "objects": {}, "destinations": {}, "patterns": {}}
    (scenes / "kuka_lbr_iiwa7.yaml").write_text(yaml.safe_dump(scene), encoding="utf-8")
    ch.update_robot("kuka_lbr_iiwa7", {
        "scene_file": "kuka_lbr_iiwa7.yaml", "limits": TEST_LIMITS,
        "default_workspace": {"x_m": [0.2, 0.6], "y_m": [-0.3, 0.3], "z_m": [0.02, 0.5]}})
    s = ch.select("kuka_lbr_iiwa7")                       # mock backend: .robot file not required
    assert s["report"]["status"] == "READY", s["report"]["blockers"]
    robot = _y(repo / "robot_controller" / "config" / "robot.yaml")
    assert robot["model"] == "LBR iiwa 7 R800" and robot["dof"] == 7 and len(robot["limits"]["joint_min_rad"]) == 7
    assert Chooser(repo).load_catalog().get("kuka_lbr_iiwa7").dof == 7     # persisted in catalog.yaml


def test_add_robot_validation(ch):
    ch.add_robot(IIWA)
    with pytest.raises(ChooserError) as e:
        ch.add_robot(IIWA)
    assert e.value.code == "DUPLICATE_ROBOT" and e.value.http == 409
    for bad in ({"id": "Bad Id!"}, {"dof": 0}, {"dof": 13}, {"nominal_reach_m": -1},
                {"library_url": "javascript:alert(1)"}, {"library_url": "http://robodk.com/x"},
                {"library_file": "../x.robot"}):
        with pytest.raises(ChooserError):
            ch.add_robot({**IIWA, "id": "x_robot", **bad})


def test_remove_robot(ch, repo):
    with pytest.raises(ChooserError) as e:
        ch.remove_robot("ur_ur5e")                        # active robot
    assert e.value.http == 409
    gen = repo / "robot_controller" / "config" / "robots" / "ur_ur3e.yaml"
    assert gen.is_file()
    s = ch.remove_robot("ur_ur3e")
    assert "ur_ur3e" not in [r["id"] for r in s["catalog"]] and not gen.exists()
    assert s["report"]["status"] == "READY"
