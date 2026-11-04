"""Shared geometry utilities for the demo simulation objects.

All lengths are millimetres. Every object is a solid of revolution of a closed
(radius, z) profile about local +Z, with its origin at the centre of the lowest
flat face (z = 0).

Only numpy + trimesh are needed (no rtree/embree): ray casting is done here with
a vectorised Moller-Trumbore test, so cavity checks run anywhere.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import trimesh

PACKAGE_ROOT = Path(__file__).resolve().parents[2]          # .../robot_controller
OBJECTS_DIR = PACKAGE_ROOT / "objects"
SPEC_FILE = OBJECTS_DIR / "object_specifications.json"

FLOAT_TOL_MM = 1e-3          # in-memory float tolerance
STL_TOL_MM = 2e-3            # STL stores float32


# --------------------------------------------------------------------------- config

def load_spec(spec_file: Path = SPEC_FILE) -> dict:
    return json.loads(Path(spec_file).read_text())


def config_path(entry: dict, spec_file: Path = SPEC_FILE) -> Path:
    return (Path(spec_file).parent / entry["config"]).resolve()


def load_object_config(entry: dict, spec_file: Path = SPEC_FILE) -> dict:
    p = config_path(entry, spec_file)
    cfg = json.loads(p.read_text())
    cfg["_config_path"] = str(p)
    if cfg.get("units") != "mm":
        raise ValueError(f"{p}: 'units' must be 'mm' (got {cfg.get('units')!r})")
    return cfg


def stl_path(cfg: dict) -> Path:
    return (Path(cfg["_config_path"]).parent / cfg["stl_file"]).resolve()


def dim(cfg: dict, key: str) -> float:
    """Value of a dimension field (mm unless the field states another unit)."""
    try:
        return float(cfg["dimensions"][key]["value"])
    except KeyError as e:
        raise KeyError(f"{cfg.get('name')}: missing dimensions.{key}.value") from e


def flag(cfg: dict, key: str) -> bool:
    return bool(cfg["dimensions"][key]["value"])


# --------------------------------------------------------------------------- revolve

def _clean_profile(profile) -> list[tuple[float, float]]:
    pts = [(float(r), float(z)) for r, z in profile]
    if len(pts) > 1 and math.isclose(pts[0][0], pts[-1][0]) and math.isclose(pts[0][1], pts[-1][1]):
        pts = pts[:-1]                                    # closed profile: drop repeated start point
    out: list[tuple[float, float]] = []
    for p in pts:
        if p[0] < -1e-9:
            raise ValueError(f"profile radius must be >= 0, got {p}")
        p = (max(p[0], 0.0), p[1])
        if not out or abs(out[-1][0] - p[0]) > 1e-9 or abs(out[-1][1] - p[1]) > 1e-9:
            out.append(p)
    if len(out) >= 2 and abs(out[0][0] - out[-1][0]) < 1e-9 and abs(out[0][1] - out[-1][1]) < 1e-9:
        out.pop()
    if len(out) < 3:
        raise ValueError("profile needs at least 3 distinct points")
    return out


def profile_signed_area(profile) -> float:
    pts = _clean_profile(profile)
    return 0.5 * sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                     for i in range(len(pts)))


def pappus_volume(profile) -> float:
    """Exact volume of the solid of revolution of a closed polygonal (r,z) profile."""
    pts = _clean_profile(profile)
    a = cr = 0.0
    for i in range(len(pts)):
        (r0, z0), (r1, z1) = pts[i], pts[(i + 1) % len(pts)]
        c = r0 * z1 - r1 * z0
        a += c
        cr += (r0 + r1) * c
    a *= 0.5
    centroid_r = cr / (6 * a)
    return abs(2 * math.pi * centroid_r * a)


@dataclass
class RevolveResult:
    mesh: trimesh.Trimesh
    profile: list[tuple[float, float]]
    sections: int
    winding_flipped: bool


def revolve_profile(profile, sections: int) -> RevolveResult:
    """Revolve a closed (r,z) polygon about +Z into a watertight mesh.

    Points with r == 0 collapse to a single axis vertex (triangle fan), so no
    zero-area faces or duplicate vertices are produced. Vertices sit exactly on
    the true radius, so the polygonal approximation never exceeds a diameter.
    Orientation of the input profile does not matter: if the result has
    negative volume, face winding is inverted (vertices unchanged, so
    dimensions are unaffected) and this is reported.
    """
    if sections < 16 or sections % 4:
        raise ValueError("angular_segments must be >= 16 and divisible by 4 (keeps AABB exact on X and Y)")
    pts = _clean_profile(profile)
    ang = np.arange(sections) * (2 * math.pi / sections)
    ca, sa = np.cos(ang), np.sin(ang)

    verts: list = []
    rings: list = []
    for r, z in pts:
        if r == 0.0:
            rings.append(len(verts))                      # single axis vertex
            verts.append([0.0, 0.0, z])
        else:
            start = len(verts)
            verts.extend(np.column_stack([r * ca, r * sa, np.full(sections, z)]).tolist())
            rings.append(np.arange(start, start + sections))

    faces = []
    k = np.arange(sections)
    k1 = (k + 1) % sections
    for i in range(len(pts)):
        a, b = rings[i], rings[(i + 1) % len(pts)]
        a_axis, b_axis = np.isscalar(a) or isinstance(a, int), np.isscalar(b) or isinstance(b, int)
        if a_axis and b_axis:
            continue                                      # segment along the axis: no surface
        if a_axis:
            faces.append(np.column_stack([np.full(sections, a), b[k], b[k1]]))
        elif b_axis:
            faces.append(np.column_stack([a[k], np.full(sections, b), a[k1]]))
        else:
            faces.append(np.column_stack([a[k], b[k], b[k1]]))
            faces.append(np.column_stack([a[k], b[k1], a[k1]]))
    mesh = trimesh.Trimesh(vertices=np.asarray(verts, float), faces=np.vstack(faces), process=True)
    flipped = False
    if mesh.volume < 0:
        mesh.invert()
        flipped = True
    return RevolveResult(mesh=mesh, profile=pts, sections=sections, winding_flipped=flipped)


# --------------------------------------------------------------------------- ray tests

def ray_hits(mesh: trimesh.Trimesh, origin, direction, eps: float = 1e-9) -> np.ndarray:
    """Sorted distances (t > 0) at which a ray crosses the mesh surface."""
    o = np.asarray(origin, float)
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    tri = mesh.triangles
    v0, e1, e2 = tri[:, 0], tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    p = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > eps
    inv = np.zeros_like(det)
    inv[ok] = 1.0 / det[ok]
    s = o - v0
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    v = (q @ d) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    hit = ok & (u >= -1e-12) & (v >= -1e-12) & (u + v <= 1 + 1e-12) & (t > 1e-9)
    ts = np.sort(t[hit])
    if ts.size:                                           # merge duplicates from shared edges
        ts = ts[np.concatenate([[True], np.diff(ts) > 1e-7])]
    return ts


_CONTAINS_DIR = np.array([0.8191, 0.3907, 0.4210])      # deliberately "irrational" direction


def contains(mesh: trimesh.Trimesh, point) -> bool:
    """Point-in-solid test by ray-crossing parity (no rtree needed)."""
    return len(ray_hits(mesh, point, _CONTAINS_DIR)) % 2 == 1


def axis_hits_from_above(mesh: trimesh.Trimesh, z_start: float, xy=(0.137, 0.091)) -> np.ndarray:
    """z-heights where a downward ray just off the symmetry axis crosses the surface."""
    ts = ray_hits(mesh, [xy[0], xy[1], z_start], [0, 0, -1])
    return z_start - ts


# --------------------------------------------------------------------------- checks

@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ObjectReport:
    object_id: str
    name: str
    checks: list[Check] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    def add(self, name: str, passed, detail: str = "") -> None:
        self.checks.append(Check(name, bool(passed), detail))


def common_mesh_checks(rep: ObjectReport, mesh: trimesh.Trimesh, expected_extents_mm, tol: float,
                       label: str = "") -> None:
    """Watertight, winding, normals, volume, AABB and unit-scale checks."""
    pre = f"{label}: " if label else ""
    rep.add(pre + "watertight", mesh.is_watertight)
    rep.add(pre + "winding consistent", mesh.is_winding_consistent)
    rep.add(pre + "is_volume (outward normals, closed, manifold)", mesh.is_volume)
    rep.add(pre + "positive volume", mesh.volume > 0, f"{mesh.volume:.1f} mm^3")
    rep.add(pre + "no degenerate faces", float(mesh.area_faces.min()) > 1e-10,
            f"min face area {mesh.area_faces.min():.3e} mm^2")
    ex = np.asarray(expected_extents_mm, float)
    lo, hi = mesh.bounds
    expect_lo = np.array([-ex[0] / 2, -ex[1] / 2, 0.0])
    expect_hi = np.array([ex[0] / 2, ex[1] / 2, ex[2]])
    ok = np.allclose(lo, expect_lo, atol=tol) and np.allclose(hi, expect_hi, atol=tol)
    rep.add(pre + "AABB matches expected (origin at bottom-centre, z>=0)", ok,
            f"min {np.round(lo, 3).tolist()} max {np.round(hi, 3).tolist()} mm; "
            f"expected extents {ex.tolist()} mm")
    ext = mesh.extents
    # A metre-scaled mesh would be ~1000x smaller; a doubled mm->m conversion ~1000x larger.
    ratio = ext / ex
    rep.add(pre + "unit scale is millimetres (not metres)", np.all(np.abs(ratio - 1) < 0.01),
            f"extent/expected ratio {np.round(ratio, 5).tolist()}")


def fmt_bool(b: bool) -> str:
    return "PASS" if b else "FAIL"


ShapeProfile = Callable[[dict], list]
ShapeChecks = Callable[[trimesh.Trimesh, dict, ObjectReport], None]
