"""Any hardware: devices are components of types defined in hardware_types/*.yaml (no code per device)."""
import pytest
import yaml

from robot_chooser.core import Chooser, ChooserError


def _y(p):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _codes(state, level="blockers"):
    return {i["code"] for i in state["report"][level]}


def test_arm_chain_mass_counts_against_payload_and_reaches_the_controller(ch, repo):
    ch.add_component({"id": "changer", "type": "tool_changer", "name": "Quick changer", "attached_to": "arm",
                      "spec": {"mass_kg": 0.3}})
    ch.update_component("main_tool", {"attached_to": "changer"})
    s = ch.add_component({"id": "wrist_cam", "type": "camera", "name": "Wrist D405", "attached_to": "main_tool",
                          "spec": {"role": "vision", "mass_kg": 0.06}})
    assert s["report"]["carried_mass_kg"] == pytest.approx(1.36) and s["report"]["status"] == "READY"
    assert s["report"]["end_effector"] == "main_tool"
    assert _y(repo / "robot_controller" / "config" / "robot.yaml")["tool_mass_kg"] == pytest.approx(1.36)
    ch.add_component({"id": "top_cam", "type": "camera", "name": "Overhead", "attached_to": "cell",
                      "spec": {"role": "monitoring"}})                      # cell-mounted: no mass needed
    assert ch.state()["report"]["carried_mass_kg"] == pytest.approx(1.36)


def test_unknown_mass_on_the_arm_blocks(ch, repo):
    s = ch.add_component({"id": "cam", "type": "camera", "name": "Cam", "attached_to": "arm",
                          "spec": {"role": "vision"}})
    assert "PAYLOAD" in _codes(s) and not (repo / "robot_controller" / "config" / "robot.yaml").exists()
    assert not (repo / "robot_controller" / "config" / "robots" / "ur_ur5e.yaml").exists()   # never understated


def test_two_end_effectors_on_the_arm_block(ch):
    s = ch.add_component({"id": "vac", "type": "gripper", "name": "Vacuum", "attached_to": "arm",
                          "spec": {"kind": "vacuum", "actuated": True, "mass_kg": 0.2,
                                   "tcp_offset_m_rad": [0, 0, 0.1, 0, 0, 0]}})
    assert "TOOLING" in _codes(s)


def test_no_end_effector_means_flange_tcp(ch, repo):
    s = ch.update_component("main_tool", {"attached_to": "cell"})       # tool parked in the cell
    assert s["report"]["status"] == "READY" and s["report"]["end_effector"] is None
    robot = _y(repo / "robot_controller" / "config" / "robot.yaml")
    assert robot["gripper_kind"] == "none" and robot["tcp_offset_m_rad"] == [0, 0, 0, 0, 0, 0]


def test_new_hardware_type_is_a_file_not_code(ch, repo):
    (repo / "robot_controller" / "robot-chooser" / "hardware_types" / "conveyor.yaml").write_text(yaml.safe_dump({
        "type": "conveyor", "label": "Conveyor belt", "attach_to": ["cell"], "roles": ["other"], "payload_field": None,
        "fields": {"length_m": {"type": "number", "min": 0, "unit": "m", "required": True},
                   "speed_m_s": {"type": "number", "min": 0, "max": 2, "unit": "m/s"}},
        "drivers": [{"id": "plc_modbus", "description": "PLC over Modbus TCP", "connects_via": "plc bridge",
                     "physical": True, "actuates": True}]}), encoding="utf-8")
    s = ch.add_component({"id": "belt", "type": "conveyor", "name": "Infeed belt", "driver": "plc_modbus",
                          "spec": {"length_m": 1.5, "speed_m_s": 0.2}})
    assert "conveyor" in s["types"]
    belt = next(c for c in s["report"]["components"] if c["id"] == "belt")
    assert "never enabled" in belt["connection"] and "CONNECTION" in _codes(s, "warnings")
    with pytest.raises(ChooserError):                                     # its own field rules apply
        ch.update_component("belt", {"spec": {"speed_m_s": 5}})
    with pytest.raises(ChooserError):                                     # only cell-mounted
        ch.add_component({"id": "belt2", "type": "conveyor", "name": "x", "attached_to": "arm",
                          "spec": {"length_m": 1}})


def test_generic_type_records_anything(ch):
    s = ch.add_component({"id": "ring_light", "type": "generic", "name": "Ring light", "attached_to": "cell",
                          "spec": {"category": "light", "power_w": 12, "color_temp_k": 5600}})
    assert any(c["id"] == "ring_light" for c in s["report"]["components"])
    with pytest.raises(ChooserError):
        ch.add_component({"id": "x", "type": "generic", "name": "x", "spec": {"Bad Key": 1, "category": "y"}})


@pytest.mark.parametrize("bad, why", [
    ({"type": "teleporter"}, "unknown type"),
    ({"type": "camera", "spec": {"role": "vision", "zoom": 3}}, "unknown field"),
    ({"type": "camera", "spec": {}}, "missing required field"),
    ({"type": "camera", "spec": {"role": "thermal"}}, "enum"),
    ({"type": "camera", "spec": {"role": "vision", "mass_kg": float("nan")}}, "nan"),
    ({"type": "camera", "spec": {"role": "vision", "pose_m_rad": [1, 2]}}, "vector length"),
    ({"type": "camera", "spec": {"role": "vision"}, "driver": "ros2"}, "unknown driver"),
    ({"type": "camera", "spec": {"role": "vision"}, "attached_to": "nowhere"}, "unknown parent"),
    ({"type": "mobile_base", "spec": {"kind": "legged"}, "attached_to": "arm"}, "attach rule"),
    ({"type": "camera", "spec": {"role": "vision"}, "id": "arm"}, "reserved id"),
])
def test_component_validation(ch, bad, why):
    with pytest.raises(ChooserError):
        ch.add_component({"id": "dev", "name": "Device", **bad})


def test_attachment_cycles_are_refused(ch):
    ch.add_component({"id": "changer", "type": "tool_changer", "name": "C", "attached_to": "arm",
                      "spec": {"mass_kg": 0.2}})
    ch.add_component({"id": "ft", "type": "force_torque_sensor", "name": "FT", "attached_to": "changer",
                      "spec": {"mass_kg": 0.3}})
    with pytest.raises(ChooserError):
        ch.update_component("changer", {"attached_to": "ft"})


def test_mobile_base_is_documented_not_driven(ch):
    s = ch.add_component({"id": "base", "type": "mobile_base", "name": "Mobile base", "driver": "external",
                          "spec": {"kind": "wheeled"}})
    base = next(c for c in s["report"]["components"] if c["id"] == "base")
    assert "physical actuator" in base["connection"] and s["report"]["status"] == "READY"


def test_v1_hardware_yaml_is_migrated(repo):
    v1 = {"schema": "motiq_hardware/1", "active_robot": "ur_ur5e", "backend": "mock", "speed_percent": 10,
          "tool": {"name": "Old tool", "gripper_kind": "symbolic", "actuated": False, "mass_kg": 0.8,
                   "mass_status": "measured", "tcp_offset_m_rad": [0, 0, 0.12, 0, 0, 0], "tcp_status": "measured"},
          "cameras": [{"id": "cam1", "name": "Cam 1", "role": "vision", "serial": "123", "calibrated": True}]}
    (repo / "robot_controller" / "robot-chooser" / "hardware.yaml").write_text(yaml.safe_dump(v1), encoding="utf-8")
    hw = Chooser(repo).load_hardware()
    tool = next(c for c in hw.components if c.id == "main_tool")
    assert tool.attached_to == "arm" and tool.spec["mass_kg"] == 0.8 and tool.status == "measured"
    assert next(c for c in hw.components if c.id == "cam1").spec["calibrated"] is True
    s = Chooser(repo).apply()
    assert s["report"]["status"] == "READY" and s["hardware"]["schema"] == "motiq_hardware/2"
