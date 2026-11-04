"""White_Bucket_Lid_04: flat flange + truncated cone with a 30 mm central through-hole.

The drawing's 25 mm dimension is INTERPRETED as the exposed flange radial width,
so the cone's lower outside diameter (139 - 2*25 = 89) is DERIVED, not measured.
"""
from __future__ import annotations

from ..core import ObjectReport, axis_hits_from_above, contains, dim, flag

OBJECT_ID = "white_bucket_lid_04"


def derived(cfg) -> dict:
    return {
        "cone_bottom_outer_diameter_mm": dim(cfg, "flange_outer_diameter_mm")
        - 2 * dim(cfg, "flange_exposed_radial_width_mm"),
        "cone_height_mm": dim(cfg, "total_height_mm") - dim(cfg, "flange_thickness_mm"),
    }


def expected_extents(cfg) -> list[float]:
    d = dim(cfg, "flange_outer_diameter_mm")
    return [d, d, dim(cfg, "total_height_mm")]


def _radii(cfg):
    rf = dim(cfg, "flange_outer_diameter_mm") / 2
    rcb = derived(cfg)["cone_bottom_outer_diameter_mm"] / 2
    rct = dim(cfg, "cone_top_outer_diameter_mm") / 2
    rh = dim(cfg, "centre_hole_diameter_mm") / 2
    return rf, rcb, rct, rh


def profile(cfg):
    rf, rcb, rct, rh = _radii(cfg)
    tf = dim(cfg, "flange_thickness_mm")
    h = dim(cfg, "total_height_mm")
    if not flag(cfg, "centre_hole_through"):
        raise NotImplementedError("blind centre hole is not modelled; set centre_hole_through=true")
    if not (0 < rh < rct <= rcb < rf and 0 < tf < h):
        raise ValueError("White lid needs hole < cone top <= cone bottom < flange radius and 0 < flange < height")
    return [(rh, 0), (rf, 0), (rf, tf), (rcb, tf), (rct, h), (rh, h), (rh, 0)]


def cavity_checks(mesh, cfg, rep: ObjectReport, tol: float) -> None:
    rf, rcb, rct, rh = _radii(cfg)
    tf = dim(cfg, "flange_thickness_mm")
    h = dim(cfg, "total_height_mm")
    zs = axis_hits_from_above(mesh, h + 50)
    rep.add("centre through-hole: axis ray crosses no material", zs.size == 0, f"axis crossings {zs.round(3).tolist()}")
    rep.add("hole wall: point just inside hole radius is outside (z=flange/2 and z=top-1)",
            not contains(mesh, [rh - 0.5, 0.05, tf / 2]) and not contains(mesh, [rh - 0.5, 0.05, h - 1]))
    rep.add("material next to hole is solid", contains(mesh, [rh + 0.5, 0.05, tf / 2]))
    rep.add("flange is solid, space above exposed flange is empty",
            contains(mesh, [(rf + rcb) / 2, 0.05, tf / 2]) and not contains(mesh, [(rf + rcb) / 2, 0.05, tf + 2]))
    zm = (tf + h) / 2
    rcone = rcb + (rct - rcb) * (zm - tf) / (h - tf)
    rep.add("cone surface at mid-height is where the profile puts it",
            contains(mesh, [rcone - 0.8, 0.05, zm]) and not contains(mesh, [rcone + 0.8, 0.05, zm]),
            f"expected cone radius {rcone:.2f} mm at z={zm}")
