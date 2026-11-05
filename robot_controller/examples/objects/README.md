# examples/objects

Repeatable tasks for the four demo objects. Each object goes back to its own slot, so no reset is needed between runs.

| File | Actions |
|---|---|
| `blue_bucket_01_pick_place.json` | Identify, pick from its slot, place back, home |
| `green_ring_02_pick_place.json` | Same |
| `orange_screw_03_pick_place.json` | Same |
| `white_bucket_lid_04_pick_place.json` | Same |
| `all_objects_pick_place.json` | The same for all four in turn (13 actions), then home |

```bash
python -m robot_control.cli demo --backend mock --task examples/objects/all_objects_pick_place.json
```
