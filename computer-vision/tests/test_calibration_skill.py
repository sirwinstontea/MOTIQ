"""La skill di calibrazione per l'app (calibration_skill/): rispetta le regole di validazione dell'app e i limiti del bridge."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "calibration_skill"
# le stesse liste di groky-app/src/skills/index.ts (REAL_ARM_PRIMITIVES) e della skill template
REAL_ARM_PRIMITIVES = {"MOVE_ABOVE", "HOME", "OPEN_GRIPPER", "WAIT", "STOP", "MOVE_UP", "MOVE_DOWN", "MOVE_LEFT",
                       "MOVE_RIGHT", "MOVE_FORWARD", "MOVE_BACKWARD", "MOVE_JOINT", "ROTATE_YAW"}
UR_JOINTS = {"shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"}
TOOLS = {"calibrate_reset", "calibrate_sample", "calibrate_solve", "calibrate_look_pose"}


def _generator():
    spec = importlib.util.spec_from_file_location("make_skill", ROOT / "make_skill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_skill_file_is_what_the_generator_writes_and_passes_app_validation():
    mod = _generator()
    doc = json.loads((ROOT / "skill_calibra_camera_braccio.json").read_text(encoding="utf-8"))
    assert doc == json.loads(json.dumps(mod.build())), "rilancia: python calibration_skill/make_skill.py"
    assert doc["schema_version"] == "1.1.0" and doc["robot"]["device"] == "arm"
    ids = [s["id"] for s in doc["steps"]]
    assert len(ids) == len(set(ids))
    for s in doc["steps"]:
        assert s["action"] == "SEQUENCE" and s["primitive_sequence"]            # validateSkill: almeno una primitiva
        assert s.get("physical_tool", "calibrate_reset") in TOOLS
        for c in s["primitive_sequence"]:
            assert c["primitive"] in REAL_ARM_PRIMITIVES                         # realArmProblems: gira sul braccio vero
            p = c.get("parameters", {})
            if c["primitive"] == "MOVE_JOINT":
                assert p["joint"] in UR_JOINTS and 0.5 <= abs(p["delta_deg"]) <= mod.MAX_JOINT_DEG
            if c["primitive"].startswith("MOVE_") and c["primitive"] != "MOVE_JOINT":
                assert 0.01 <= p["distance_m"] <= mod.MAX_STEP_M                 # il bridge: passi 1..10 cm
    assert doc["safety"]["physical_execution_allowed"] is False                  # lo approva l'operatore nell'app


def test_skill_sequence_takes_12_samples_between_reset_and_solve_and_returns_home():
    doc = json.loads((ROOT / "skill_calibra_camera_braccio.json").read_text(encoding="utf-8"))
    tools = [s.get("physical_tool") for s in doc["steps"]]
    assert tools[0] == "calibrate_reset" and tools[-2:] == ["calibrate_solve", "calibrate_look_pose"]
    assert tools.count("calibrate_sample") == 12
    # la somma di tutte le mosse è zero: il braccio torna da solo nella posa di osservazione di partenza
    net: dict[str, float] = {}
    sign = {"MOVE_FORWARD": ("x", 1), "MOVE_BACKWARD": ("x", -1), "MOVE_LEFT": ("y", 1), "MOVE_RIGHT": ("y", -1),
            "MOVE_UP": ("z", 1), "MOVE_DOWN": ("z", -1)}
    for s in doc["steps"]:
        for c in s["primitive_sequence"]:
            p = c.get("parameters", {})
            if c["primitive"] in sign:
                axis, sg = sign[c["primitive"]]
                net[axis] = net.get(axis, 0.0) + sg * p["distance_m"]
            elif c["primitive"] == "MOVE_JOINT":
                net[p["joint"]] = net.get(p["joint"], 0.0) + p["delta_deg"]
    assert all(abs(v) < 1e-6 for v in net.values()), net
