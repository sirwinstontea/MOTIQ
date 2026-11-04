"""Every documented number must be traceable to the downloaded official documents."""
import hashlib
from pathlib import Path

import yaml

from hardware_capability_check.profiles import DEFAULT_PROFILE_DIR, PROJECT_ROOT
from hardware_capability_check.provenance import verify_all, verify_profile_file


def test_all_profile_sources_are_found_in_official_documents():
    results = verify_all()
    assert len(results) >= 30
    bad = [r for r in results if not r.ok]
    assert not bad, "\n".join(f"{r.profile}:{r.path}: {r.detail}" for r in bad)


def test_verifier_actually_detects_a_wrong_quote(tmp_path):
    prof = yaml.safe_load((DEFAULT_PROFILE_DIR / "ur5e.yaml").read_text(encoding="utf-8"))
    prof["limits"]["reach_m"]["src"]["quote"] = "Reach 999 mm / 39.3 in"
    p = tmp_path / "ur5e.yaml"
    p.write_text(yaml.safe_dump(prof, allow_unicode=True), encoding="utf-8")
    docs = {k: dict(v) for k, v in prof["documents"].items()}
    results = verify_profile_file(p, docs)
    assert any((not r.ok) and r.path.endswith("reach_m.src") for r in results)


def test_declared_source_files_exist_with_hashes():
    prof = yaml.safe_load((DEFAULT_PROFILE_DIR / "ur5e.yaml").read_text(encoding="utf-8"))
    for doc_id, d in prof["documents"].items():
        f = PROJECT_ROOT / d["file"]
        assert f.exists(), f"{doc_id}: {f} missing"
        assert (PROJECT_ROOT / d["text"]).exists(), f"{doc_id}: text extract missing"
    sums = (PROJECT_ROOT / "docs" / "SOURCES.md").read_text(encoding="utf-8")
    for f in (PROJECT_ROOT / "docs" / "sources").glob("*.pdf"):
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        assert digest in sums, f"{f.name} sha256 missing from docs/SOURCES.md"


def test_payload_curve_matches_manual_values(profiles):
    r = profiles.robot
    assert r.rated_payload_kg(0.0) == 5.0
    assert r.rated_payload_kg(0.30) == 5.0
    assert abs(r.rated_payload_kg(0.50) - 3.74) < 0.05
    assert abs(r.rated_payload_kg(0.60) - 3.2) < 0.05
    assert r.rated_payload_kg(0.70) == 0.0       # beyond the plotted range: not extrapolated


def test_documented_values_loaded(profiles):
    r = profiles.robot
    assert r.reach_m == 0.85 and r.payload_rated_kg == 5.0
    assert r.joint_torque_max_nm == [150, 150, 150, 28, 28, 28]
    assert abs(r.joint_max_rad[2] - 160 * 3.141592653589793 / 180) < 1e-9     # elbow +-160 deg (manual)
    assert r.joint_max_rad[0] > 6.28
    assert r.d == [0.1625, 0.0, 0.0, 0.1333, 0.0997, 0.0996]
    assert r.a[1] == -0.425 and r.a[2] == -0.3922
