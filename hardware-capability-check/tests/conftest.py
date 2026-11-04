import copy
import json
from pathlib import Path

import pytest
import yaml

from hardware_capability_check import load_profiles

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "skill_pick_place_red_cube.json"


@pytest.fixture(scope="session")
def profiles():
    return load_profiles()


@pytest.fixture(scope="session")
def example_dict():
    return json.loads(EXAMPLE.read_text(encoding="utf-8-sig"))


@pytest.fixture()
def skill_dict(example_dict):
    return copy.deepcopy(example_dict)


@pytest.fixture()
def actuated_profiles(tmp_path):
    """Same hardware, but with a parallel gripper that can close (opening 80 mm, 40 N, 0.5 kg)."""
    tool = yaml.safe_load((ROOT / "profiles" / "tool_custom_3d_printed.yaml").read_text(encoding="utf-8"))
    t = tool["tool"]
    t["id"] = "test_parallel_gripper"
    t["name"] = "Test parallel gripper"
    t["actuation"].update({"actuated": True, "kind": "parallel", "opening_max_m": 0.08, "opening_min_m": 0.0, "grip_force_n": 40.0, "control_interface": "tool_digital_output"})
    t["mass_kg"] = 0.5
    t["tcp_offset_m"] = 0.15
    t["cog_offset_from_flange_m"] = 0.08
    (tmp_path / "tool_actuated.yaml").write_text(yaml.safe_dump(tool), encoding="utf-8")
    return load_profiles(tool=str(tmp_path / "tool_actuated.yaml"))
