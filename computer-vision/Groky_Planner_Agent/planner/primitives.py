"""Loader and catalog for UR5e action primitives from skill_pick_place_ur5e.json."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, Optional

# Locate the reference json file relative to this module
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
REFERENCE_JSON_PATH = PROJECT_ROOT / "skill_pick_place_ur5e.json"

_CATALOG_CACHE: Optional[Dict[str, Any]] = None


def get_primitives_catalog() -> Dict[str, Any]:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE

    if not REFERENCE_JSON_PATH.exists():
        raise FileNotFoundError(f"Missing reference UR5e schema at: {REFERENCE_JSON_PATH}")

    with open(REFERENCE_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    primitives_list = data.get("action_primitives", [])
    primitives_map = {p["id"]: p for p in primitives_list}

    _CATALOG_CACHE = {
        "robot": data.get("robot", {}),
        "limits": data.get("robot", {}).get("operational_limits", {}),
        "home_joints_deg": data.get("robot", {}).get("home_joints_deg", [0, -90, 0, -90, 0, 0]),
        "primitives": primitives_map,
        "direction_mapping": data.get("robot", {}).get("cartesian_dof", {}).get("direction_mapping", {})
    }
    return _CATALOG_CACHE


def list_primitive_ids() -> list[str]:
    cat = get_primitives_catalog()
    return list(cat["primitives"].keys())


def is_valid_primitive(primitive_id: str) -> bool:
    return primitive_id in list_primitive_ids()
