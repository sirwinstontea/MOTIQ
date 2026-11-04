"""Robot-agnostic manipulation controller (simulation-only build).

This revision can drive the Mock backend and the RoboDK *simulator* only.
The physical backend exists as an interface but refuses every motion and
gripper command with ``PHYSICAL_BACKEND_DISABLED``.
"""

SCHEMA_VERSION = "1.0"
__version__ = "0.1.0"
