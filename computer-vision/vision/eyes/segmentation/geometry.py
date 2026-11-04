"""Geometria di una maschera: centroide, rettangolo ruotato (lati e angolo di presa) e poligono per il JSON.

Convenzione dell'angolo, la stessa di `calibration.aruco_calib.estimate_grasp_angle`: angolo dell'asse lungo
rispetto all'asse x dell'immagine, in gradi `[-90, 90]`, con y verso il basso (positivo = orario sullo schermo).
L'angolo si ricava dai vertici di `cv2.boxPoints`, non da quello restituito da `minAreaRect`, la cui convenzione
cambia tra le versioni di OpenCV.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

MAX_POLYGON_POINTS = 32
POLYGON_EPS = 0.01  # tolleranza di approxPolyDP, frazione del perimetro


@dataclass(frozen=True)
class MaskGeometry:
    centroid: tuple[float, float]       # px, baricentro dei pixel della maschera
    rect_center: tuple[float, float]    # px, centro del rettangolo ruotato
    long_px: float                      # lato lungo del rettangolo ruotato
    short_px: float                     # lato corto
    angle_deg: float                    # asse lungo, [-90, 90] (vedi docstring del modulo)
    area_px: int
    border: frozenset[str]              # bordi del frame toccati: "left", "top", "right", "bottom"
    long_clipped: bool                  # il lato lungo è tagliato dal bordo del frame: non è una misura vera
    short_clipped: bool


def mask_geometry(mask: np.ndarray) -> MaskGeometry | None:
    """Misure della maschera (uint8 o bool, grande quanto il frame). `None` se è vuota o quasi."""
    m = (np.asarray(mask) > 0).astype(np.uint8)
    if m.ndim != 2:
        return None
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    pts = np.vstack(cnts).reshape(-1, 2)
    if len(pts) < 3:
        return None
    mom = cv2.moments(m, binaryImage=True)
    if mom["m00"] <= 0:
        return None

    rect = cv2.minAreaRect(pts)
    p = cv2.boxPoints(rect)
    e0, e1 = p[1] - p[0], p[2] - p[1]
    l0, l1 = float(np.hypot(*e0)), float(np.hypot(*e1))
    long_edge = e0 if l0 >= l1 else e1
    angle = _fold_angle(math.degrees(math.atan2(float(long_edge[1]), float(long_edge[0]))))

    H, W = m.shape
    border = frozenset(name for name, touched in (
        ("left", m[:, 0].any()), ("right", m[:, W - 1].any()),
        ("top", m[0, :].any()), ("bottom", m[H - 1, :].any())) if touched)
    # Approssimazione: un asse quasi orizzontale viene tagliato dai bordi sinistro/destro, uno verticale da sopra/sotto
    horizontal = {"left", "right"} & border
    vertical = {"top", "bottom"} & border
    long_is_horizontal = abs(angle) <= 45.0
    long_clipped = bool(horizontal if long_is_horizontal else vertical)
    short_clipped = bool(vertical if long_is_horizontal else horizontal)

    return MaskGeometry(
        centroid=(mom["m10"] / mom["m00"], mom["m01"] / mom["m00"]),
        rect_center=(float(rect[0][0]), float(rect[0][1])),
        long_px=max(l0, l1),
        short_px=min(l0, l1),
        angle_deg=round(angle, 1),
        area_px=int(mom["m00"]),
        border=border,
        long_clipped=long_clipped,
        short_clipped=short_clipped,
    )


def mask_to_polygon(mask: np.ndarray, W: int | None = None, H: int | None = None,
                    max_points: int = MAX_POLYGON_POINTS, eps: float = POLYGON_EPS) -> list[tuple[float, float]] | None:
    """Contorno esterno della maschera come poligono normalizzato 0-1, al massimo `max_points` vertici.

    `W` e `H` sono le dimensioni del frame in cui sono espressi i pixel della maschera (di default la sua forma).
    La tolleranza parte da `eps` × perimetro e cresce finché i vertici non sono abbastanza pochi.
    """
    m = (np.asarray(mask) > 0).astype(np.uint8)
    if m.ndim != 2:
        return None
    H = H or m.shape[0]
    W = W or m.shape[1]
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    tol = max(0.5, eps * cv2.arcLength(c, True))
    approx = cv2.approxPolyDP(c, tol, True)
    while len(approx) > max_points:
        tol *= 1.5
        approx = cv2.approxPolyDP(c, tol, True)
    pts = approx.reshape(-1, 2)
    if len(pts) < 3:
        return None
    return [(round(min(1.0, max(0.0, x / W)), 4), round(min(1.0, max(0.0, y / H)), 4)) for x, y in pts.tolist()]


def _fold_angle(a: float) -> float:
    """Porta un angolo di asse (definito a meno di 180°) in `[-90, 90]`."""
    while a > 90.0:
        a -= 180.0
    while a < -90.0:
        a += 180.0
    return a
