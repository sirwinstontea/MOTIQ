"""Custom objects registry (no RoboDK required)."""
from __future__ import annotations

import json

import yaml


def test_register_objects_writes_vocab_and_labels(ch, repo):
    (repo / "computer-vision" / "data").mkdir(parents=True, exist_ok=True)
    (repo / "computer-vision" / "logs").mkdir(parents=True, exist_ok=True)

    scene_path = ch.ctrl_cfg / "scene.example.yaml"
    assert scene_path.is_file()

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
    data = yaml.safe_load(scene_path.read_text(encoding="utf-8"))
    assert isinstance(data.get("objects"), dict)
    labels = []
    for o in data["objects"].values():
        labels.extend(o.get("class_labels") or [])
    assert any("mug" in str(x).lower() or "red" in str(x).lower() for x in labels) or "red_mug" in data["objects"]
