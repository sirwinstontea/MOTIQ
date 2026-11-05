"""Genera skill_calibra_camera_braccio.json: la skill dell'app (schema 1.1.0) che calibra la camera sul braccio.

    python make_skill.py            # riscrive il JSON accanto a questo file

Per ogni posa: una mossa a piccoli passi (le primitive che il bridge già fa sul braccio fisico) e un campione
(`calibrate_sample` -> vision server). A fine giro il braccio torna da solo nella posa di partenza.
Le pose sono OFFSET dalla posa di partenza: (x, y, z in m; wrist_1, wrist_2, wrist_3 in gradi). x avanti, y sinistra,
z su, come direction_mapping dell'app. Wrist 1 e 2 inclinano la camera su due assi, wrist 3 la ruota.
"""
from __future__ import annotations

import json
from pathlib import Path

POSES = [  # dx, dy, dz, w1, w2, w3
    (0, 0, 0, 0, 0, 0),
    (0, 0, 0, 10, 0, 0),
    (0, 0, 0, -10, 0, 0),
    (0, 0, 0, 0, 10, 0),
    (0, 0, 0, 0, -10, 0),
    (0, 0, 0, 10, 10, 10),
    (0, 0, 0, -10, 10, -10),
    (0, 0, 0, -10, -10, 10),
    (0, 0, 0, 10, -10, -10),
    (0.03, 0, 0.03, 8, 0, 0),
    (-0.03, 0.03, -0.03, 0, 8, -8),
    (0, -0.03, 0.03, -8, -8, 8),
]
JOINTS = ["wrist_1_joint", "wrist_2_joint", "wrist_3_joint"]
AXES = [("MOVE_FORWARD", "MOVE_BACKWARD"), ("MOVE_LEFT", "MOVE_RIGHT"), ("MOVE_UP", "MOVE_DOWN")]
MAX_STEP_M, MAX_JOINT_DEG = 0.10, 20.0  # il bridge accetta passi <= 10 cm; i giunti restano prudenti


def move(prev, cur):
    """Primitive per andare da `prev` a `cur` (offset assoluti). Vuota se non ci si muove."""
    calls = []
    for i, (pos, neg) in enumerate(AXES):
        d = cur[i] - prev[i]
        if abs(d) >= 0.01:
            assert abs(d) <= MAX_STEP_M, d
            calls.append({"primitive": pos if d > 0 else neg,
                          "parameters": {"distance_m": round(abs(d), 3), "speed_mps": 0.05}})
    for j, name in enumerate(JOINTS):
        d = cur[3 + j] - prev[3 + j]
        if abs(d) >= 0.5:
            assert abs(d) <= MAX_JOINT_DEG, d
            calls.append({"primitive": "MOVE_JOINT", "parameters": {"joint": name, "delta_deg": float(d)}})
    return calls


def step(n, description, calls, tool=None):
    s = {"id": f"step_{n:03d}", "action": "SEQUENCE", "description": description,
         "primitive_sequence": calls or [{"primitive": "WAIT", "parameters": {"seconds": 0.5}}],
         "failure_policy": "REPORT_FAILURE"}
    if tool:
        s["physical_tool"] = tool
    return s


def build() -> dict:
    steps = []

    def add(description, calls, tool=None):
        steps.append(step(len(steps) + 1, description, calls, tool))

    add("Clear previous calibration samples.", [], "calibrate_reset")
    prev = POSES[0]
    for i, pose in enumerate(POSES, 1):
        calls = move(prev, pose)
        if calls:
            add(f"Pose {i}/{len(POSES)}: small move, the camera keeps looking at the sheet.", calls)
        add(f"Pose {i}/{len(POSES)}: take the sample (arm still).",
            [{"primitive": "WAIT", "parameters": {"seconds": 1}}], "calibrate_sample")
        prev = pose
    back = move(prev, POSES[0])
    if back:
        add("Back to the starting pose (the observation pose).", back)
    add("Solve the hand-eye and write the marker centers in the robot base frame.", [], "calibrate_solve")
    add("From the observation pose: fixed-pose calibration (fixed_pose.json), active immediately.", [],
        "calibrate_look_pose")
    return {
        "schema_version": "1.1.0",
        "skill": {
            "id": "arm_calibrate_camera", "name": "Calibrate arm camera", "version": "1.0.0", "status": "draft",
            "description": "Calibrates the wrist camera without touching the sheet. Before: ArUco sheet flat and still "
                           "on the table, arm in the observation pose with the sheet in view, vision server started "
                           "with --robot-ip. Takes 12 camera poses with small moves, solves, and ends with the "
                           "fixed-pose calibration. The vision server never moves the robot: only the app's small "
                           "steps do.",
            "created_at": "2026-11-05T00:00:00Z", "updated_at": "2026-11-05T00:00:00Z",
        },
        "intent": {
            "goal": "Calibrate the arm camera.",
            "natural_language_instruction": "Calibrate the arm camera from the ArUco sheet.",
            "success_conditions": ["hand_eye.json, markers_mm.json and fixed_pose.json are written.",
                                   "The arm is back in the observation pose."],
        },
        "robot": {"device": "arm", "model": "Universal Robots UR5e"},
        "environment": {"coordinate_system": "world", "units": "meters",
                        "workspace": {"type": "tabletop", "surface": "table"}, "objects": []},
        "constraints": {"object_constraints": [], "execution_constraints": {
            "collision_avoidance": True, "require_object_reidentification": False, "require_workspace_validation": True,
            "require_grasp_validation": False, "allow_adaptive_replanning": False,
            "open_gripper_before_descending": False, "lift_before_horizontal_travel": False,
            "respect_operational_limits": True}},
        "steps": steps,
        "safety": {"physical_execution_allowed": False, "requires_feasibility_check": True,
                   "requires_operator_approval": True, "requires_robot_controller_limits": True,
                   "unknown_parameters_policy": "NEEDS_VALIDATION", "stop_primitive_always_available": True,
                   "reject_moves_outside_operational_limits": True},
        "review": {"revision": 1, "approved": False, "approved_by": None, "approval_timestamp": None},
        "app": {"icon": "camera-outline"},
    }


if __name__ == "__main__":
    out = Path(__file__).with_name("skill_calibra_camera_braccio.json")
    doc = build()
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{out.name}: {len(doc['steps'])} passi")
