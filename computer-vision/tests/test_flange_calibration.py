"""Skills -> Calibration (flange eye-in-hand, chessboard 21.8 mm, single-shot locate). No hardware: synthetic
geometry, rendered chessboard images, a fake read-only RTDE receiver and a simulated flange provider."""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from synthetic_board import look_at, render  # noqa: E402

from vision.eyes.calibration.boards import (  # noqa: E402
    BoardRejected, canonical_order, chessboard_object_points, observe_board,
)
from vision.eyes.calibration.flange_calibration import (  # noqa: E402
    Sample, activation_problems, load_active, rotation_diversity, solve, transform_problems,
)
from vision.eyes.calibration.setup_config import (  # noqa: E402
    BoardSetup, CalibrationSetup, QualityGates, board_problems, identity_sha256, load_setup,
    mount_estimate_in_flange_m, orientation_ambiguous, parse_setup, save_setup,
)
from vision.eyes.capture.realsense import Intrinsics, UnsupportedDistortion, opencv_distortion  # noqa: E402
from vision.eyes.capture.ur_flange import (  # noqa: E402
    FlangeReading, MockFlangeProvider, RtdeFlangeProvider, flange_from_tcp, pose_delta, pose_to_T, stable_pair,
    ur5e_fk_flange,
)
from vision.eyes.pipeline import EyePipeline, SourceNode  # noqa: E402
from vision.eyes.schemas import EyeObject  # noqa: E402
from vision.eyes.segmentation.measure import deproject  # noqa: E402

INTR = Intrinsics(fx=615.0, fy=612.0, cx=322.0, cy=241.0, width=640, height=480)
K = np.array([[INTR.fx, 0, INTR.cx], [0, INTR.fy, INTR.cy], [0, 0, 1.0]])
SQ = 0.0218
COLS, ROWS = 8, 5                                   # odd x even inner corners: no orientation ambiguity
X_TRUE = pose_to_T([0.072, -0.012, 0.041, 0.03, -0.04, 1.52])   # T_flange_camera to recover
T_BASE_BOARD = pose_to_T([-0.55, 0.08, 0.0, math.pi, 0.0, 0.0])  # board on the table, z into the table
EXAMPLE = json.loads((Path(__file__).resolve().parents[1] / "data" / "calibration" /
                      "calibration_setup.example.json").read_text(encoding="utf-8"))


def Rz(a):
    T = np.eye(4)
    T[:3, :3] = cv2.Rodrigues(np.array([0.0, 0.0, a]))[0]
    return T


def camera_poses(n, seed=0, roll=0.5, spread=0.15):
    """T_base_camera looking at the board centre from varied positions (tilt about two axes) and rolls."""
    rng = np.random.default_rng(seed)
    centre = (T_BASE_BOARD @ np.array([COLS * SQ / 2, ROWS * SQ / 2, 0, 1]))[:3]
    out = []
    for _ in range(n):
        eye = centre + np.array([*rng.uniform(-spread, spread, 2), rng.uniform(0.33, 0.45)])
        out.append(look_at(eye, centre) @ Rz(rng.uniform(-roll, roll)))
    return out


def flange_poses(n, **kw):
    return [Tc @ np.linalg.inv(X_TRUE) for Tc in camera_poses(n, **kw)]


def setup_dict(**board):
    d = json.loads(json.dumps(EXAMPLE))
    d["robot"]["pose_mode"] = "simulation"
    d["board"].update({"inner_corners_columns": COLS, "inner_corners_rows": ROWS, **board})
    return d


# --- 1. frame algebra and units ---------------------------------------------------------------------------------

def test_frame_algebra_and_units():
    T_bf = pose_to_T([-0.4, 0.2, 0.5, math.pi, 0, 0])
    p_cam = np.array([0.05, 0.01, 0.44, 1.0])
    p_base = T_bf @ X_TRUE @ p_cam
    assert np.allclose(np.linalg.inv(T_bf @ X_TRUE) @ p_base, p_cam)
    # mm in the setup -> m internally: 21.8 mm squares are 0.0218 m apart
    P = chessboard_object_points(COLS, ROWS, BoardSetup(square_size_mm=21.8).square_size_mm / 1000.0)
    assert np.allclose(np.diff(P[:COLS, 0]), 0.0218) and np.allclose(P[COLS, 1] - P[0, 1], 0.0218)
    assert P.shape == (COLS * ROWS, 3) and np.all(P[:, 2] == 0)


# --- 2. flange is not TCP ---------------------------------------------------------------------------------------

def test_ur5e_fk_zero_pose_matches_nominal_geometry():
    T = ur5e_fk_flange([0, 0, 0, 0, 0, 0])
    assert np.allclose(T[:3, 3], [-0.8172, -0.2329, 0.0628], atol=1e-4)


class FakeRtde:
    """rtde_receive stand-in: TCP pose = FK(q) @ T_flange_tcp (the controller's own TCP)."""

    def __init__(self, q, T_flange_tcp, speed=0.0, qd=0.0):
        self.q, self.T_ft, self.speed, self.qd = list(q), T_flange_tcp, speed, qd

    def isConnected(self):
        return True

    def getActualQ(self):
        return self.q

    def getActualTCPPose(self):
        T = ur5e_fk_flange(self.q) @ self.T_ft
        return [*T[:3, 3], *cv2.Rodrigues(np.ascontiguousarray(T[:3, :3]))[0].ravel()]

    def getActualTCPSpeed(self):
        return [self.speed, 0, 0, 0, 0, 0]

    def getActualQd(self):
        return [self.qd] * 6


Q = [0.3, -1.6, 1.4, -1.4, -1.57, 0.2]
TCP_OFFSET = [0.01, -0.02, 0.18, 0.1, -0.05, 0.3]      # gripper TCP: translation AND rotation


def _setup_with_tcp(offset, confirmed=True, method="tcp_minus_verified_offset"):
    d = json.loads(json.dumps(EXAMPLE))
    d["robot"].update(active_tcp_offset_m_rad=offset, active_tcp_offset_confirmed=confirmed, flange_pose_method=method)
    return parse_setup(d)[0]


def test_flange_from_verified_tcp_offset_matches_fk():
    rt = FakeRtde(Q, pose_to_T(TCP_OFFSET))
    cfg = _setup_with_tcp(TCP_OFFSET)
    r = RtdeFlangeProvider("10.0.0.1", lambda: cfg, receive_factory=lambda ip: rt).read()
    assert r.valid and r.source == "ur_rtde_receive_verified_flange" and not r.moving
    dt, dr = pose_delta(r.T_base_flange, ur5e_fk_flange(Q))
    assert dt < 1e-6 and dr < 1e-6
    # the raw TCP pose is NOT the flange (180 mm away here)
    assert pose_delta(pose_to_T(rt.getActualTCPPose()), r.T_base_flange)[0] > 150


def test_unverified_or_wrong_tcp_offset_is_rejected_never_used_as_flange():
    rt = FakeRtde(Q, pose_to_T(TCP_OFFSET))
    for cfg in (_setup_with_tcp(TCP_OFFSET, confirmed=False), _setup_with_tcp(None, confirmed=False)):
        r = RtdeFlangeProvider("x", lambda c=cfg: c, receive_factory=lambda ip: rt).read()
        assert not r.valid and r.status == "invalid_flange_pose" and r.T_base_flange is None
    r = RtdeFlangeProvider("x", lambda: _setup_with_tcp([0] * 6), receive_factory=lambda ip: rt).read()
    assert not r.valid and "disagrees with joint kinematics" in r.reason


def test_flange_moving_offline_and_simulation_guard():
    cfg = _setup_with_tcp(TCP_OFFSET)
    moving = RtdeFlangeProvider("x", lambda: cfg, receive_factory=lambda ip: FakeRtde(Q, pose_to_T(TCP_OFFSET), qd=0.01))
    assert moving.read().moving

    def boom(ip):
        raise OSError("no route to host")
    off = RtdeFlangeProvider("x", lambda: cfg, receive_factory=boom).read()
    assert not off.valid and off.status == "robot_offline" and off.T_base_flange is None
    sim = MockFlangeProvider(lambda: np.eye(4), lambda: cfg)       # cfg is physical_read_only
    assert not sim.read().valid
    assert RtdeFlangeProvider("x", lambda: parse_setup(setup_dict())[0],
                              receive_factory=lambda ip: FakeRtde(Q, np.eye(4))).read().valid is False


def test_flange_module_is_read_only():
    src = (Path(__file__).resolve().parents[1] / "vision/eyes/capture/ur_flange.py").read_text(encoding="utf-8")
    svc = (Path(__file__).resolve().parents[1] / "vision/eyes/calibration/service.py").read_text(encoding="utf-8")
    for bad in ("rtde_control", "RTDEControl", "send_script", "movel(", "movej(", "ur_jog"):
        assert bad not in src and bad not in svc, bad


# --- 3. mount estimate ------------------------------------------------------------------------------------------

def test_mount_estimate_is_metadata_only(tmp_path):
    cfg, errors = parse_setup(EXAMPLE)
    assert not errors and cfg.camera_mount_estimate.x_longitudinal_mm == 75.0 and cfg.camera_mount_estimate.y_mm is None
    assert cfg.camera_mount_estimate.rotation_rpy_deg is None and cfg.camera_mount_estimate.z_mm == 35.0
    bad = json.loads(json.dumps(EXAMPLE))
    bad["camera_mount_estimate"]["use_as_calibration"] = True
    assert parse_setup(bad)[0] is None
    save_setup(cfg, tmp_path)
    again, _, src = load_setup(tmp_path)
    assert src == "calibration_setup.json" and again.camera_mount_estimate.x_longitudinal_mm == 75.0
    assert mount_estimate_in_flange_m(cfg) is None            # reference and signs unconfirmed: not interpreted
    d = json.loads(json.dumps(EXAMPLE))
    d["camera_mount_estimate"].update(reference_frame_as_measured="tcp", x_sign_confirmed=True, z_sign_confirmed=True)
    d["robot"].update(active_tcp_offset_m_rad=TCP_OFFSET, active_tcp_offset_confirmed=True)
    est = mount_estimate_in_flange_m(parse_setup(d)[0])
    assert np.allclose(est, (pose_to_T(TCP_OFFSET) @ np.array([0.075, 0, 0.035, 1]))[:3])


def test_setup_rejects_bad_values_and_reports_missing_corners():
    for path, val in ((("board", "square_size_mm"), 0), (("board", "square_size_mm"), float("inf")),
                      (("board", "inner_corners_columns"), 2), (("capture", "allow_automatic_robot_motion"), True),
                      (("localization", "send_robot_motion_commands"), True), (("robot", "calibration_frame"), "tcp")):
        d = json.loads(json.dumps(EXAMPLE))
        d[path[0]][path[1]] = val
        cfg, errors = parse_setup(d)
        assert cfg is None and errors, path
    cfg = parse_setup(EXAMPLE)[0]
    assert board_problems(cfg) and "INNER" in board_problems(cfg)[0]
    assert orientation_ambiguous(7, 5) and not orientation_ambiguous(8, 5)


# --- 4./6. chessboard detection and PnP -------------------------------------------------------------------------

def _view(roll=0.0):
    Tc = camera_poses(1, seed=3)[0] @ Rz(roll)
    return np.linalg.inv(Tc) @ T_BASE_BOARD


def test_chessboard_pose_matches_truth():
    Tcb = _view()
    obs = observe_board(render(Tcb, K, COLS, ROWS, SQ), INTR, BoardSetup(inner_corners_columns=COLS, inner_corners_rows=ROWS))
    dt, dr = pose_delta(obs.T_camera_board, Tcb)
    assert dt < 0.5 and dr < 0.3 and obs.rmse_px < 0.5 and len(obs.image_points) == COLS * ROWS


def test_chessboard_wrong_dimensions_or_occlusion_rejected():
    Tcb = _view()
    img = render(Tcb, K, COLS, ROWS, SQ)
    with pytest.raises(BoardRejected) as e:
        observe_board(img, INTR, BoardSetup(inner_corners_columns=COLS + 1, inner_corners_rows=ROWS))
    assert e.value.status == "board_not_detected"
    occl = img.copy()
    occl[:, : img.shape[1] // 2] = 180
    with pytest.raises(BoardRejected):
        observe_board(occl, INTR, BoardSetup(inner_corners_columns=COLS, inner_corners_rows=ROWS))
    with pytest.raises(BoardRejected) as e:
        observe_board(img, INTR, BoardSetup())                        # no inner corners entered
    assert e.value.status == "board_geometry_incomplete"


def test_chessboard_with_lens_distortion():
    dist = np.array([0.08, -0.15, 0.001, -0.0008, 0.05])
    intr = Intrinsics(INTR.fx, INTR.fy, INTR.cx, INTR.cy, 640, 480, "brown_conrady", tuple(dist))
    Tcb = _view()
    img = render(Tcb, K, COLS, ROWS, SQ, dist=dist)
    obs = observe_board(img, intr, BoardSetup(inner_corners_columns=COLS, inner_corners_rows=ROWS))
    assert pose_delta(obs.T_camera_board, Tcb)[0] < 1.0
    # ignoring the distortion would be measurably worse
    obs0 = observe_board(img, INTR, BoardSetup(inner_corners_columns=COLS, inner_corners_rows=ROWS))
    assert obs0.rmse_px > obs.rmse_px
    # depth deprojection undistorts too: project a point with distortion, deproject it back
    p = np.array([[0.12, -0.08, 0.5]])
    uv = cv2.projectPoints(p, np.zeros(3), np.zeros(3), K, dist)[0].ravel()
    assert np.allclose(deproject(uv[0], uv[1], 0.5, intr), p.ravel(), atol=1e-4)
    with pytest.raises(UnsupportedDistortion):
        opencv_distortion(Intrinsics(1, 1, 0, 0, 1, 1, "inverse_brown_conrady", (0.1, 0, 0, 0, 0)))
    assert not opencv_distortion(INTR).any()


def test_symmetric_board_orientation_is_canonicalized():
    c = np.array([[10.0, 10], [20, 10], [30, 10]])
    assert np.allclose(canonical_order(c[::-1])[0], c) and canonical_order(c)[1] is False
    obs = observe_board(render(_view(), K, 7, ROWS, SQ), INTR, BoardSetup(inner_corners_columns=7, inner_corners_rows=ROWS))
    assert obs.orientation_ambiguous
    assert obs.image_points[0].sum() < obs.image_points[-1].sum()


# --- 5. synthetic hand-eye ----------------------------------------------------------------------------------------

def _ideal_samples(Tfs):
    P = chessboard_object_points(COLS, ROWS, SQ)
    out = []
    for k, Tf in enumerate(Tfs):
        Tcb = np.linalg.inv(Tf @ X_TRUE) @ T_BASE_BOARD
        img = cv2.projectPoints(P, cv2.Rodrigues(np.ascontiguousarray(Tcb[:3, :3]))[0], Tcb[:3, 3], K, np.zeros(5))[0]
        out.append(Sample(k + 1, Tf, Tcb, img.reshape(-1, 2), P, 0.1))
    return out


def test_solve_recovers_T_flange_camera_with_holdout():
    rep = solve(_ideal_samples(flange_poses(20)), QualityGates(), 12, K, np.zeros(5))
    assert rep["ok"], rep["reason"]
    dt, dr = pose_delta(rep["T_flange_camera"], X_TRUE)
    assert dt < 1e-3 and dr < 1e-4
    q = rep["quality"]
    assert q["n_holdout"] == 4 and q["holdout_error_mm"] < 1e-3 and q["board_spread_mm"] < 1e-3
    assert not transform_problems(rep["T_flange_camera"])


def test_solve_with_noise_meets_gates_and_detects_flipped_samples():
    rng = np.random.default_rng(5)
    samples = _ideal_samples(flange_poses(20, seed=1))
    for s in samples:                     # 0.3 mm / 0.05 deg robot noise
        s.T_base_flange = s.T_base_flange @ pose_to_T([*rng.normal(0, 0.0003, 3), *rng.normal(0, 0.0009, 3)])
    rep = solve(samples, QualityGates(), 12, K, np.zeros(5))
    assert rep["ok"] and pose_delta(rep["T_flange_camera"], X_TRUE)[0] < 3.0
    # two samples with the board indexed the other way round (180° about the board normal)
    flip = pose_to_T([(COLS - 1) * SQ, (ROWS - 1) * SQ, 0, 0, 0, math.pi])  # reversed corner indexing
    samples[1].T_camera_board = samples[1].T_camera_board @ flip
    samples[2].T_camera_board = samples[2].T_camera_board @ flip
    rep = solve(samples, QualityGates(), 12, K, np.zeros(5))
    assert {r["index"] for r in rep["rejected_samples"]} == {samples[1].index, samples[2].index}
    assert rep["ok"]


def test_degenerate_motion_is_rejected():
    centre = (T_BASE_BOARD @ np.array([0.08, 0.05, 0, 1]))[:3]
    base = look_at(centre + np.array([0, 0, 0.4]), centre)
    only_translation = [pose_to_T([*np.random.default_rng(k).uniform(-0.05, 0.05, 3), 0, 0, 0]) @ base
                        @ np.linalg.inv(X_TRUE) for k in range(15)]
    rep = solve(_ideal_samples(only_translation), QualityGates(), 12, K, np.zeros(5))
    assert not rep["ok"] and rep["status"] == "insufficient_pose_diversity"
    one_axis = [base @ Rz(a) @ np.linalg.inv(X_TRUE) for a in np.linspace(-0.6, 0.6, 15)]
    assert rotation_diversity(one_axis)["second_axis_rotation_deg"] < 1.0
    assert solve(_ideal_samples(one_axis), QualityGates(), 12, K, np.zeros(5))["status"] == "insufficient_pose_diversity"
    assert solve(_ideal_samples(flange_poses(5)), QualityGates(), 12, K, np.zeros(5))["status"] == "insufficient_samples"


# --- 7. time / pose synchronization -------------------------------------------------------------------------------

def _reading(T, moving=False, age_s=0.0, valid=True):
    now = time.monotonic() - age_s
    return FlangeReading(T, now, now, time.time() - age_s, None, "test", moving, valid, None if valid else "x",
                         "ok" if valid else "robot_offline")


def test_stable_pair_rules():
    T = pose_to_T([-0.4, 0.1, 0.4, math.pi, 0, 0])
    assert stable_pair(_reading(T), _reading(T), 1.0, 0.5, 250)[0]
    assert stable_pair(_reading(T), _reading(T, moving=True), 1.0, 0.5, 250)[1] == "robot_moving"
    assert stable_pair(_reading(T), _reading(T @ pose_to_T([0.002, 0, 0, 0, 0, 0])), 1.0, 0.5, 250)[1] == "robot_moving"
    assert stable_pair(_reading(T), _reading(T @ pose_to_T([0, 0, 0, 0.02, 0, 0])), 1.0, 0.5, 250)[1] == "robot_moving"
    assert stable_pair(_reading(T), _reading(T, age_s=1.0), 1.0, 0.5, 250)[1] == "stale_robot_pose"
    assert stable_pair(_reading(T), _reading(None, valid=False), 1.0, 0.5, 250)[1] == "robot_offline"
    assert np.allclose(flange_from_tcp(T @ pose_to_T(TCP_OFFSET), pose_to_T(TCP_OFFSET)), T)


# --- full workflow over HTTP with a simulated robot and rendered camera -------------------------------------------

class SimCell:
    """Simulated robot (flange pose set by the test = 'the operator on the pendant') and a camera on the flange."""

    def __init__(self):
        self.T_flange = flange_poses(1)[0]
        self.moving = False
        self.frame_id = 0
        self.frozen_frame = False
        self.after_read = None          # hook: called after each flange read (to move the robot mid-capture)

    # flange provider
    def pose(self):
        T = self.T_flange.copy()
        if self.after_read:
            self.after_read()
        return T


class SimReader:
    mode = "synthetic_sim"
    device_name = "Intel RealSense D435 (sim)"
    device_serial = "SIM-0001"
    is_connected = True
    _running = False

    def __init__(self, cell):
        self.cell, self.intrinsics = cell, INTR

    def _frame(self):
        Tcb = np.linalg.inv(self.cell.T_flange @ X_TRUE) @ T_BASE_BOARD
        return render(Tcb, K, COLS, ROWS, SQ)

    def read_once(self):
        ok, c, _d, fid, ts = self.get_frames()
        return ok, c, fid, ts

    def get_frames(self):
        if not self.cell.frozen_frame:
            self.cell.frame_id += 1
        return True, self._frame(), np.full((480, 640), 400, np.uint16), self.cell.frame_id, time.time()

    def get_depth(self):
        return np.full((480, 640), 400, np.uint16)

    def start_background(self):
        pass

    def stop(self):
        pass


class FakeTracker:
    def __init__(self):
        self.objects = []

    def process_frame(self, frame, apply_calibration=False):
        return [o.model_copy() for o in self.objects]


def _client(tmp_path, cell, setup=None):
    from fastapi.testclient import TestClient

    from vision.eyes.calibration.service import CalibrationService
    from vision.eyes.server.app import create_app
    save_setup(parse_setup(setup or setup_dict())[0], tmp_path)
    pipe = EyePipeline()
    node = SourceNode(source="arm", reader=SimReader(cell), tracker=FakeTracker())
    pipe.nodes["arm"] = node
    prov = MockFlangeProvider(cell.pose, lambda: load_setup(tmp_path)[0], moving_fn=lambda: cell.moving)
    svc = CalibrationService(pipe, "arm", tmp_path, flange_provider=prov, sleep=lambda s: None)
    return TestClient(create_app(pipe, calibration_service=svc, calib_dir=tmp_path)), node, svc


def _collect(client, cell, sid, n, seed=2):
    for Tf in flange_poses(n, seed=seed):
        cell.T_flange = Tf
        r = client.post(f"/calibration/sessions/{sid}/capture")
        assert r.status_code == 200 and r.json()["ok"], r.json()


def test_http_workflow_setup_capture_solve_activate_locate(tmp_path):
    cell = SimCell()
    client, node, svc = _client(tmp_path, cell, setup_dict(inner_corners_columns=None, inner_corners_rows=None))
    st = client.get("/calibration/status").json()
    assert st["mode_label"] == "SIMULATION" and st["card_state"] == "Not calibrated" and st["motion_allowed"] is False
    # incomplete board: no session
    r = client.post("/calibration/sessions")
    assert r.status_code == 422 and r.json()["status"] == "board_geometry_incomplete"
    setup = client.get("/calibration/setup").json()["setup"]
    setup["board"].update(inner_corners_columns=COLS, inner_corners_rows=ROWS)
    assert client.put("/calibration/setup", json=setup).json()["ok"]
    bad = json.loads(json.dumps(setup))
    bad["board"]["square_size_mm"] = -1
    r = client.put("/calibration/setup", json=bad)
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "board.square_size_mm"

    sid = client.post("/calibration/sessions").json()["session"]["session_id"]
    assert client.get("/calibration/sessions/../../etc").status_code in (400, 404)
    assert client.get("/calibration/sessions/20260101T000000-zzzzzz").status_code == 400
    _collect(client, cell, sid, 16)
    # same pose again -> rejected, counted
    r = client.post(f"/calibration/sessions/{sid}/capture")
    assert r.status_code == 409 and r.json()["status"] == "duplicate_pose"
    sess = client.get(f"/calibration/sessions/{sid}").json()
    assert sess["n_valid"] == 16 and sess["n_rejected"] == 1
    assert client.get("/calibration/status").json()["card_state"] == "Collecting samples"
    sample = json.loads((tmp_path / "sessions" / sid / "samples" / "0001.json").read_text())
    for k in ("T_base_flange", "T_camera_board", "frame_id", "reprojection_rmse_px", "robot_reading_wall_s",
              "robot_pose_source", "board", "image_points"):
        assert k in sample
    assert (tmp_path / "sessions" / sid / "samples" / "0001.png").exists()

    # activation before solve / without confirmation is refused
    assert client.post(f"/calibration/sessions/{sid}/activate", json={"confirm": True}).status_code == 409
    res = client.post(f"/calibration/sessions/{sid}/solve").json()
    assert res["ok"], res
    X = np.asarray(res["result"]["T_flange_camera"])
    assert pose_delta(X, X_TRUE)[0] < 2.0 and pose_delta(X, X_TRUE)[1] < 0.3
    assert res["result"]["quality"]["n_holdout"] >= 3 and res["result"]["approved_for_localization"] is False
    assert not (tmp_path / "hand_eye_flange.json").exists()               # solving never activates
    assert client.get("/calibration/status").json()["card_state"] == "Pending validation"
    cell.T_flange = flange_poses(1, seed=99)[0]
    v = client.post(f"/calibration/sessions/{sid}/validate").json()
    assert v["ok"] and v["validation"]["error_mm"] < 5
    assert client.post(f"/calibration/sessions/{sid}/activate", json={"confirm": False}).status_code == 400
    a = client.post(f"/calibration/sessions/{sid}/activate", json={"confirm": True, "operator_note": "test"}).json()
    assert a["ok"] and a["calibration_id"].startswith("hef-")
    assert node.hand_eye is not None and node.hand_eye.tcp_source == "ur_flange"
    st = client.get("/calibration/status").json()
    assert st["card_state"] == "Calibrated" and st["active_calibration"]["valid"]

    # --- single-shot locate
    p_true = np.array([-0.52, 0.11, 0.015, 1.0])
    cell.T_flange = flange_poses(1, seed=7)[0]
    p_cam = np.linalg.inv(cell.T_flange @ X_TRUE) @ p_true
    obj = dict(id=4, label="vite_arancione", name="orange_screw_03", conf=0.9, bbox=(0.4, 0.4, 0.1, 0.1),
               mask=[(0.4, 0.4), (0.5, 0.4), (0.5, 0.5)], xyz_cam_m=tuple(p_cam[:3]), depth_points=900,
               depth_valid_ratio=0.95)
    node.tracker.objects = [EyeObject(**obj)]
    r = client.post("/localization/snapshot", json={"source": "arm", "object_label": "orange_screw_03"}).json()
    assert r["ok"] and r["motion_allowed"] is False and r["point_semantics"] == "visible_surface_centroid_3d"
    assert np.allclose(r["position_base_m"], p_true[:3], atol=2e-3), (r["position_base_m"], p_true)
    assert r["calibration_id"] == a["calibration_id"] and r["coordinate_frame"] == "ur5e_base"

    def snap():
        return client.post("/localization/snapshot", json={"object_label": "orange_screw_03"})
    node.tracker.objects = [EyeObject(**{**obj, "bbox": (0.0, 0.4, 0.1, 0.1)})]
    assert snap().json()["status"] == "object_partially_visible"
    node.tracker.objects = [EyeObject(**{**obj, "depth_valid_ratio": 0.1})]
    assert snap().json()["status"] == "invalid_depth"
    node.tracker.objects = [EyeObject(**{**obj, "xyz_cam_m": None})]
    assert snap().json()["status"] == "invalid_depth"
    node.tracker.objects = []
    assert snap().json()["status"] == "object_not_detected"
    node.tracker.objects = [EyeObject(**obj)]
    cell.moving = True
    assert snap().json()["status"] == "robot_moving"
    cell.moving = False
    reads = {"n": 0}

    def drift():                    # the robot moves between the first stable pair and the closing read
        reads["n"] += 1
        if reads["n"] == 2:
            cell.T_flange = cell.T_flange @ pose_to_T([0.003, 0, 0, 0, 0, 0])
    cell.after_read = drift
    assert snap().json()["status"] == "unsynced_robot_pose"
    cell.after_read = None
    assert snap().json()["ok"]

    # --- all objects from one frame (digital twin feed): per-object status, never a fabricated point
    cell.T_flange = flange_poses(1, seed=7)[0]      # undo the 3 mm drift above: obj was made for this pose
    good = EyeObject(**obj)
    cut = EyeObject(**{**obj, "id": 5, "label": "anello_verde", "name": "green_ring_02", "bbox": (0.0, 0.4, 0.1, 0.1)})
    node.tracker.objects = [good, cut]
    allr = client.post("/localization/snapshot_all").json()
    assert allr["ok"] and allr["motion_allowed"] is False and allr["coordinate_frame"] == "ur5e_base"
    assert allr["calibration_id"] == a["calibration_id"] and allr["calibration_reference_frame"] == "flange"
    assert allr["robot_stationary"] is True and allr["pose_frame_synchronized"] is True
    by = {o["track_id"]: o for o in allr["objects"]}
    assert by[4]["status"] == "ok" and np.allclose(by[4]["position_base_m"], p_true[:3], atol=2e-3)
    assert by[5]["status"] == "object_partially_visible" and by[5]["position_base_m"] is None
    cell.moving = True
    assert client.post("/localization/snapshot_all").json()["status"] == "robot_moving"
    cell.moving = False
    # changed camera -> refused; changed mount in the setup -> calibration needs revalidation
    node.reader.device_serial = "OTHER"
    assert snap().json()["status"] == "camera_mismatch"
    node.reader.device_serial = "SIM-0001"
    setup = client.get("/calibration/setup").json()["setup"]
    setup["camera_mount_estimate"]["y_mm"] = 5.0
    assert client.put("/calibration/setup", json=setup).json()["calibration_requires_revalidation"] is True
    assert snap().json()["status"] == "missing_calibration"
    assert client.get("/calibration/status").json()["card_state"] == "Error"


def test_capture_rejects_moving_robot_and_stale_frames(tmp_path):
    cell = SimCell()
    client, _node, _svc = _client(tmp_path, cell)
    sid = client.post("/calibration/sessions").json()["session"]["session_id"]
    cell.moving = True
    assert client.post(f"/calibration/sessions/{sid}/capture").json()["status"] == "robot_moving"
    cell.moving = False
    assert client.post(f"/calibration/sessions/{sid}/capture").json()["ok"]
    cell.T_flange = flange_poses(2, seed=8)[1]
    cell.frozen_frame = True                      # camera keeps returning the same frame id
    r = client.post(f"/calibration/sessions/{sid}/capture").json()
    assert not r["ok"] and r["status"] == "stale_frame"
    cell.frozen_frame = False
    # setup changed mid-session: captures refused
    setup = client.get("/calibration/setup").json()["setup"]
    setup["quality_gates"]["max_board_spread_mm"] = 4.0
    client.put("/calibration/setup", json=setup)
    assert client.post(f"/calibration/sessions/{sid}/capture").json()["status"] == "setup_changed"


def test_auth_required_when_key_set(tmp_path, monkeypatch):
    cell = SimCell()
    client, _n, _s = _client(tmp_path, cell)
    monkeypatch.setenv("BRIDGE_KEY", "secret-key-123")
    assert client.get("/calibration/status").status_code == 401
    assert client.post("/calibration/sessions").status_code == 401
    assert client.post("/calibration/arm/reset").status_code == 401        # legacy mutating route too
    assert client.get("/calibration/status", headers={"X-Bridge-Key": "secret-key-123"}).status_code == 200


# --- 9. persistence -----------------------------------------------------------------------------------------------

def test_persistence_rejects_dummy_legacy_and_unapproved(tmp_path):
    cfg = parse_setup(setup_dict())[0]
    cam = {"serial": "S", "width": 640, "height": 480, "fx": 615.0, "fy": 612.0, "cx": 322.0, "cy": 241.0}
    assert load_active(tmp_path, cfg, cam)[1] == "missing_calibration"
    (tmp_path / "hand_eye_flange.json").write_text(json.dumps({"T_tool_cam": np.eye(4).tolist()}))
    assert "legacy" in load_active(tmp_path, cfg, cam)[2]
    dummy = {"reference_frame": "flange", "calibration_type": "eye_in_hand_3d", "T_flange_camera": np.eye(4).tolist(),
             "passes_quality_gates": True, "quality": {"holdout_error_mm": 1.0}, "approved_for_localization": True,
             "camera_serial": "S", "identity_sha256": identity_sha256(cfg)}
    assert any("identity" in p for p in activation_problems(dummy))
    (tmp_path / "hand_eye_flange.json").write_text(json.dumps(dummy))
    assert load_active(tmp_path, cfg, cam)[0] is None
    good = {**dummy, "T_flange_camera": X_TRUE.tolist(), "approved_for_localization": False, "calibration_id": "hef-x",
            "intrinsics": {"width": 640, "height": 480, "fx": 615.0, "fy": 612.0, "cx": 322.0, "cy": 241.0}}
    (tmp_path / "hand_eye_flange.json").write_text(json.dumps(good))
    assert "not approved" in load_active(tmp_path, cfg, cam)[2]
    (tmp_path / "hand_eye_flange.json").write_text(json.dumps({**good, "approved_for_localization": True}))
    cal, status, _ = load_active(tmp_path, cfg, cam)
    assert status == "ok" and np.allclose(cal.T_flange_camera, X_TRUE)
    assert load_active(tmp_path, cfg, {**cam, "fx": 640.0})[1] == "camera_mismatch"
    assert load_active(tmp_path, cfg, {**cam, "serial": "T"})[1] == "camera_mismatch"


def test_activation_keeps_previous_file_in_history(tmp_path):
    cell = SimCell()
    client, _node, _svc = _client(tmp_path, cell)
    ids = []
    for seed in (2, 3):
        sid = client.post("/calibration/sessions").json()["session"]["session_id"]
        _collect(client, cell, sid, 15, seed=seed)
        assert client.post(f"/calibration/sessions/{sid}/solve").json()["ok"]
        ids.append(client.post(f"/calibration/sessions/{sid}/activate", json={"confirm": True}).json()["calibration_id"])
    assert (tmp_path / "history" / f"hand_eye_flange.{ids[0]}.json").exists()
    assert json.loads((tmp_path / "hand_eye_flange.json").read_text())["calibration_id"] == ids[1]


# --- 10. the app wizard never routes into motion ---------------------------------------------------------------

def test_app_calibration_client_has_no_motion_paths():
    app = Path(__file__).resolve().parents[2] / "groky-app" / "src"
    for f in (app / "lib" / "calibration.ts", app / "components" / "CalibrationWizard.tsx"):
        if not f.exists():
            pytest.skip("groky-app not present")
        src = f.read_text(encoding="utf-8")
        for bad in ("runSkill", "skills/runner", "/arm/move", "MOVE_", "physical_tool", "armApproval", "/visit"):
            assert bad not in src, (f.name, bad)
