# shapes

One module per demo object. Each exposes `derived`, `expected_extents`, `profile` and `cavity_checks`.

| File | Object | Notes |
|---|---|---|
| `blue_bucket_01.py` | Open-top cup | Constant 139 mm outer diameter, 8 mm floor |
| `green_ring_02.py` | Flat ring | OD 139 mm, 78 mm hole, 7 mm thick |
| `orange_screw_03.py` | Ribbed screw | Only length (90) and max diameter (30) are measured. The rest is estimated. |
| `white_bucket_lid_04.py` | Flange and truncated cone | 30 mm hole. The 25 mm drawing dimension is interpreted as flange width. |

Dimensions live in `objects/<id>/config/<id>.json`, not here.
