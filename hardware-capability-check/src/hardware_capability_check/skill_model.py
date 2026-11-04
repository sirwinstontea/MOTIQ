"""Tolerant reader for skill.json files.

Future skill files will not look exactly like the example, so nothing here insists on a rigid
schema: required structure is minimal (a list of steps), everything else is optional and read
through alias tables. Problems are collected as ``issues`` instead of raising, so the checker can
report them as "needs validation" rather than crash.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

UNIT_TO_M = {
    "m": 1.0, "meter": 1.0, "meters": 1.0, "metre": 1.0, "metres": 1.0,
    "mm": 0.001, "millimeter": 0.001, "millimeters": 0.001, "millimetre": 0.001, "millimetres": 0.001,
    "cm": 0.01, "centimeter": 0.01, "centimeters": 0.01, "centimetre": 0.01, "centimetres": 0.01,
    "in": 0.0254, "inch": 0.0254, "inches": 0.0254,
    "ft": 0.3048, "foot": 0.3048, "feet": 0.3048,
}
MASS_TO_KG = {"kg": 1.0, "g": 0.001, "gram": 0.001, "grams": 0.001, "lb": 0.45359237, "lbs": 0.45359237, "oz": 0.0283495}

BASE_FRAME_NAMES = {"base", "base_link", "robot", "robot_base", "base_frame", "arm_base"}
WORLD_FRAME_NAMES = {"world", "table", "workspace", "global", "map"}


def _key(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", k.lower())


def find_value(node: Any, aliases: Iterable[str], max_depth: int = 4) -> Any:
    """Breadth-first search for the first key matching one of the aliases (case/punctuation-insensitive)."""
    wanted = [_key(a) for a in aliases]
    queue: list[tuple[Any, int]] = [(node, 0)]
    while queue:
        cur, depth = queue.pop(0)
        if isinstance(cur, dict):
            for w in wanted:  # alias order = priority
                for k, v in cur.items():
                    if _key(str(k)) == w:
                        return v
            if depth < max_depth:
                queue.extend((v, depth + 1) for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list) and depth < max_depth:
            queue.extend((v, depth + 1) for v in cur if isinstance(v, (dict, list)))
    return None


def as_float(x: Any) -> float | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)) and math.isfinite(x):
        return float(x)
    if isinstance(x, str):
        try:
            v = float(x.strip().split()[0])
            return v if math.isfinite(v) else None
        except (ValueError, IndexError):
            return None
    return None


def parse_vec3(x: Any) -> np.ndarray | None:
    """{x,y,z} / [x,y,z] / {position:[..]} -> array; None if the value is not a point."""
    if isinstance(x, dict):
        for keys in (("x", "y", "z"), ("X", "Y", "Z")):
            if all(k in x for k in keys):
                vals = [as_float(x[k]) for k in keys]
                if None not in vals:
                    return np.array(vals, dtype=float)
        for inner in ("position", "location", "xyz", "translation", "point", "coordinates"):
            if inner in x:
                return parse_vec3(x[inner])
        # partial {x, y}: z unknown
        return None
    if isinstance(x, (list, tuple)) and len(x) == 3:
        vals = [as_float(v) for v in x]
        if None not in vals:
            return np.array(vals, dtype=float)
    return None


def parse_xy(x: Any) -> tuple[float, float] | None:
    if isinstance(x, dict) and "x" in x and "y" in x:
        a, b = as_float(x["x"]), as_float(x["y"])
        if a is not None and b is not None:
            return a, b
    return None


def quantity_in(d: Any, base_names: tuple[str, ...], table: dict[str, float], default_unit: str) -> float | None:
    """Read e.g. mass_kg / mass_g / weight / {"mass": {"value": 120, "unit": "g"}} -> SI value."""
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        ks = _key(str(k))
        for b in base_names:
            bk = _key(b)
            if ks == bk:
                if isinstance(v, dict) and "value" in v:
                    val = as_float(v["value"])
                    unit = str(v.get("unit", default_unit)).lower()
                    return None if val is None else val * table.get(unit, table[default_unit])
                val = as_float(v)
                return None if val is None else val * table[default_unit]
            if ks.startswith(bk):
                suffix = ks[len(bk):]
                for unit, factor in table.items():
                    if suffix == unit:
                        val = as_float(v)
                        return None if val is None else val * factor
    return None


def parse_size(x: Any, scale: float) -> np.ndarray | None:
    """Bounding-box size (dx, dy, dz) in metres from the many ways a skill may state it."""
    if x is None:
        return None
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        s = float(x) * scale
        return np.array([s, s, s])
    if isinstance(x, (list, tuple)) and len(x) == 3:
        vals = [as_float(v) for v in x]
        return None if None in vals else np.array(vals) * scale
    if isinstance(x, dict):
        for keys in (("x", "y", "z"), ("width", "depth", "height"), ("length", "width", "height"), ("w", "d", "h"), ("l", "w", "h")):
            if all(k in x for k in keys):
                vals = [as_float(x[k]) for k in keys]
                if None not in vals:
                    return np.array(vals) * scale
        for k in ("edge_length", "side", "side_length", "edge", "size"):
            if k in x:
                return parse_size(x[k], scale)
        if "diameter" in x and "height" in x:
            dia, h = as_float(x["diameter"]), as_float(x["height"])
            if dia is not None and h is not None:
                return np.array([dia, dia, h]) * scale
        if "radius" in x and "height" in x:
            r, h = as_float(x["radius"]), as_float(x["height"])
            if r is not None and h is not None:
                return np.array([2 * r, 2 * r, h]) * scale
        if "diameter" in x:
            dia = as_float(x["diameter"])
            if dia is not None:
                return np.array([dia, dia, dia]) * scale
    return None


@dataclass
class SceneObject:
    id: str
    name: str
    category: str
    position: np.ndarray | None  # metres, skill frame
    position_basis: str  # stated | missing
    orientation_type: str | None
    size: np.ndarray | None  # full extents (dx,dy,dz) in metres
    size_basis: str  # stated | inferred | unknown
    mass_kg: float | None
    inner_size: np.ndarray | None
    wall_height_m: float | None
    is_container: bool
    detection_required: bool
    raw: dict


@dataclass
class Step:
    index: int
    id: str
    action: str  # normalised upper-case verb
    target: str | None
    params: dict
    raw: dict


@dataclass
class Skill:
    id: str
    name: str
    description: str
    unit_scale: float
    unit_name: str
    unit_known: bool
    frame: str  # "world" | "base"
    frame_known: bool
    objects: dict[str, SceneObject]
    steps: list[Step]
    constraints: dict
    execution_flags: dict
    success_conditions: list[str]
    raw: dict
    issues: list[str] = field(default_factory=list)

    def object_by_ref(self, ref: str | None) -> SceneObject | None:
        if not ref:
            return None
        if ref in self.objects:
            return self.objects[ref]
        r = _key(ref)
        for o in self.objects.values():
            if _key(o.id) == r or _key(o.name) == r:
                return o
        return None


CONTAINER_WORDS = {"container", "box", "bin", "tray", "basket", "bowl", "cup", "crate", "drawer", "tote", "pot"}


def parse_skill(data: dict) -> Skill:
    issues: list[str] = []
    sk = data.get("skill") if isinstance(data.get("skill"), dict) else {}
    root = data

    env = root.get("environment") if isinstance(root.get("environment"), dict) else {}
    unit_name = str(env.get("units", "meters")).strip().lower()
    unit_known = unit_name in UNIT_TO_M
    if "units" not in env:
        issues.append("environment.units missing: assuming meters")
        unit_known = False
    elif not unit_known:
        issues.append(f"unknown length unit {env.get('units')!r}: assuming meters")
    scale = UNIT_TO_M.get(unit_name, 1.0)

    frame_raw = str(env.get("coordinate_system", env.get("frame", "world"))).strip().lower()
    if frame_raw in BASE_FRAME_NAMES:
        frame, frame_known = "base", True
    elif frame_raw in WORLD_FRAME_NAMES:
        frame, frame_known = "world", True
    else:
        frame, frame_known = "world", False
        issues.append(f"unknown coordinate_system {env.get('coordinate_system')!r}: treating it as the world frame")

    objects: dict[str, SceneObject] = {}
    for i, o in enumerate(env.get("objects", []) or []):
        if not isinstance(o, dict):
            issues.append(f"environment.objects[{i}] is not an object")
            continue
        oid = str(o.get("id", o.get("name", f"object_{i}")))
        attrs = o.get("attributes") if isinstance(o.get("attributes"), dict) else {}
        init = o.get("initial_state") if isinstance(o.get("initial_state"), dict) else {}
        loc = parse_vec3(init.get("location", init.get("position", o.get("location", o.get("position")))))
        if loc is not None:
            loc = loc * scale
        ori = init.get("orientation", o.get("orientation"))
        ori_type = None
        if isinstance(ori, dict):
            ori_type = ori.get("type")
        elif isinstance(ori, str):
            ori_type = ori
        merged = {**attrs, **{k: v for k, v in o.items() if k not in ("attributes", "initial_state")}}
        size_raw = find_value(merged, ("dimensions", "dimension", "size", "bounding_box", "bbox", "extents", "edge_length", "side_length", "side"))
        size = parse_size(size_raw, scale)
        size_basis = "stated" if size is not None else "unknown"
        mass = quantity_in(merged, ("mass", "weight"), MASS_TO_KG, "kg")
        inner_raw = find_value(merged, ("inner_size", "inner_dimensions", "interior_dimensions", "interior", "inside_dimensions", "inner"))
        inner = parse_size(inner_raw, scale)
        wall_h = None
        wh = find_value(merged, ("wall_height", "rim_height", "inner_height", "depth_inside"))
        if as_float(wh) is not None:
            wall_h = as_float(wh) * scale
        category = str(o.get("category", attrs.get("shape", ""))).lower()
        name = str(o.get("name", oid))
        is_container = category in CONTAINER_WORDS or any(w in name.lower() for w in CONTAINER_WORDS) or inner is not None
        det = o.get("detection") if isinstance(o.get("detection"), dict) else {}
        if wall_h is None and size is not None and is_container:
            wall_h = float(size[2])
        if size is None and loc is not None and not is_container and loc[2] > 0 and ori_type in (None, "upright", "flat"):
            # An object standing on the table with its centre at height z has height ~ 2 z.
            size = np.array([2 * loc[2]] * 3)
            size_basis = "inferred"
        objects[oid] = SceneObject(
            id=oid, name=name, category=category, position=loc,
            position_basis="stated" if loc is not None else "missing",
            orientation_type=str(ori_type) if ori_type is not None else None,
            size=size, size_basis=size_basis, mass_kg=mass, inner_size=inner, wall_height_m=wall_h,
            is_container=is_container, detection_required=bool(det.get("required", False)), raw=o,
        )

    steps: list[Step] = []
    raw_steps = root.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        issues.append("skill has no 'steps' list: nothing to execute")
        raw_steps = []
    for i, s in enumerate(raw_steps):
        if not isinstance(s, dict):
            issues.append(f"steps[{i}] is not an object")
            continue
        action = str(s.get("action", s.get("type", s.get("skill", "")))).strip().upper().replace(" ", "_").replace("-", "_")
        if not action:
            issues.append(f"steps[{i}] has no 'action'")
        tgt = s.get("target", s.get("object"))
        if isinstance(tgt, dict):
            tgt = tgt.get("id", tgt.get("name"))
        params = dict(s.get("parameters", {}) or {}) if isinstance(s.get("parameters", {}), dict) else {}
        # some skills put parameters directly on the step
        for extra in ("position", "location", "pose", "waypoints", "force", "speed", "duration", "destination"):
            if extra in s and extra not in params:
                params[extra] = s[extra]
        steps.append(Step(index=i, id=str(s.get("id", f"step_{i + 1:03d}")), action=action, target=None if tgt is None else str(tgt), params=params, raw=s))

    constraints = root.get("constraints") if isinstance(root.get("constraints"), dict) else {}
    exec_flags = constraints.get("execution_constraints") if isinstance(constraints.get("execution_constraints"), dict) else {}
    intent = root.get("intent") if isinstance(root.get("intent"), dict) else {}
    sc = intent.get("success_conditions", [])
    return Skill(
        id=str(sk.get("id", root.get("id", "unnamed_skill"))),
        name=str(sk.get("name", root.get("name", "unnamed skill"))),
        description=str(sk.get("description", "")),
        unit_scale=scale, unit_name=unit_name, unit_known=unit_known,
        frame=frame, frame_known=frame_known,
        objects=objects, steps=steps, constraints=constraints, execution_flags=exec_flags,
        success_conditions=[str(x) for x in sc] if isinstance(sc, list) else [],
        raw=data, issues=issues,
    )


def load_skill(path: str | Path) -> Skill:
    p = Path(path)
    try:
        with open(p, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise SystemExit(f"skill file not found: {p}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"skill file is not valid JSON ({p}): {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"skill file must contain a JSON object at top level: {p}")
    return parse_skill(data)
