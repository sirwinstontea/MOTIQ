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
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Sequence

import numpy as np

from .calibration.aruco_calib import ArmCalibrator
from .calibration.hand_eye import FIXED_POSE, ControllerTcp, FixedTcp, HandEye, TcpReading, still_for_frame
from .capture.reader import SourceMode, VideoReader
from .capture.realsense import RealSenseReader
from .dog.telemetry import DogNavigator, check_hand_safety_stop, check_person_safety_stop
from .marks.som import draw_marks
from .schemas import EyeObject, FramePayload
from .segmentation import MaskCache, TableBackground, attach_masks
from .state.vision_state import LiveVisionState
from .tracking.tracker import ObjectTracker, compute_iou
from ..grok.changes import draw_changes
from .zoom.manager import DigitalZoomManager

# `realsense`: colore + profondità allineati (D415/D435); `target` è il seriale, oppure 0/None = la prima
PipelineMode = SourceMode | Literal["realsense", "robodk"]


# Frame di tavolo vuoto per la mediana (il piano ne prevedeva 15: 10 bastano e la registrazione dura meno)
BG_FRAMES = 10


def _calibration_id(path: str | Path) -> str:
    p = Path(path)
    return f"{p.stem}_{time.strftime('%Y%m%d-%H%M', time.localtime(p.stat().st_mtime))}"


@dataclass
class SourceNode:
    source: str
    reader: VideoReader | RealSenseReader | Any
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
    # Camera sul braccio: con la hand-eye e una posa TCP, `xyz_cam_m` diventa `xyz_base_m` (vedi calibration/hand_eye)
    hand_eye: HandEye | None = None
    tcp_fn: Callable[[], TcpReading | None] | None = None
    tcp_readings: deque = field(default_factory=lambda: deque(maxlen=10))
    robot_still: TcpReading | None = None  # posa del robot fermo durante l'ultimo frame, None se in moto o ignota
    calibration_id: str | None = None      # file di calibrazione + data, riportato da /locate
    robot_pose_fn: Callable[[], TcpReading | None] | None = None  # posa del braccio per la calibrazione dall'app (RTDE)
    camera_name: str = ""
    _bg_frame_id: int = -1
    open_vocab: Any = None
    grok_objects: list[dict] = field(default_factory=list)
    label_meta: dict[str, dict] = field(default_factory=dict)
    meta: dict[int, dict] = field(default_factory=dict)
    ref_frame: np.ndarray | None = None
    changes: list[dict] = field(default_factory=list)

    def to_base(self, objects: Sequence[EyeObject | dict], frame_ts: float) -> list[EyeObject | dict]:
        """Aggiunge `xyz_base_m` / `yaw_base_deg` agli oggetti con `xyz_cam_m`, solo se il robot era fermo durante il
        frame. Senza hand-eye o senza posa TCP restituisce gli oggetti così come sono."""
        if self.hand_eye is None or self.tcp_fn is None:
            return list(objects)
        reading = self.tcp_fn()
        if reading is not None:
            self.tcp_readings.append(reading)
        self.robot_still = still_for_frame(self.tcp_readings, frame_ts) if reading is not None else None
        if self.robot_still is None:
            return list(objects)
        out: list[EyeObject | dict] = []
        for o in objects:
            get = o.get if isinstance(o, dict) else lambda k, _o=o: getattr(_o, k)
            xyz = get("xyz_cam_m")
            if xyz is None:
                out.append(o)
                continue
            p, yaw = self.hand_eye.to_base(xyz, get("axis_cam"), self.robot_still.pose)
            update = {"xyz_base_m": tuple(round(v, 4) for v in p),
                      "yaw_base_deg": round(yaw, 1) if yaw is not None else None}
            out.append({**o, **update} if isinstance(o, dict) else o.model_copy(update=update))
        return out

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
        hand_eye_file: str | Path | None = None,
        controller_url: str | None = None,
        look_pose: Sequence[float] | None = None,
        fixed_pose_file: str | Path | None = None,
        robot_pose: Literal["controller", "robodk"] = "controller",
        color_labels: dict[str, str] | None = None,
        robodk_options: dict | None = None,
    ) -> SourceNode:
        """Configura e registra una sorgente (`arm` o `dog`). `masks`: sagome e misure; di default solo per `arm`.

        Camera sul braccio, posa fissa: con `fixed_pose_file` esistente (`calibrate-look-pose`) le coordinate in base
        sono `T_base_cam · xyz_cam_m`, senza leggere il robot; ha la precedenza sulla hand-eye.
        Camera sul braccio, pose qualsiasi: con `hand_eye_file` esistente la posa TCP arriva da `look_pose` (fissa, m e rad) se data,
        altrimenti da `controller_url` (robot_controller, sola lettura) o, con `robot_pose="robodk"`, dalla flangia in
        RoboDK. L'omografia fissa del tavolo non si usa più: con la camera che si muove darebbe millimetri sbagliati.
        `mode="robodk"`: camera D435 simulata sulla flangia (`capture.robodk`, opzioni in `robodk_options`).
        `color_labels`: con il tracker mock, colore (italiano) -> etichetta, es. {"arancione": "vite_arancione"}.
        """
        calibration_id = None
        hand_eye = tcp_fn = None
        if fixed_pose_file is not None and Path(fixed_pose_file).exists():
            # Posa fissa: T_base_cam costante, nessuna posa del robot da leggere (vedi hand_eye.calibrate_fixed)
            hand_eye, tcp_fn = HandEye.load(fixed_pose_file), FixedTcp(FIXED_POSE)
            if hand_eye.tcp_source != "fixed":
                raise ValueError(f"{fixed_pose_file} non è una calibrazione a posa fissa (calibrate-look-pose)")
            ex = hand_eye.extra
            print(f"[Posa fissa] {fixed_pose_file}: riproiezione {ex.get('reproj_px', '?')} px, tavolo dalla "
                  f"profondità {ex.get('table_check_mm', '?')} mm. Misure valide solo con il braccio nella posa "
                  "di osservazione")
            calibration_file, calibrator = None, None
            calibration_id = _calibration_id(fixed_pose_file)
        elif hand_eye_file is not None and Path(hand_eye_file).exists():
            hand_eye = HandEye.load(hand_eye_file)
            if look_pose:
                tcp_fn, runtime_src = FixedTcp(look_pose), "manual"
            elif robot_pose == "robodk":
                from .capture.robodk import RoboDKFlange
                tcp_fn, runtime_src = RoboDKFlange((robodk_options or {}).get("robot_name")), "robodk"
            else:
                tcp_fn, runtime_src = ControllerTcp(controller_url or "http://127.0.0.1:8765"), "controller"
            print(f"[Hand-eye] {hand_eye_file}: {hand_eye.n_samples} pose, dispersione "
                  f"{hand_eye.spread_mm if hand_eye.spread_mm is not None else '?'} mm, posa TCP da {runtime_src}")
            if hand_eye.tcp_source != runtime_src:
                print(f"[Hand-eye] ATTENZIONE: calibrata con la posa TCP da '{hand_eye.tcp_source}', ora da "
                      f"'{runtime_src}': se i due TCP non coincidono le coordinate in base sono sbagliate")
            calibration_file, calibrator = None, None
            calibration_id = _calibration_id(hand_eye_file)
        if calibrator is None and calibration_file is not None and Path(calibration_file).exists():
            calibrator = ArmCalibrator.load(calibration_file)
        elif calibrator is None and source == "arm" and hand_eye is None:
            calibrator = ArmCalibrator()

        open_vocab = None
        try:
            import os
            from ..grok.open_vocab import DESK_CLASSES, OpenVocab
            world_conf = float(os.getenv("WORLD_CONF", "0.2"))
            open_vocab = OpenVocab(conf=world_conf, base=DESK_CLASSES)
            print(f"[Pipeline] YOLO-World (OpenVocab) inizializzato per '{source}' con {len(DESK_CLASSES)} classi base.")
        except Exception as exc:
            print(f"[Pipeline] Avviso: OpenVocab non disponibile per '{source}': {exc}")

        calib_fn = calibrator.compute_object_coords if (calibrator and source == "arm") else None
        tracker = ObjectTracker(
            model_name=model_name,
            conf_threshold=conf_threshold,
            use_mock=use_mock_tracker,
            calibrator_fn=calib_fn,
            ema_alpha=ema_alpha,
            min_hits=min_hits,
            color_labels=color_labels,
        )
        if mode == "robodk":
            from .capture.robodk import RoboDKCameraReader
            reader = RoboDKCameraReader(source_name=source, **(robodk_options or {}))
        elif mode == "realsense":
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
            hand_eye=hand_eye,
            tcp_fn=tcp_fn,
            calibration_id=calibration_id,
            camera_name=getattr(reader, "device_name", "") or str(mode),
            open_vocab=open_vocab,
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
        if hasattr(node.reader, "get_frames"):  # RealSense o RoboDK: colore e profondità dello stesso frame
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

        # Integrazione YOLO-World & Grok
        if node.open_vocab is not None:
            node.open_vocab.feed(frame)
            live = [dict(o) for o in node.open_vocab.objects]
            seen = {o["label"] for o in live}
            if seen and node.grok_objects:
                node.grok_objects = [o for o in node.grok_objects if o.get("label") not in seen]
            for o in live:
                o.update(node.label_meta.get(o["label"], {}))
            core_boxes = [o.bbox for o in objects]
            live = [o for o in live if not any(compute_iou(o["bbox"], b) > 0.45 for b in core_boxes)]
            for o in live:
                eye_obj = EyeObject(
                    id=o["id"],
                    label=o["label"],
                    name=o.get("name"),
                    conf=float(o.get("conf", 0.5)),
                    bbox=tuple(o["bbox"]),
                    color=o.get("color"),
                )
                objects.append(eye_obj)

        if node.grok_objects:
            for g in node.grok_objects:
                eye_obj = EyeObject(
                    id=g["id"],
                    label=g["label"],
                    name=g.get("name"),
                    conf=float(g.get("conf", 0.9)),
                    bbox=tuple(g["bbox"]),
                    color=g.get("color"),
                )
                objects.append(eye_obj)

        for o in objects:
            if o.id in node.meta:
                for k, v in node.meta[o.id].items():
                    if hasattr(o, k):
                        setattr(o, k, v)

        if node.recording_bg:
            node._feed_background(frame, depth, frame_id)
        objects = node.attach(objects, frame)
        objects = node.to_base(objects, ts)

        dog_telemetry = None
        if node.dog_navigator is not None:
            dog_telemetry, safety_stop, safety_reason = node.dog_navigator.evaluate(objects)
        else:
            safety_stop, safety_reason = check_person_safety_stop(objects)
            if not safety_stop and source == "arm":  # una mano dove lavora il braccio: fermo
                safety_stop, safety_reason = check_hand_safety_stop(objects)

        marked = draw_marks(
            frame,
            objects,
            show_grasp=apply_calib,
            safety_stop=safety_stop,
            min_conf=min_display_conf,
            show_conf=show_conf,
            show_masks=show_masks,
        )
        if node.changes:
            marked = draw_changes(marked, node.changes)

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
