"""Per tutti i test: la selezione per il braccio va in una cartella temporanea, non nel vero logs/selection.json."""
import pytest

from vision.grok import voice_tools


@pytest.fixture(autouse=True)
def selection_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_tools, "SELECTION_PATH", tmp_path / "selection.json")
