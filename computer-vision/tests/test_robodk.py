"""Simulazione RoboDK senza RoboDK: un RoboDK finto che "rende" tavolo + vite arancione da una posa nota, nello stesso
formato del vero (PNG per il colore, .grey32 big-endian per la profondità). Verifica tutta la catena:
reader RoboDK -> maschera -> centroide 3D della superficie -> base -> /locate, contro la posizione vera."""
from __future__ import annotations

import math

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from vision.eyes.calibration.hand_eye import HandEye, pose_to_T, table_footprint
from vision.eyes.capture import robodk as rdk_mod
from vision.eyes.capture.robodk import U32_MAX, grey32_to_depth_mm, intrinsics_from_fov
from vision.eyes.pipeline import EyePipeline
from vision.eyes.server.app import create_app

W, H, FAR_MM = 640, 480, 2000.0
FLANGE = [-0.45, -0.20, 0.45, math.pi, 0.0, 0.0]          # flangia in osservazione, 45 cm sopra il tavolo
MOUNT = [0.0, -0.07, 0.03, 0.0, 0.0, 0.0]                  # camera 7 cm di lato e 3 cm avanti rispetto alla flangia
SCREW = dict(center=(-0.44, -0.15), size=(0.095, 0.035), yaw_deg=30.0, top=0.035)  # vite sdraiata, cima a 3,5 cm


class _Mat:
    """Come robomath.Mat (mm), indicizzabile con [i, j]."""

    def __init__(self, T_m: np.ndarray):
        self.M = np.array(T_m, dtype=float)
        self.M[:3, 3] *= 1000.0

    def __getitem__(self, ij):
        return self.M[ij]


class _Item:
    def __init__(self, name="", valid=True, **kw):
        self.name, self.valid = name, valid
        self.__dict__.update(kw)
        self.pose_abs = np.eye(4)

    def Valid(self):
        return self.valid

    def Name(self):
        return self.name

    def setName(self, n):
        self.name = n

    def setPoseAbs(self, T):
        self.pose_abs = np.asarray(T, dtype=float)


class FakeRoboDK:
    def __init__(self):
        self.robot = _Item("UR5e")
        self.robot.Joints = lambda: None
        self.robot.SolveFK = lambda joints: _Mat(pose_to_T(FLANGE))
        self.robot.PoseAbs = lambda: _Mat(np.eye(4))      # base del robot = origine della stazione
        self.robot.Busy = lambda: 0
        screw_T = pose_to_T([*SCREW["center"], 0.0, 0, 0, 0])
        self.items = {"vite_arancione": _Item("vite_arancione", PoseAbs=lambda: _Mat(screw_T))}
        self.frame = None
        self.intr = intrinsics_from_fov(W, H, rdk_mod.D435_COLOR_VFOV_DEG)

    def Item(self, name, item_type=None):
        if item_type == rdk_mod.ITEM_TYPE_ROBOT:
            return self.robot
        return self.items.get(name, _Item(name, valid=False))

    def AddFrame(self, name):
        self.frame = _Item(name)
        return self.frame

    def Cam2D_Add(self, frame, params, old=None):
        return _Item("cam", depth="DEPTH" in params)

    def _render(self):
        """Raggi della camera (posa = quella che il reader ha messo sul frame) contro tavolo e cima della vite."""
        T = self.frame.pose_abs
        vs, us = np.mgrid[0:H, 0:W]
        rays = np.stack([(us - self.intr.cx) / self.intr.fx, (vs - self.intr.cy) / self.intr.fy, np.ones((H, W))], -1)
        d = rays @ T[:3, :3].T
        o = T[:3, 3]
        s_top = (SCREW["top"] - o[2]) / d[..., 2]
        hit = o[:2] + s_top[..., None] * d[..., :2] - np.array(SCREW["center"])
        a = math.radians(SCREW["yaw_deg"])
        along = hit[..., 0] * math.cos(a) + hit[..., 1] * math.sin(a)
        across = -hit[..., 0] * math.sin(a) + hit[..., 1] * math.cos(a)
        on_screw = (np.abs(along) <= SCREW["size"][0] / 2) & (np.abs(across) <= SCREW["size"][1] / 2)
        z = np.where(on_screw, s_top, (0.0 - o[2]) / d[..., 2])     # Z camera = s, perché il raggio ha z = 1
        color = np.full((H, W, 3), 200, np.uint8)
        color[on_screw] = (0, 128, 255)                              # arancione (BGR)
        return color, z * 1000.0

    def Cam2D_Snapshot(self, path, cam, params=""):
        color, z_mm = self._render()
        if not cam.depth:
            return cv2.imencode(".png", color)[1].tobytes()
        raw = np.flipud(np.round(z_mm / FAR_MM * U32_MAX)).astype(np.uint64)
        return np.concatenate([[W, H], raw.ravel()]).astype(">u4").tobytes()   # come RoboDK 6.2: big-endian, w, h, pixel


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(rdk_mod, "np_to_mat", lambda T: T)          # senza il pacchetto robodk
    return FakeRoboDK()


def test_grey32_conversion_and_intrinsics():
    intr = intrinsics_from_fov(640, 480, 42.0)
    assert abs(intr.fy - 240 / math.tan(math.radians(21))) < 1e-9 and intr.fx == intr.fy and intr.cx == 320
    raw = np.array([[0, U32_MAX * 0.25]], dtype=float)
    assert grey32_to_depth_mm(raw, 2000.0, intr).tolist() == [[0, 500]]          # 0 = nessuna misura
    assert grey32_to_depth_mm(raw, 2000.0, intr, invert=True).tolist() == [[0, 1500]]
    ray = np.full((480, 640), U32_MAX * 0.25)
    z = grey32_to_depth_mm(ray, 2000.0, intr, is_ray=True)
    assert z[240, 320] == 500 and z[0, 0] < 500                                  # ai bordi la Z è più corta del raggio


def test_robodk_reader_matches_the_rendered_scene(fake):
    r = rdk_mod.RoboDKCameraReader(rdk=fake, mount=MOUNT)
    T_true = pose_to_T(FLANGE) @ pose_to_T(MOUNT)
    assert np.allclose(r.camera_in_base(), T_true)
    r.read_once()
    ok, color, depth, fid, _ = r.get_frames()
    assert ok and fid == 1 and color.shape == (480, 640, 3) and depth.dtype == np.uint16
    assert depth[10, 10] > 0 and abs(np.median(depth) - T_true[2, 3] * 1000) < 40       # il tavolo visto dall'alto


def test_full_chain_locate_screw_in_simulation(fake, tmp_path):
    """Calibrazione 'vera' (la posa della camera nota a RoboDK) -> /locate della vite -> errore rispetto a RoboDK."""
    T_true = pose_to_T(FLANGE) @ pose_to_T(MOUNT)
    fixed = HandEye(T_tool_cam=T_true, method="robodk_truth", tcp_source="fixed").save(tmp_path / "fixed_pose.json")
    pipe = EyePipeline()
    node = pipe.add_source("arm", mode="robodk", use_mock_tracker=True, fixed_pose_file=fixed,
                           color_labels={"arancione": "vite_arancione"},
                           robodk_options={"rdk": fake, "mount": MOUNT})
    fp = table_footprint(T_true, node.reader.intrinsics, 0.0)
    assert fp is not None and cv2.pointPolygonTest(fp.astype(np.float32), SCREW["center"], False) > 0
    client = TestClient(create_app(pipe))
    res = client.get("/locate/arm?label=vite_arancione&n=3&timeout_s=5").json()
    assert res["status"] == "ok", res
    est = np.array(res["position_base_m"])
    assert np.linalg.norm(est[:2] - np.array(SCREW["center"])) < 0.004          # < 4 mm in XY
    assert abs(est[2] - SCREW["top"]) < 0.004                                   # la cima della vite
    assert abs(res["yaw_base_deg"] - SCREW["yaw_deg"]) < 3.0
    assert res["point_semantics"] == "visible_surface_centroid_3d" and res["num_valid_points"] > 100
    assert res["calibration_id"].startswith("fixed_pose_") and res["object_class"] == "vite_arancione"
    gone = client.get("/locate/arm?label=secchio_blu&n=2&timeout_s=1").json()
    assert gone["status"] == "object_not_detected" and gone["position_base_m"] is None


def test_locate_refuses_objects_cut_by_the_image_border(fake, tmp_path, monkeypatch):
    monkeypatch.setitem(SCREW, "center", (-0.43, -0.27))          # vite sul bordo basso dell'immagine
    T_true = pose_to_T(FLANGE) @ pose_to_T(MOUNT)
    fixed = HandEye(T_tool_cam=T_true, tcp_source="fixed").save(tmp_path / "fixed_pose.json")
    pipe = EyePipeline()
    pipe.add_source("arm", mode="robodk", use_mock_tracker=True, fixed_pose_file=fixed,
                    color_labels={"arancione": "vite_arancione"}, robodk_options={"rdk": fake, "mount": MOUNT})
    res = TestClient(create_app(pipe)).get("/locate/arm?label=vite_arancione&n=3&timeout_s=2").json()
    assert res["status"] == "object_partially_visible" and res["position_base_m"] is None


def test_robodk_flange_is_read_only_pose(fake):
    r = rdk_mod.RoboDKFlange(rdk=fake)()
    assert np.allclose(pose_to_T(r.pose), pose_to_T(FLANGE)) and not r.moving
    assert np.allclose(rdk_mod.item_position_base(fake, fake.robot, "vite_arancione")[:2], SCREW["center"])
    with pytest.raises(RuntimeError):
        rdk_mod.item_position_base(fake, fake.robot, "non_esiste")
