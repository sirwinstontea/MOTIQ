"""Opt-in URSim + PyBullet synchronized integration test. Never connects to hardware.

Run:  RUN_URSIM_TESTS=1 python -m pytest -m integration tests/test_ursim_sync_smoke.py -q
Needs: PyBullet GUI with the demo station (setup-station) and the URSim container
(robot RUNNING: powered on, brakes released) on 127.0.0.1.
"""
import json
import math
import os
import socket
from pathlib import Path

import pytest

from robot_control.backends.ursim_sync import URSimSyncController
from robot_control.models import ActionStatus
from robot_control.scene_registry import SceneRegistry
from robot_control.task_executor import TaskExecutor
from robot_control.vision import MockDetector

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]


def _up(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture
def executor(robot_cfg, safety_cfg, scene_cfg):
    if os.environ.get("RUN_URSIM_TESTS") != "1" or not (_up(20500) and _up(30004)):
        pytest.skip("set RUN_URSIM_TESTS=1 with PyBullet + URSim running")
    pytest.importorskip("rtde_control")
    c = URSimSyncController(robot_cfg, scene_cfg)
    ex = TaskExecutor(c, robot_cfg, safety_cfg, SceneRegistry(scene_cfg), MockDetector())
    ex.start()
    yield ex, c
    c.disconnect()


def test_object_task_runs_in_sync(executor):
    ex, c = executor
    task = json.loads((ROOT / "examples/objects/orange_screw_03_pick_place.json").read_text())
    st = ex.submit(task, wait=True)
    assert st.status == ActionStatus.SUCCEEDED, st.error
    ur = c._ursim_joints_deg()
    dk = c.robot.Joints().list()
    assert max(abs(a - b) for a, b in zip(ur, dk)) < 0.5
    import rtde_receive
    r = rtde_receive.RTDEReceiveInterface("127.0.0.1")
    try:
        tcp = r.getActualTCPPose()
    finally:
        r.disconnect()
    home = ex.scene.cfg.targets["home"].pose_m_rad
    assert math.dist(tcp[:3], home[:3]) < 0.002           # URSim TCP (same 150 mm offset) is at home
