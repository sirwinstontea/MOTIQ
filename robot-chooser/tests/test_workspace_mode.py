import yaml

from robot_control.config import load_safety_config


def test_kinematic_mode_is_generated_and_persisted(ch, repo):
    state = ch.update_hardware({'workspace_mode': 'kinematic'})
    assert state['report']['status'] == 'READY'
    config = load_safety_config(repo / 'robot_controller/config/safety.yaml')
    assert config.workspace.mode == 'kinematic'
    assert config.require_path_check and config.require_collision_geometry
    stored = yaml.safe_load((repo / 'robot-chooser/hardware.yaml').read_text(encoding='utf-8'))
    assert stored['workspace_mode'] == 'kinematic'
    # Reapplying does not silently restore a table-footprint box.
    ch.apply()
    assert load_safety_config(repo / 'robot_controller/config/safety.yaml').workspace.mode == 'kinematic'


def test_box_mode_remains_available(ch, repo):
    ch.update_hardware({'workspace_mode': 'box'})
    config = load_safety_config(repo / 'robot_controller/config/safety.yaml')
    assert config.workspace.mode == 'box'


def test_contact_policy_persists_and_uses_table_surface_floor(ch, repo):
    state = ch.update_hardware({'workspace_mode': 'kinematic', 'collision_policy': 'table_and_tagged'})
    assert state['report']['status'] == 'READY'
    scene = yaml.safe_load((repo / 'robot_controller/config/scene.yaml').read_text())
    table = scene['station_geometry'][scene.get('table_geometry', 'table')]
    assert scene['collision_policy'] == 'table_and_tagged'
    safety = load_safety_config(repo / 'robot_controller/config/safety.yaml')
    assert safety.workspace.z_m[0] == table['pose_m_rad'][2] + table['size_m'][2]
    assert safety.require_path_check and safety.require_collision_geometry
    ch.apply()
    stored = yaml.safe_load((repo / 'robot-chooser/hardware.yaml').read_text())
    assert stored['collision_policy'] == 'table_and_tagged'
