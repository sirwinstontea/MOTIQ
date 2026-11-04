"""Build (in memory) and validate one object from its config."""
from __future__ import annotations

import math
from pathlib import Path

import trimesh

from .core import (FLOAT_TOL_MM, STL_TOL_MM, ObjectReport, RevolveResult, common_mesh_checks, dim,
                   pappus_volume, revolve_profile, stl_path)
from .shapes import SHAPES


def shape_for(cfg: dict):
    try:
        return SHAPES[cfg["id"]]
    except KeyError:
        raise KeyError(f"no geometry module for object id {cfg.get('id')!r}; known: {sorted(SHAPES)}") from None


def build_mesh(cfg: dict) -> RevolveResult:
    shape = shape_for(cfg)
    return revolve_profile(shape.profile(cfg), int(cfg["mesh"]["angular_segments"]))


def config_checks(rep: ObjectReport, cfg: dict) -> None:
    shape = shape_for(cfg)
    for key, val in shape.derived(cfg).items():
        stored = dim(cfg, key)
        rep.add(f"derived field {key} consistent", math.isclose(stored, val, abs_tol=1e-6),
                f"stored {stored}, computed {val:g}")
    exp = shape.expected_extents(cfg)
    rec = [float(x) for x in cfg["expected_bounds_mm"]]
    rep.add("expected_bounds_mm consistent with dimensions", all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(rec, exp)),
            f"recorded {rec}, from dimensions {exp}")
    bad = [k for k, v in cfg["dimensions"].items()
           if v.get("status") not in ("measured", "derived", "interpreted", "estimated")]
    rep.add("every dimension has a provenance status", not bad, f"missing/invalid: {bad}" if bad else "")


def validate_object(cfg: dict, check_stl: bool = True) -> ObjectReport:
    """Validate generated geometry and (if present) the STL on disk."""
    shape = shape_for(cfg)
    rep = ObjectReport(cfg["id"], cfg["name"])
    config_checks(rep, cfg)
    res = build_mesh(cfg)
    m = res.mesh
    ext = shape.expected_extents(cfg)
    common_mesh_checks(rep, m, ext, FLOAT_TOL_MM, "generated")
    vp = pappus_volume(res.profile)
    n = res.sections
    poly_factor = n * math.sin(2 * math.pi / n) / (2 * math.pi)        # inscribed-polygon area ratio
    rep.add("generated volume matches analytic solid of revolution", abs(m.volume / (vp * poly_factor) - 1) < 1e-6,
            f"mesh {m.volume:.1f} vs analytic {vp:.1f} mm^3 (polygon factor {poly_factor:.6f})")
    shape.cavity_checks(m, cfg, rep, FLOAT_TOL_MM)
    rep.stats.update(vertices=len(m.vertices), faces=len(m.faces), volume_mm3=round(float(m.volume), 2),
                     bounds_mm=[[round(float(x), 4) for x in r] for r in m.bounds],
                     winding_flipped_during_build=res.winding_flipped, angular_segments=n)

    p = stl_path(cfg)
    rep.stats["stl_file"] = str(p)
    if not check_stl:
        rep.stats["stl_validated"] = "NOT RUN (check_stl=False)"
        return rep
    if not p.is_file():
        rep.add("STL file exists", False, f"{p} missing: run generate_all first")
        rep.stats["stl_validated"] = "NOT RUN (file missing)"
        return rep
    s = trimesh.load(str(p), force="mesh")
    common_mesh_checks(rep, s, ext, STL_TOL_MM, "STL on disk")
    rep.add("STL on disk is up to date with config (volume, face count)",
            len(s.faces) == len(m.faces) and abs(s.volume / m.volume - 1) < 1e-4,
            f"STL {len(s.faces)} faces / {s.volume:.1f} mm^3 vs config {len(m.faces)} / {m.volume:.1f}")
    stl_rep = ObjectReport(cfg["id"], cfg["name"])
    shape.cavity_checks(s, cfg, stl_rep, STL_TOL_MM)
    for c in stl_rep.checks:
        rep.add("STL on disk: " + c.name, c.passed, c.detail)
    rep.stats["stl_validated"] = "yes"
    return rep


def export_stl(cfg: dict) -> tuple[Path, RevolveResult]:
    res = build_mesh(cfg)
    if not (res.mesh.is_watertight and res.mesh.is_winding_consistent and res.mesh.volume > 0):
        raise RuntimeError(f"{cfg['name']}: generated mesh is not a valid closed volume; not exporting")
    p = stl_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = trimesh.exchange.stl.export_stl(res.mesh)        # binary STL, coordinates in mm
    p.write_bytes(data)
    return p, res
