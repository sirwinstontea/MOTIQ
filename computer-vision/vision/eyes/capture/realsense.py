"""Lettore Intel RealSense (D415/D435): colore e profondità allineati, stessa interfaccia di `VideoReader`.

    reader = RealSenseReader("arm")
    reader.start_background()
    ok, color, depth_mm, frame_id, ts = reader.get_frames()   # depth_mm: uint16, 0 = nessuna misura
    fx, fy, cx, cy = reader.intrinsics.as_tuple()

- `pyrealsense2` si importa solo all'apertura: senza il pacchetto il resto di Visione A funziona lo stesso.
- Il formato si sceglie da solo: 848×480@30 in USB 3, 640×480@15 in USB 2, dove 30 fps colore+profondità non
  passano per la banda. Ogni formato si prova davvero (start e due frame), se non arrivano frame si passa al successivo.
- Profondità: preset High Accuracy, laser al massimo, soglia a `max_depth_m`, filtri spatial e temporal sulla
  disparità, **niente hole filling**: un buco inventato darebbe una Z falsa alla maschera.
- La profondità viene allineata al colore, quindi `intrinsics` sono quelli della camera colore e un pixel (u, v)
  del frame colore ha la sua profondità in `depth_mm[v, u]`.
- Su macOS serve `sudo` (senza, la camera si vede ma non si apre: "failed to set power state") e pyrealsense può
  andare in segmentation fault all'uscita del processo. Per non lasciare la camera bloccata la pipeline si ferma
  sempre (`stop()`, anche da `atexit`) e, se all'avvio la camera non risponde, si prova un `hardware_reset`.
"""
from __future__ import annotations

import atexit
import importlib
import logging
import threading
import time
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import numpy as np

from .reader import make_disconnected_frame

log = logging.getLogger(__name__)

# (larghezza, altezza, fps) in ordine di preferenza
FORMATS_USB3: tuple[tuple[int, int, int], ...] = ((848, 480, 30), (640, 480, 30), (848, 480, 15), (640, 480, 15))
FORMATS_USB2: tuple[tuple[int, int, int], ...] = ((640, 480, 15), (424, 240, 15), (640, 480, 6))

MIN_DEPTH_M = 0.15
MAX_DEPTH_M = 1.5           # oltre il tavolo non serve, e la soglia toglie molto rumore
FIRST_FRAME_TIMEOUT_MS = 5000
FRAME_TIMEOUT_MS = 1000
FAILURES_BEFORE_REOPEN = 3  # frame mancati di fila prima di riaprire la camera
REOPEN_DELAY_S = 2.0
RESET_WAIT_S = 10.0         # attesa massima perché la camera ricompaia dopo hardware_reset


class RealSenseError(RuntimeError):
    """La camera non si apre (assente, occupata, senza permessi) o il pacchetto manca."""


@dataclass(frozen=True)
class Intrinsics:
    """Intrinseci della camera colore (la profondità è allineata a lei), in pixel.

    `model` / `coeffs`: distortion as the device reports it (librealsense order k1, k2, p1, p2, k3). "none" with zero
    coefficients = ideal pinhole (the usual D435 colour stream). See `opencv_distortion`.
    """
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int
    model: str = "none"
    coeffs: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.fx, self.fy, self.cx, self.cy

    def to_dict(self) -> dict:
        return {"fx": self.fx, "fy": self.fy, "cx": self.cx, "cy": self.cy, "width": self.width,
                "height": self.height, "distortion_model": self.model, "coeffs": [float(c) for c in self.coeffs]}


class UnsupportedDistortion(ValueError):
    """The camera reports a distortion model we cannot reproduce exactly with OpenCV."""


# librealsense model names that are the OpenCV radial-tangential (Brown-Conrady) model, same coefficient order
_OPENCV_COMPATIBLE = {"brown_conrady", "distortion.brown_conrady", "rs2_distortion_brown_conrady"}


def opencv_distortion(intrinsics: Any) -> np.ndarray:
    """Distortion coefficients for `cv2.solvePnP` / `cv2.undistortPoints` (5 values).

    Zero coefficients (any model) -> zeros. Non-zero Brown-Conrady -> the coefficients. Any other model with non-zero
    coefficients (e.g. librealsense *inverse* Brown-Conrady, whose coefficients map the other way) raises
    `UnsupportedDistortion`: calibration and localization fail closed instead of silently using the wrong model.
    Objects without `coeffs` (legacy intrinsics, PyBullet) are treated as ideal pinhole.
    """
    if isinstance(intrinsics, dict):
        coeffs = [float(c) for c in intrinsics.get("coeffs") or []]
        model = str(intrinsics.get("distortion_model") or "")
    else:
        coeffs = [float(c) for c in (getattr(intrinsics, "coeffs", None) or [])]
        model = str(getattr(intrinsics, "model", "") or "")
    if not any(abs(c) > 1e-9 for c in coeffs):
        return np.zeros(5)
    if model.lower() not in _OPENCV_COMPATIBLE:
        raise UnsupportedDistortion(f"distortion model '{model}' with non-zero coefficients {coeffs} is not supported")
    return np.array((coeffs + [0.0] * 5)[:5], dtype=np.float64)


def load_pyrealsense() -> ModuleType:
    try:
        return importlib.import_module("pyrealsense2")
    except ImportError as exc:  # pragma: no cover - dipende dall'ambiente
        raise RealSenseError(
            "pyrealsense2 non è installato: `pip install pyrealsense2` (Linux/Windows) "
            "o `pip install pyrealsense2-macosx` (Mac con Apple Silicon)") from exc


class RealSenseReader:
    """Lettore sincrono e asincrono (thread-safe) per una RealSense, con la stessa interfaccia di `VideoReader`.

    `read_once`, `get_latest`, `start_background` e `stop` si comportano come in `VideoReader` e restituiscono solo
    il colore; `get_depth` e `get_frames` danno anche la profondità in mm dello stesso frame.
    """

    mode = "realsense"

    def __init__(
        self,
        source_name: str = "arm",
        serial: str | None = None,
        fps: float | None = None,
        width: int | None = None,
        height: int | None = None,
        max_depth_m: float = MAX_DEPTH_M,
        filters: bool = True,
        reset_on_failure: bool = True,
        tune_depth: bool = True,
        rs_module: ModuleType | None = None,
    ):
        self.source_name = source_name
        self.serial = serial
        self.max_depth_m = float(max_depth_m)
        self.use_filters = filters
        self.reset_on_failure = reset_on_failure
        self.tune_depth = tune_depth  # False: la camera come esce dalla fabbrica (per capire se le opzioni danno noia)
        # Formato chiesto da fuori: si prova per primo, poi quelli automatici
        self._requested = (int(width), int(height), int(fps)) if width and height and fps else None
        self._rs = rs_module

        self.width, self.height, self.fps = width or 640, height or 480, float(fps or 30)
        self.intrinsics: Intrinsics | None = None
        self.depth_scale_mm: float = 1.0   # mm per unità grezza del sensore
        self.device_name: str = ""
        self.device_serial: str = ""       # serial actually opened (not just the requested one)
        self.firmware: str = ""
        self.usb_type: str = ""

        self._pipe: Any = None
        self._align: Any = None
        self._blocks: list[Any] = []
        self._filter_frameset = True       # i filtri vanno sul frameset prima di allineare (consigliato da Intel)
        self._frame_id = 0
        self._connected = False
        self._failures = 0
        self._reset_allowed = True         # un solo hardware_reset finché la camera non torna a funzionare
        self.last_error: str | None = None

        self._lock = threading.Lock()  # protegge gli ultimi frame
        self._io_lock = threading.Lock()  # una sola chiamata alla pipeline alla volta
        self._latest_color: np.ndarray = make_disconnected_frame(self.width, self.height, source_name,
                                                                 "RealSense non collegata")
        self._latest_depth: np.ndarray | None = None
        self._latest_ts = time.time()
        self._running = False
        self._thread: threading.Thread | None = None
        self._closed = False

        atexit.register(self.stop)
        self._open()

    # --- apertura -----------------------------------------------------------------------------------------

    def _open(self) -> bool:
        """Apre la camera. In caso di errore resta scollegata (frame di riserva) e ci si riprova dal thread."""
        if self._rs is None:
            self._rs = load_pyrealsense()  # questo sì solleva: senza pacchetto non c'è niente da riprovare
        with self._io_lock:
            self._close_pipe()
            try:
                self._start_pipe()
            except Exception as exc:  # noqa: BLE001 - qualsiasi errore della camera vale "scollegata"
                first_error = exc
                can_reset = self.reset_on_failure and self._reset_allowed
                self._reset_allowed = False
                if not (can_reset and self._hardware_reset()):
                    return self._fail(first_error)
                try:
                    self._start_pipe()
                except Exception as exc2:  # noqa: BLE001
                    return self._fail(exc2)
        self._connected = True
        self._failures = 0
        self._reset_allowed = True
        self.last_error = None
        log.info("RealSense %s (USB %s) aperta a %dx%d@%d", self.device_name, self.usb_type,
                 self.width, self.height, int(self.fps))
        return True

    def _fail(self, exc: Exception) -> bool:
        self._connected = False
        self.last_error = _explain(exc)
        log.warning("RealSense non disponibile: %s", self.last_error)
        with self._lock:
            self._latest_color = make_disconnected_frame(self.width, self.height, self.source_name,
                                                         "RealSense non disponibile - riprovo...")
            self._latest_depth = None
        return False

    def _find_device(self) -> Any:
        rs = self._rs
        devices = list(rs.context().query_devices())
        if self.serial:
            devices = [d for d in devices if d.get_info(rs.camera_info.serial_number) == str(self.serial)]
        if not devices:
            raise RealSenseError(f"nessuna RealSense trovata{f' con seriale {self.serial}' if self.serial else ''}")
        return devices[0]

    def _start_pipe(self) -> None:
        rs = self._rs
        dev = self._find_device()
        serial = dev.get_info(rs.camera_info.serial_number)
        self.device_serial = str(serial)
        self.firmware = _info(rs, dev, "firmware_version")
        self.device_name = _info(rs, dev, "name")
        self.usb_type = _info(rs, dev, "usb_type_descriptor")

        self._configure_depth(dev)  # prima di start: su macOS cambiare opzioni a stream avviato può fallire

        formats = list(FORMATS_USB3 if self.usb_type.startswith("3") else FORMATS_USB2)
        if self._requested:
            formats = [self._requested] + [f for f in formats if f != self._requested]

        errors = []
        for w, h, fps in formats:
            pipe = rs.pipeline()
            cfg = rs.config()
            cfg.enable_device(serial)
            cfg.enable_stream(rs.stream.color, w, h, rs.format.bgr8, fps)
            cfg.enable_stream(rs.stream.depth, w, h, rs.format.z16, fps)
            try:
                profile = pipe.start(cfg)
            except Exception as exc:  # noqa: BLE001 - formato non supportato: si prova il prossimo
                errors.append(f"{w}x{h}@{fps}: {exc}")
                continue
            try:
                self.depth_scale_mm = _depth_scale_mm(profile)
                for timeout in (FIRST_FRAME_TIMEOUT_MS, FRAME_TIMEOUT_MS):
                    pipe.wait_for_frames(timeout)
            except Exception as exc:  # noqa: BLE001 - partita ma senza frame (banda USB): prossimo formato
                errors.append(f"{w}x{h}@{fps}: {exc}")
                _safe_stop(pipe)
                continue
            self._pipe = pipe
            self.width, self.height, self.fps = w, h, float(fps)
            self.intrinsics = _color_intrinsics(rs, profile)
            self._align = rs.align(rs.stream.color)
            self._blocks = self._make_filters() if self.use_filters else []
            self._filter_frameset = True
            return
        raise RealSenseError("nessun formato colore+profondità funziona: " + "; ".join(errors))

    def _configure_depth(self, dev: Any) -> None:
        if not self.tune_depth:
            return
        rs = self._rs
        sensor = dev.first_depth_sensor()
        for option, value in (
            (rs.option.visual_preset, lambda: int(rs.rs400_visual_preset.high_accuracy)),
            (rs.option.emitter_enabled, lambda: 1),
            (rs.option.laser_power, lambda: sensor.get_option_range(rs.option.laser_power).max),
        ):
            try:
                if sensor.supports(option):
                    sensor.set_option(option, value())
            except Exception as exc:  # noqa: BLE001 - un'opzione in meno non deve impedire di aprire la camera
                log.warning("RealSense: opzione %s non impostata: %s", option, exc)
                if "power state" in str(exc).lower():
                    return  # la camera non risponde: inutile provare le altre

    def _make_filters(self) -> list[Any]:
        rs = self._rs
        thr = rs.threshold_filter(MIN_DEPTH_M, self.max_depth_m)
        return [thr, rs.disparity_transform(True), rs.spatial_filter(), rs.temporal_filter(),
                rs.disparity_transform(False)]

    def _hardware_reset(self) -> bool:
        """Riavvia la camera (es. lasciata bloccata da un processo chiuso male) e aspetta che ricompaia."""
        try:
            self._find_device().hardware_reset()
        except Exception as exc:  # noqa: BLE001
            log.info("RealSense: hardware_reset non riuscito: %s", exc)
            return False
        log.warning("RealSense non risponde: hardware_reset, attendo che ricompaia...")
        time.sleep(2.0)  # la camera sparisce dal bus per un attimo
        deadline = time.monotonic() + RESET_WAIT_S
        while time.monotonic() < deadline:
            try:
                self._find_device()
                return True
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        return False

    def _close_pipe(self) -> None:
        pipe, self._pipe = self._pipe, None
        if pipe is not None:
            _safe_stop(pipe)

    # --- lettura ------------------------------------------------------------------------------------------

    def _grab(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Un frame colore + profondità (mm) allineati, o `None` se non è arrivato."""
        with self._io_lock:
            if self._pipe is None:
                return None
            try:
                frames = self._pipe.wait_for_frames(FRAME_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - timeout: lo conta chi chiama
                return None
            aligned = self._align_and_filter(frames)
            if aligned is None:
                return None
            color_f, depth_f = aligned.get_color_frame(), aligned.get_depth_frame()
            if not color_f or not depth_f:
                return None
            color = np.asanyarray(color_f.get_data()).copy()
            raw = np.asanyarray(depth_f.get_data())
        return color, self._to_mm(raw)

    def _align_and_filter(self, frames: Any) -> Any:
        if self._blocks and self._filter_frameset:
            try:
                f = frames
                for blk in self._blocks:
                    f = blk.process(f)
                return self._align.process(f.as_frameset())
            except Exception as exc:  # noqa: BLE001 - versione che non filtra i frameset: si filtra dopo
                log.info("RealSense: filtri sul frameset non supportati (%s), filtro la profondità allineata", exc)
                self._filter_frameset = False
        aligned = self._align.process(frames)
        if self._blocks:
            # Dopo l'allineamento la disparità non ha senso: solo soglia, spatial e temporal
            depth = aligned.get_depth_frame()
            for blk in (self._blocks[0], self._blocks[2], self._blocks[3]):
                depth = blk.process(depth)
            return _AlignedPair(aligned.get_color_frame(), depth)
        return aligned

    def _to_mm(self, raw: np.ndarray) -> np.ndarray:
        if abs(self.depth_scale_mm - 1.0) < 1e-6:
            return raw.astype(np.uint16, copy=True)
        return np.clip(np.rint(raw.astype(np.float32) * self.depth_scale_mm), 0, 65535).astype(np.uint16)

    def read_once(self) -> tuple[bool, np.ndarray, int, float]:
        """Legge un frame (sincrono): `(ok, colore_bgr, frame_id, timestamp)`, come `VideoReader.read_once`.

        Con il thread di acquisizione attivo restituisce l'ultimo frame, per non leggere la pipeline da due thread.
        """
        if self._running:
            return self.get_latest()
        ok = self._read_into_latest()
        _connected, color, frame_id, ts = self.get_latest()
        return ok, color, frame_id, ts

    def _read_into_latest(self) -> bool:
        ts = time.time()
        got = self._grab()
        if got is None:
            self._failures += 1
            if self._failures >= FAILURES_BEFORE_REOPEN:
                self._connected = False
            return False
        color, depth = got
        self._failures = 0
        self._connected = True
        self._frame_id += 1
        with self._lock:
            self._latest_color, self._latest_depth, self._latest_ts = color, depth, ts
        return True

    def get_latest(self) -> tuple[bool, np.ndarray, int, float]:
        """Ultimo frame colore senza bloccare: `(connesso, colore_bgr, frame_id, timestamp)`."""
        with self._lock:
            return self._connected, self._latest_color.copy(), self._frame_id, self._latest_ts

    def get_depth(self) -> np.ndarray | None:
        """Profondità (mm, uint16, 0 = nessuna misura) dell'ultimo frame, allineata al colore."""
        with self._lock:
            return None if self._latest_depth is None else self._latest_depth.copy()

    def get_frames(self) -> tuple[bool, np.ndarray, np.ndarray | None, int, float]:
        """Colore e profondità dello stesso frame: `(connesso, colore, depth_mm, frame_id, ts)`."""
        with self._lock:
            depth = None if self._latest_depth is None else self._latest_depth.copy()
            return self._connected, self._latest_color.copy(), depth, self._frame_id, self._latest_ts

    @property
    def is_connected(self) -> bool:
        return self._connected

    # --- thread -------------------------------------------------------------------------------------------

    def start_background(self) -> None:
        """Avvia il thread di acquisizione continua (la pipeline dà già il ritmo: niente sleep)."""
        if self._running:
            return
        self._read_into_latest()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name=f"RealSense-{self.source_name}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            if self._pipe is None or not self._connected:
                time.sleep(REOPEN_DELAY_S)
                if self._running:
                    self._open()
                continue
            self._read_into_latest()

    def stop(self) -> None:
        """Ferma il thread e la pipeline. Va chiamato sempre: una pipeline lasciata aperta blocca la camera."""
        self._running = False
        if self._thread is not None and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=(FRAME_TIMEOUT_MS / 1000.0) * 2 + 0.5)
        self._thread = None
        if not self._closed:
            with self._io_lock:
                self._close_pipe()
            self._connected = False
        self._closed = True
        try:
            atexit.unregister(self.stop)
        except Exception:  # noqa: BLE001
            pass


@dataclass
class _AlignedPair:
    """Colore e profondità filtrata dopo l'allineamento, con la stessa interfaccia di un frameset."""
    color: Any
    depth: Any

    def get_color_frame(self) -> Any:
        return self.color

    def get_depth_frame(self) -> Any:
        return self.depth


def list_devices(rs_module: ModuleType | None = None) -> list[dict[str, str]]:
    """RealSense collegate: nome, seriale, USB. Lista vuota se non ce ne sono o se la camera non si apre."""
    rs = rs_module or load_pyrealsense()
    try:
        devices = list(rs.context().query_devices())
    except Exception:  # noqa: BLE001
        return []
    return [{"name": _info(rs, d, "name"), "serial": _info(rs, d, "serial_number"),
             "usb": _info(rs, d, "usb_type_descriptor")} for d in devices]


def default_camera(cam: int = 0) -> tuple[str, int | None]:
    """(mode, target) della camera da aprire: la RealSense collegata via USB-C (colore + profondità) se c'è, se no
    la webcam del PC (indice `cam`). La RealSense si cerca con pyrealsense2 e non per indice OpenCV: su Windows
    l'ordine degli indici cambia e la D435 ne occupa più di uno (RGB, profondità)."""
    try:
        devices = list_devices()
    except RealSenseError as exc:  # pyrealsense2 non installato
        print(f"[Cam] {exc}")
        devices = []
    if devices:
        print(f"[Cam] Trovata {devices[0]['name']} (USB {devices[0]['usb']})")
        return "realsense", None
    print(f"[Cam] Nessuna RealSense collegata: uso la webcam del PC (Dev {cam})")
    return "webcam", cam


def _info(rs: ModuleType, dev: Any, field: str) -> str:
    try:
        return str(dev.get_info(getattr(rs.camera_info, field)))
    except Exception:  # noqa: BLE001 - non tutte le camere hanno tutti i campi
        return ""


def _color_intrinsics(rs: ModuleType, profile: Any) -> Intrinsics:
    i = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
    coeffs = tuple(float(c) for c in (getattr(i, "coeffs", []) or []))
    model = str(getattr(i, "model", "none")).split(".")[-1].lower()
    intr = Intrinsics(float(i.fx), float(i.fy), float(i.ppx), float(i.ppy), int(i.width), int(i.height),
                      model=model, coeffs=coeffs or (0.0,) * 5)
    try:
        opencv_distortion(intr)
    except UnsupportedDistortion as exc:
        # the calibration wizard and /localization/snapshot refuse to run with this; legacy paths stay pinhole
        log.warning("RealSense: %s - calibration/localization will fail closed", exc)
    return intr


def _depth_scale_mm(profile: Any) -> float:
    return float(profile.get_device().first_depth_sensor().get_depth_scale()) * 1000.0


def _safe_stop(pipe: Any) -> None:
    try:
        pipe.stop()
    except Exception:  # noqa: BLE001 - già ferma
        pass


def _explain(exc: Exception) -> str:
    msg = str(exc) or type(exc).__name__
    low = msg.lower()
    if "power state" in low or "permission" in low or "access" in low:
        msg += " (su macOS serve sudo; se persiste stacca e riattacca la camera)"
    elif "no device" in low or "nessuna realsense" in low:
        msg += " (camera collegata? su macOS serve sudo)"
    return msg
