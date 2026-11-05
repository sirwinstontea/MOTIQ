"""push_object: controller task (simulation backends), pre-flight refusals, and the taught push paths' geometry."""
import math

import pytest

from robot_control.models import ActionStatus

PUSHABLE = ["blue_bucket_01", "orange_screw_03", "white_bucket_lid_04"]


def task(*actions, task_id="push1"):
    return {"schema_version": "1.0", "task_id": task_id, "actions": [
        {"action_id": f"a{i}", "action": a, "parameters": p} for i, (a, p) in enumerate(actions, 1)]}


@pytest.mark.parametrize("oid", PUSHABLE)
def test_push_succeeds_on_mock(make_executor, oid):
    ex = make_executor()
    st = ex.submit(task(("identify_object", {"object_id": oid}), ("push_object", {"object_id": oid})), wait=True)
    assert st.status == ActionStatus.SUCCEEDED, st.error
    g = ex.c.get_gripper_state()
    assert g.state == "closed" and g.holding_object_id is None          # pushed, never grasped
    assert ex.c.pushed == [oid]


def test_ring_is_not_pushable(make_executor):
    ex = make_executor()
    st = ex.submit(task(("identify_object", {"object_id": "green_ring_02"}),
                        ("push_object", {"object_id": "green_ring_02"})), wait=True)
    assert st.status != ActionStatus.SUCCEEDED and "cannot be pushed" in st.error.message


def test_push_while_holding_is_refused_before_any_motion(make_executor):
    ex = make_executor()
    st = ex.submit(task(("identify_object", {"object_id": "orange_screw_03"}),
                        ("pick_object", {"object_id": "orange_screw_03"}),
                        ("identify_object", {"object_id": "blue_bucket_01"}),
                        ("push_object", {"object_id": "blue_bucket_01"})), wait=True)
    assert st.status != ActionStatus.SUCCEEDED and "empty gripper" in st.error.message
    assert not getattr(ex.c, "pushed", [])


def test_push_needs_a_fresh_detection(make_executor):
    ex = make_executor()
    st = ex.submit(task(("push_object", {"object_id": "blue_bucket_01"})), wait=True)
    assert st.status != ActionStatus.SUCCEEDED
    assert not getattr(ex.c, "pushed", [])


def _xy(scene, t):
    return scene.targets[t].pose_m_rad[:2]


def test_push_paths_keep_objects_on_table_and_clear_of_neighbours(scene_cfg):
    table = scene_cfg.station_geometry["table"]
    tx, ty = table.pose_m_rad[:2]
    hx, hy = table.size_m[0] / 2, table.size_m[1] / 2
    centres = {oid: _xy(scene_cfg, o.grasp_target) for oid, o in scene_cfg.objects.items()}
    radius = {oid: max(o.dimensions_m[:2]) / 2 for oid, o in scene_cfg.objects.items()}
    for oid in PUSHABLE:
        pu = scene_cfg.objects[oid].push
        s, e = _xy(scene_cfg, pu.start_target), _xy(scene_cfg, pu.end_target)
        travel = math.dist(s, e)
        assert travel == pytest.approx(pu.distance_m, abs=1e-6), oid
        c = centres[oid]
        gap = math.dist(s, c) - radius[oid]
        assert 0.02 <= gap <= 0.03, (oid, gap)                         # starts beside it, not touching
        # end of the push: object centre moved by the same vector
        nc = (c[0] + e[0] - s[0], c[1] + e[1] - s[1])
        assert abs(nc[0] - tx) + radius[oid] <= hx and abs(nc[1] - ty) + radius[oid] <= hy, (oid, nc)
        for other, oc in centres.items():
            if other != oid:
                assert math.dist(nc, oc) > radius[oid] + radius[other], (oid, other)
        # straight down beside it, push at the same height, lift straight up
        z = [scene_cfg.targets[t].pose_m_rad[2] for t in (pu.above_target, pu.start_target, pu.end_target,
                                                          pu.lift_target)]
        assert z[1] == z[2] and z[0] > 0.2 and z[3] > 0.2 and 0.015 <= z[1] < scene_cfg.objects[oid].dimensions_m[2]
        assert _xy(scene_cfg, pu.above_target) == s and _xy(scene_cfg, pu.lift_target) == e
