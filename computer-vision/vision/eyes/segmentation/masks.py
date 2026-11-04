"""Maschere degli oggetti: solo i pixel dell'oggetto dentro il suo riquadro. Funzioni pure, nessuna camera.

Tre sorgenti, in ordine di preferenza (la scelta la fa `attach.compute_mask`):

- `depth`: il pixel è più vicino del tavolo vuoto di almeno `min_height_mm` (D435, camera fissa);
- `color`: il pixel ha un colore Lab diverso dal tavolo vuoto (oggetti bassi, che la profondità non vede);
- `grabcut`: nessun tavolo vuoto (webcam, oggetti in mano), si riusa `vision.grok.geometry.refine_box`.

Ogni maschera è un array `uint8` grande quanto il frame (0 = sfondo, 255 = oggetto), calcolato solo nel riquadro
allargato del `BOX_MARGIN`, ripulito da `clean_mask` (una sola componente connessa). `None` = nessun oggetto trovato.
I riquadri in pixel sono `(x1, y1, x2, y2)`, con `x2` e `y2` esclusi come negli slice di numpy.
"""
from __future__ import annotations

import cv2
import numpy as np

from vision.eyes.segmentation.background import TableBackground
from vision.grok.geometry import refine_box

BoxPx = tuple[int, int, int, int]

BOX_MARGIN = 0.10       # il riquadro del detector taglia spesso i bordi dell'oggetto
MIN_HEIGHT_MM = 6.0     # sopra il rumore della D435 a ~60 cm
COLOR_DIFF = 45.0       # distanza Lab (scala OpenCV 8 bit) oltre cui il pixel non è più tavolo
L_WEIGHT = 0.35         # la luminosità conta meno di a/b: le ombre cambiano soprattutto L
MIN_AREA_PX = 20        # sotto è rumore


def bbox_to_px(bbox, W: int, H: int) -> BoxPx:
    """Riquadro normalizzato `[x, y, w, h]` -> `(x1, y1, x2, y2)` in pixel, dentro il frame."""
    x, y, w, h = (float(v) for v in bbox)
    x1, y1 = int(np.clip(np.floor(x * W), 0, W)), int(np.clip(np.floor(y * H), 0, H))
    x2, y2 = int(np.clip(np.ceil((x + w) * W), 0, W)), int(np.clip(np.ceil((y + h) * H), 0, H))
    return x1, y1, x2, y2


def expand_box(box_px: BoxPx, W: int, H: int, margin: float = BOX_MARGIN) -> BoxPx:
    """Allarga il riquadro di `margin` per lato (frazione della sua larghezza/altezza), restando nel frame."""
    x1, y1, x2, y2 = box_px
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    return (max(0, int(x1 - mx)), max(0, int(y1 - my)), min(W, int(np.ceil(x2 + mx))), min(H, int(np.ceil(y2 + my))))


def clean_mask(mask: np.ndarray, box_px: BoxPx | None = None, kernel_px: int | None = None) -> np.ndarray | None:
    """Apertura (via i puntini), chiusura (via i buchetti) e una sola componente connessa.

    Con `box_px` (stesse coordinate di `mask`) si tiene la componente che copre di più il riquadro, non la più
    grande in assoluto: nel margine può entrare un oggetto vicino più grosso. Restituisce `uint8` 0/255 o `None`.
    """
    m = (np.asarray(mask) > 0).astype(np.uint8)
    if m.ndim != 2 or not m.any():
        return None
    if kernel_px is None:
        kernel_px = 3 if min(m.shape) < 120 else 5  # piccolo: un anello sottile non deve sparire
    if kernel_px > 1:
        ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_px, kernel_px))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, ker)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, ker)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 1:
        return None
    areas = stats[1:, cv2.CC_STAT_AREA]
    best = 1 + int(np.argmax(areas))
    if box_px is not None:
        x1, y1, x2, y2 = box_px
        inside = np.bincount(labels[y1:y2, x1:x2].ravel(), minlength=n)[1:]
        if inside.any():
            best = 1 + int(np.argmax(inside))
    if stats[best, cv2.CC_STAT_AREA] < MIN_AREA_PX:
        return None
    return (labels == best).astype(np.uint8) * 255


def mask_from_depth(depth_mm: np.ndarray, bg: TableBackground | np.ndarray, box_px: BoxPx,
                    min_height_mm: float = MIN_HEIGHT_MM, margin: float = BOX_MARGIN) -> np.ndarray | None:
    """Oggetto = pixel più vicini alla camera del tavolo vuoto di almeno `min_height_mm`. Gli zeri non contano."""
    raw = depth_roi(depth_mm, bg, box_px, min_height_mm, margin)
    return None if raw is None else finish_mask(raw, box_px, depth_mm.shape[:2], margin)


def mask_from_color(frame: np.ndarray, bg: TableBackground | np.ndarray, box_px: BoxPx,
                    diff: float = COLOR_DIFF, margin: float = BOX_MARGIN) -> np.ndarray | None:
    """Oggetto = pixel con distanza Lab dal tavolo vuoto sopra `diff` (L pesata `L_WEIGHT`)."""
    raw = color_roi(frame, bg, box_px, diff, margin)
    return None if raw is None else finish_mask(raw, box_px, frame.shape[:2], margin)


def mask_grabcut(frame: np.ndarray, bbox) -> np.ndarray | None:
    """GrabCut nel riquadro normalizzato `bbox` (involucro di `refine_box`): dal contorno alla maschera piena."""
    res = refine_box(frame, bbox)
    if res is None:
        return None
    _, contour = res
    mask = np.zeros(frame.shape[:2], np.uint8)
    cv2.fillPoly(mask, [contour.reshape(-1, 1, 2).astype(np.int32)], 255)
    return mask if cv2.countNonZero(mask) >= MIN_AREA_PX else None


# --- pezzi riusati da attach.compute_mask per unire depth e color prima della pulizia -------------------------

def depth_roi(depth_mm: np.ndarray, bg: TableBackground | np.ndarray, box_px: BoxPx,
              min_height_mm: float = MIN_HEIGHT_MM, margin: float = BOX_MARGIN) -> np.ndarray | None:
    """Maschera grezza (bool, solo il riquadro allargato) della sorgente depth, o `None` se il tavolo non c'è."""
    table = bg.depth if isinstance(bg, TableBackground) else bg
    if table is None or table.shape[:2] != depth_mm.shape[:2]:
        return None
    x1, y1, x2, y2 = expand_box(box_px, depth_mm.shape[1], depth_mm.shape[0], margin)
    d = depth_mm[y1:y2, x1:x2].astype(np.int32)
    t = table[y1:y2, x1:x2].astype(np.int32)
    return (d > 0) & (t > 0) & ((t - d) >= min_height_mm)


def color_roi(frame: np.ndarray, bg: TableBackground | np.ndarray, box_px: BoxPx,
              diff: float = COLOR_DIFF, margin: float = BOX_MARGIN) -> np.ndarray | None:
    """Maschera grezza (bool, solo il riquadro allargato) della sorgente color, o `None` se il tavolo non c'è."""
    ref = bg.color if isinstance(bg, TableBackground) else bg
    if ref is None or ref.shape != frame.shape or frame.ndim != 3:
        return None
    x1, y1, x2, y2 = expand_box(box_px, frame.shape[1], frame.shape[0], margin)
    if x2 <= x1 or y2 <= y1:
        return np.zeros((max(0, y2 - y1), max(0, x2 - x1)), bool)
    cur = _lab(frame[y1:y2, x1:x2])
    tab = _lab(ref[y1:y2, x1:x2])
    d = cur - tab
    d[..., 0] *= L_WEIGHT
    return np.sqrt((d * d).sum(axis=2)) > diff


def finish_mask(raw_roi: np.ndarray, box_px: BoxPx, shape_hw: tuple[int, int],
                margin: float = BOX_MARGIN) -> np.ndarray | None:
    """Pulisce la maschera grezza del riquadro allargato e la rimette in un array grande quanto il frame."""
    H, W = shape_hw
    ex1, ey1, ex2, ey2 = expand_box(box_px, W, H, margin)
    if raw_roi.shape != (ey2 - ey1, ex2 - ex1):
        raise ValueError(f"maschera grezza {raw_roi.shape} diversa dal riquadro allargato {(ey2 - ey1, ex2 - ex1)}")
    x1, y1, x2, y2 = box_px
    roi = clean_mask(raw_roi, (x1 - ex1, y1 - ey1, x2 - ex1, y2 - ey1))
    if roi is None:
        return None
    out = np.zeros((H, W), np.uint8)
    out[ey1:ey2, ex1:ex2] = roi
    return out


def _lab(bgr: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(bgr, (5, 5), 0)  # il rumore del sensore non deve sembrare un oggetto
    return cv2.cvtColor(blurred, cv2.COLOR_BGR2LAB).astype(np.float32)
