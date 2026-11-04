"""RoboDK per provare la camera sul braccio in simulazione: SOLA LETTURA, non muove mai il robot.

- `RoboDKFlange`: posa della **flangia** nel base (`SolveFK(Joints())` senza tool, come chiede la specifica: niente
  TCP), in m e rotation vector, come `TcpReading`. Si usa come fonte della posa per hand-eye o per controllo.
- `RoboDKCameraReader`: una D435 simulata (colore + profondità) montata sulla flangia con `mount` (= T_F_C vera). A ogni
  frame la camera viene messa dove la flangia è in quel momento, quindi immagine e posa del robot sono sempre
  sincronizzate. Stessa interfaccia di `RealSenseReader` (`get_frames`, `intrinsics`, ...).
- `item_position_base`: posizione vera di un oggetto della stazione nel base, per misurare l'errore della visione.

Unità: RoboDK lavora in mm, qui tutto esce in m. Frame della camera RoboDK: Z+ = direzione di vista, Y+ = in giù,
quindi X+ = a destra: lo stesso frame ottico della RealSense.

Profondità, verificato su RoboDK 6.2 (4/11/2026): `Cam2D_Snapshot("", cam, "DEPTH")` dà byte uint32 big-endian
`[w, h, pixel...]` con le righe dal basso; mm = valore / (2^32 - 1) · FAR_LENGTH, già Z del piano (non lungo il raggio),
senza inversione. Se una versione diversa si comporta in altro modo ci sono `depth_invert` e `depth_is_ray`: lo si vede
dal controllo del tavolo di `calibrate-look-pose` e da `robodk-check`.
"""
from __future__ import annotations

import importlib
import math
import threading
import time
from collections.abc import Sequence
from typing import Any

import cv2
import numpy as np

from ..calibration.hand_eye import T_to_pose, TcpReading, pose_to_T
from .realsense import Intrinsics
from .reader import make_disconnected_frame

D435_COLOR_VFOV_DEG = 42.0   # campo visivo verticale del colore della D435 (69° x 42°)
U32_MAX = float(2**32 - 1)
try:  # i numeri veri da robolink; i fallback servono solo ai test senza il pacchetto
    from robodk.robolink import ITEM_TYPE_CAMERA, ITEM_TYPE_FRAME, ITEM_TYPE_ROBOT  # type: ignore
except ImportError:  # pragma: no cover - dipende dall'ambiente
    ITEM_TYPE_ROBOT, ITEM_TYPE_FRAME, ITEM_TYPE_CAMERA = 2, 3, 19


def connect(rdk: Any = None) -> Any:
    """`Robolink` già aperto o uno nuovo. `pip install robodk`, con RoboDK avviato."""
    if rdk is not None:
        return rdk
    try:
        robolink = importlib.import_module("robodk.robolink")
    except ImportError as exc:  # pragma: no cover - dipende dall'ambiente
        raise RuntimeError("manca il pacchetto robodk: pip install robodk (e RoboDK avviato)") from exc
    return robolink.Robolink()


def mat_to_np(pose: Any) -> np.ndarray:
    """Mat di RoboDK (mm) -> numpy 4x4 (m)."""
    T = np.array([[float(pose[i, j]) for j in range(4)] for i in range(4)])
    T[:3, 3] /= 1000.0
    return T


def np_to_mat(T: np.ndarray) -> Any:
    robomath = importlib.import_module("robodk.robomath")
    M = np.array(T, dtype=float)
    M[:3, 3] *= 1000.0
    return robomath.Mat(M.tolist())


def find_robot(rdk: Any, name: str | None = None) -> Any:
    robot =rdk.Item(name or "", ITEM_TYPE_ROBOT)
    if not robot.Valid():
        raise RuntimeError(f"robot '{name or '(il primo)'}' non trovato nella stazione RoboDK")
    return robot


def flange_in_base(robot: Any) -> np.ndarray:
    """T_B_F (m): flangia nel base del robot, dalla cinematica diretta senza tool."""
    return mat_to_np(robot.SolveFK(robot.Joints()))


class RoboDKFlange:
    """Posa della flangia da RoboDK, in sola lettura, come `ControllerTcp`."""

    def __init__(self, robot_name: str | None = None, rdk: Any = None):
        self.rdk = connect(rdk)
        self.robot = find_robot(self.rdk, robot_name)
        self.last_error: str | None = None

    def __call__(self) -> TcpReading | None:
        try:
            pose = T_to_pose(flange_in_base(self.robot))
            moving = bool(self.robot.Busy())
        except Exception as exc:  # noqa: BLE001 - RoboDK chiuso o stazione cambiata
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        self.last_error = None
        return TcpReading(pose=tuple(pose), t=time.time(), moving=moving)


def item_position_base(rdk: Any, robot: Any, item_name: str) -> np.ndarray:
    """Origine di un oggetto della stazione nel base del robot (m). Attenzione: l'origine dipende dal CAD (spesso è il
    centro della base dell'oggetto, non la superficie in cima che vede la camera)."""
    item = rdk.Item(item_name)
    if not item.Valid():
        raise RuntimeError(f"oggetto '{item_name}' non trovato nella stazione RoboDK")
    T = np.linalg.inv(mat_to_np(robot.PoseAbs())) @ mat_to_np(item.PoseAbs())
    return T[:3, 3]


def grey32_to_depth_mm(raw: np.ndarray, far_mm: float, intrinsics: Intrinsics, invert: bool = False,
                       is_ray: bool = False) -> np.ndarray:
    """Profondità RoboDK a 32 bit (h, w) -> Z in mm (uint16, 0 = nessuna misura), come la RealSense."""
    raw = np.asarray(raw, dtype=np.float64)
    valid = raw > 0
    frac = raw / U32_MAX
    d = (1.0 - frac if invert else frac) * far_mm
    if is_ray:  # distanza lungo il raggio -> Z del piano dell'immagine
        h, w = raw.shape
        vs, us = np.mgrid[0:h, 0:w]
        d = d / np.sqrt(1.0 + ((us - intrinsics.cx) / intrinsics.fx) ** 2 + ((vs - intrinsics.cy) / intrinsics.fy) ** 2)
    d[~valid] = 0.0
    return np.clip(np.rint(d), 0, 65535).astype(np.uint16)


def intrinsics_from_fov(width: int, height: int, vfov_deg: float) -> Intrinsics:
    f = (height / 2.0) / math.tan(math.radians(vfov_deg) / 2.0)  # pixel quadrati
    return Intrinsics(fx=f, fy=f, cx=width / 2.0, cy=height / 2.0, width=width, height=height)


class RoboDKCameraReader:
    """D435 simulata in RoboDK, montata sulla flangia del robot. Stessa interfaccia di `RealSenseReader`."""

    mode = "robodk"

    def __init__(self, source_name: str = "arm", robot_name: str | None = None,
                 mount: Sequence[float] = (0.0, -0.07, 0.03, 0.0, 0.0, 0.0),
                 width: int = 640, height: int = 480, vfov_deg: float = D435_COLOR_VFOV_DEG,
                 far_mm: float = 2000.0, depth_invert: bool = False, depth_is_ray: bool = False,
                 fps: float = 5.0, rdk: Any = None):
        """`mount`: camera nella flangia `[x, y, z, rx, ry, rz]` (m, rad) = la T_F_C vera della simulazione."""
        self.source_name = source_name
        self.rdk = connect(rdk)
        self.robot = find_robot(self.rdk, robot_name)
        self.T_F_C = pose_to_T(mount)
        self.width, self.height, self.fps = int(width), int(height), float(fps)
        self.far_mm, self.depth_invert, self.depth_is_ray = float(far_mm), depth_invert, depth_is_ray
        self.intrinsics = intrinsics_from_fov(self.width, self.height, vfov_deg)
        self.device_name = "RoboDK D435 simulata"

        params = f"FOV={vfov_deg} FAR_LENGTH={far_mm} SIZE={self.width}x{self.height} WINDOWFIXED"
        self.frame = self._item("MOTIQ camera sim", ITEM_TYPE_FRAME) or self.rdk.AddFrame("MOTIQ camera sim")
        self.cam_color = self._camera("MOTIQ D435 colore", params)
        self.cam_depth = self._camera("MOTIQ D435 profondita", params + " DEPTH")

        self._lock = threading.Lock()
        self._color = make_disconnected_frame(self.width, self.height, source_name, "RoboDK: in attesa")
        self._depth: np.ndarray | None = None
        self._frame_id, self._ts, self._connected = 0, time.time(), False
        self._running, self._thread = False, None
        self.last_error: str | None = None

    def _item(self, name: str, item_type: int) -> Any:
        it = self.rdk.Item(name, item_type)
        return it if it.Valid() and it.Name() == name else None

    def _camera(self, name: str, params: str) -> Any:
        """Camera già nella stazione (da un avvio precedente) riconfigurata, oppure nuova."""
        old = self._item(name, ITEM_TYPE_CAMERA)
        cam = self.rdk.Cam2D_Add(self.frame, params, old) if old is not None else self.rdk.Cam2D_Add(self.frame, params)
        cam.setName(name)
        return cam

    def camera_in_base(self) -> np.ndarray:
        """T_B_C vera di adesso (m): flangia · montaggio."""
        return flange_in_base(self.robot) @ self.T_F_C

    def _grab(self) -> tuple[np.ndarray, np.ndarray] | None:
        T_B_C = self.camera_in_base()
        self.frame.setPoseAbs(np_to_mat(mat_to_np(self.robot.PoseAbs()) @ T_B_C))  # camera dove è la flangia ora
        png = self.rdk.Cam2D_Snapshot("", self.cam_color)
        if not isinstance(png, bytes) or not png:
            return None
        color = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        # Profondità come byte via socket (RoboDK >= 5.4.3): stesso formato del file .grey32, cioè uint32 big-endian
        # [w, h, pixel...] con le righe dal basso. Il file non si usa: RoboDK lo tiene aperto e la lettura fallisce.
        buf = self.rdk.Cam2D_Snapshot("", self.cam_depth, "DEPTH")
        if not isinstance(buf, bytes) or len(buf) < 8:
            return None
        g = np.frombuffer(buf, dtype=">u4")
        w, h = int(g[0]), int(g[1])
        raw = np.flipud(g[2:2 + w * h].reshape(h, w))
        depth = grey32_to_depth_mm(raw, self.far_mm, self.intrinsics, self.depth_invert, self.depth_is_ray)
        if color is None or color.shape[:2] != depth.shape:
            return None
        return color, depth

    def _read_into_latest(self) -> bool:
        ts = time.time()
        try:
            got = self._grab()
        except Exception as exc:  # noqa: BLE001 - RoboDK chiuso: frame di riserva, si riprova
            self.last_error, got = f"{type(exc).__name__}: {exc}", None
        if got is None:
            self._connected = False
            return False
        with self._lock:
            self._color, self._depth = got
            self._frame_id += 1
            self._ts, self._connected = ts, True
        return True

    def read_once(self) -> tuple[bool, np.ndarray, int, float]:
        if not self._running:
            self._read_into_latest()
        return self.get_latest()

    def get_latest(self) -> tuple[bool, np.ndarray, int, float]:
        with self._lock:
            return self._connected, self._color.copy(), self._frame_id, self._ts

    def get_depth(self) -> np.ndarray | None:
        with self._lock:
            return None if self._depth is None else self._depth.copy()

    def get_frames(self) -> tuple[bool, np.ndarray, np.ndarray | None, int, float]:
        with self._lock:
            depth = None if self._depth is None else self._depth.copy()
            return self._connected, self._color.copy(), depth, self._frame_id, self._ts

    @property
    def is_connected(self) -> bool:
        return self._connected

    def start_background(self) -> None:
        if self._running:
            return
        self._read_into_latest()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name=f"RoboDK-{self.source_name}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            t0 = time.perf_counter()
            self._read_into_latest()
            time.sleep(max(0.0, 1.0 / self.fps - (time.perf_counter() - t0)))

    def stop(self) -> None:
        self._running = False
        if self._thread is not None and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None
