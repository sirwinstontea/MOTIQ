import copy
from pathlib import Path

import pytest
import yaml

from robot_control.backends.mock import MockController
from robot_control.config import RobotConfig, SafetyConfig, SceneConfig
from robot_control.scene_registry import SceneRegistry
from robot_control.task_executor import TaskExecutor
from robot_control.vision import MockDetector

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config"


def _yaml(name):
    return yaml.safe_load((CFG / name).read_text())


@pytest.fixture
def robot_dict():
    return copy.deepcopy(_yaml("robot.example.yaml"))


@pytest.fixture
def safety_dict():
    return copy.deepcopy(_yaml("safety.example.yaml"))


@pytest.fixture
def scene_dict():
    return copy.deepcopy(_yaml("scene.example.yaml"))


@pytest.fixture
def robot_cfg(robot_dict):
    return RobotConfig.model_validate(robot_dict)


@pytest.fixture
def safety_cfg(safety_dict):
    return SafetyConfig.model_validate(safety_dict)


@pytest.fixture
def scene_cfg(scene_dict):
    return SceneConfig.model_validate(scene_dict)


@pytest.fixture
def make_executor(robot_cfg, safety_cfg, scene_cfg):
    """Factory: make_executor(mock_kwargs..., detector=..., path_checker=..., safety=...)."""
    def _make(detector=None, path_checker=None, safety=None, connect=True, **mock_kwargs):
        mock = MockController(robot_cfg, scene_cfg, **mock_kwargs)
        ex = TaskExecutor(mock, robot_cfg, safety or safety_cfg, SceneRegistry(scene_cfg),
                          detector or MockDetector(), path_checker=path_checker)
        if connect:
            ex.start()
        return ex
    return _make


def demo_task(task_id="t1", cycles=2):
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "actions": [
            {"action_id": "a1", "action": "identify_object", "parameters": {"object_id": "red_cube"}},
            {"action_id": "a2", "action": "pick_object", "parameters": {"object_id": "red_cube"}},
            {"action_id": "a3", "action": "shake_object", "parameters": {"pattern_id": "gentle_shake", "cycles": cycles}},
            {"action_id": "a4", "action": "place_object", "parameters": {"destination_id": "delivery_zone"}},
        ],
    }
