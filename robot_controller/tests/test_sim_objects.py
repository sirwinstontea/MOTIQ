"""Offline tests for the demo simulation objects (no RoboDK needed)."""
import copy
import json
import socket

import pytest

pytest.importorskip("trimesh")

from robot_control.sim_objects import robodk_import  # noqa: E402
from robot_control.sim_objects.build import build_mesh, export_stl, validate_object  # noqa: E402
from robot_control.sim_objects.core import (contains, load_object_config, load_spec, pappus_volume,  # noqa: E402
                                            revolve_profile)

SPEC = load_spec()
CFGS = {e["id"]: load_object_config(e) for e in SPEC["objects"]}


def test_four_objects_with_expected_names():
    assert [c["name"] for c in CFGS.values()] == ["Blue_Bucket_01", "Green_Ring_02", "Orange_Screw_03",
                                                  "White_Bucket_Lid_04"]


@pytest.mark.parametrize("oid", list(CFGS))
def test_generated_geometry_validates(oid):
    rep = validate_object(CFGS[oid], check_stl=False)
    failed = [(c.name, c.detail) for c in rep.checks if not c.passed]
    assert not failed, failed


@pytest.mark.parametrize("oid", list(CFGS))
def test_stl_export_roundtrip(oid, tmp_path):
    cfg = copy.deepcopy(CFGS[oid])
    cfg["_config_path"] = str(tmp_path / "config" / "x.json")      # stl_file is ../model/<id>.stl
    p, _ = export_stl(cfg)
    assert p.is_file() and str(p).startswith(str(tmp_path))
    rep = validate_object(cfg, check_stl=True)
    assert rep.passed, [(c.name, c.detail) for c in rep.checks if not c.passed]


def test_user_corrected_dimensions():
    blue, green = CFGS["blue_bucket_01"], CFGS["green_ring_02"]
    assert blue["dimensions"]["outer_diameter_mm"]["value"] == 139
    assert green["dimensions"]["through_hole_diameter_mm"]["value"] == 78
    m = build_mesh(blue).mesh
    assert m.extents[0] == pytest.approx(139, abs=1e-6)


def test_profile_orientation_does_not_matter():
    prof = [(0, 0), (69.5, 0), (69.5, 115), (52.5, 115), (52.5, 8), (0, 8), (0, 0)]
    a = revolve_profile(prof, 64)
    b = revolve_profile(list(reversed(prof)), 64)
    assert a.mesh.volume == pytest.approx(b.mesh.volume) and a.mesh.volume > 0
    assert a.mesh.is_volume and b.mesh.is_volume
    assert a.winding_flipped != b.winding_flipped


def test_pappus_cylinder():
    assert pappus_volume([(0, 0), (10, 0), (10, 5), (0, 5)]) == pytest.approx(3.141592653589793 * 100 * 5)


def test_inconsistent_derived_field_is_flagged():
    cfg = copy.deepcopy(CFGS["green_ring_02"])
    cfg["dimensions"]["through_hole_diameter_mm"]["value"] = 88       # derived width / hole check now stale
    rep = validate_object(cfg, check_stl=False)
    assert not rep.passed
    assert any("radial_width" in c.name and not c.passed for c in rep.checks)


def test_orange_estimated_params_are_editable():
    cfg = copy.deepcopy(CFGS["orange_screw_03"])
    d = cfg["dimensions"]
    d["rib_count"]["value"] = 6
    d["rib_pitch_mm"]["value"] = d["rib_zone_length_mm"]["value"] / 6
    rep = validate_object(cfg, check_stl=False)
    assert rep.passed, [(c.name, c.detail) for c in rep.checks if not c.passed]
    for k in ("rib_count", "valley_diameter_mm", "cavity_depth_mm", "cavity_opening_diameter_mm"):
        assert d[k]["status"] == "estimated"


def test_orange_radius_limit_enforced():
    cfg = copy.deepcopy(CFGS["orange_screw_03"])
    cfg["dimensions"]["rib_peak_diameter_mm"]["value"] = 31
    with pytest.raises(ValueError):
        build_mesh(cfg)


def test_contains_on_bucket():
    m = build_mesh(CFGS["blue_bucket_01"]).mesh
    assert contains(m, [0.2, 0.1, 4]) and not contains(m, [0.2, 0.1, 60])


def test_dry_run_does_not_touch_robodk(monkeypatch, capsys):
    import builtins
    real_import = builtins.__import__

    def guard(name, *a, **k):
        if name.startswith("robodk"):
            raise AssertionError("dry-run imported robodk")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", guard)
    rc = robodk_import.main(["--dry-run"])
    out = capsys.readouterr().out
    assert "DRY RUN" in out and out.count("would import") == 4
    assert rc in (0, 1)                     # 1 only if STLs were not generated yet


def test_import_reports_missing_robodk_gracefully(tmp_path):
    with socket.socket() as s:              # find a closed port
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    spec = json.loads(json.dumps(SPEC))
    for e in spec["objects"]:
        e["config"] = str((robodk_import.SPEC_FILE.parent / e["config"]).resolve())
        cfg = load_object_config(e)
        cfg["_config_path"] = str(tmp_path / e["id"] / "config" / "x.json")
        export_stl(cfg)
        c = json.loads(open(e["config"]).read())
        c["stl_file"] = str(tmp_path / e["id"] / "model" / f"{e['id']}.stl")
        p = tmp_path / f"{e['id']}.json"
        p.write_text(json.dumps(c))
        e["config"] = str(p)
    sf = tmp_path / "spec.json"
    sf.write_text(json.dumps(spec))
    lines = []
    rc = robodk_import.run_import(sf, port=port, out=lines.append)
    assert rc == 2 and any("NOT CONNECTED" in ln for ln in lines)
