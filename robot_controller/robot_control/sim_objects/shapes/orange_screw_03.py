"""Orange_Screw_03: ribbed solid of revolution with a blind cup-shaped cavity at the top.

Only total length (90) and max outside diameter (30) are measured. Rib count,
valley diameter, collar/end lengths, cavity size/depth and rib shape are
ESTIMATED from photos and live in the JSON config. Ribs are annular ridges,
NOT a helical thread.
"""
from __future__ import annotations

import numpy as np

from ..core import ObjectReport, axis_hits_from_above, contains, dim, flag

OBJECT_ID = "orange_screw_03"


def derived(cfg) -> dict:
    zone = dim(cfg, "total_length_mm") - dim(cfg, "upper_collar_length_mm") - dim(cfg, "lower_end_length_mm")
    return {"rib_zone_length_mm": zone, "rib_pitch_mm": zone / dim(cfg, "rib_count")}


def expected_extents(cfg) -> list[float]:
    d = dim(cfg, "max_outer_diameter_mm")
    return [d, d, dim(cfg, "total_length_mm")]


def _params(cfg):
    p = dict(
        L=dim(cfg, "total_length_mm"),
        r_max=dim(cfg, "max_outer_diameter_mm") / 2,
        r_peak=dim(cfg, "rib_peak_diameter_mm") / 2,
        r_valley=dim(cfg, "valley_diameter_mm") / 2,
        r_collar=dim(cfg, "upper_collar_diameter_mm") / 2,
        r_low=dim(cfg, "lower_end_diameter_mm") / 2,
        n=int(round(dim(cfg, "rib_count"))),
        collar=dim(cfg, "upper_collar_length_mm"),
        low=dim(cfg, "lower_end_length_mm"),
        r_cav=dim(cfg, "cavity_opening_diameter_mm") / 2,
        cav_depth=dim(cfg, "cavity_depth_mm"),
        flank=dim(cfg, "rib_flank_fraction"),
        peak=dim(cfg, "rib_peak_fraction"),
    )
    return p


def profile(cfg):
    p = _params(cfg)
    if not flag(cfg, "bottom_closed"):
        raise NotImplementedError("open-bottom variant not modelled; set bottom_closed=true")
    radii = [p["r_peak"], p["r_collar"], p["r_low"], p["r_valley"]]
    if max(radii) > p["r_max"] + 1e-9:
        raise ValueError(f"a radius exceeds max outer radius {p['r_max']} mm: {radii}")
    if not (0 < p["r_valley"] < p["r_peak"]):
        raise ValueError("valley diameter must be smaller than rib peak diameter")
    if not (0 < p["r_cav"] < min(p["r_collar"], p["r_valley"]) and 0 < p["cav_depth"] < p["L"]):
        raise ValueError("cavity must be narrower than collar/valley and shallower than total length")
    if p["n"] < 1 or 2 * p["flank"] + p["peak"] > 1 + 1e-9 or p["flank"] <= 0 or p["peak"] < 0:
        raise ValueError("need rib_count >= 1, flank > 0 and 2*flank + peak <= 1")
    z0, z1 = p["low"], p["L"] - p["collar"]
    if not (0 <= z0 < z1 <= p["L"]):
        raise ValueError("collar + lower end are longer than the screw")
    pitch = (z1 - z0) / p["n"]
    valley = 1 - 2 * p["flank"] - p["peak"]

    pts = [(0.0, 0.0), (p["r_low"], 0.0)]
    if z0 > 0:
        pts.append((p["r_low"], z0))
    pts.append((p["r_valley"], z0))
    for i in range(p["n"]):
        b = z0 + i * pitch
        pts += [(p["r_valley"], b + pitch * valley / 2),
                (p["r_peak"], b + pitch * (valley / 2 + p["flank"])),
                (p["r_peak"], b + pitch * (valley / 2 + p["flank"] + p["peak"])),
                (p["r_valley"], b + pitch * (valley / 2 + 2 * p["flank"] + p["peak"]))]
    pts.append((p["r_valley"], z1))
    pts += [(p["r_collar"], z1), (p["r_collar"], p["L"]),
            (p["r_cav"], p["L"]), (p["r_cav"], p["L"] - p["cav_depth"]),
            (0.0, p["L"] - p["cav_depth"]), (0.0, 0.0)]
    return pts


def cavity_checks(mesh, cfg, rep: ObjectReport, tol: float) -> None:
    p = _params(cfg)
    L, floor = p["L"], p["L"] - p["cav_depth"]
    zs = axis_hits_from_above(mesh, L + 50)
    rep.add("blind top cavity: axis ray from above first meets cavity floor",
            zs.size >= 1 and abs(zs[0] - floor) < tol, f"axis crossings z={zs.round(3).tolist()} (floor {floor})")
    rep.add("closed bottom: exactly 2 axis crossings (cavity floor + base)", zs.size == 2 and abs(zs[-1]) < tol)
    rep.add("cavity mouth open (axis point just below top is outside)", not contains(mesh, [0.2, 0.1, L - 0.5]))
    rep.add("core below cavity is solid", contains(mesh, [0.2, 0.1, floor / 2]))
    rep.add("collar wall around cavity is solid", contains(mesh, [(p["r_cav"] + p["r_collar"]) / 2, 0.05, L - 1]))
    r = np.hypot(mesh.vertices[:, 0], mesh.vertices[:, 1])
    rep.add("outer radius <= max everywhere", float(r.max()) <= p["r_max"] + tol,
            f"max vertex radius {r.max():.4f} mm (limit {p['r_max']})")
    # Count ribs: radius sampled along a line at y~0 just outside valley radius.
    zz = np.linspace(0, L, 3601)
    probe_r = (p["r_valley"] + p["r_peak"]) / 2
    inside = np.array([contains(mesh, [probe_r, 0.01, z]) for z in zz])
    lo, hi = p["low"], L - p["collar"]
    band = inside[(zz > lo + 1e-6) & (zz < hi - 1e-6)]
    ribs = int(np.sum(np.diff(band.astype(int)) == 1) + (1 if band.size and band[0] else 0))
    rep.add("rib count in ribbed zone matches config (annular ridges)", ribs == p["n"],
            f"found {ribs} ridges at probe radius {probe_r:.2f} mm, expected {p['n']}")
    wall = (p["r_collar"] - p["r_cav"]) if p["cav_depth"] <= p["collar"] else (p["r_valley"] - p["r_cav"])
    rep.add("cavity wall thickness >= 1 mm", wall >= 1.0, f"min wall around cavity {wall:.2f} mm")
