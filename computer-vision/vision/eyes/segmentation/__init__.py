"""Maschere di segmentazione per Visione A: solo i pixel dell'oggetto, non tutto il riquadro.

Nessuna rete neurale nuova: sottrazione del tavolo vuoto (profondità D435 e colore Lab) e, senza tavolo vuoto,
GrabCut tramite `vision.grok.geometry.refine_box` (solo importata, Visione B non si modifica).
"""
from vision.eyes.segmentation.attach import MaskCache, MaskResult, attach_masks, compute_mask, measure
from vision.eyes.segmentation.background import TableBackground
from vision.eyes.segmentation.geometry import MaskGeometry, mask_geometry, mask_to_polygon
from vision.eyes.segmentation.masks import clean_mask, mask_from_color, mask_from_depth, mask_grabcut
from vision.eyes.segmentation.measure import Measures, monocular_distance_m, size_mm_from

__all__ = [
    "MaskCache", "MaskGeometry", "MaskResult", "Measures", "TableBackground",
    "attach_masks", "clean_mask", "compute_mask", "mask_from_color", "mask_from_depth", "mask_geometry",
    "mask_grabcut", "mask_to_polygon", "measure", "monocular_distance_m", "size_mm_from",
]
