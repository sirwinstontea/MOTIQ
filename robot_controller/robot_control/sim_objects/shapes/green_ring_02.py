"""Green_Ring_02: flat annular disk, OD 139, through-hole 78 (user-corrected from 88), 7 thick."""
from __future__ import annotations

import numpy as np

from ..core import ObjectReport, axis_hits_from_above, contains, dim

OBJECT_ID = "green_ring_02"


def derived(cfg) -> dict:
    return {"radial_width_mm": (dim(cfg, "outer_diameter_mm") - dim(cfg, "through_hole_diameter_mm")) / 2}


def expected_extents(cfg) -> list[float]:
    d = dim(cfg, "outer_diameter_mm")
    return [d, d, dim(cfg, "thickness_mm")]


def profile(cfg):
    ro = dim(cfg, "outer_diameter_mm") / 2
    ri = dim(cfg, "through_hole_diameter_mm") / 2
    t = dim(cfg, "thickness_mm")
    if not (0 < ri < ro and t > 0):
        raise ValueError("Green ring needs 0 < hole < outer diameter and thickness > 0")
    return [(ri, 0), (ro, 0), (ro, t), (ri, t), (ri, 0)]


def cavity_checks(mesh, cfg, rep: ObjectReport, tol: float) -> None:
    ro = dim(cfg, "outer_diameter_mm") / 2
    ri = dim(cfg, "through_hole_diameter_mm") / 2
    t = dim(cfg, "thickness_mm")
    zs = axis_hits_from_above(mesh, t + 50)
    rep.add("through-hole: axis ray crosses no material", zs.size == 0, f"axis crossings {zs.round(3).tolist()}")
    rep.add("hole edge: point just inside hole radius is outside solid", not contains(mesh, [ri - 0.5, 0.05, t / 2]))
    rep.add("ring material: point just outside hole radius is inside solid", contains(mesh, [ri + 0.5, 0.05, t / 2]))
    rep.add("ring material: mid-width point is inside solid", contains(mesh, [(ri + ro) / 2, 0.05, t / 2]))
    hole_d = 2 * float(np.hypot(mesh.vertices[:, 0], mesh.vertices[:, 1]).min())
    rep.add("hole diameter (min vertex radius x2) matches config", abs(hole_d - 2 * ri) < tol,
            f"{hole_d:.3f} mm vs {2 * ri} mm")
