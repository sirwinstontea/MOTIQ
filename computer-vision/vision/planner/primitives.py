"""Catalogo chiuso delle primitive del braccio.

La fonte di verità è `skill_pick_place_ur5e.json` (sezioni `action_primitives` e `robot`):
qui la si carica senza duplicarla. Nessuna primitiva fuori da questo catalogo può essere
eseguita: le skill nuove sono solo composizioni di queste.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from .schemas import ParamSpec

ROOT = Path(__file__).resolve().parents[2]  # computer-vision/
UR5E_SPEC_PATH = ROOT / "skill_pick_place_ur5e.json"

# Primitive che spostano il TCP lungo un asse del frame base.
LINEAR_PRIMITIVES = {
    "MOVE_UP", "MOVE_DOWN", "MOVE_LEFT", "MOVE_RIGHT", "MOVE_FORWARD", "MOVE_BACKWARD",
}
ROTATION_PRIMITIVES = {"ROTATE_ROLL", "ROTATE_PITCH", "ROTATE_YAW"}

# Limiti impliciti non scritti nel JSON UR5e.
_IMPLICIT_MIN = {"distance_m": 0.0, "speed_mps": 0.001, "speed_dps": 0.1, "seconds": 0.0, "height_m": 0.0}


@dataclass(frozen=True)
class PrimitiveSpec:
    id: str
    description: str
    params: dict[str, ParamSpec]
    direction: Optional[str] = None
    aliases_it: tuple[str, ...] = ()

    def compact(self) -> dict[str, Any]:
        """Rappresentazione compatta da mettere nei prompt di Grok."""
        ps = {}
        for name, p in self.params.items():
            d: dict[str, Any] = {"type": p.type}
            for k in ("default", "min", "max", "values", "length"):
                v = getattr(p, k)
                if v is not None:
                    d[k] = v
            if p.optional:
                d["optional"] = True
            ps[name] = d
        out: dict[str, Any] = {"id": self.id, "description": self.description, "params": ps}
        if self.direction:
            out["direction"] = self.direction
        return out


@dataclass(frozen=True)
class RobotSpec:
    model: str
    joint_ids: tuple[str, ...]
    home_joints_deg: tuple[float, ...]
    limits: dict[str, float]
    direction_mapping: dict[str, tuple[str, int]]
    gripper_stroke_m: float = 0.085
    physical_execution_allowed: bool = False
    reach_m: float = 0.85

    def axis_vector(self, direction: str) -> tuple[float, float, float]:
        axis, sign = self.direction_mapping[direction]
        v = [0.0, 0.0, 0.0]
        v["xyz".index(axis)] = float(sign)
        return (v[0], v[1], v[2])


@dataclass(frozen=True)
class Catalog:
    primitives: dict[str, PrimitiveSpec]
    robot: RobotSpec
    source: str = field(default=str(UR5E_SPEC_PATH))

    def get(self, pid: str) -> Optional[PrimitiveSpec]:
        return self.primitives.get(pid)

    def compact(self) -> list[dict[str, Any]]:
        return [p.compact() for p in self.primitives.values()]


def _param_from_json(name: str, raw: dict[str, Any]) -> ParamSpec:
    data = {k: raw[k] for k in ("type", "default", "min", "max", "values", "length", "optional") if k in raw}
    if "note" in raw:
        data["description"] = raw["note"]
    p = ParamSpec(**data)
    if p.min is None and name in _IMPLICIT_MIN:
        p = p.model_copy(update={"min": _IMPLICIT_MIN[name]})
    return p


def load_catalog(path: Path | str = UR5E_SPEC_PATH) -> Catalog:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    prims: dict[str, PrimitiveSpec] = {}
    for p in raw["action_primitives"]:
        params = {n: _param_from_json(n, spec) for n, spec in (p.get("parameters") or {}).items()}
        prims[p["id"]] = PrimitiveSpec(
            id=p["id"],
            description=p.get("description", ""),
            params=params,
            direction=p.get("direction"),
            aliases_it=tuple(p.get("voice_aliases_it", [])),
        )

    r = raw["robot"]
    dm = {k: (v["axis"], int(v["sign"])) for k, v in r["direction_mapping"].items()
          if isinstance(v, dict) and "axis" in v}
    limits = {k: float(v) for k, v in r["operational_limits"].items() if isinstance(v, (int, float))}
    robot = RobotSpec(
        model=r.get("model", "UR5e"),
        joint_ids=tuple(j["id"] for j in r["joints"]),
        home_joints_deg=tuple(float(x) for x in r["home_joints_deg"]),
        limits=limits,
        direction_mapping=dm,
        gripper_stroke_m=float(r.get("gripper", {}).get("stroke_m", 0.085)),
        physical_execution_allowed=bool(raw.get("safety", {}).get("physical_execution_allowed", False)),
        reach_m=float(r.get("reach_m", 0.85)),
    )
    return Catalog(primitives=prims, robot=robot, source=str(path))


@lru_cache(maxsize=4)
def default_catalog() -> Catalog:
    return load_catalog(UR5E_SPEC_PATH)
