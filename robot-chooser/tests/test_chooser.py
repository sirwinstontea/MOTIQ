import os

import pytest
import yaml

from robot_chooser.core import Chooser, ChooserError

UR3E_SCENE = {
    "home_target": "home",
    "targets": {"home": {"joints_rad": [0.0, -1.5708, 1.5708, -1.5708, -1.5708, 0.0]},
                "above_table": {"pose_m_rad": [-0.30, 0.0, 0.20, 3.14159, 0, 0]}},
    "objects": {}, "destinations": {}, "patterns": {},
}


def _codes(state, level="blockers"):
    return {i["code"] for i in state["report"][level]}


def _y(p):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def test_catalog_has_the_five_robots_with_official_links(ch):  # A01
    rows = ch.state()["catalog"]
    assert [r["id"] for r in rows] == ["ufactory_xarm5", "ur_ur3e", "ur_ur5e", "elite_cs63", "elite_cs66"]
    assert all(r["library_url"].startswith("https://robodk.com/robot/") for r in rows)
    assert {r["id"]: r["dof"] for r in rows}["ufactory_xarm5"] == 5


def test_filter_brand_and_dof(ch):  # A02
    assert ch.filter_catalog(brand="Elite") == ["elite_cs63", "elite_cs66"]
    assert ch.filter_catalog(dof=5) == ["ufactory_xarm5"]
    assert ch.filter_catalog("ur5") == ["ur_ur5e"]
    assert len(ch.load_catalog().robots) == 5                       # filtering never edits the catalog


def test_ur5e_ready_and_generated_files_load_in_the_controller(ch, repo):  # A03
    from robot_control.config import cross_validate, load_robot_config, load_safety_config, load_scene_config
    s = ch.select("ur_ur5e")
    assert s["report"]["status"] == "READY"
    cfg = repo / "robot_controller" / "config"
    robot, safety, scene = (load_robot_config(cfg / "robot.yaml"), load_safety_config(cfg / "safety.yaml"),
                            load_scene_config(cfg / "scene.yaml"))
    cross_validate(robot, safety, scene)
    assert robot.model == "UR5e" and robot.dof == 6 and robot.tool_mass_kg == 1.0
    assert safety.speed_percent_of_model_max == 10 and safety.workspace.x_m == (-0.7, -0.2)
    sel = _y(cfg / "selection.yaml")
    assert sel["robot_id"] == "ur_ur5e" and sel["status"] == "READY" and sel["simulation_only"] is True
    assert (cfg / "robots" / "ur_ur5e.yaml").is_file()


def test_switch_to_unconfigured_robot_and_back_never_mixes_configs(ch, repo):  # A04
    cfg = repo / "robot_controller" / "config"
    v1 = _y(cfg / "selection.yaml")["selection_version"]
    s = ch.select("ur_ur3e")
    assert s["report"]["status"] == "BLOCKED" and "CONFIG_INCOMPLETE" in _codes(s)   # no UR3e scene yet
    assert not (cfg / "robot.yaml").exists() and not (cfg / "scene.yaml").exists()   # no stale UR5e files
    assert _y(cfg / "selection.yaml")["status"] == "BLOCKED"
    s = ch.select("ur_ur5e")
    assert s["report"]["status"] == "READY" and _y(cfg / "robot.yaml")["model"] == "UR5e"
    assert _y(cfg / "selection.yaml")["selection_version"] == v1 + 2


def test_xarm5_without_model_file_is_blocked_on_robodk_sim(ch, repo):  # A05, A17
    ch.update_hardware({"backend": "robodk_sim"})
    s = ch.select("ufactory_xarm5")
    assert {"MODEL_FILE_MISSING", "CONFIG_INCOMPLETE"} <= _codes(s)
    assert s["report"]["label"] == "Model missing"
    assert s["selection"]["files"] == []
    assert not (repo / "robot_controller" / "config" / "robot.yaml").exists()


def test_ur3e_becomes_ready_once_model_scene_and_workspace_exist(ch, repo):
    (repo / "robot_controller" / "stations" / "UR3e.robot").write_bytes(b"stub")
    scenes = repo / "robot_controller" / "config" / "scenes"
    scenes.mkdir()
    (scenes / "ur_ur3e.yaml").write_text(yaml.safe_dump(UR3E_SCENE), encoding="utf-8")
    ch.update_hardware({"backend": "robodk_sim"})
    ch.update_component("main_tool", {"spec": {"mass_kg": 0.5}})
    ch.update_robot("ur_ur3e", {"scene_file": "ur_ur3e.yaml",
                                "default_workspace": {"x_m": [-0.45, -0.15], "y_m": [-0.25, 0.25], "z_m": [0.015, 0.40]}})
    s = ch.select("ur_ur3e")
    assert s["report"]["status"] == "READY", s["report"]["blockers"]
    robot = _y(repo / "robot_controller" / "config" / "robot.yaml")
    assert robot["model"] == "UR3e" and robot["robot_library_file"] == "stations/UR3e.robot"
    assert robot["limits"]["max_payload_kg"] == 3.0 and robot["tool_mass_kg"] == 0.5
    assert _y(repo / "robot_controller" / "config" / "safety.yaml")["workspace"]["z_m"] == [0.015, 0.40]
    assert "scene_file: ur_ur3e.yaml" in (repo / "robot-chooser" / "catalog.yaml").read_text(encoding="utf-8")


def test_tool_heavier_than_payload_blocks(ch):  # A10
    s = ch.update_component("main_tool", {"spec": {"mass_kg": 5.0}})
    assert "PAYLOAD" in _codes(s) and s["report"]["payload_budget_kg"] == 0


def test_speed_outside_0_100_is_rejected_and_nothing_changes(ch, repo):  # A11
    before = (repo / "robot-chooser" / "hardware.yaml").read_text(encoding="utf-8")
    for bad in (0, 150, float("nan")):
        with pytest.raises(ChooserError) as e:
            ch.update_hardware({"speed_percent": bad})
        assert e.value.code == "VALIDATION_ERROR"
    assert (repo / "robot-chooser" / "hardware.yaml").read_text(encoding="utf-8") == before


def test_physical_backends_cannot_be_selected(ch):  # safety contract 9.1
    for b in ("physical_disabled", "ursim_sync"):
        with pytest.raises(ChooserError):
            ch.update_hardware({"backend": b})


def test_unknown_robot_is_404(ch):
    with pytest.raises(ChooserError) as e:
        ch.select("kuka_kr6")
    assert e.value.code == "UNKNOWN_ROBOT" and e.value.http == 404


@pytest.mark.parametrize("bad", ["../../evil.robot", "C:\\x\\UR3e.robot", "/etc/UR3e.robot", "UR3e.exe"])
def test_model_file_must_be_a_plain_name_in_stations(ch, bad):
    with pytest.raises(ChooserError) as e:
        ch.update_robot("ur_ur3e", {"library_file": bad})
    assert e.value.code == "VALIDATION_ERROR"


def test_only_cell_fields_of_a_robot_are_editable(ch):
    with pytest.raises(ChooserError):
        ch.update_robot("ur_ur5e", {"dof": 7})


def test_limits_cannot_be_guessed_partially_or_left_unverified(ch, repo):
    with pytest.raises(ChooserError):                                   # incomplete limits are refused
        ch.update_robot("elite_cs63", {"limits": {"verified": True, "source": "guess", "reach_m": 0.6}})
    s = ch.update_robot("ur_ur5e", {"limits": {"verified": False}})
    assert s["report"]["status"] == "BLOCKED" and "CONFIG_INCOMPLETE" in _codes(s)
    assert not (repo / "robot_controller" / "config" / "robots" / "ur_ur5e.yaml").exists()


def test_six_joint_targets_on_a_five_axis_robot_are_refused(ch, repo):  # A06
    scenes = repo / "robot_controller" / "config" / "scenes"
    scenes.mkdir()
    (scenes / "x5.yaml").write_text(yaml.safe_dump(UR3E_SCENE), encoding="utf-8")   # 6-value joints
    ch.update_robot("ufactory_xarm5", {
        "scene_file": "x5.yaml",
        "default_workspace": {"x_m": [-0.5, -0.2], "y_m": [-0.3, 0.3], "z_m": [0.02, 0.4]},
        "limits": {"verified": True, "source": "TEST ONLY - not real xArm5 data",
                   "joint_min_rad": [-1.0] * 5, "joint_max_rad": [1.0] * 5, "max_joint_speed_rad_s": [1.0] * 5,
                   "max_joint_accel_rad_s2": 1.0, "max_tcp_speed_m_s": 0.5, "max_tcp_accel_m_s2": 1.0,
                   "max_payload_kg": 3.0, "reach_m": 0.7}})
    s = ch.select("ufactory_xarm5")
    assert "MODEL_MISMATCH" in _codes(s) and s["report"]["status"] == "BLOCKED"
    assert "ORIENTATION" in _codes(s, "notes")


def test_hand_written_controller_file_is_backed_up_not_lost(ch, repo):
    cfg = repo / "robot_controller" / "config"
    (cfg / "robot.yaml").write_text("# my own file\n", encoding="utf-8")
    ch.apply()
    assert (cfg / "robot.yaml.bak").read_text(encoding="utf-8") == "# my own file\n"
    assert (cfg / "robot.yaml").read_text(encoding="utf-8").startswith("# GENERATED by robot-chooser")


def test_hardware_yaml_is_the_requirements_document(ch, repo):
    ch.update_component("main_tool", {"spec": {"mass_kg": 0.42}, "status": "measured"})
    doc = _y(repo / "robot-chooser" / "hardware.yaml")
    assert doc["components"][0]["spec"]["mass_kg"] == 0.42
    req = doc["requirements"]
    assert req["status"] == "READY" and req["payload_budget_kg"] == pytest.approx(4.58)
    assert "Custom 3D-printed" not in " ".join(w["message"] for w in req["warnings"] if w["code"] == "PLACEHOLDER")
    assert Chooser(repo).load_hardware().components[0].spec["mass_kg"] == 0.42          # re-loadable after rendering


def test_controller_cli_refuses_a_blocked_selection(ch, repo, monkeypatch):  # no silent UR5e fallback
    from robot_control.cli import _default_config
    from robot_control.models import RobotControlError
    monkeypatch.chdir(repo / "robot_controller")
    assert _default_config("robot") == os.path.join("config", "robot.yaml")
    ch.select("elite_cs66")
    with pytest.raises(RobotControlError) as e:
        _default_config("robot")
    assert "elite_cs66" in e.value.message and "BLOCKED" in e.value.message
