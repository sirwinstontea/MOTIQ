"""Which parts of the skill file are physically evaluated, which are descriptive, which are not understood (and reported)."""
from __future__ import annotations

import re

EVALUATED = [
    r"^environment\.(units|coordinate_system)$", r"^environment\.objects\[\d+\]\.(id|name|category)$",
    r"^environment\.objects\[\d+\]\.attributes\.(dimensions|size|inner_size|wall_height|mass|mass_kg|mass_g|weight|weight_kg)",
    r"^environment\.objects\[\d+\]\.initial_state\.location", r"^environment\.objects\[\d+\]\.initial_state\.orientation",
    r"^environment\.objects\[\d+\]\.detection\.", r"^constraints\.object_constraints\[\d+\]\.",
    r"^constraints\.execution_constraints\.", r"^constraints\.timing",
    r"^steps\[\d+\]\.(action|target|failure_policy|parameters|primitive_sequence)",
    r"^steps\[\d+\]\.(preconditions|postconditions|success_conditions)\[\d+\]$",
    r"^success_criteria\.\w+_verified$", r"^robot\.", r"^action_primitives\[\d+\]\.(id|parameters|urscript|direction)",
]
DESCRIPTIVE = [
    r"^schema_version$", r"^skill\.", r"^intent\.", r"^interpretation\.", r"^safety\.", r"^review\.",
    r"^environment\.workspace\.", r"^environment\.objects\[\d+\]\.attributes\.(color|shape)$",
    r"^success_criteria\.all_steps_completed$", r"^steps\[\d+\]\.(id|description|name)$",
    r"^action_primitives\[\d+\]\.(description|note|voice_aliases_it)", r"^action_primitives\[\d+\]\.",
]


def leaves(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from leaves(v, f"{path}.{k}" if path else str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from leaves(v, f"{path}[{i}]")
    else:
        yield path


def coverage(raw: dict) -> dict:
    ev, de, miss = 0, 0, []
    for p in leaves(raw):
        if any(re.search(r, p) for r in EVALUATED):
            ev += 1
        elif any(re.search(r, p) for r in DESCRIPTIVE):
            de += 1
        else:
            miss.append(p)
    return {"evaluated_fields": ev, "descriptive_only_fields": de, "not_evaluated_fields": miss}
