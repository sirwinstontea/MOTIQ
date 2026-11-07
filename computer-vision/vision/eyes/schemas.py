"""Schemi Pydantic per Visione A ("Occhi").

Compatibili al 100% con il contratto di gruppo (`vision/grok/schemas.py`):
- `bbox` è sempre normalizzato `[x, y, w, h]` nell'intervallo `[0.0, 1.0]`
  dove `(x, y)` è l'angolo in alto a sinistra.
- I dizionari esportati da `EyeObject.to_group_dict()` o `model_dump()` possono
  essere passati direttamente a `vision.grok.schemas.parse_objects()`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

MaskSource = Literal["depth", "color", "grabcut"]


class CoordsMM(BaseModel):
    """Coordinate cartesiane sul piano di lavoro del braccio (in millimetri) e angolo di presa."""
    x: float = Field(description="Coordinata X in millimetri nel riferimento del braccio")
    y: float = Field(description="Coordinata Y in millimetri nel riferimento del braccio")
    angle_deg: float = Field(default=0.0, ge=-90.0, le=90.0, description="Angolo di presa in gradi [-90, +90]")


class EyeObject(BaseModel):
    """Oggetto rilevato e tracciato da Visione A."""
    id: int
    label: str
    name: str | None = None
    conf: float = Field(ge=0.0, le=1.0)
    bbox: tuple[float, float, float, float]  # (x, y, w, h) normalizzati 0-1
    color: str | None = None
    coords_mm: CoordsMM | None = None
    distance_m: float | None = Field(default=None, description="Stima distanza approssimativa dalla camera in metri")
    # Maschera di segmentazione (opzionale, vedi vision/eyes/segmentation/). Assente = solo bbox.
    mask: list[tuple[float, float]] | None = Field(
        default=None, description="Sagoma dell'oggetto: poligono [(x, y), ...] normalizzato 0-1, al massimo 32 punti")
    mask_source: MaskSource | None = Field(
        default=None, description="Da dove viene la maschera: depth (D435), color (tavolo vuoto) o grabcut")
    size_mm: tuple[float, float] | None = Field(
        default=None, description="Lato lungo e lato corto dell'oggetto in mm, misurati sulla maschera con la profondità")
    # Camera sul braccio (vedi calibration/hand_eye.py). Solo con la profondità: senza, restano null.
    xyz_cam_m: tuple[float, float, float] | None = Field(
        default=None, description="Centroide 3D della superficie visibile (non il centro geometrico), frame camera, "
                                  "m (x destra, y giù, z avanti)")
    depth_points: int | None = Field(default=None, description="Punti 3D validi usati per xyz_cam_m")
    depth_valid_ratio: float | None = Field(default=None, ge=0.0, le=1.0,
                                            description="Frazione della maschera con profondità valida")
    axis_cam: tuple[float, float, float] | None = Field(
        default=None, description="Versore dell'asse lungo nel frame camera; null se l'oggetto è quasi rotondo")
    xyz_base_m: tuple[float, float, float] | None = Field(
        default=None, description="Centroide nel frame base del robot, m. Solo a robot fermo e con la hand-eye")
    yaw_base_deg: float | None = Field(
        default=None, ge=-90.0, le=90.0, description="Asse lungo attorno a Z della base, gradi [-90, 90]")

    @field_validator("mask")
    @classmethod
    def _validate_mask(cls, v: list[tuple[float, float]] | None) -> list[tuple[float, float]] | None:
        if v is None:
            return None
        pts = [(round(max(0.0, min(1.0, float(x))), 4), round(max(0.0, min(1.0, float(y))), 4)) for x, y in v]
        return pts if len(pts) >= 3 else None

    @field_validator("bbox")
    @classmethod
    def _validate_bbox(cls, v: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        x, y, w, h = v
        x = max(0.0, min(1.0, float(x)))
        y = max(0.0, min(1.0, float(y)))
        w = max(0.0, min(1.0 - x, float(w)))
        h = max(0.0, min(1.0 - y, float(h)))
        return (round(x, 4), round(y, 4), round(w, 4), round(h, 4))

    @property
    def center_norm(self) -> tuple[float, float]:
        """Centro normalizzato (cx, cy) in [0, 1]."""
        x, y, w, h = self.bbox
        return (x + w / 2.0, y + h / 2.0)


class FramePayload(BaseModel):
    """Messaggio JSON completo emesso a ogni frame per App, Robotica e Visione B."""
    source: str
    frame_id: int
    ts: float
    objects: list[EyeObject] = Field(default_factory=list)
    safety_stop: bool = False
    safety_reason: str | None = None


class ZoneVerificationResult(BaseModel):
    """Risultato della misura oggettiva locale di verifica (sparito dalla zona / arrivato a destinazione)."""
    success: bool
    disappeared_from_source: bool
    arrived_at_destination: bool | None = None
    source_iou: float = 0.0
    destination_iou: float = 0.0
    reason: str
