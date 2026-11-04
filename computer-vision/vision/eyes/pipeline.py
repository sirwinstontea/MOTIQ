"""Pipeline di percezione locale in tempo reale per Visione A ("Occhi").

Unisce:
- `VideoReader` (acquisizione webcam / stream / replay / synthetic)
- `ObjectTracker` (YOLO + ByteTrack / Mock fallback)
- `ArmCalibrator` (omografia ArUco pixel -> mm + angolo di presa per `arm`)
- `DogNavigator` (errore orizzontale, altezza bbox e safety stop persone per `dog`)
- `attach_masks` (sagoma dell'oggetto + distanza/dimensioni dalla maschera, solo per `arm`)
- `draw_marks` (Set-of-Mark con badge numerici)
- `LiveVisionState` (stato condiviso thread-safe per Visione B, App e Robotica)
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

import numpy as np

from .calibration.aruco_calib import ArmCalibrator
from .capture.reader import SourceMode, VideoReader
from .capture.realsense import RealSenseReader
from .dog.telemetry import DogNavigator, check_person_safety_stop
from .marks.som import draw_marks
from .schemas import EyeObject, FramePayload
from .segmentation import MaskCache, TableBackground, attach_masks
from .state.vision_state import LiveVisionState
from .tracking.tracker import ObjectTracker
from .zoom.manager import DigitalZoomManager

# `realsense`: colore + profondità allineati (D415/D435); `target` è il seriale, oppure 0/None = la prima
PipelineMode = SourceMode | Literal["realsense"]


# Frame di tavolo vuoto per la mediana (il piano ne prevedeva 15: 10 bastano e la registrazione dura meno)
BG_FRAMES = 10


@dataclass
class SourceNode:
    source: str
    reader: VideoReader | RealSenseReader
    tracker: ObjectTracker
    calibrator: ArmCalibrator | None = None
    dog_navigator: DogNavigator | None = None
    zoom: DigitalZoomManager | None = None
    # Maschere di segmentazione (vedi vision/eyes/segmentation/): solo la camera del braccio, che è fissa sul tavolo
    masks: bool = False
    bg: TableBackground = field(default_factory=lambda: TableBackground(n_frames=BG_FRAMES))
    mask_cache: MaskCache = field(default_factory=MaskCache)     # oggetti del tracker (step)
    extra_cache: MaskCache = field(default_factory=MaskCache)    # oggetti aggiunti fuori (YOLO-World, Grok)
    recording_bg: bool = False
    last_depth: np.ndarray | None = None  # profondità (mm) dell'ultimo frame di `step`, None senza RealSense
    _bg_frame_id: int = -1

    def record_empty_table(self) -> None:
        """Comincia a registrare il tavolo vuoto: i prossimi `BG_FRAMES` frame di `step`, senza oggetti sul tavolo."""
        self.bg.reset()
        self.mask_cache.clear()
        self.extra_cache.clear()
        self._bg_frame_id = -1
        self.recording_bg = True

    def attach(self, objects: Sequence[EyeObject | dict], frame: np.ndarray,
               cache: MaskCache | None = None) -> list[EyeObject | dict]:
        """Maschera e misure per `objects` con il tavolo vuoto, la profondità e gli intrinseci di questa sorgente.

        `frame` deve essere quello dell'ultimo `step` (la profondità è la sua). Con `masks` spento restituisce gli
        oggetti così come sono."""
        if not self.masks:
            return list(objects)
        return attach_masks(objects, frame, depth=self.last_depth, bg=self.bg if self.bg.ready else None,
                            intrinsics=getattr(self.reader, "intrinsics", None),
                            cache=self.mask_cache if cache is None else cache)

    def _feed_background(self, frame: np.ndarray, depth: np.ndarray | None, frame_id: int) -> None:
        if frame_id == self._bg_frame_id:  # lo stesso frame letto due volte non conta
            return
        self._bg_frame_id = frame_id
        if self.bg.add_frame(frame, depth):
            self.recording_bg = False


class EyePipeline:
    """Motore principale di Visione A ("Occhi")."""

    def __init__(
        self,
        state: LiveVisionState | None = None,
        fps: float = 25.0,
    ):
        self.state = state or LiveVisionState()
        self.fps = max(1.0, float(fps))
        self.nodes: dict[str, SourceNode] = {}
        self._running = False
        self._thread: threading.Thread | None = None

    def add_source(
        self,
        source: str = "arm",
        mode: PipelineMode = "synthetic",
        target: int | str | Path = 0,
        use_mock_tracker: bool | None = None,
        calibration_file: str | Path | None = None,
        calibrator: ArmCalibrator | None = None,
        model_name: str | None = None,
        conf_threshold: float = 0.40,
        ema_alpha: float = 0.70,
        min_hits: int = 1,
        masks: bool | None = None,
    ) -> SourceNode:
        """Configura e registra una sorgente (`arm` o `dog`). `masks`: sagome e misure; di default solo per `arm`."""
        if calibrator is None and calibration_file is not None and Path(calibration_file).exists():
            calibrator = ArmCalibrator.load(calibration_file)
        elif calibrator is None and source == "arm":
            calibrator = ArmCalibrator()

        calib_fn = calibrator.compute_object_coords if (calibrator and source == "arm") else None
        tracker = ObjectTracker(
            model_name=model_name,
            conf_threshold=conf_threshold,
            use_mock=use_mock_tracker,
            calibrator_fn=calib_fn,
            ema_alpha=ema_alpha,
            min_hits=min_hits,
        )
        if mode == "realsense":
            serial = str(target) if target not in (None, "", 0, "0") else None
            reader = RealSenseReader(source_name=source, serial=serial)
        else:
            reader = VideoReader(source_name=source, mode=mode, target=target, fps=self.fps)
        dog_nav = DogNavigator() if source == "dog" else None
        zoom_mgr = DigitalZoomManager()

        node = SourceNode(
            source=source,
            reader=reader,
            tracker=tracker,
            calibrator=calibrator,
            dog_navigator=dog_nav,
            zoom=zoom_mgr,
            masks=(source == "arm") if masks is None else bool(masks),
        )
        self.nodes[source] = node
        return node

    def step(
        self,
        source: str,
        min_display_conf: float = 0.0,
        show_conf: bool = False,
        apply_zoom: bool = True,
        show_masks: bool = True,
    ) -> FramePayload:
        """Esegue un singolo ciclo completo di percezione per la sorgente indicata."""
        if source not in self.nodes:
            raise KeyError(f"Sorgente '{source}' non registrata nella pipeline.")
        node = self.nodes[source]

        depth = None
        if isinstance(node.reader, RealSenseReader):  # colore e profondità dello stesso frame
            if node.reader._running:
                _ok, frame, depth, frame_id, ts = node.reader.get_frames()
            else:
                _ok, frame, frame_id, ts = node.reader.read_once()
                depth = node.reader.get_depth()
        elif node.reader._running:
            _ok, frame, frame_id, ts = node.reader.get_latest()
        else:
            _ok, frame, frame_id, ts = node.reader.read_once()
        node.last_depth = depth

        apply_calib = bool(node.calibrator and node.calibrator.is_calibrated and source == "arm")
        objects = node.tracker.process_frame(frame, apply_calibration=apply_calib)
        if node.recording_bg:
            node._feed_background(frame, depth, frame_id)
        objects = node.attach(objects, frame)

        dog_telemetry = None
        if node.dog_navigator is not None:
            dog_telemetry, safety_stop, safety_reason = node.dog_navigator.evaluate(objects)
        else:
            safety_stop, safety_reason = check_person_safety_stop(objects)

        marked = draw_marks(
            frame,
            objects,
            show_grasp=apply_calib,
            safety_stop=safety_stop,
            min_conf=min_display_conf,
            show_conf=show_conf,
            show_masks=show_masks,
        )

        if apply_zoom and node.zoom is not None and node.zoom.is_active():
            node.zoom.update_with_tracked_objects(objects)
            marked = node.zoom.apply(marked)

        return self.state.update(
            source=source,
            raw_frame=frame,
            objects=objects,
            frame_id=frame_id,
            ts=ts,
            marked_frame=marked,
            dog_telemetry=dog_telemetry,
            safety_stop=safety_stop,
            safety_reason=safety_reason,
        )

    def start(self) -> None:
        """Avvia i reader e il ciclo continuo di percezione in background."""
        if self._running:
            return
        for node in self.nodes.values():
            node.reader.start_background()
            self.step(node.source)

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="EyePipelineLoop", daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        interval = 1.0 / self.fps
        while self._running:
            t0 = time.perf_counter()
            for source in list(self.nodes.keys()):
                try:
                    self.step(source)
                except Exception:
                    pass
            elapsed = time.perf_counter() - t0
            if elapsed < interval:
                time.sleep(interval - elapsed)

    def stop(self) -> None:
        """Ferma il loop e tutti i reader video."""
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        for node in self.nodes.values():
            node.reader.stop()
