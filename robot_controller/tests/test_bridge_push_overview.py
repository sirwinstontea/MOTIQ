"""Bridge tools for the new built-in skills: overview (camera only, no motion) and push (one controller task)."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def bridge(tmp_path):
    pytest.importorskip("httpx")
    sys.path.insert(0, str(REPO / "bridge"))
    import motiq_bridge as mb
    b = mb.Bridge("http://127.0.0.1:9", "http://127.0.0.1:9", REPO / "robot_controller/config/scene.example.yaml",
                  tmp_path / "vision.json")
    b.tasks = []
    b.run_task = lambda actions, did: (b.tasks.append(actions), {"ok": True, "did": did})[1]
    b.ensure_visible = lambda oid: None
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
