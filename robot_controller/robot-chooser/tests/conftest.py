import shutil
import sys
from pathlib import Path

import pytest

CHOOSER = Path(__file__).resolve().parents[1]
REPO = CHOOSER.parent.parent
sys.path.insert(0, str(CHOOSER))
sys.path.insert(0, str(REPO / "robot_controller"))

from robot_chooser.core import Chooser  # noqa: E402


@pytest.fixture
def repo(tmp_path) -> Path:
    """A throw-away copy of the parts of the MOTIQ repo the chooser reads and writes."""
    chooser_dir = tmp_path / "robot_controller" / "robot-chooser"
    chooser_dir.mkdir(parents=True)
    for f in ("catalog.yaml", "hardware.yaml"):
        shutil.copy(CHOOSER / f, chooser_dir / f)
    shutil.copytree(CHOOSER / "hardware_types", chooser_dir / "hardware_types")
    cfg = tmp_path / "robot_controller" / "config"
    cfg.mkdir(parents=True)
    for f in (REPO / "robot_controller" / "config").glob("*.example.yaml"):
        shutil.copy(f, cfg / f.name)
    st = tmp_path / "robot_controller" / "stations"
    st.mkdir()
    (st / "UR5e.robot").write_bytes(b"stub")          # presence is all the chooser checks
    return tmp_path


@pytest.fixture
def ch(repo) -> Chooser:
    c = Chooser(repo)
    c.update_hardware({"backend": "mock", "active_robot": "ur_ur5e", "speed_percent": 10})
    return c
