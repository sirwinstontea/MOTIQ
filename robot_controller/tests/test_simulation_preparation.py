"""Offline preparation CLI never starts an executor or moves a robot."""
from types import SimpleNamespace
import socket

import pytest

from robot_control import cli
from robot_control.backends import robodk_sim
from robot_control.models import ErrorCode, RobotControlError


@pytest.mark.parametrize('repair', [False, True])
def test_preparation_uses_selected_config_and_explicit_repair_flag(monkeypatch, robot_cfg, scene_cfg, capsys, repair):
    def closed(*args, **kwargs): raise ConnectionRefusedError()
    monkeypatch.setattr(socket, 'create_connection', closed)
    monkeypatch.setattr(cli, 'load_robot_config', lambda path: robot_cfg.model_copy(update={'backend': 'robodk_sim'}))
    monkeypatch.setattr(cli, 'load_scene_config', lambda path: scene_cfg)
    calls = []
    class Setup:
        def __init__(self, robot, scene): calls.append(('selected', robot.model, scene))
        def connect(self): calls.append('connect')
        def check_ready(self): return []
        def repair_resting_contacts(self): calls.append('repair'); return ['blue_bucket_01']
        def disconnect(self): calls.append('disconnect')
    monkeypatch.setattr(robodk_sim, 'RoboDKSimController', Setup)
    assert cli.cmd_prepare_simulation(SimpleNamespace(config='robot', scene='scene', repair_resting_contacts=repair)) == 0
    assert ('repair' in calls) == repair
    assert calls[-1] == 'disconnect'
    assert '"robot_motion_requested": false' in capsys.readouterr().out


def test_preparation_refuses_running_controller_even_if_its_robot_connection_is_broken(monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setattr(socket, 'create_connection', lambda *args, **kwargs: nullcontext())
    with pytest.raises(RobotControlError) as caught:
        cli.cmd_prepare_simulation(SimpleNamespace())
    assert caught.value.code == ErrorCode.BUSY


def test_preparation_rejects_non_simulation_selection(monkeypatch, robot_cfg):
    def closed(*args, **kwargs): raise ConnectionRefusedError()
    monkeypatch.setattr(socket, 'create_connection', closed)
    monkeypatch.setattr(cli, 'load_robot_config', lambda path: robot_cfg.model_copy(update={'backend': 'physical_disabled'}))
    with pytest.raises(RobotControlError) as caught:
        cli.cmd_prepare_simulation(SimpleNamespace(config='robot'))
    assert caught.value.code == ErrorCode.MODE_VIOLATION
