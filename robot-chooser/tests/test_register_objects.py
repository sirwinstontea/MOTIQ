"""Custom objects registry (no RoboDK required)."""
from __future__ import annotations

import json
import shutil

import yaml


def test_register_objects_writes_vocab_and_labels(ch, repo):
    (repo / "computer-vision" / "data").mkdir(parents=True, exist_ok=True)
    (repo / "computer-vision" / "logs").mkdir(parents=True, exist_ok=True)

    # Active generated scene (never mutate scene.example.yaml — it is BASE_SCENE for arm import).
    example = ch.ctrl_cfg / "scene.example.yaml"
    assert example.is_file()
    scene_path = ch.ctrl_cfg / "scene.yaml"
    shutil.copy(example, scene_path)
    before_example = example.read_text(encoding="utf-8")

    out = ch.register_objects({
        "objects": [
            {"name": "Red Mug", "aliases": ["mug", "cup"], "role": "pickable"},
            {"id": "blue_bin", "name": "Blue Bin", "role": "container"},
        ]
    })
    assert len(out["objects"]) == 2
    assert ch.objects_path().is_file()
    vocab = json.loads(ch.vocab_path().read_text(encoding="utf-8"))
    assert any("mug" in k for k in vocab) or "red mug" in vocab

    # example template must stay untouched
    assert example.read_text(encoding="utf-8") == before_example

    data = yaml.safe_load(scene_path.read_text(encoding="utf-8"))
    assert isinstance(data.get("objects"), dict)
    assert "red_mug" in data["objects"]
    # ObjectDef forbids unknown keys like "description"
    assert "description" not in data["objects"]["red_mug"]
    labels = data["objects"]["red_mug"].get("class_labels") or []
    assert any("mug" in str(x).lower() or "red" in str(x).lower() for x in labels)
