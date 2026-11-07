"""Stops the arm when a person or a hand is in the camera's work area."""
from __future__ import annotations

from typing import Sequence

from .schemas import EyeObject

PERSON_LABELS = {"person", "persona", "pedestrian", "human"}
HAND_LABELS = {"mano", "polso"}


def _coerce_objects(objects: Sequence[EyeObject | dict]) -> list[EyeObject]:
    out: list[EyeObject] = []
    for o in objects:
        if isinstance(o, EyeObject):
            out.append(o)
        elif isinstance(o, dict):
            out.append(EyeObject.model_validate(o))
        else:
            out.append(EyeObject.model_validate(o.model_dump()))
    return out


def check_person_safety_stop(
    objects: Sequence[EyeObject | dict],
    conf_threshold: float = 0.50,
    height_threshold: float = 0.45,
    area_threshold: float = 0.20,
) -> tuple[bool, str | None]:
    """A person close to the camera. Returns `(safety_stop, reason)`."""
    for obj in _coerce_objects(objects):
        if obj.label.lower() in PERSON_LABELS and obj.conf >= conf_threshold:
            _, _, bw, bh = obj.bbox
            area = bw * bh
            if bh >= height_threshold or area >= area_threshold:
                return True, f"Persona vicina rilevata (id={obj.id}, h={bh:.2f}, area={area:.2f})"
    return False, None


def check_hand_safety_stop(
    objects: Sequence[EyeObject | dict],
    conf_threshold: float = 0.50,
) -> tuple[bool, str | None]:
    """A hand or wrist in the arm's work area. The label "braccio" is ignored: from the arm
    camera that box is often the robot itself."""
    for obj in _coerce_objects(objects):
        if obj.label.lower() in HAND_LABELS and obj.conf >= conf_threshold:
            return True, f"Mano nel campo di lavoro (id={obj.id}, {obj.label})"
    return False, None
