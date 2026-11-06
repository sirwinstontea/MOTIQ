# eyes/segmentation

Object masks, so measurements use only an object's pixels and not its whole box. No new neural network is involved: the mask comes from subtracting the empty table, or from GrabCut when no empty table is recorded.

| File | Purpose |
|---|---|
| `background.py` | `TableBackground`: median colour and depth of the empty table. Record it again if the camera moves. |
| `masks.py` | Pure functions: `mask_from_depth`, `mask_from_color`, `mask_grabcut`, `clean_mask` |
| `geometry.py` | Centroid, rotated rectangle, grasp angle and JSON polygon |
| `measure.py` | Z, real size in mm, surface centroid, grasp angle, monocular fallback |
| `attach.py` | `attach_masks`: adds `mask`, `mask_source` and measures to each object, with a per-source `MaskCache` |

Grasp angle follows `estimate_grasp_angle`: the long axis against image x, in degrees from -90 to 90, with y pointing down. Depth is taken on an eroded mask because edges contain flying pixels, and zeros are excluded.
