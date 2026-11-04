"""Misure dalla maschera: distanza Z, dimensione reale in mm, angolo di presa e distanza monoculare.

- Con la profondità (D435/D415): Z = mediana della profondità sulla maschera **erosa** (i bordi hanno pixel volanti),
  zeri esclusi. `size_mm` = lati del rettangolo ruotato riportati in mm con Z e gli intrinseci.
- Senza profondità (webcam): il lato del rettangolo ruotato non tagliato dal bordo dell'immagine dà la distanza col
  modello pinhole e la dimensione tipica della classe (`depth.estimator.CLASS_PRIORS_CM`). Con il box intero
  l'oggetto ruotato sembra più grande e lo sfondo entra nella misura.
- Angolo di presa: quello del rettangolo ruotato (`geometry.mask_geometry`), solo se l'oggetto è allungato: per un
  oggetto quasi rotondo l'asse lungo non esiste e l'angolo salterebbe a caso.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from vision.eyes.depth.estimator import CLASS_PRIORS_CM, DEFAULT_PRIOR_CM, FOCAL_NORM_X, FOCAL_NORM_Y
from vision.eyes.segmentation.geometry import MaskGeometry

ERODE_PX = 2               # bordo tolto alla maschera prima di leggere la profondità
MIN_DEPTH_PX = 15          # pixel validi minimi per fidarsi della mediana
MIN_DEPTH_FRACTION = 0.2   # ... e almeno questa frazione della maschera erosa
MIN_ELONGATION = 1.15      # lato lungo / lato corto sotto cui l'angolo di presa non ha senso
MONO_RANGE_M = (0.15, 15.0)
PEOPLE = {"person", "persona"}  # inquadrate a mezzo busto: resta l'euristica dedicata di estimate_distance_m


def erode_mask(mask: np.ndarray, px: int = ERODE_PX) -> np.ndarray:
    """Maschera erosa di `px` pixel. Se l'oggetto è così piccolo da sparire, resta la maschera originale."""
    m = (np.asarray(mask) > 0).astype(np.uint8)
    if px <= 0:
        return m
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
    inner = cv2.erode(m, k)
    return inner if inner.any() else m


def depth_on_mask_mm(depth_mm: np.ndarray, inner: np.ndarray, min_px: int = MIN_DEPTH_PX,
                     min_fraction: float = MIN_DEPTH_FRACTION) -> float | None:
    """Mediana della profondità (mm) sui pixel della maschera con una misura. `None` se troppo pochi."""
    if depth_mm is None or depth_mm.shape[:2] != inner.shape[:2]:
        return None
    sel = inner > 0
    n_mask = int(sel.sum())
    if n_mask == 0:
        return None
    vals = depth_mm[sel]
    vals = vals[vals > 0]
    if len(vals) < max(min_px, int(min_fraction * n_mask)):
        return None
    return float(np.median(vals))


def focal_px(intrinsics: Any) -> tuple[float, float] | None:
    """(fx, fy) in pixel da `Intrinsics` di `capture.realsense`, da una tupla (fx, fy, cx, cy) o da un dict."""
    if intrinsics is None:
        return None
    if isinstance(intrinsics, dict):
        fx, fy = intrinsics.get("fx"), intrinsics.get("fy")
    elif hasattr(intrinsics, "fx"):
        fx, fy = intrinsics.fx, getattr(intrinsics, "fy", intrinsics.fx)
    else:
        seq = list(intrinsics)
        fx, fy = (seq[0], seq[1]) if len(seq) >= 2 else (None, None)
    if not fx or not fy or fx <= 0 or fy <= 0:
        return None
    return float(fx), float(fy)


def side_angle_rad(length_px: float, angle_deg: float, fx: float, fy: float) -> float:
    """Ampiezza angolare (tangente) di un segmento lungo `length_px` con direzione `angle_deg` nell'immagine.

    Con fx ≠ fy un lato obliquo va scomposto: la parte orizzontale si divide per fx, quella verticale per fy.
    """
    a = math.radians(angle_deg)
    return math.hypot(length_px * math.cos(a) / fx, length_px * math.sin(a) / fy)


def size_mm_from(geom: MaskGeometry, z_m: float, fx: float, fy: float) -> tuple[float, float] | None:
    """(lato lungo, lato corto) in mm alla distanza `z_m`. `None` se un lato è tagliato dal bordo del frame."""
    if z_m is None or z_m <= 0 or geom.long_clipped or geom.short_clipped:
        return None
    long_mm = side_angle_rad(geom.long_px, geom.angle_deg, fx, fy) * z_m * 1000.0
    short_mm = side_angle_rad(geom.short_px, geom.angle_deg + 90.0, fx, fy) * z_m * 1000.0
    return round(long_mm, 1), round(short_mm, 1)


def monocular_distance_m(label: str | None, geom: MaskGeometry, frame_hw: tuple[int, int],
                         intrinsics: Any = None) -> float | None:
    """Distanza pinhole dal lato del rettangolo ruotato e dalla dimensione tipica della classe.

    Si usa il lato lungo (contro la dimensione reale più grande della classe); se è tagliato dal bordo, il lato corto
    (contro la più piccola). Se lo sono entrambi, o l'oggetto è una persona, `None`: resta la stima dal box.
    Senza intrinseci si usano le focali normalizzate della webcam (`FOCAL_NORM_X/Y`), come `estimate_distance_m`.
    """
    clean = (label or "object").strip().lower()
    if clean in PEOPLE:
        return None
    H, W = frame_hw
    f = focal_px(intrinsics) or (FOCAL_NORM_X * W, FOCAL_NORM_Y * H)
    real_long_m, real_short_m = (v / 100.0 for v in sorted(CLASS_PRIORS_CM.get(clean, DEFAULT_PRIOR_CM), reverse=True))
    if not geom.long_clipped:
        ang = side_angle_rad(geom.long_px, geom.angle_deg, *f)
        real = real_long_m
    elif not geom.short_clipped:
        ang = side_angle_rad(geom.short_px, geom.angle_deg + 90.0, *f)
        real = real_short_m
    else:
        return None
    if ang <= 0:
        return None
    lo, hi = MONO_RANGE_M
    return round(max(lo, min(hi, real / ang)), 2)


def grasp_angle(geom: MaskGeometry, min_elongation: float = MIN_ELONGATION) -> float | None:
    """Angolo di presa dal rettangolo ruotato, `None` per oggetti quasi rotondi."""
    if geom.short_px <= 0 or geom.long_px / geom.short_px < min_elongation:
        return None
    return geom.angle_deg


@dataclass(frozen=True)
class Measures:
    distance_m: float | None = None
    distance_source: str | None = None   # "depth" o "mono"
    size_mm: tuple[float, float] | None = None
    angle_deg: float | None = None
