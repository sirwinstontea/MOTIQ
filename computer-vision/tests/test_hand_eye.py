"""Camera sul braccio: deproiezione, foglio ArUco, calibrazione hand-eye, camera -> base e misura su più frame."""
from __future__ import annotations

import math
import time

import cv2
import numpy as np
import pytest

from vision.eyes.calibration.aruco_calib import render_aruco_workspace
from vision.eyes.calibration.hand_eye import (
    FixedTcp, HandEye, TcpReading, aggregate, board_pose, board_spread_mm, calibrate, calibrate_fixed, parse_pose_mm,
    pose_to_T, table_height_error_mm,
    still_for_frame,
)
from vision.eyes.capture.realsense import Intrinsics
from vision.eyes.pipeline import EyePipeline, SourceNode
from vision.eyes.schemas import EyeObject
from vision.eyes.segmentation.measure import axis_cam_from, deproject

INTR = Intrinsics(fx=615.0, fy=612.0, cx=322.0, cy=241.0, width=640, height=480)
DOWN = [math.pi, 0.0, 0.0]  # TCP con la pinza verso il basso (come i target della scena UR5e)


def _K():
    return np.array([[INTR.fx, 0, INTR.cx], [0, INTR.fy, INTR.cy], [0, 0, 1]])


def test_deproject_inverts_projection():
    p = np.array([0.05, -0.03, 0.42])
    u, v = INTR.fx * p[0] / p[2] + INTR.cx, INTR.fy * p[1] / p[2] + INTR.cy
    assert np.allclose(deproject(u, v, p[2], INTR), p, atol=1e-9)
    assert deproject(u, v, 0.0, INTR) is None and deproject(u, v, 0.4, (600.0, 600.0)) is None
    ax = axis_cam_from(30.0, 600.0, 600.0)
    assert np.allclose(ax, (math.cos(math.radians(30)), math.sin(math.radians(30)), 0.0))


def test_board_pose_recovers_printed_sheet():
    """Il foglio di `--generate-board` visto da una posa nota: solvePnP sui 16 corner la ritrova."""
    sheet, _ = render_aruco_workspace(width=3508, height=2480, marker_size_px=600, margin_px=150)
    T = pose_to_T([-0.13, -0.09, 0.45, 0.15, -0.1, 0.05])           # foglio nel frame camera
    S = np.diag([0.0254 / 300, 0.0254 / 300, 1.0])                 # pixel del foglio -> m
    H = _K() @ np.column_stack([T[:3, 0], T[:3, 1], T[:3, 3]]) @ S
    img = cv2.warpPerspective(sheet, H, (640, 480), flags=cv2.INTER_AREA, borderValue=(255, 255, 255))
    found = board_pose(img, INTR)
    assert found is not None
    T_est, n, err = found
    assert n == 4 and err < 1.0
    assert np.linalg.norm(T_est[:3, 3] - T[:3, 3]) < 0.003                       # < 3 mm
    assert np.degrees(np.arccos((np.trace(T_est[:3, :3].T @ T[:3, :3]) - 1) / 2)) < 0.5


def test_calibrate_hand_eye_recovers_known_mount():
    rng = np.random.default_rng(0)
    T_tool_cam = pose_to_T([0.04, -0.06, 0.03, 0.05, -0.08, 1.57])  # camera di lato alla pinza, ruotata di 90°
    T_base_board = pose_to_T([-0.55, -0.15, 0.0, 0.0, 0.0, 0.3])
    poses, boards = [], []
    for _ in range(12):
        R = cv2.Rodrigues(np.array(DOWN))[0] @ cv2.Rodrigues(rng.uniform(-0.35, 0.35, 3))[0]
        pose = [*rng.uniform([-0.6, -0.3, 0.35], [-0.4, 0.0, 0.5]), *cv2.Rodrigues(R)[0].ravel()]
        poses.append(pose)
        boards.append(np.linalg.inv(pose_to_T(pose) @ T_tool_cam) @ T_base_board)
    T = calibrate(poses, boards, "park")
    assert np.linalg.norm(T[:3, 3] - T_tool_cam[:3, 3]) < 1e-3
    assert np.degrees(np.arccos(np.clip((np.trace(T[:3, :3].T @ T_tool_cam[:3, :3]) - 1) / 2, -1, 1))) < 0.1
    assert board_spread_mm(poses, boards, T) < 0.5
    with pytest.raises(ValueError):
        calibrate(poses[:2], boards[:2])


def test_to_base_with_camera_looking_down(tmp_path):
    he = HandEye(T_tool_cam=np.eye(4), n_samples=12, spread_mm=1.0)
    he = HandEye.load(he.save(tmp_path / "he.json"))                # passa dal JSON come a runtime
    tcp = [-0.45, -0.20, 0.40, *DOWN]                                # Rx(pi): x resta, y e z si invertono
    p, yaw = he.to_base((0.01, 0.02, 0.30), (1.0, 0.0, 0.0), tcp)
    assert np.allclose(p, (-0.44, -0.22, 0.10), atol=1e-9) and abs(yaw) < 1e-6
    _, yaw = he.to_base((0.0, 0.0, 0.3), (0.0, 1.0, 0.0), tcp)     # asse lungo la y camera = -y base
    assert abs(abs(yaw) - 90.0) < 1e-6
    assert he.to_base((0.0, 0.0, 0.3), None, tcp)[1] is None


def test_still_for_frame_needs_the_same_pose_around_the_frame():
    pose = (-0.45, -0.2, 0.4, *DOWN)
    moved = (-0.45, -0.195, 0.4, *DOWN)
    assert still_for_frame([TcpReading(pose, 0.0), TcpReading(pose, 0.2)], 0.15) is not None
    assert still_for_frame([TcpReading(pose, 0.0), TcpReading(moved, 0.2)], 0.15) is None   # 5 mm in mezzo
    assert still_for_frame([TcpReading(pose, 0.0), TcpReading(pose, 0.2, moving=True)], 0.15) is None
    assert still_for_frame([TcpReading(pose, 0.1), TcpReading(pose, 0.2)], 0.15) is None    # nessuna lettura prima
    assert still_for_frame([TcpReading(pose, 0.0), TcpReading(pose, 0.2, age_ms=900)], 0.15) is None


def test_source_node_fills_base_coords_only_when_still():
    he = HandEye(T_tool_cam=np.eye(4))
    node = SourceNode(source="arm", reader=None, tracker=None, hand_eye=he,
                      tcp_fn=FixedTcp([-0.45, -0.20, 0.40, *DOWN]))
    obj = EyeObject(id=1, label="vite_arancione", conf=0.9, bbox=(0.4, 0.4, 0.1, 0.1),
                    xyz_cam_m=(0.0, 0.0, 0.3), axis_cam=(1.0, 0.0, 0.0))
    first = node.to_base([obj], time.time())[0]
    assert first.xyz_base_m is None                                 # una lettura sola: non si sa se era fermo
    time.sleep(0.12)                                                # il frame arriva 0,12 s dopo la prima lettura
    frame_ts = time.time()
    out = node.to_base([obj, {"id": 2, "label": "x", "conf": 0.5, "bbox": [0, 0, 0.1, 0.1]}], frame_ts)
    assert out[0].xyz_base_m == (-0.45, -0.2, 0.1) and out[0].yaw_base_deg == 0.0
    assert "xyz_base_m" not in out[1]                               # senza profondità resta com'era


def test_aggregate_median_spread_and_yaw_wrap():
    pts = [((-0.45 + d, -0.2, 0.1), 89.0 if i % 2 else -89.0) for i, d in enumerate((0.0, 0.001, -0.001, 0.002))]
    res = aggregate(pts)
    assert res["ok"] and abs(res["xyz_base_m"][0] + 0.4495) < 1e-3 and abs(abs(res["yaw_base_deg"]) - 90) < 0.2
    bad = aggregate(pts + [((-0.40, -0.2, 0.1), None)], max_spread_m=0.01)
    assert not bad["ok"] and "sparse" in bad["reason"]
    assert not aggregate([])["ok"]
    assert aggregate([((0, 0, 0), None)] * 3)["yaw_base_deg"] is None


def test_parse_pose_mm_like_the_pendant():
    assert parse_pose_mm("-450, -200.5, 400, 3.1416, 0, 0") == [-0.45, -0.2005, 0.4, 3.1416, 0.0, 0.0]
    with pytest.raises(ValueError):
        parse_pose_mm("1 2 3")


# --- posa fissa ---------------------------------------------------------------------------------------------------

def _fixed_scene(table_z=0.02):
    """Foglio sul tavolo, ruotato e specchiato rispetto al base come capita davvero, visto da una camera che guarda
    in giù da ~0,45 m. Restituisce immagine, profondità del tavolo, centri dei marker nel base (mm) e T_base_cam vera."""
    sheet, centers_px = render_aruco_workspace(width=3508, height=2480, marker_size_px=600, margin_px=150)
    a = math.radians(25)
    R2 = np.array([[math.cos(a), math.sin(a)], [math.sin(a), -math.cos(a)]])   # rotazione + specchio (y del foglio in giù)
    off = np.array([-0.60, -0.12])
    px_m = 0.0254 / 300
    to_base = lambda p: R2 @ (np.asarray(p) * px_m) + off                       # pixel del foglio -> XY base (m)
    markers_mm = {m: tuple(to_base(c) * 1000.0) for m, c in centers_px.items()}
    T_base_cam = pose_to_T([-0.42, -0.15, 0.50 + table_z, math.pi, 0.0, 0.0]) @ pose_to_T([0, 0, 0, 0.06, -0.04, 0.15])
    T_cam_base = np.linalg.inv(T_base_cam)
    M = np.array([[R2[0, 0] * px_m, R2[0, 1] * px_m, off[0]],
                  [R2[1, 0] * px_m, R2[1, 1] * px_m, off[1]],
                  [0, 0, table_z], [0, 0, 1]])                                 # pixel del foglio -> punto base
    H = _K() @ T_cam_base[:3] @ M
    img = cv2.warpPerspective(sheet, H, (640, 480), flags=cv2.INTER_AREA, borderValue=(255, 255, 255))
    # profondità del tavolo: intersezione di ogni raggio della camera con il piano z = table_z
    vs, us = np.mgrid[0:480, 0:640]
    rays = np.stack([(us - INTR.cx) / INTR.fx, (vs - INTR.cy) / INTR.fy, np.ones_like(us, float)], -1)
    dirs = rays @ T_base_cam[:3, :3].T
    depth_m = (table_z - T_base_cam[2, 3]) / dirs[..., 2]
    return img, np.round(depth_m * 1000).astype(np.uint16), markers_mm, T_base_cam


def test_calibrate_fixed_recovers_camera_in_base():
    img, depth, markers_mm, T_true = _fixed_scene()
    found = calibrate_fixed(img, INTR, markers_mm, table_z_m=0.02)
    assert found is not None
    T, n, err = found
    assert n == 4 and err < 1.0
    assert np.degrees(np.arccos(np.clip((np.trace(T[:3, :3].T @ T_true[:3, :3]) - 1) / 2, -1, 1))) < 0.5
    # Quello che conta per il pick: un punto sul tavolo visto dalla camera finisce nel posto giusto del base
    for u, v in ((320, 240), (120, 100), (520, 380)):
        p_cam = np.array([*deproject(u, v, depth[v, u] / 1000.0, INTR), 1.0])
        assert np.linalg.norm((T @ p_cam)[:3] - (T_true @ p_cam)[:3]) < 0.002                 # < 2 mm
    assert abs(table_height_error_mm(depth, INTR, T, 0.02)) < 2.0          # la profondità vede il tavolo alla quota
    assert abs(table_height_error_mm(depth, INTR, T, 0.05) + 30.0) < 2.0   # quota sbagliata di 3 cm: si vede
    with pytest.raises(ValueError):
        calibrate_fixed(img, INTR, {0: (0, 0), 1: (100, 0)})               # meno di 3 centri misurati


def test_fixed_pose_file_drives_the_pipeline(tmp_path):
    T_base_cam = pose_to_T([-0.45, -0.20, 0.45, math.pi, 0.0, 0.0])
    path = HandEye(T_tool_cam=T_base_cam, method="fixed_pnp", tcp_source="fixed",
                   extra={"reproj_px": 0.3, "table_check_mm": 1.0}).save(tmp_path / "fixed_pose.json")
    node = EyePipeline().add_source("arm", mode="synthetic", use_mock_tracker=True, fixed_pose_file=path,
                                    calibration_file=tmp_path / "missing.json")
    assert node.calibrator is None and node.hand_eye.extra["reproj_px"] == 0.3   # niente omografia fissa
    obj = EyeObject(id=1, label="vite_arancione", conf=0.9, bbox=(0.4, 0.4, 0.1, 0.1),
                    xyz_cam_m=(0.01, 0.02, 0.40), axis_cam=(1.0, 0.0, 0.0))
    node.to_base([obj], time.time())
    time.sleep(0.12)
    out = node.to_base([obj], time.time())[0]
    assert np.allclose(out.xyz_base_m, (-0.44, -0.22, 0.05), atol=1e-4) and out.yaw_base_deg == 0.0
    bad = HandEye(T_tool_cam=np.eye(4)).save(tmp_path / "not_fixed.json")   # una hand-eye non è una posa fissa
    with pytest.raises(ValueError):
        EyePipeline().add_source("arm", mode="synthetic", use_mock_tracker=True, fixed_pose_file=bad)


def test_hand_eye_gives_marker_centers_without_touching():
    """Dalla hand-eye escono anche i centri dei marker nel base (markers_mm.json) e la quota del foglio."""
    from vision.eyes.calibration.hand_eye import _sheet_centers_m, board_markers_in_base

    rng = np.random.default_rng(1)
    T_tool_cam = pose_to_T([0.04, -0.06, 0.03, 0.05, -0.08, 1.57])
    T_base_board = pose_to_T([-0.60, 0.06, 0.012, 0, 0, 0.2]) @ pose_to_T([0, 0, 0, math.pi, 0, 0])  # piatto, z = 12 mm
    poses, boards = [], []
    for _ in range(12):
        R = cv2.Rodrigues(np.array(DOWN))[0] @ cv2.Rodrigues(rng.uniform(-0.35, 0.35, 3))[0]
        pose = [*rng.uniform([-0.6, -0.3, 0.35], [-0.4, 0.0, 0.5]), *cv2.Rodrigues(R)[0].ravel()]
        poses.append(pose)
        boards.append(np.linalg.inv(pose_to_T(pose) @ T_tool_cam) @ T_base_board)
    T = calibrate(poses, boards)
    centers, z_mm = board_markers_in_base(poses, boards, T)
    for m, (sx, sy) in _sheet_centers_m().items():
        exp = (T_base_board @ np.array([sx, sy, 0.0, 1.0]))[:2] * 1000
        assert np.allclose(centers[m], exp, atol=1.0)                   # entro 1 mm
    assert abs(z_mm - 12.0) < 1.0


# --- calibrazione dall'app (skill): /calibration/arm/* ------------------------------------------------------------

class _FakeReader:
    """Camera finta con gli intrinseci e un frame fisso (colore, profondità)."""

    def __init__(self, color, depth):
        self.intrinsics, self.color, self.depth = INTR, color, depth

    def read_once(self):
        return True, self.color, 1, time.time()

    def get_latest(self):
        return True, self.color, 1, time.time()

    def get_frames(self):
        return True, self.color, self.depth, 1, time.time()


def _app(reader, calib_dir):
    from fastapi.testclient import TestClient

    from vision.eyes.server.app import create_app
    pipe = EyePipeline()
    pipe.nodes["arm"] = SourceNode(source="arm", reader=reader, tracker=None)
    return TestClient(create_app(pipe, calib_dir=calib_dir)), pipe.nodes["arm"]


def test_app_calibration_endpoints_hand_eye_then_look_pose(tmp_path, monkeypatch):
    import json

    from vision.eyes.server import app as app_mod

    # 1) campioni -> /solve: i centri dei marker nel base escono dalla hand-eye, senza toccare il foglio
    rng = np.random.default_rng(2)
    T_tool_cam = pose_to_T([0.04, -0.06, 0.03, 0.05, -0.08, 1.57])
    T_base_board = pose_to_T([-0.60, 0.06, 0.012, 0, 0, 0.2]) @ pose_to_T([0, 0, 0, math.pi, 0, 0])
    current = {}
    monkeypatch.setattr(app_mod, "board_pose",
                        lambda frame, intr: (np.linalg.inv(pose_to_T(current["pose"]) @ T_tool_cam) @ T_base_board, 4, 0.2))
    client, node = _app(_FakeReader(np.zeros((480, 640, 3), np.uint8), None), tmp_path)
    assert client.post("/calibration/arm/sample", json={}).json()["ok"] is False        # niente posa: non inventa
    for _ in range(12):
        R = cv2.Rodrigues(np.array(DOWN))[0] @ cv2.Rodrigues(rng.uniform(-0.35, 0.35, 3))[0]
        current["pose"] = [*rng.uniform([-0.6, -0.3, 0.35], [-0.4, 0.0, 0.5]), *cv2.Rodrigues(R)[0].ravel()]
        r = client.post("/calibration/arm/sample", json={"tcp_pose": current["pose"]}).json()
        assert r["ok"], r
    assert client.post("/calibration/arm/sample", json={"tcp_pose": current["pose"]}).json()["ok"] is False  # ferma
    assert client.get("/calibration/arm").json()["n"] == 12
    he_file, mk_file = tmp_path / "hand_eye.json", tmp_path / "markers_mm.json"
    # legacy file parameters: bare names inside the calibration directory only, never arbitrary paths
    assert client.post(f"/calibration/arm/solve?hand_eye_file={he_file}").status_code == 400
    assert client.post("/calibration/arm/solve?hand_eye_file=../x.json").status_code == 400
    res = client.post("/calibration/arm/solve?hand_eye_file=hand_eye.json&markers_file=markers_mm.json").json()
    assert res["ok"] and res["spread_mm"] < 1.0, res
    mk = json.loads(mk_file.read_text(encoding="utf-8"))
    from vision.eyes.calibration.hand_eye import _sheet_centers_m
    for m, (sx, sy) in _sheet_centers_m().items():
        assert np.allclose(mk[str(m)], (T_base_board @ np.array([sx, sy, 0.0, 1.0]))[:2] * 1000, atol=1.0)
    assert abs(mk["_table_z_mm"] - 12.0) < 1.0
    assert client.post("/calibration/arm/reset").json()["n"] == 0
    assert client.post("/calibration/arm/solve").json()["ok"] is False   # troppo pochi

    # 2) dalla posa di osservazione: il foglio, con i centri nel base, dà fixed_pose.json e viene attivata subito
    img, depth, markers_mm, T_true = _fixed_scene()
    (tmp_path / "markers_base.json").write_text(
        json.dumps({"_table_z_mm": 20.0, **{str(k): list(v) for k, v in markers_mm.items()}}), encoding="utf-8")
    client, node = _app(_FakeReader(img, depth), tmp_path)
    r = client.post("/calibration/arm/look_pose?markers_file=markers_base.json&out=fixed_pose.json").json()
    assert r["ok"] and r["reproj_px"] < 1.0 and abs(r["table_check_mm"]) < 2.0, r
    assert node.hand_eye is not None and node.hand_eye.tcp_source == "fixed" and node.calibration_id.startswith("fixed_pose")
    sheet_mm = {0: [38.1, 38.1], 1: [258.9, 38.1], 2: [258.9, 171.8], 3: [38.1, 171.8]}
    (tmp_path / "sheet.json").write_text(json.dumps({str(k): v for k, v in sheet_mm.items()}), encoding="utf-8")
    bad = client.post("/calibration/arm/look_pose?markers_file=sheet.json&out=x.json").json()
    assert bad["ok"] is False and "foglio" in bad["reason"]            # i centri sul foglio non si accettano
