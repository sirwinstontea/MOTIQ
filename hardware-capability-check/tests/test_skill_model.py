import numpy as np

from hardware_capability_check.skill_model import parse_skill, parse_size, quantity_in, MASS_TO_KG


def test_example_skill_is_parsed(example_dict):
    s = parse_skill(example_dict)
    assert [st.action for st in s.steps] == ["DETECT", "PICK", "PLACE", "VERIFY"]
    cube, box = s.objects["object_cube"], s.objects["object_box"]
    assert np.allclose(cube.position, [0.30, 0.15, 0.02])
    assert cube.size_basis == "inferred" and np.allclose(cube.size, 0.04)
    assert box.is_container and box.size is None
    assert s.frame == "world" and s.unit_scale == 1.0 and not s.issues
    assert s.object_by_ref("red cube") is cube and s.object_by_ref("object_box") is box


def test_units_are_converted(example_dict):
    example_dict["environment"]["units"] = "millimeters"
    for o in example_dict["environment"]["objects"]:
        loc = o["initial_state"]["location"]
        for k in loc:
            loc[k] = loc[k] * 1000
    s = parse_skill(example_dict)
    assert np.allclose(s.objects["object_cube"].position, [0.30, 0.15, 0.02])


def test_unknown_units_and_frame_are_reported_not_crashed(example_dict):
    example_dict["environment"]["units"] = "furlongs"
    example_dict["environment"]["coordinate_system"] = "galaxy"
    s = parse_skill(example_dict)
    assert any("furlongs" in i for i in s.issues) and any("galaxy" in i for i in s.issues)


def test_garbage_does_not_crash():
    s = parse_skill({"steps": [1, {"nonsense": True}], "environment": {"objects": ["x", {"id": 5}]}})
    assert s.issues
    s2 = parse_skill({})
    assert s2.steps == [] and s2.issues


def test_mass_and_size_aliases():
    assert quantity_in({"mass_g": 250}, ("mass", "weight"), MASS_TO_KG, "kg") == 0.25
    assert quantity_in({"weight": {"value": 2, "unit": "lb"}}, ("mass", "weight"), MASS_TO_KG, "kg") - 0.907 < 1e-3
    assert quantity_in({"mass": "heavy"}, ("mass",), MASS_TO_KG, "kg") is None
    assert np.allclose(parse_size({"width": 0.1, "depth": 0.2, "height": 0.3}, 1.0), [0.1, 0.2, 0.3])
    assert np.allclose(parse_size({"edge_length": 4}, 0.01), [0.04, 0.04, 0.04])
    assert np.allclose(parse_size([1, 2, 3], 0.001), [0.001, 0.002, 0.003])
    assert parse_size("big", 1.0) is None


def test_object_stated_dimensions_and_mass(example_dict):
    o = example_dict["environment"]["objects"][0]
    o["attributes"]["dimensions"] = {"x": 0.05, "y": 0.05, "z": 0.05}
    o["attributes"]["mass_g"] = 120
    s = parse_skill(example_dict)
    cube = s.objects["object_cube"]
    assert cube.size_basis == "stated" and np.allclose(cube.size, 0.05) and abs(cube.mass_kg - 0.12) < 1e-9
