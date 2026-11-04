"""Blue_Bucket_01: open-top cup with constant 139 mm outer diameter and an 8 mm sealed floor."""
from __future__ import annotations

import numpy as np

from ..core import ObjectReport, axis_hits_from_above, contains, dim

OBJECT_ID = "blue_bucket_01"


def derived(cfg) -> dict:
    return {
        "wall_thickness_mm": (dim(cfg, "outer_diameter_mm") - dim(cfg, "inner_opening_diameter_mm")) / 2,
        "inner_depth_mm": dim(cfg, "total_height_mm") - dim(cfg, "bottom_thickness_mm"),
    }


def expected_extents(cfg) -> list[float]:
    d = dim(cfg, "outer_diameter_mm")
    return [d, d, dim(cfg, "total_height_mm")]


def profile(cfg) -> list[tuple[float, float]]:
    ro = dim(cfg, "outer_diameter_mm") / 2
    ri = dim(cfg, "inner_opening_diameter_mm") / 2
    h = dim(cfg, "total_height_mm")
    tb = dim(cfg, "bottom_thickness_mm")
    if not (0 < ri < ro and 0 < tb < h):
        raise ValueError("Blue bucket needs 0 < inner < outer diameter and 0 < bottom thickness < height")
    return [(0, 0), (ro, 0), (ro, h), (ri, h), (ri, tb), (0, tb), (0, 0)]


def cavity_checks(mesh, cfg, rep: ObjectReport, tol: float) -> None:
    ro = dim(cfg, "outer_diameter_mm") / 2
    ri = dim(cfg, "inner_opening_diameter_mm") / 2
    h = dim(cfg, "total_height_mm")
    tb = dim(cfg, "bottom_thickness_mm")
    zs = axis_hits_from_above(mesh, h + 50)
    rep.add("open mouth: axis ray from above first meets the inner floor", zs.size >= 1 and abs(zs[0] - tb) < tol,
            f"axis crossings at z={zs.round(3).tolist()} mm (floor expected at z={tb})")
    rep.add("sealed bottom: exactly 2 axis crossings (floor top + base)", zs.size == 2 and abs(zs[-1]) < tol)
    rep.add("cavity is empty (point on axis at mid-depth is outside the solid)",
            not contains(mesh, [0.3, 0.2, (tb + h) / 2]))
    rep.add("mouth not capped (point just below rim on axis is outside)", not contains(mesh, [0.3, 0.2, h - 0.5]))
    rep.add("floor is solid (point inside bottom slab)", contains(mesh, [0.3, 0.2, tb / 2]))
    rep.add("wall is solid (point mid-wall)", contains(mesh, [(ro + ri) / 2, 0.1, h / 2]))
    top = mesh.triangles_center[abs(mesh.triangles_center[:, 2] - h) < tol]
    rmin = float(np.hypot(top[:, 0], top[:, 1]).min()) if len(top) else float("nan")
    rep.add("rim face is an annulus (no top face inside the opening)", rmin >= ri - 1.0,
            f"min radius of top-face centroids {rmin:.2f} mm, opening radius {ri}")
