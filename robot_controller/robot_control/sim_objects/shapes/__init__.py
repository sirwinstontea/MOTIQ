"""Per-object geometry generators + validators, keyed by object id."""
from . import blue_bucket_01, green_ring_02, orange_screw_03, white_bucket_lid_04

SHAPES = {m.OBJECT_ID: m for m in (blue_bucket_01, green_ring_02, orange_screw_03, white_bucket_lid_04)}
