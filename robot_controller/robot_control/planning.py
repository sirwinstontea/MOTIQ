"""Pure geometry helpers for the existing executor; metres/base frame, no robot execution."""
import math

from .models import ErrorCode, Pose, RobotControlError

DIRECTIONS = {"left": (0, 1), "right": (0, -1), "forward": (1, 0), "backward": (-1, 0)}


def table_surface(scene, x, y, dimensions, yaw_rad=0.0):
    table = scene.station_geometry.get(scene.table_geometry)
    if table is None:
        raise RobotControlError(ErrorCode.CONFIG_ERROR, "configured table geometry missing")
    if any(abs(v) > 1e-8 for v in table.pose_m_rad[3:]):
        raise RobotControlError(ErrorCode.UNSUPPORTED, "table placement currently requires an axis-aligned horizontal table")
    cx, cy, z = table.pose_m_rad[:3]
    # Exact horizontal bounding rectangle for the configured box footprint at this yaw.
    c, s = abs(math.cos(yaw_rad)), abs(math.sin(yaw_rad))
    hx = (c * dimensions[0] + s * dimensions[1]) / 2
    hy = (s * dimensions[0] + c * dimensions[1]) / 2
    if (abs(x - cx) + hx > table.size_m[0] / 2 + 1e-9 or
            abs(y - cy) + hy > table.size_m[1] / 2 + 1e-9):
        raise RobotControlError(ErrorCode.SAFETY_VETO, "object footprint would extend beyond the table")
    return z + table.size_m[2]


def circle_waypoints(center, radius, z, clockwise, current, max_chord_error_m=None):
    # Bound polygon approximation error, not motion distance. Cap computational work explicitly.
    if max_chord_error_m is None:
        segments = max(24, math.ceil(2 * math.pi * radius / 0.01))
    else:
        # Sagitta = r * (1-cos(pi/n)); asin form avoids subtractive cancellation.
        half_angle = 2 * math.asin(math.sqrt(min(max_chord_error_m / radius, 1.0) / 2))
        if half_angle == 0:
            raise RobotControlError(ErrorCode.UNSUPPORTED, "circle tolerance below numerical resolution")
        segments = max(24, math.ceil(math.pi / half_angle))
    if segments > 256:
        raise RobotControlError(ErrorCode.UNSUPPORTED, "circle requires more than 256 checked waypoints; use explicit smaller path sections")
    start = math.atan2(current.y - center[1], current.x - center[0])
    sign = -1 if clockwise else 1
    return [Pose(x=center[0] + radius * math.cos(start + sign * 2 * math.pi * i / segments),
                 y=center[1] + radius * math.sin(start + sign * 2 * math.pi * i / segments), z=z,
                 rx=current.rx, ry=current.ry, rz=current.rz) for i in range(segments + 1)]
