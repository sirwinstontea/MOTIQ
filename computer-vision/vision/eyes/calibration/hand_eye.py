"""Camera sul braccio (eye-in-hand): passaggio camera -> base del robot, in due modi.

1. Posa fissa (consigliato, `calibrate-look-pose`): il braccio osserva sempre dalla stessa posa, quindi
       P_base = T_base_cam (costante, da una foto del foglio con i marker misurati nel base) · P_cam
2. Pose qualsiasi (`calibrate-hand-eye`):
       P_base = T_base_tool (posa TCP allo scatto) · T_tool_cam (calibrazione hand-eye) · P_cam

- Posa TCP come la dà un UR: `[x, y, z]` in m e rotation vector `[rx, ry, rz]` in rad, frame base. A runtime arriva
  da robot_controller `GET /robot/state` (sola lettura) o da una posa fissa letta sul pendant (`--look-pose`).
  Calibrazione e runtime devono usare la **stessa** fonte: TCP diversi danno una `T_tool_cam` diversa.
- Bersaglio: il foglio A4 di `calibrate-arm --generate-board` (DICT_4X4_50, ID 0-3, lato 50,8 mm), fermo sul tavolo.
- Si misura solo a robot fermo (`still_for_frame`): la posa letta deve essere quella del frame.
- Presa dall'alto: lo yaw è l'asse lungo dell'oggetto proiettato sul piano XY della base, in `[-90, 90]` (una pinza a
  due dita è simmetrica di 180°).
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .aruco_calib import detect_aruco_corners

# Geometria del foglio, la stessa di `cli calibrate-arm --generate-board` (A4 orizzontale a 300 dpi)
_DPI, _W_PX, _H_PX, _SIDE_PX, _MARGIN_PX = 300, 3508, 2480, 600, 150


def _px_m(px: float) -> float:
    return px / _DPI * 0.0254


BOARD_SIDE_M = _px_m(_SIDE_PX)  # 50,8 mm
# Angolo in alto a sinistra di ogni marker sul foglio (m). Frame del foglio: x a destra, y in giù, z dentro il tavolo
BOARD_TOPLEFT_M: dict[int, tuple[float, float]] = {
    0: (_px_m(_MARGIN_PX), _px_m(_MARGIN_PX)),
    1: (_px_m(_W_PX - _MARGIN_PX - _SIDE_PX), _px_m(_MARGIN_PX)),
    2: (_px_m(_W_PX - _MARGIN_PX - _SIDE_PX), _px_m(_H_PX - _MARGIN_PX - _SIDE_PX)),
    3: (_px_m(_MARGIN_PX), _px_m(_H_PX - _MARGIN_PX - _SIDE_PX)),
}

STILL_TOL_M = 0.001          # robot "fermo": la posa TCP cambia meno di 1 mm ...
STILL_TOL_DEG = 0.5          # ... e meno di mezzo grado
STILL_BEFORE_S = 0.1         # ... da almeno 0,1 s prima del frame (la RealSense può dare un frame già in coda)
MAX_STATE_AGE_MS = 500.0     # posa del controller più vecchia di così: non vale
DEFAULT_MAX_SPREAD_M = 0.01  # /locate: campioni più sparsi di 1 cm = misura non affidabile


# --- pose ----------------------------------------------------------------------------------------------------

def pose_to_T(pose: Sequence[float]) -> np.ndarray:
    """`[x, y, z, rx, ry, rz]` (m, rotation vector UR) -> matrice omogenea 4x4."""
    T = np.eye(4)
    T[:3, :3] = cv2.Rodrigues(np.asarray(pose[3:6], dtype=np.float64))[0]
    T[:3, 3] = pose[:3]
    return T


def T_to_pose(T: np.ndarray) -> list[float]:
    rv = cv2.Rodrigues(np.asarray(T[:3, :3], dtype=np.float64))[0].ravel()
    return [float(v) for v in (*T[:3, 3], *rv)]


def parse_pose_mm(text: str) -> list[float]:
    """`"x, y, z, rx, ry, rz"` come sul pendant UR (mm e rad) -> `[x, y, z, rx, ry, rz]` in m e rad."""
    vals = [float(v) for v in text.replace(";", " ").replace(",", " ").split()]
    if len(vals) != 6:
        raise ValueError(f"servono 6 numeri (x y z in mm, rx ry rz in rad), ricevuti {len(vals)}")
    return [vals[0] / 1000.0, vals[1] / 1000.0, vals[2] / 1000.0, *vals[3:]]


def is_stationary(a: Sequence[float], b: Sequence[float], tol_m: float = STILL_TOL_M,
                  tol_deg: float = STILL_TOL_DEG) -> bool:
    """Due pose TCP uguali entro `tol_m` e `tol_deg`."""
    if np.linalg.norm(np.subtract(a[:3], b[:3])) > tol_m:
        return False
    Ra, Rb = pose_to_T(a)[:3, :3], pose_to_T(b)[:3, :3]
    cos = (np.trace(Ra.T @ Rb) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, cos)))) <= tol_deg


@dataclass(frozen=True)
class TcpReading:
    pose: tuple[float, ...]       # [x, y, z, rx, ry, rz], m e rad, frame base
    t: float                      # time.time() della lettura
    moving: bool = False
    age_ms: float | None = None   # età del dato nel controller


def still_for_frame(readings: Sequence[TcpReading], frame_ts: float,
                    before_s: float = STILL_BEFORE_S) -> TcpReading | None:
    """Ultima lettura se il robot era fermo **durante** il frame, altrimenti `None`.

    Il frame è stretto tra una lettura fatta almeno `before_s` prima e l'ultima, fatta dopo: se le due pose sono uguali
    e nessuna delle due dice "in moto", il frame è stato preso a robot fermo in quella posa.
    """
    if not readings:
        return None
    cur = readings[-1]
    before = next((r for r in reversed(readings) if r.t <= frame_ts - before_s), None)
    if before is None or cur.t < frame_ts or cur.moving or before.moving:
        return None
    if cur.age_ms is not None and cur.age_ms > MAX_STATE_AGE_MS:
        return None
    return cur if is_stationary(before.pose, cur.pose) else None


class ControllerTcp:
    """Posa TCP da robot_controller `GET /robot/state`, sola lettura. Senza risposta riprova dopo `backoff_s`, così
    un controller spento non rallenta la pipeline a ogni frame."""

    def __init__(self, url: str, timeout_s: float = 0.3, backoff_s: float = 2.0):
        self.url = url.rstrip("/") + "/robot/state"
        self.timeout_s, self.backoff_s = timeout_s, backoff_s
        self._retry_at = 0.0
        self.last_error: str | None = None

    def __call__(self) -> TcpReading | None:
        now = time.time()
        if now < self._retry_at:
            return None
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout_s) as r:
                st = json.loads(r.read().decode("utf-8"))
            p = st["tcp_pose"]
            pose = tuple(float(p[k]) for k in ("x", "y", "z", "rx", "ry", "rz"))
        except Exception as exc:  # noqa: BLE001 - controller spento, posa assente: niente coordinate in base
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._retry_at = now + self.backoff_s
            return None
        self.last_error = None
        return TcpReading(pose=pose, t=time.time(), moving=bool(st.get("busy", False)),
                          age_ms=st.get("data_age_ms"))


class FixedTcp:
    """Posa TCP fissa (letta sul pendant): per provare dalla look pose senza un controller collegato al robot."""

    def __init__(self, pose: Sequence[float]):
        self.pose = tuple(float(v) for v in pose)

    def __call__(self) -> TcpReading:
        return TcpReading(pose=self.pose, t=time.time())


# --- foglio ArUco ----------------------------------------------------------------------------------------------

def camera_matrix(intrinsics: Any) -> np.ndarray:
    return np.array([[intrinsics.fx, 0, intrinsics.cx], [0, intrinsics.fy, intrinsics.cy], [0, 0, 1]], dtype=np.float64)


def board_pose(frame_bgr: np.ndarray, intrinsics: Any, min_markers: int = 2) -> tuple[np.ndarray, int, float] | None:
    """Posa del foglio nel frame camera: `(T_cam_board 4x4, marker visti, errore di riproiezione px)`.

    Con meno di `min_markers` marker la posa di un solo quadratino è troppo incerta per la calibrazione.
    """
    corners = {m: c for m, c in detect_aruco_corners(frame_bgr).items() if m in BOARD_TOPLEFT_M}
    if len(corners) < min_markers:
        return None
    s = BOARD_SIDE_M
    obj, img = [], []
    for m_id, c in corners.items():
        x0, y0 = BOARD_TOPLEFT_M[m_id]
        obj += [(x0, y0, 0.0), (x0 + s, y0, 0.0), (x0 + s, y0 + s, 0.0), (x0, y0 + s, 0.0)]
        img += [tuple(p) for p in c]
    obj_a, img_a = np.array(obj, dtype=np.float64), np.array(img, dtype=np.float64)
    K, dist = camera_matrix(intrinsics), np.zeros(5)
    ok, rvec, tvec = cv2.solvePnP(obj_a, img_a, K, dist, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        return None
    rvec, tvec = cv2.solvePnPRefineLM(obj_a, img_a, K, dist, rvec, tvec)
    proj, _ = cv2.projectPoints(obj_a, rvec, tvec, K, dist)
    err = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img_a) ** 2, axis=1))))
    T = np.eye(4)
    T[:3, :3] = cv2.Rodrigues(rvec)[0]
    T[:3, 3] = tvec.ravel()
    return T, len(corners), err


# --- calibrazione ----------------------------------------------------------------------------------------------

# Altri metodi solo se la build di OpenCV ha `calibrateHandEye` (la 5.0 di pip non ce l'ha, ha solo le costanti)
CV_METHODS = {"tsai": cv2.CALIB_HAND_EYE_TSAI, "horaud": cv2.CALIB_HAND_EYE_HORAUD,
              "daniilidis": cv2.CALIB_HAND_EYE_DANIILIDIS}


def _park_martin(Tg: Sequence[np.ndarray], Tc: Sequence[np.ndarray]) -> np.ndarray:
    """Park & Martin (1994) su tutte le coppie di pose: `A X = X B` con A = moto della pinza, B = moto del foglio.

    Il foglio è fermo: `Tg_i X Tc_i = Tg_j X Tc_j`, quindi `A = Tg_j⁻¹ Tg_i` e `B = Tc_j Tc_i⁻¹`. Rotazione: i vettori
    di rotazione soddisfano `a = R_X b`, si risolve con Kabsch (SVD). Traslazione: `(R_A − I) t_X = R_X t_B − t_A`
    ai minimi quadrati.
    """
    A, B = [], []
    for i in range(len(Tg)):
        for j in range(i + 1, len(Tg)):
            A.append(np.linalg.inv(Tg[j]) @ Tg[i])
            B.append(Tc[j] @ np.linalg.inv(Tc[i]))
    a = np.array([cv2.Rodrigues(m[:3, :3])[0].ravel() for m in A])
    b = np.array([cv2.Rodrigues(m[:3, :3])[0].ravel() for m in B])
    U, _, Vt = np.linalg.svd(b.T @ a)
    D = np.diag([1.0, 1.0, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    C = np.vstack([m[:3, :3] - np.eye(3) for m in A])
    d = np.concatenate([R @ mb[:3, 3] - ma[:3, 3] for ma, mb in zip(A, B)])
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, np.linalg.lstsq(C, d, rcond=None)[0]
    return T


def calibrate(tcp_poses: Sequence[Sequence[float]], T_cam_boards: Sequence[np.ndarray],
              method: str = "park") -> np.ndarray:
    """`T_tool_cam` da N coppie (posa TCP nel base, posa del foglio nella camera). Servono almeno 3 pose con
    rotazioni diverse; 12-15 su due assi danno un risultato stabile."""
    if len(tcp_poses) != len(T_cam_boards) or len(tcp_poses) < 3:
        raise ValueError(f"servono almeno 3 coppie posa/foglio, ricevute {len(tcp_poses)}/{len(T_cam_boards)}")
    Tg = [pose_to_T(p) for p in tcp_poses]
    if method == "park":
        return _park_martin(Tg, T_cam_boards)
    if not hasattr(cv2, "calibrateHandEye"):
        raise ValueError(f"metodo '{method}' non disponibile: questa OpenCV non ha calibrateHandEye (usa 'park')")
    R, t = cv2.calibrateHandEye([T[:3, :3] for T in Tg], [T[:3, 3] for T in Tg],
                                [T[:3, :3] for T in T_cam_boards], [T[:3, 3] for T in T_cam_boards],
                                method=CV_METHODS[method])
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, np.asarray(t).ravel()
    return T


def board_spread_mm(tcp_poses: Sequence[Sequence[float]], T_cam_boards: Sequence[np.ndarray],
                    T_tool_cam: np.ndarray) -> float:
    """Il foglio è fermo: `T_base_tool · T_tool_cam · T_cam_board` deve dare sempre lo stesso punto. Restituisce lo
    scarto quadratico medio (mm) dell'origine del foglio dalla sua media: è l'accuratezza della calibrazione."""
    pts = np.array([(pose_to_T(p) @ T_tool_cam @ Tb)[:3, 3] for p, Tb in zip(tcp_poses, T_cam_boards)])
    return float(np.sqrt(np.mean(np.sum((pts - pts.mean(axis=0)) ** 2, axis=1))) * 1000.0)


@dataclass
class HandEye:
    T_tool_cam: np.ndarray
    method: str = "park"
    n_samples: int = 0
    spread_mm: float | None = None
    tcp_source: str = "controller"           # "controller" o "manual": a runtime va usata la stessa fonte
    image_size: tuple[int, int] | None = None
    extra: dict = field(default_factory=dict)

    def to_base(self, xyz_cam: Sequence[float], axis_cam: Sequence[float] | None,
                tcp_pose: Sequence[float]) -> tuple[tuple[float, float, float], float | None]:
        """Punto camera (m) e asse lungo -> punto nel frame base (m) e yaw dell'asse attorno a Z base (gradi)."""
        T_base_cam = pose_to_T(tcp_pose) @ self.T_tool_cam
        p = T_base_cam @ np.array([*xyz_cam, 1.0])
        yaw = None
        if axis_cam is not None:
            a = T_base_cam[:3, :3] @ np.asarray(axis_cam, dtype=np.float64)
            if math.hypot(a[0], a[1]) > 1e-6:  # asse quasi verticale: lo yaw non esiste
                yaw = math.degrees(math.atan2(a[1], a[0]))
                yaw = (yaw + 90.0) % 180.0 - 90.0  # pinza simmetrica: [-90, 90)
        return (float(p[0]), float(p[1]), float(p[2])), yaw

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "T_tool_cam": self.T_tool_cam.tolist(), "method": self.method, "n_samples": self.n_samples,
            "spread_mm": self.spread_mm, "tcp_source": self.tcp_source,
            "image_size": list(self.image_size) if self.image_size else None, **self.extra,
        }, indent=2), encoding="utf-8")
        return out

    @classmethod
    def load(cls, path: str | Path) -> HandEye:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        T = np.asarray(d["T_tool_cam"], dtype=np.float64)
        if T.shape != (4, 4):
            raise ValueError(f"T_tool_cam deve essere 4x4, è {T.shape}")
        known = {"T_tool_cam", "method", "n_samples", "spread_mm", "tcp_source", "image_size"}
        return cls(T_tool_cam=T, method=d.get("method", "park"), n_samples=int(d.get("n_samples", 0)),
                   spread_mm=d.get("spread_mm"), tcp_source=d.get("tcp_source", "controller"),
                   image_size=tuple(d["image_size"]) if d.get("image_size") else None,
                   extra={k: v for k, v in d.items() if k not in known})


# --- posa fissa: la camera guarda sempre dalla stessa posa del braccio ------------------------------------------
#
# Se il braccio osserva il tavolo sempre dalla stessa posa, camera -> base è UNA matrice costante `T_base_cam`:
# niente hand-eye e niente posa del robot. Si calibra con una foto del foglio ArUco i cui 4 centri sono stati misurati
# nel frame base (toccandoli con il TCP), e si salva come `HandEye` con `tcp_source="fixed"` e T_tool_cam = T_base_cam:
# a runtime la "posa TCP" è l'identità, quindi `to_base` fa proprio `T_base_cam · P_cam`.

FIXED_POSE = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)  # posa identità: con tcp_source="fixed" il "tool" coincide con la base


def _sheet_centers_m() -> dict[int, tuple[float, float]]:
    h = BOARD_SIDE_M / 2.0
    return {m: (x + h, y + h) for m, (x, y) in BOARD_TOPLEFT_M.items()}


def board_corners_in_base(markers_base_mm: dict[int, Sequence[float]], table_z_m: float) -> dict[int, np.ndarray]:
    """Corner (4, 3) in m nel frame base di ogni marker, dai centri misurati nel base (mm, sul piano del tavolo).

    Il passaggio foglio -> base si stima come trasformazione affine 2D dai centri: assorbe rotazione, spostamento,
    specchiatura degli assi e un'eventuale stampa non al 100%. Servono almeno 3 centri; con 4 l'errore si vede.
    """
    sheet = _sheet_centers_m()
    ids = [m for m in markers_base_mm if m in sheet]
    if len(ids) < 3:
        raise ValueError(f"servono almeno 3 centri di marker misurati nel base, trovati {len(ids)}")
    src = np.array([[*sheet[m], 1.0] for m in ids])
    dst = np.array([[markers_base_mm[m][0] / 1000.0, markers_base_mm[m][1] / 1000.0] for m in ids])
    A = np.linalg.lstsq(src, dst, rcond=None)[0]  # (3, 2): [x, y, 1] del foglio -> [X, Y] del base
    s = BOARD_SIDE_M
    out = {}
    for m, (x0, y0) in BOARD_TOPLEFT_M.items():
        sq = np.array([[x0, y0, 1], [x0 + s, y0, 1], [x0 + s, y0 + s, 1], [x0, y0 + s, 1]])
        xy = sq @ A
        out[m] = np.column_stack([xy, np.full(4, table_z_m)])
    return out


def calibrate_fixed(frame_bgr: np.ndarray, intrinsics: Any, markers_base_mm: dict[int, Sequence[float]],
                    table_z_m: float = 0.0, min_markers: int = 3) -> tuple[np.ndarray, int, float] | None:
    """`(T_base_cam, marker visti, errore di riproiezione px)` da una foto del foglio fatta dalla posa fissa."""
    base_corners = board_corners_in_base(markers_base_mm, table_z_m)
    seen = {m: c for m, c in detect_aruco_corners(frame_bgr).items() if m in base_corners and m in markers_base_mm}
    if len(seen) < min_markers:
        return None
    obj = np.vstack([base_corners[m] for m in seen]).astype(np.float64)
    img = np.vstack([seen[m] for m in seen]).astype(np.float64)
    K, dist = camera_matrix(intrinsics), np.zeros(5)
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        return None
    rvec, tvec = cv2.solvePnPRefineLM(obj, img, K, dist, rvec, tvec)
    proj, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
    err = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img) ** 2, axis=1))))
    T_cam_base = np.eye(4)
    T_cam_base[:3, :3] = cv2.Rodrigues(rvec)[0]
    T_cam_base[:3, 3] = tvec.ravel()
    return np.linalg.inv(T_cam_base), len(seen), err


def table_footprint(T_base_cam: np.ndarray, intrinsics: Any, table_z_m: float) -> np.ndarray | None:
    """Rettangolo di tavolo inquadrato da una posa: i 4 angoli dell'immagine proiettati sul piano z = `table_z_m`,
    (4, 2) in m nel base (alto-sinistra, alto-destra, basso-destra, basso-sinistra). `None` se la camera non guarda
    verso il tavolo. Serve a scegliere la posa di osservazione: vite e secchio devono stare qui dentro, lontano dai
    bordi."""
    w, h = intrinsics.width, intrinsics.height
    out = []
    for u, v in ((0, 0), (w, 0), (w, h), (0, h)):
        ray = T_base_cam[:3, :3] @ np.array([(u - intrinsics.cx) / intrinsics.fx, (v - intrinsics.cy) / intrinsics.fy, 1.0])
        if ray[2] >= -1e-6:  # raggio orizzontale o verso l'alto: non tocca il tavolo
            return None
        s = (table_z_m - T_base_cam[2, 3]) / ray[2]
        out.append((T_base_cam[:3, 3] + s * ray)[:2])
    return np.array(out)


def table_height_error_mm(depth_mm: np.ndarray, intrinsics: Any, T_base_cam: np.ndarray, table_z_m: float,
                          step_px: int = 8) -> float | None:
    """Controllo con la profondità: il tavolo visto dalla D435, portato nel base, deve stare a `table_z_m`.

    Restituisce mediana(Z_base) − quota del tavolo, in mm (gli oggetti sul tavolo sono pochi pixel: la mediana è il
    tavolo). `None` se la profondità è assente. Qualche mm va bene; 1 cm o più = calibrazione o quota sbagliate.
    """
    if depth_mm is None:
        return None
    d = depth_mm[::step_px, ::step_px].astype(np.float64) / 1000.0
    vs, us = np.mgrid[0:depth_mm.shape[0]:step_px, 0:depth_mm.shape[1]:step_px]
    ok = d > 0
    if ok.sum() < 50:
        return None
    z = d[ok]
    pts = np.column_stack([(us[ok] - intrinsics.cx) * z / intrinsics.fx, (vs[ok] - intrinsics.cy) * z / intrinsics.fy,
                           z, np.ones_like(z)])
    zb = (T_base_cam @ pts.T)[2]
    return float((np.median(zb) - table_z_m) * 1000.0)


# --- misura robusta su più frame (GET /locate) -------------------------------------------------------------------

def aggregate(samples: Sequence[tuple[Sequence[float], float | None]],
              max_spread_m: float = DEFAULT_MAX_SPREAD_M) -> dict:
    """Mediana di N campioni `(xyz_base, yaw)`. `{"ok": False, "reason"}` se mancano o sono troppo sparsi.

    `spread_m` = distanza massima di un campione dalla mediana. Lo yaw si media sull'angolo doppio, perché -89° e +89°
    sono quasi lo stesso asse.
    """
    if not samples:
        return {"ok": False, "reason": "nessun campione"}
    pts = np.array([s[0] for s in samples], dtype=np.float64)
    med = np.median(pts, axis=0)
    spread = float(np.max(np.linalg.norm(pts - med, axis=1)))
    if spread > max_spread_m:
        return {"ok": False, "reason": f"misure troppo sparse: {spread * 1000:.1f} mm > {max_spread_m * 1000:.0f} mm",
                "spread_m": round(spread, 4), "n": len(samples)}
    yaws = [math.radians(2 * s[1]) for s in samples if s[1] is not None]
    yaw = None
    if yaws and len(yaws) * 2 >= len(samples):  # yaw solo se la maggior parte dei frame l'ha visto
        yaw = math.degrees(math.atan2(sum(map(math.sin, yaws)), sum(map(math.cos, yaws)))) / 2.0
        yaw = round((yaw + 90.0) % 180.0 - 90.0, 1)
    return {"ok": True, "xyz_base_m": [round(float(v), 4) for v in med], "yaw_base_deg": yaw,
            "spread_m": round(spread, 4), "n": len(samples)}
