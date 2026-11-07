"""Bridge tools for the new built-in skills: overview (camera only, no motion) and push (one controller task)."""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _make(tmp_path, sim):
    pytest.importorskip("httpx")
    import robot_control.arm_bridge as mb
    b = mb.Bridge("http://127.0.0.1:9", "http://127.0.0.1:9", REPO / "robot_controller/config/scene.example.yaml",
                  tmp_path / "vision.json", sim=sim)
    b.tasks = []
    b.run_task = lambda actions, did: (b.tasks.append(actions), {"ok": True, "did": did})[1]
    b.ensure_visible = lambda oid: None
    return mb, b


@pytest.fixture
def bridge(tmp_path):
    """SIM=1 bridge: push is a controller task (PyBullet / URSim)."""
    return _make(tmp_path, sim=True)


@pytest.fixture
def real(tmp_path, monkeypatch):
    """Bridge on the physical arm: push/touch go to PhysicalArm (faked here), never to the controller."""
    mb, b = _make(tmp_path, sim=False)
    monkeypatch.setattr(mb, "TOUCH_OFFSET_FILE", tmp_path / "touch_offset.json")
    calls = []
    b.physical.push = lambda *args: (calls.append(("push", args)), {"ok": True, "did": "pushed"})[1]
    b.physical.touch = lambda *args, **kw: (calls.append(("touch", args, kw)), {"ok": True, "did": "touched"})[1]
    b.calls = calls
    return mb, b


def test_push_runs_identify_then_push_object(bridge):
    mb, b = bridge
    res = b.tool("push", {"object": "blue bucket"})
    assert res["ok"] and "pushed the blue bucket 01" in res["did"]
    assert b.tasks == [[("identify_object", {"object_id": "blue_bucket_01"}),
                        ("push_object", {"object_id": "blue_bucket_01"})]]


def test_push_ring_refused_before_any_task(bridge):
    mb, b = bridge
    with pytest.raises(mb.Refused, match="can't be pushed"):
        b.tool("push", {"object": "green ring"})
    assert b.tasks == []


def test_overview_never_moves(bridge):
    mb, b = bridge
    looked = []
    b._look = lambda target, seconds: (looked.append(target), {"orange_screw_03": {"conf": 0.9}})[1]
    res = b.tool("overview", {})
    assert res["ok"] and res["moved"] is False and looked == [None] and b.tasks == []
    assert "orange screw 03" in res["did"] and "blue_bucket_01" in res["missing"]


TOOL_DOWN = [3.14159265, 0.0, 0.0]   # rotation vector: tool pointing straight down


def test_real_push_needs_taught_offset_and_never_uses_the_controller(real):
    mb, b = real
    with pytest.raises(mb.Refused, match="touch offset isn't taught"):
        b.tool("push", {"object": "blue bucket"})
    assert b.tasks == [] and b.calls == []


def test_real_push_uses_scene_direction_and_size(real):
    mb, b = real
    mb.TOUCH_OFFSET_FILE.write_text('{"offset_tool_m": [0.07, 0.02, 0.0]}')
    res = b.tool("push", {"object": "blue bucket"})
    assert res["ok"] and b.tasks == []
    kind, args = b.calls[0]
    _, label, offset, direction, footprint, height, distance, down, force = args
    assert kind == "push" and label == "blue bucket 01" and offset == [0.07, 0.02, 0.0]
    assert direction == pytest.approx((0.0, -1.0))            # scene: bucket is pushed along -Y
    assert footprint == pytest.approx(139) and height == pytest.approx(115) and distance == pytest.approx(50)
    assert down == mb.TOUCH_MAX_DOWN_MM and force == mb.TOUCH_FORCE_N


def test_touch_limits_and_sim_refusal(real, tmp_path):
    mb, b = real
    mb.TOUCH_OFFSET_FILE.write_text('{"offset_tool_m": [0.07, 0.02, 0.0]}')
    with pytest.raises(mb.Refused, match="force_n"):
        b.tool("touch", {"object": "green ring", "force_n": 80})
    with pytest.raises(mb.Refused, match="max_down_mm"):
        b.tool("touch", {"object": "green ring", "max_down_mm": 900})
    _, sim = _make(tmp_path, sim=True)
    with pytest.raises(mb.Refused, match="real arm only"):
        sim.tool("touch", {"object": "green ring"})


def test_teach_offset_round_trip(real):
    """Offset saved in the tool frame puts the touching point back where it was taught (base XY, same height)."""
    mb, b = real
    centred = [-0.55, 0.0, 0.30] + TOOL_DOWN
    b.touch_teach = {"tcp": centred, "label": "green ring 02", "ts": __import__("time").time()}
    b.physical.tcp_now = lambda: [-0.48, 0.02, 0.30] + TOOL_DOWN     # operator jogged +70 mm X, +20 mm Y
    res = b.tool("touch_teach_save", {})
    assert res["ok"] and res["moved"] is False
    import json
    off = json.loads(mb.TOUCH_OFFSET_FILE.read_text())["offset_tool_m"]
    assert off[0] == pytest.approx(0.07, abs=1e-4) and off[1] == pytest.approx(-0.02, abs=1e-4)  # tool Y = -base Y
    over = [-0.30, 0.10, 0.25] + TOOL_DOWN
    tip = b.physical._tip_over(over, off)
    assert tip[:3] == pytest.approx([-0.23, 0.12, 0.25], abs=1e-4)


def test_teach_save_refuses_without_start_or_huge_offset(real):
    mb, b = real
    with pytest.raises(mb.Refused, match="touch_teach_start first"):
        b.tool("touch_teach_save", {})
    b.touch_teach = {"tcp": [-0.55, 0.0, 0.30] + TOOL_DOWN, "label": "x", "ts": __import__("time").time()}
    b.physical.tcp_now = lambda: [-0.25, 0.0, 0.30] + TOOL_DOWN
    with pytest.raises(mb.Refused, match="not a camera offset"):
        b.tool("touch_teach_save", {})
