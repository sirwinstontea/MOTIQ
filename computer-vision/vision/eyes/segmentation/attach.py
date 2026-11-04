"""Aggiunge la maschera (`mask`, `mask_source`) e le misure che ne escono agli oggetti di un frame.

    cache = MaskCache()                      # una per sorgente video, vive quanto la pipeline
    objs = attach_masks(objs, frame, depth=depth, bg=bg, intrinsics=reader.intrinsics, cache=cache)

Sorgente scelta per ogni oggetto:
- tavolo vuoto pronto e profondità disponibile: unione di depth e color (`mask_source="depth"` se la maggior parte
  dei pixel viene dalla profondità, altrimenti `"color"`);
- tavolo vuoto pronto, senza profondità: color;
- nessun tavolo vuoto, o niente trovato sopra: grabcut.

Misure (vedi `measure.py`), ricalcolate a ogni frame anche quando la maschera viene dalla cache:
- `distance_m`: con la profondità, mediana sulla maschera erosa; senza, pinhole dal lato del rettangolo ruotato.
  Con la cache, mediana mobile sugli ultimi `SMOOTH_FRAMES` frame per id, azzerata se l'oggetto si sposta.
  Senza maschera, o senza un lato misurabile, resta il valore in ingresso (la stima dal box del tracker).
- `size_mm`: solo con profondità e intrinseci, e solo se nessun lato è tagliato dal bordo del frame.
- `coords_mm.angle_deg`: angolo del rettangolo ruotato al posto di quello di Otsu, se `coords_mm` c'è e l'oggetto
  è allungato. `x` e `y` restano quelli del calibratore.
"""
from __future__ import annotations

import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from vision.eyes.schemas import EyeObject, MaskSource
from vision.eyes.segmentation.background import TableBackground
from vision.eyes.segmentation.geometry import MaskGeometry, mask_geometry, mask_to_polygon
from vision.eyes.segmentation.masks import (
    MIN_HEIGHT_MM, bbox_to_px, color_roi, depth_roi, expand_box, finish_mask, mask_grabcut,
)
from vision.eyes.segmentation.measure import (
    Measures, depth_on_mask_mm, erode_mask, focal_px, grasp_angle, monocular_distance_m, size_mm_from,
)
from vision.eyes.tracking.tracker import compute_iou

log = logging.getLogger(__name__)

REFRESH_IOU = 0.9     # sotto questo IoU con l'ultimo riquadro la maschera si ricalcola
REFRESH_EVERY = 5     # e comunque ogni 5 frame
FORGET_AFTER = 30     # frame senza vedere un id prima di toglierlo dalla cache
SMOOTH_FRAMES = 10    # mediana mobile della distanza per id
JUMP_REL = 0.10       # salto relativo oltre cui la storia si butta (oggetto sollevato o spostato in Z)

# Oggetti bassi della demo: soglia di altezza più piccola (il colore li prende comunque)
# (l'anello della demo è alto 7 mm: con la soglia normale di 6 mm il margine sul rumore sarebbe di 1 mm)
MIN_HEIGHT_BY_LABEL: dict[str, float] = {"anello_verde": 4.0, "anello": 4.0, "ring": 4.0}


@dataclass(frozen=True)
class MaskResult:
    mask: np.ndarray                   # uint8 0/255, grande quanto il frame
    source: MaskSource
    polygon: list[tuple[float, float]]
    geometry: MaskGeometry | None = None
    inner: np.ndarray | None = None    # maschera erosa, per leggere la profondità senza i pixel volanti dei bordi


@dataclass
class _Entry:
    bbox: tuple[float, float, float, float]
    result: MaskResult | None          # anche "nessuna maschera" si tiene: GrabCut costa
    age: int
    last_seen: int
    history: deque                     # ultime distanze (m) per la mediana mobile


class MaskCache:
    """Maschere dell'ultimo frame per id: si ricalcolano solo se il riquadro si sposta o sono vecchie."""

    def __init__(self, refresh_iou: float = REFRESH_IOU, refresh_every: int = REFRESH_EVERY,
                 forget_after: int = FORGET_AFTER, smooth_frames: int = SMOOTH_FRAMES, jump_rel: float = JUMP_REL):
        self.refresh_iou = refresh_iou
        self.refresh_every = max(1, int(refresh_every))
        self.forget_after = max(1, int(forget_after))
        self.smooth_frames = max(1, int(smooth_frames))
        self.jump_rel = jump_rel
        self._entries: dict[int, _Entry] = {}
        self._context: tuple | None = None
        self._tick = 0
        self.computed = 0  # maschere calcolate davvero (utile per test e fps)

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def begin_frame(self, context: tuple) -> None:
        """Nuovo frame. Se cambiano risoluzione, tavolo vuoto o presenza della profondità, si riparte da zero."""
        self._tick += 1
        if context != self._context:
            self._entries.clear()
            self._context = context
        stale = [k for k, e in self._entries.items() if self._tick - e.last_seen > self.forget_after]
        for k in stale:
            del self._entries[k]

    def lookup(self, oid: int, bbox) -> tuple[bool, MaskResult | None]:
        e = self._entries.get(oid)
        if e is None or e.age + 1 >= self.refresh_every or compute_iou(e.bbox, tuple(bbox)) < self.refresh_iou:
            return False, None
        e.age += 1
        e.last_seen = self._tick
        return True, e.result

    def store(self, oid: int, bbox, result: MaskResult | None) -> None:
        bbox = tuple(float(v) for v in bbox)
        old = self._entries.get(oid)
        # Ricalcolo per età: l'oggetto è fermo e la storia resta. Ricalcolo perché il box si è spostato: si riparte.
        if old is not None and compute_iou(old.bbox, bbox) >= self.refresh_iou:
            history = old.history
        else:
            history = deque(maxlen=self.smooth_frames)
        self._entries[oid] = _Entry(bbox, result, 0, self._tick, history)

    def smooth(self, oid: int, value: float) -> float:
        """Mediana mobile della distanza per id. Un salto oltre `jump_rel` butta la storia: conta il valore nuovo."""
        e = self._entries.get(oid)
        if e is None:
            return value
        h = e.history
        if h and abs(value - float(np.median(h))) > self.jump_rel * max(float(np.median(h)), 1e-6):
            h.clear()
        h.append(value)
        return round(float(np.median(h)), 3)


def attach_masks(objects: Sequence[EyeObject | dict], frame: np.ndarray, depth: np.ndarray | None = None,
                 bg: TableBackground | None = None, intrinsics: Any = None,
                 cache: MaskCache | None = None) -> list[EyeObject | dict]:
    """Restituisce gli oggetti con maschera e misure, dello stesso tipo dell'ingresso (EyeObject o dict).

    `depth` è in mm (uint16, 0 = nessuna misura), allineata a `frame`. `intrinsics` è un `Intrinsics` della
    RealSense, una tupla (fx, fy, cx, cy) o un dict con fx e fy: serve per `size_mm` e, senza profondità, migliora
    la distanza monoculare. Un errore su un oggetto lo lascia com'era e non ferma gli altri: `EyePipeline._run_loop`
    ingoia le eccezioni e il frame resterebbe bloccato.
    """
    if cache is not None:
        cache.begin_frame(_context(frame, depth, bg))
    out: list[EyeObject | dict] = []
    for o in objects:
        try:
            oid, bbox, label = _fields(o)
            hit, res = cache.lookup(oid, bbox) if cache is not None and oid is not None else (False, None)
            if not hit:
                res = compute_mask(frame, bbox, depth=depth, bg=bg, label=label)
                if cache is not None:
                    cache.computed += 1
                    if oid is not None:
                        cache.store(oid, bbox, res)
            smooth = (lambda v, _id=oid: cache.smooth(_id, v)) if cache is not None and oid is not None else None
            meas = measure(res, label, frame.shape[:2], depth=depth, intrinsics=intrinsics, smooth=smooth)
            out.append(_with_mask(o, res, meas))
        except Exception as exc:  # noqa: BLE001 - un oggetto rotto non deve fermare il frame
            _log_once(o, exc)
            out.append(o)
    return out


def compute_mask(frame: np.ndarray, bbox, depth: np.ndarray | None = None, bg: TableBackground | None = None,
                 label: str | None = None) -> MaskResult | None:
    """Maschera di un solo oggetto dal riquadro normalizzato `bbox`, senza cache."""
    H, W = frame.shape[:2]
    box_px = bbox_to_px(bbox, W, H)
    if box_px[2] - box_px[0] < 2 or box_px[3] - box_px[1] < 2:
        return None

    if bg is not None and bg.matches(frame):
        raw_c = color_roi(frame, bg, box_px)
        raw_d = None
        if depth is not None and bg.has_depth:
            raw_d = depth_roi(depth, bg, box_px, min_height_for(label))
        raw = raw_c if raw_d is None else (raw_d if raw_c is None else raw_d | raw_c)
        mask = finish_mask(raw, box_px, (H, W)) if raw is not None else None
        if mask is not None:
            source: MaskSource = "color"
            if raw_d is not None:
                from_depth = int(raw_d[_roi_slice(mask, box_px, W, H)].sum())
                if from_depth * 2 >= int(np.count_nonzero(mask)):
                    source = "depth"
            res = _result(mask, source)
            if res is not None:
                return res

    mask = mask_grabcut(frame, bbox)
    return _result(mask, "grabcut") if mask is not None else None


def measure(res: MaskResult | None, label: str | None, frame_hw: tuple[int, int], depth: np.ndarray | None = None,
            intrinsics: Any = None, smooth=None) -> Measures:
    """Misure di un oggetto dalla sua maschera. `smooth(distanza) -> distanza` applica la mediana mobile."""
    if res is None or res.geometry is None:
        return Measures()
    geom = res.geometry
    f = focal_px(intrinsics)

    dist, src = None, None
    if depth is not None and res.inner is not None:
        z_mm = depth_on_mask_mm(depth, res.inner)
        if z_mm is not None:
            dist, src = z_mm / 1000.0, "depth"
    if dist is None:
        mono = monocular_distance_m(label, geom, frame_hw, intrinsics)
        if mono is not None:
            dist, src = mono, "mono"
    if dist is not None and smooth is not None:
        dist = smooth(dist)

    size = size_mm_from(geom, dist, *f) if src == "depth" and f is not None else None
    if dist is not None:
        dist = round(dist, 3) if src == "depth" else round(dist, 2)
    return Measures(distance_m=dist, distance_source=src, size_mm=size, angle_deg=grasp_angle(geom))


def min_height_for(label: str | None) -> float:
    return MIN_HEIGHT_BY_LABEL.get((label or "").strip().lower(), MIN_HEIGHT_MM)


# --- interni ---------------------------------------------------------------------------------------------------

_logged: set[tuple[Any, str]] = set()


def _context(frame: np.ndarray, depth: np.ndarray | None, bg: TableBackground | None) -> tuple:
    bg_key = (id(bg), bg.version) if bg is not None and bg.ready else None
    return frame.shape, depth is not None, bg_key


def _fields(o: EyeObject | dict) -> tuple[int | None, tuple[float, float, float, float], str | None]:
    if isinstance(o, EyeObject):
        return o.id, o.bbox, o.label
    if isinstance(o, dict):
        oid = o.get("id")
        return (int(oid) if oid is not None else None), tuple(o["bbox"]), o.get("label")
    raise TypeError(f"oggetto non supportato: {type(o).__name__}")


def _with_mask(o: EyeObject | dict, res: MaskResult | None, meas: Measures | None = None) -> EyeObject | dict:
    meas = meas or Measures()
    update: dict[str, Any] = {
        "mask": res.polygon if res else None,
        "mask_source": res.source if res else None,
        "size_mm": meas.size_mm,
    }
    if meas.distance_m is not None:
        update["distance_m"] = meas.distance_m
    coords = o.coords_mm if isinstance(o, EyeObject) else o.get("coords_mm")
    if coords is not None and meas.angle_deg is not None:
        if isinstance(coords, dict):
            update["coords_mm"] = {**coords, "angle_deg": meas.angle_deg}
        else:
            update["coords_mm"] = coords.model_copy(update={"angle_deg": meas.angle_deg})
    if isinstance(o, EyeObject):
        return o.model_copy(update=update)
    return {**o, **update}


def _result(mask: np.ndarray, source: MaskSource) -> MaskResult | None:
    poly = mask_to_polygon(mask)
    if not poly:
        return None
    return MaskResult(mask, source, poly, geometry=mask_geometry(mask), inner=erode_mask(mask))


def _roi_slice(mask: np.ndarray, box_px, W: int, H: int):
    """Slice della maschera grezza (riquadro allargato) che corrisponde ai pixel accesi della maschera finale."""
    x1, y1, x2, y2 = expand_box(box_px, W, H)
    return mask[y1:y2, x1:x2] > 0


def _log_once(o: Any, exc: Exception) -> None:
    oid = o.get("id") if isinstance(o, dict) else getattr(o, "id", None)
    key = (oid, type(exc).__name__)
    if key not in _logged:
        if len(_logged) > 1000:
            _logged.clear()
        _logged.add(key)
        log.warning("maschera non calcolata per l'oggetto %s: %r", oid, exc, exc_info=True)
