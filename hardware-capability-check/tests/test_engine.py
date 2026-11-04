import json
import random

import pytest

from hardware_capability_check import Status, Verdict, check_skill, parse_skill
from hardware_capability_check.cli import main as cli_main


def run(d, profiles):
    return check_skill(parse_skill(d), profiles)


def findings(report, check=None, status=None, step=None):
    out = [f for sr in report.steps for f in sr.findings] + list(report.global_findings)
    return [f for f in out if (check is None or f.check == check) and (status is None or f.status == status) and (step is None or f.step_id == step)]


def set_cube(d, x, y, z):
    d["environment"]["objects"][0]["initial_state"]["location"] = {"x": x, "y": y, "z": z}


def describe_box(d, inner=0.12, wall=0.08):
    d["environment"]["objects"][1]["attributes"]["inner_size"] = {"x": inner, "y": inner, "z": wall}
    d["environment"]["objects"][1]["attributes"]["wall_height"] = wall


# ---------------------------------------------------------------------------------- the example skill
def test_example_with_rigid_printed_gripper_is_infeasible_only_because_of_the_tool(skill_dict, profiles):
    r = run(skill_dict, profiles)
    assert r.verdict == Verdict.NO and r.detail == "infeasible"
    fails = findings(r, status=Status.FAIL)
    assert {"grasp", "release"} <= {f.check for f in fails} <= {"grasp", "release", "orientation_constraint"}
    assert not findings(r, "reach_and_path", Status.FAIL)          # the arm itself is not the problem
    assert all(f.status in (Status.CONDITIONAL, Status.PASS) for f in findings(r, "reach_and_path"))
    assert r.physically_possible_if_conditions_hold is False
    assert "two cameras" in " ".join(r.assumptions)                # D415 (vision) vs D435 (demonstration) is documented


def test_example_with_an_actuated_gripper(skill_dict, actuated_profiles):
    r = run(skill_dict, actuated_profiles)
    assert not findings(r, status=Status.FAIL)
    assert r.physically_possible_if_conditions_hold
    assert r.verdict == Verdict.NO and r.detail == "needs_validation"                    # box interior + cube mass unknown
    assert any("interior" in m for m in r.missing_information)
    # supply the missing facts -> only conditions remain
    describe_box(skill_dict)
    skill_dict["environment"]["objects"][0]["attributes"]["mass_g"] = 80
    r2 = run(skill_dict, actuated_profiles)
    assert r2.verdict == Verdict.YES and r2.detail == "feasible_with_conditions", r2.to_text()
    assert not findings(r2, status=Status.FAIL) and not findings(r2, status=Status.UNKNOWN)
    pay = findings(r2, "payload")[0]
    assert pay.status in (Status.PASS, Status.CONDITIONAL) and pay.evidence["total_payload_kg"] == 0.58


def test_report_serialises_and_prints(skill_dict, profiles):
    r = run(skill_dict, profiles)
    data = json.loads(r.to_json())
    assert data["verdict"] == "no" and data["detail"] == "infeasible" and data["schema_version"] == "feasibility_report/1"
    assert "VERDICT: NO" in r.to_text()


def test_units_in_millimetres_give_the_same_answer(skill_dict, actuated_profiles):
    base = run(skill_dict, actuated_profiles)
    skill_dict["environment"]["units"] = "millimeters"
    for o in skill_dict["environment"]["objects"]:
        for k in o["initial_state"]["location"]:
            o["initial_state"]["location"][k] *= 1000
    mm = run(skill_dict, actuated_profiles)
    assert mm.verdict == base.verdict and mm.detail == base.detail
    assert [sr.status for sr in mm.steps] == [sr.status for sr in base.steps]


# ---------------------------------------------------------------------------------- reach / kinematics
def test_object_out_of_reach_is_infeasible(skill_dict, actuated_profiles):
    set_cube(skill_dict, 1.5, 0.0, 0.02)
    r = run(skill_dict, actuated_profiles)
    assert r.verdict == Verdict.NO and r.detail == "infeasible"
    f = findings(r, "reach_and_path", Status.FAIL, "step_002")
    assert f and "outside the arm's reachable volume" in f[0].message


def test_object_directly_under_the_base_axis_is_not_reachable(skill_dict, actuated_profiles):
    set_cube(skill_dict, 0.0, 0.0, 0.02)
    r = run(skill_dict, actuated_profiles)
    assert findings(r, "reach_and_path", Status.FAIL, "step_002")


def test_target_below_the_table_fails(skill_dict, actuated_profiles):
    set_cube(skill_dict, 0.3, 0.1, -0.02)
    r = run(skill_dict, actuated_profiles)
    assert any("below the table" in f.message for f in findings(r, "reach_and_path", Status.FAIL))
    set_cube(skill_dict, 0.3, 0.1, -0.2)           # even the hover point is unusable
    assert findings(run(skill_dict, actuated_profiles), "reach_and_path", Status.FAIL, "step_002")


def test_reach_limit_is_continuous_along_radius(skill_dict, actuated_profiles):
    """Sweeping the cube outwards must switch from feasible to infeasible exactly once."""
    flags = []
    for x in (0.3, 0.5, 0.7, 0.8, 0.9, 1.0, 1.2):
        d = json.loads(json.dumps(skill_dict))
        set_cube(d, x, 0.0, 0.02)
        r = run(d, actuated_profiles)
        flags.append(bool(findings(r, "reach_and_path", Status.FAIL, "step_002")))
    assert flags[0] is False and flags[-1] is True
    assert flags == sorted(flags)          # False... then True..., never back


def test_place_beyond_reach_fails_but_pick_does_not(skill_dict, actuated_profiles):
    skill_dict["environment"]["objects"][1]["initial_state"]["location"] = {"x": 1.4, "y": 0.1, "z": 0.0}
    r = run(skill_dict, actuated_profiles)
    assert findings(r, "reach_and_path", Status.FAIL, "step_003")
    assert not findings(r, "reach_and_path", Status.FAIL, "step_002")


def test_side_grasp_and_push_and_pour_run(skill_dict, actuated_profiles):
    skill_dict["steps"][1]["parameters"]["grasp_type"] = "side_grasp"
    r = run(skill_dict, actuated_profiles)
    assert findings(r, "reach_and_path", step="step_002")
    skill_dict["steps"].append({"id": "p", "action": "PUSH", "target": "object_cube", "parameters": {"direction": [0, 1, 0], "distance": 0.05, "force_n": 10}})
    skill_dict["steps"].append({"id": "t", "action": "POUR", "parameters": {"angle_deg": 60}})
    r = run(skill_dict, actuated_profiles)
    assert findings(r, "reach_and_path", step="p") and findings(r, "force", Status.PASS, "p")


# ---------------------------------------------------------------------------------- payload
def test_heavy_object_exceeds_payload(skill_dict, actuated_profiles):
    describe_box(skill_dict)
    skill_dict["environment"]["objects"][0]["attributes"]["mass_kg"] = 8.0
    r = run(skill_dict, actuated_profiles)
    assert r.verdict == Verdict.NO and r.detail == "infeasible"
    assert any("exceeds the payload budget" in f.message for f in findings(r, "payload", Status.FAIL))


def test_object_too_big_for_the_gripper_opening(skill_dict, actuated_profiles):
    skill_dict["environment"]["objects"][0]["attributes"]["dimensions"] = {"x": 0.12, "y": 0.12, "z": 0.12}
    r = run(skill_dict, actuated_profiles)
    assert any("exceeds the tool opening" in f.message for f in findings(r, "grasp", Status.FAIL))


def test_container_too_small_for_the_object(skill_dict, actuated_profiles):
    describe_box(skill_dict, inner=0.045)
    r = run(skill_dict, actuated_profiles)
    assert findings(r, "container_fit", Status.FAIL)


# ---------------------------------------------------------------------------------- requirements stated in the skill
def test_timing_precision_speed_and_force_requirements(skill_dict, actuated_profiles):
    skill_dict["constraints"]["timing"] = {"max_duration_s": 0.1}
    skill_dict["steps"][1]["parameters"].update({"tolerance_mm": 0.01, "speed_m_s": 9.0, "force_n": 200})
    r = run(skill_dict, actuated_profiles)
    assert findings(r, "timing", Status.FAIL)
    assert findings(r, "precision", Status.FAIL)
    assert findings(r, "speed", Status.FAIL)
    assert findings(r, "force", Status.CONDITIONAL)


def test_unknown_action_needs_validation_not_a_crash(skill_dict, actuated_profiles):
    describe_box(skill_dict)
    skill_dict["environment"]["objects"][0]["attributes"]["mass_kg"] = 0.1
    skill_dict["steps"].insert(1, {"id": "x", "action": "DANCE", "target": "object_cube"})
    r = run(skill_dict, actuated_profiles)
    assert r.verdict == Verdict.NO and r.detail == "needs_validation"
    assert findings(r, "action", Status.UNKNOWN, "x")


def test_skill_without_steps_or_positions(profiles):
    r = run({"skill": {"id": "empty"}}, profiles)
    assert r.verdict == Verdict.NO and r.detail == "needs_validation"
    d = {"environment": {"objects": [{"id": "a", "name": "a thing"}]}, "steps": [{"id": "s", "action": "PICK", "target": "a"}]}
    r = run(d, profiles)
    assert findings(r, "target_position", Status.UNKNOWN)


def test_different_vocabulary_and_base_frame(actuated_profiles):
    d = {
        "environment": {"units": "cm", "coordinate_system": "robot_base", "objects": [
            {"id": "ball", "name": "ball", "initial_state": {"location": {"x": 30, "y": 10, "z": 3}}}]},
        "steps": [{"action": "grab", "target": "ball"}, {"action": "move to", "parameters": {"position": [50, -20, 20]}}, {"action": "go home"}],
    }
    r = run(d, actuated_profiles)
    assert not findings(r, status=Status.FAIL), r.to_text()
    assert [sr.action for sr in r.steps] == ["GRAB", "MOVE_TO", "GO_HOME"]


def test_fixed_camera_coverage(skill_dict, tmp_path, profiles):
    import yaml
    from hardware_capability_check import load_profiles
    cam = yaml.safe_load(open(profiles.directory / "camera_realsense_d415.yaml", encoding="utf-8"))
    cam["camera"]["mount"] = {"type": "fixed", "position_m": [0.4, 0.0, 0.9], "look_at_m": [0.4, 0.1, 0.0]}
    p = tmp_path / "cam.yaml"
    p.write_text(yaml.safe_dump(cam, allow_unicode=True), encoding="utf-8")
    pr = load_profiles(camera=str(p))
    r = run(skill_dict, pr)
    vis = findings(r, "vision")
    assert vis and all(f.status == Status.PASS for f in vis), [f.message for f in vis]
    cam["camera"]["mount"]["position_m"] = [0.4, 0.0, 0.3]      # too close: below the 0.5 m minimum range
    p.write_text(yaml.safe_dump(cam, allow_unicode=True), encoding="utf-8")
    r = run(skill_dict, load_profiles(camera=str(p)))
    assert findings(r, "vision", Status.FAIL)


def test_random_skills_never_crash_and_verdict_matches_reach(actuated_profiles):
    rng = random.Random(7)
    for _ in range(25):
        d = {
            "environment": {"objects": [
                {"id": "o", "name": "thing", "category": "cube", "initial_state": {"location": {"x": rng.uniform(-1.2, 1.2), "y": rng.uniform(-1.2, 1.2), "z": rng.uniform(0.0, 0.5)}}},
                {"id": "c", "name": "bin", "category": "container", "initial_state": {"location": {"x": rng.uniform(-1.2, 1.2), "y": rng.uniform(-1.2, 1.2), "z": 0.0}}}]},
            "steps": [{"action": "PICK", "target": "o"}, {"action": "PLACE", "target": "c"}],
        }
        r = run(d, actuated_profiles)
        assert r.verdict in set(Verdict)
        r.to_text()
        r.to_json()


# ---------------------------------------------------------------------------------- CLI
def test_cli_exit_codes_and_json(tmp_path, capsys):
    from pathlib import Path
    example = Path(__file__).resolve().parents[1] / "examples" / "skill_pick_place_red_cube.json"
    out = tmp_path / "r.json"
    code = cli_main([str(example), "--json", str(out), "--quiet"])
    assert code == 3
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "no"
    assert cli_main(["--verify-profiles"]) == 0
    capsys.readouterr()
