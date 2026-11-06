"""Digital twin: RoboDK guards, marker placement, service lifecycle, local API, bridge proxy, static safety."""
import ast
import json
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from robot_control.digital_twin.config import TwinConfig
from robot_control.digital_twin.frames import rotz, transl
from robot_control.digital_twin.robodk_writer import COLOR_FRESH, COLOR_STALE, GuardError, RoboDKWriter
from robot_control.digital_twin.service import TwinService, WriterLock
from robot_control.digital_twin.vision_client import VisionUnavailable, parse_frame

from digital_twin_fakes import FakeJoints, FakeObs, FakeRDK, NoLock, rl, rm
from test_digital_twin_core import Q, live_frame, phys_cfg, phys_dict, sim_cfg

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def writer_for(cfg, rdk):
    return RoboDKWriter(cfg.robodk, link=rdk, rl=rl, rm=rm)


def svc_for(cfg, rdk=None, joints=None, obs=None, **kw):
    rdk = rdk or FakeRDK()
    s = TwinService(cfg, writer=writer_for(cfg, rdk), joint_source=joints or FakeJoints(), obs_source=obs,
                    lock=NoLock(), log_path=None, **kw)
    return s, rdk


# ---- 6. RoboDK write guards -----------------------------------------------------------------------------------
@pytest.mark.parametrize("setup,status", [
    (lambda r: setattr(r, "run_mode", rl.RUNMODE_RUN_ROBOT), "robodk_not_simulating"),
    (lambda r: setattr(r, "driver_state", rl.ROBOTCOM_READY), "robot_driver_connected"),
    (lambda r: setattr(r, "driver_state", rl.ROBOTCOM_WORKING), "robot_driver_connected"),
    (lambda r: setattr(r, "busy", True), "robodk_robot_busy"),
    (lambda r: setattr(r.robot, "name", "UR10e"), "robot_not_found"),
])
def test_unsafe_robodk_blocks_every_write(setup, status):
    cfg = phys_cfg()
    s, rdk = svc_for(cfg)
    setup(rdk)
    res = s.snapshot(objects=False)
    assert not res["ok"] and res["status"] == status
    assert rdk.writes() == []                       # nothing at all was written
    with pytest.raises(GuardError):
        writer_for(cfg, rdk).update_marker("MOTIQ_Detected_x", [0, 0, 0])
    assert rdk.writes() == []


def test_wrong_station_refused():
    d = phys_dict()
    d["robodk"]["station_name"] = "Twin_Station"
    d["robodk"]["base_alignment"].update(validated=True, validated_for="physical", validated_by="t")
    s, rdk = svc_for(TwinConfig.model_validate(d))
    assert s.snapshot(objects=False)["status"] == "wrong_station" and rdk.writes() == []


def test_guard_rechecked_before_each_batch_mid_run():
    """Run mode flips to RUN_ROBOT while live: the loop fails closed and writes nothing more."""
    cfg = phys_cfg()
    s, rdk = svc_for(cfg)
    assert s.start(objects=False)["ok"]
    _wait(lambda: len(rdk.writes("setJoints")) >= 2)
    rdk.run_mode = rl.RUNMODE_RUN_ROBOT
    _wait(lambda: s.state == "ERROR")
    n = len(rdk.writes("setJoints"))
    time.sleep(0.3)
    assert len(rdk.writes("setJoints")) == n and s.last_error["status"] == "robodk_not_simulating"
    assert not s._threads_alive() or _wait(lambda: not s._threads_alive())
    s.stop()


def test_marker_never_hijacks_station_items():
    cfg = phys_cfg()
    rdk = FakeRDK()
    w = writer_for(cfg, rdk)
    with pytest.raises(GuardError, match="not a MOTIQ_Detected_"):
        w.update_marker("Blue_Bucket_01", [0, 0, 0])
    rdk.items.append(type(rdk.robot)(rdk, "MOTIQ_Detected_blue_bucket_01", rl.ITEM_TYPE_FRAME))
    with pytest.raises(GuardError) as e:
        w.update_marker("MOTIQ_Detected_blue_bucket_01", [0, 0, 0])
    assert e.value.status == "marker_name_collision"


# ---- 2. marker placement through the real parent chain -------------------------------------------------------
def test_marker_lands_at_measured_point_with_rotated_objects_frame_and_offset_base():
    T_S_R = transl(0.4, -0.3, 0.75) @ rotz(-60)      # robot base somewhere in the station
    rdk = FakeRDK(T_S_R=T_S_R)
    cfg = phys_cfg()
    w = writer_for(cfg, rdk)
    p_R = [-0.62, -0.24, 0.115]
    info = w.update_marker("MOTIQ_Detected_blue_bucket_01", p_R)
    m = rdk.Item("MOTIQ_Detected_blue_bucket_01")
    assert info["parent"] == "Objects_Frame" and m.Parent() is rdk.objects_frame
    assert m.abs_m()[:3, 3] == pytest.approx((T_S_R @ [*p_R, 1])[:3], abs=1e-9)
    assert m.color == COLOR_FRESH and len(rdk.writes("AddShape")) == 1
    w.update_marker("MOTIQ_Detected_blue_bucket_01", [-0.5, 0, 0.05])     # reused, not re-created
    assert len(rdk.writes("AddShape")) == 1


def test_non_identity_T_R_B_applied_before_station_transform():
    d = phys_dict()
    d["robodk"]["base_alignment"].update(validated=True, validated_for="physical", validated_by="t",
                                         T_R_B=(transl(0.01, 0.02, 0) @ rotz(180)).tolist())
    cfg = TwinConfig.model_validate(d)
    rdk = FakeRDK()
    s, _ = svc_for(cfg, rdk=rdk, obs=FakeObs(lambda: live_frame()))
    assert s.snapshot()["ok"]
    m = rdk.Item("MOTIQ_Detected_orange_screw_03")
    assert m.abs_m()[:3, 3] == pytest.approx([0.46, 0.14, 0.09], abs=1e-9)   # rotz(180)·(-0.45,-0.12) + (0.01,0.02)


# ---- 4. CAD untouched unless an explicit validated policy ---------------------------------------------------
def _bucket_frame():
    f = {"object_class": "secchio_blu", "object_label": "blue_bucket_01", "track_id": 1, "confidence": 0.9,
         "position_base_m": [-0.60, -0.25, 0.115], "depth_valid_ratio": 0.9, "depth_points": 500, "status": "ok"}
    return live_frame(objects=[f])


def test_surface_marker_policy_leaves_cad_bucket_alone():
    s, rdk = svc_for(phys_cfg(), obs=FakeObs(_bucket_frame))
    before = rdk.bucket.abs_m().copy()
    assert s.snapshot()["ok"]
    assert np.allclose(rdk.bucket.abs_m(), before) and ("setPose", "Blue_Bucket_01") not in rdk.log
    assert rdk.Item("MOTIQ_Detected_blue_bucket_01").abs_m()[2, 3] == pytest.approx(0.115)


def test_tabletop_policy_puts_cad_bottom_on_table_not_at_visible_top():
    d = phys_dict()
    d["robodk"]["base_alignment"].update(validated=True, validated_for="physical", validated_by="t")
    d["robodk"]["allow_cad_model_reposition"] = True
    d["objects"]["blue_bucket_01"].update(pose_policy="tabletop_contact_preserve_orientation",
                                          table_z_base_m=0.0, tabletop_policy_validated=True)
    s, rdk = svc_for(TwinConfig.model_validate(d), obs=FakeObs(_bucket_frame))
    R_before = rdk.bucket.abs_m()[:3, :3].copy()
    assert s.snapshot()["ok"]
    T = rdk.bucket.abs_m()
    assert T[:3, 3] == pytest.approx([-0.60, -0.25, 0.0], abs=1e-9)    # bottom face on the table, z != 0.115
    assert np.allclose(T[:3, :3], R_before)                             # orientation preserved, not guessed
    # not validated -> CAD untouched again
    d["objects"]["blue_bucket_01"]["tabletop_policy_validated"] = False
    s2, rdk2 = svc_for(TwinConfig.model_validate(d), obs=FakeObs(_bucket_frame))
    assert s2.snapshot()["ok"] and ("setPose", "Blue_Bucket_01") not in rdk2.log


# ---- 5. quality failures never update ---------------------------------------------------------------------
@pytest.mark.parametrize("frame", [
    lambda: live_frame(objects=[{"object_class": "vite_arancione", "confidence": 0.2, "status": "ok",
                                 "position_base_m": [-0.4, 0, 0.1], "depth_valid_ratio": 0.9, "depth_points": 300}]),
    lambda: live_frame(ts=time.time() - 5),
    lambda: live_frame(robot_stationary=False),
    lambda: live_frame(calibration_reference_frame="tcp"),
])
def test_rejected_observations_write_no_marker(frame):
    s, rdk = svc_for(phys_cfg(), obs=FakeObs(frame))
    assert s.snapshot()["ok"]
    assert not [w for w in rdk.log if w[1] and w[1].startswith("MOTIQ_")] and not rdk.writes("AddShape")


def test_moving_robot_skips_vision_call():
    j = FakeJoints()
    j.qd = [0.0, 0.05, 0, 0, 0, 0]
    obs = FakeObs(lambda: live_frame())
    s, rdk = svc_for(phys_cfg(), joints=j, obs=obs)
    res = s.snapshot()
    assert res["objects"]["reason"] == "moving" and obs.calls == 0 and rdk.writes("setJoints")


def test_invalid_after_valid_keeps_marker_but_greys_it():
    frames = [live_frame(), live_frame(objects=[{"object_class": "vite_arancione", "status": "invalid_depth",
                                                 "confidence": 0.9, "track_id": 3}])]
    s, rdk = svc_for(phys_cfg(), obs=FakeObs(lambda: frames.pop(0)))
    s.snapshot()
    m = rdk.Item("MOTIQ_Detected_orange_screw_03")
    pos = m.abs_m()[:3, 3].copy()
    s.snapshot()
    assert np.allclose(m.abs_m()[:3, 3], pos) and m.color == COLOR_STALE
    o = s.status()["objects"]["per_object"]["orange_screw_03"]
    assert o["state"] == "stale" and o["reason"] == "no_depth"


def test_objects_require_validated_alignment_unless_robot_only():
    cfg = TwinConfig.model_validate({**phys_dict(), "vision": {**phys_dict()["vision"]}})
    s, rdk = svc_for(cfg, obs=FakeObs(lambda: live_frame()))
    assert s.start(objects=True)["status"] == "objects_blocked" and not rdk.writes()
    res = s.snapshot(objects=False)
    assert res["ok"] and "robot_only" in res["objects_mode"] and res["objects"] is None


# ---- 7. lifecycle ------------------------------------------------------------------------------------------
def _wait(pred, timeout=3.0):
    t = time.time() + timeout
    while time.time() < t:
        if pred():
            return True
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def test_start_is_idempotent_and_stop_only_stops_mirroring():
    j = FakeJoints()
    s, rdk = svc_for(phys_cfg(), joints=j, obs=FakeObs(lambda: live_frame()))
    assert s.start()["status"] == "started"
    assert s.start()["status"] == "already_running"
    assert len([t for t in threading.enumerate() if t.name == "twin-joints"]) == 1
    _wait(lambda: len(rdk.writes("setJoints")) >= 3 and s.objects["orange_screw_03"]["state"] == "fresh")
    st = s.status()
    assert st["state"] == "RUNNING" and st["physical_motion_enabled"] is False
    assert "NO PHYSICAL MOTION" in st["label"]
    res = s.stop()
    assert res["robot_command_sent"] is False and s.state == "STOPPED"
    n = len(rdk.writes("setJoints"))
    time.sleep(0.25)
    assert len(rdk.writes("setJoints")) == n
    assert set(dir(j)) & {"moveJ", "moveL", "stopJ", "stopL", "sendCustomScript", "servoJ"} == set()


def test_stale_telemetry_freezes_scene_then_resumes():
    j = FakeJoints()
    s, rdk = svc_for(sim_cfg(sync={"stale_after_ms": 150}), joints=j,
                     obs=FakeObs(lambda: __import__("robot_control.digital_twin.vision_client",
                                                    fromlist=["ReplaySource"]).ReplaySource(
                         sim_cfg().vision.replay_file).snapshot()))
    assert s.start()["ok"]
    _wait(lambda: s.objects["blue_bucket_01"]["state"] == "fresh")
    j.freeze = True                                 # controller timestamp stops advancing
    _wait(lambda: s.state == "PAUSED_STALE")
    n = len(rdk.writes("setJoints"))
    time.sleep(0.25)
    assert len(rdk.writes("setJoints")) == n         # frozen, not showing an old pose as live
    assert rdk.Item("MOTIQ_Detected_blue_bucket_01").color == COLOR_STALE
    j.freeze = False
    _wait(lambda: s.state == "RUNNING")
    s.stop()


def test_disconnect_marks_offline_without_crash():
    j = FakeJoints()
    s, rdk = svc_for(phys_cfg(), joints=j)
    j.fail = True
    res = s.snapshot(objects=False)
    assert not res["ok"] and res["status"] == "robot_offline" and not rdk.writes()
    assert s.status()["robot"]["connected"] is False


def test_vision_offline_reported_not_fatal():
    s, rdk = svc_for(phys_cfg(), obs=FakeObs(error=VisionUnavailable("vision_offline", "down")))
    res = s.snapshot()
    assert res["ok"] and res["objects"]["reason"] == "vision_offline" and rdk.writes("setJoints")


def test_writer_lock_is_exclusive(tmp_path):
    a, b = WriterLock(tmp_path / "l"), WriterLock(tmp_path / "l")
    assert a.acquire() and not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_check_reports_prerequisites_without_writing():
    s, rdk = svc_for(TwinConfig.model_validate(phys_dict()))
    res = s.check()
    assert res["ok"] and res["can_start_robot_only"] and not res["can_start_with_objects"]
    assert any("not validated" in b for b in res["object_blockers"])
    assert res["robodk"]["fk_consistency"]["robodk_flange_vs_ur_nominal_dh_flange_mm"] < 0.01
    assert rdk.writes() == []
    assert res["physical_motion_enabled"] is False


# ---- local API ----------------------------------------------------------------------------------------------
def test_api_routes_auth_and_validation():
    from fastapi.testclient import TestClient

    from robot_control.digital_twin.api import create_app
    s, rdk = svc_for(phys_cfg(), obs=FakeObs(lambda: live_frame()))
    c = TestClient(create_app(s, key="secret-key"))
    assert c.get("/digital-twin/status").status_code == 401
    h = {"X-Bridge-Key": "secret-key"}
    assert c.get("/digital-twin/status", headers=h).json()["state"] == "DISCONNECTED"
    assert c.post("/digital-twin/check", headers=h).json()["ok"]
    assert c.post("/digital-twin/start", headers=h, json={"objects": "yes"}).status_code == 400
    assert c.post("/digital-twin/start", headers=h, json={"objects": True, "robot": "x"}).status_code == 400
    assert c.post("/digital-twin/start", headers=h, json={"objects": False}).json()["status"] == "started"
    assert c.post("/digital-twin/start", headers=h).json()["status"] == "already_running"
    r = c.post("/digital-twin/stop", headers=h).json()
    assert r["ok"] and r["robot_command_sent"] is False


# ---- bridge proxy -----------------------------------------------------------------------------------------
def _bridge():
    sys.path.insert(0, str(ROOT / "bridge"))
    import motiq_bridge
    return motiq_bridge


def test_bridge_proxies_only_to_digital_twin_routes():
    httpx = pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    mb = _bridge()
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, req.headers.get("x-bridge-key"), req.content))
        return httpx.Response(200, json={"ok": True, "state": "RUNNING"})

    twin = mb.DigitalTwinProxy("http://127.0.0.1:8770", "k-123456",
                               client=httpx.Client(transport=httpx.MockTransport(handler)))

    class NoBridge:                                  # any access to the arm bridge would raise
        def __getattr__(self, name):
            raise AssertionError(f"digital twin route touched Bridge.{name}")

    c = TestClient(mb.create_app(NoBridge(), "k-123456", twin))
    h = {"X-Bridge-Key": "k-123456"}
    assert c.post("/digital-twin/start", json={"objects": False}).status_code == 401
    assert c.post("/digital-twin/start", headers=h, json={"objects": False}).json()["ok"]
    assert c.post("/digital-twin/stop", headers=h).json()["ok"]
    assert c.get("/digital-twin/status", headers=h).json()["state"] == "RUNNING"
    assert c.post("/digital-twin/snapshot", headers=h, json={"objects": 1}).status_code == 400
    assert c.post("/digital-twin/move", headers=h).status_code == 404
    assert [p for _, p, _, _ in seen] == ["/digital-twin/start", "/digital-twin/stop", "/digital-twin/status"]
    assert all(k == "k-123456" for _, _, k, _ in seen)
    with pytest.raises(ValueError):
        mb.DigitalTwinProxy("http://192.168.1.5:8770")
    down = mb.DigitalTwinProxy("http://127.0.0.1:9", client=httpx.Client(
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")))))
    code, body = down.call("GET", "status")
    assert code == 503 and body["status"] == "service_unavailable"


# ---- 6. static safety: no command channel anywhere in the sync path ------------------------------------------
FORBIDDEN = [r"rtde_control", r"RTDEControl", r"\bmoveJ\b", r"\bmoveL\b", r"\bMoveJ\b", r"\bMoveL\b",
             r"\bMoveC\b", r"RunProgram", r"RunCode", r"send_script", r"sendCustomScript", r"\bservoJ\b",
             r"setRunMode", r"\.Connect\(", r"/tools/stop", r"/robot/stop", r"stopDevice", r"powerOn",
             r"brakeRelease", r"dashboard_client", r"30001|30002|30003|29999"]


def _code_only(path: Path) -> str:
    """Source without docstrings/comments (docs may name forbidden calls to say they are not used)."""
    src = path.read_text()
    tree = ast.parse(src)
    doc_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and \
                ast.get_docstring(node) is not None:
            d = node.body[0]
            doc_lines.update(range(d.lineno, d.end_lineno + 1))
    return "\n".join(re.sub(r"#.*", "", ln) for i, ln in enumerate(src.splitlines(), 1) if i not in doc_lines)


def test_no_command_paths_in_digital_twin_code():
    files = sorted((ROOT / "robot_control" / "digital_twin").glob("*.py")) + \
        [ROOT / "scripts" / "mirror_ursim_to_robodk.py"]
    for f in files:
        code = _code_only(f)
        for pat in FORBIDDEN:
            assert not re.search(pat, code), f"{f.name}: forbidden pattern {pat}"
    tel = _code_only(ROOT / "robot_control" / "digital_twin" / "telemetry.py")
    assert "RTDEReceiveInterface" in tel and "rtde_receive" in tel


def test_bridge_twin_proxy_has_no_motion_reference():
    src = (ROOT / "bridge" / "motiq_bridge.py").read_text()
    cls = src[src.index("class DigitalTwinProxy"):src.index("def parse_twin_body")]
    for bad in ("PhysicalArm", "self.physical", "self.controller", "Bridge(", "/tools", "jog", "send_script",
                ".stop("):
        assert bad not in cls.split('"""', 2)[-1], bad


def test_app_digital_twin_client_uses_only_twin_endpoints():
    ts = REPO / "groky-app" / "src" / "lib" / "digitalTwin.ts"
    if not ts.exists():
        pytest.skip("app not present")
    src = re.sub(r"//.*", "", ts.read_text())
    paths = set(re.findall(r"`\$\{[^}]+\}(/[^`?]*)`", src)) | set(re.findall(r"'(/[a-z\-/]+)'", src))
    assert paths and all(p.startswith("/digital-twin/") for p in paths), paths
    for bad in ("stopDevice", "runTool", "runSkill", "armPhysical", "/tools/", "armSend"):
        assert bad not in src, bad
    panel = REPO / "groky-app" / "src" / "components" / "DigitalTwinPanel.tsx"
    p = re.sub(r"//.*", "", panel.read_text())
    assert "MIRROR ONLY — NO PHYSICAL MOTION" in p
    for bad in ("stopDevice", "runTool", "runSkill", "armPhysical", "/tools/"):
        assert bad not in p, bad


def test_one_guard_per_objects_batch_and_no_rewrite_of_unchanged_markers():
    s, rdk = svc_for(sim_cfg(), obs=FakeObs(lambda: __import__(
        "robot_control.digital_twin.vision_client", fromlist=["ReplaySource"]).ReplaySource(
        sim_cfg().vision.replay_file).snapshot()))
    calls = {"n": 0}
    real = s.writer.guard

    def counting():
        calls["n"] += 1
        return real()
    s.writer.guard = counting
    s.snapshot()                                     # 1 guard for joints + 1 for the whole objects batch
    assert calls["n"] == 2 and len(rdk.writes("AddShape")) == 3
    poses, colors = len(rdk.writes("setPose")), len(rdk.writes("setColor"))
    s.snapshot()                                     # same points again: no re-render of the markers
    assert len(rdk.writes("setPose")) == poses and len(rdk.writes("setColor")) == colors


def test_status_never_overstates_freshness_and_marker_follows():
    s, rdk = svc_for(sim_cfg(sync={"stale_after_ms": 100}), obs=FakeObs(lambda: __import__(
        "robot_control.digital_twin.vision_client", fromlist=["ReplaySource"]).ReplaySource(
        sim_cfg().vision.replay_file).snapshot()))
    s.snapshot()
    assert s.status()["objects"]["updated"] == 3
    time.sleep(0.15)
    st = s.status()["objects"]
    assert st["updated"] == 0 and st["stale"] == 3                 # aged in the status view
    assert rdk.Item("MOTIQ_Detected_blue_bucket_01").color == COLOR_FRESH
    s._age_markers()                                               # next objects-loop tick
    assert rdk.Item("MOTIQ_Detected_blue_bucket_01").color == COLOR_STALE
