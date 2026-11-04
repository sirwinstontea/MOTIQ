"""Loading and querying hardware profiles (robot arm, tool, camera, work cell).

Profiles are plain YAML (see ``profiles/``). They are intentionally data, not code: adapting the
checker to another UR arm (or later another robot family) means writing another YAML.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .kinematics import URKinematics

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_DIR = PROJECT_ROOT / "profiles"


class ProfileError(ValueError):
    """A profile file is missing or malformed."""


def _load_yaml(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ProfileError(f"profile file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ProfileError(f"profile file is not valid YAML: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"profile file must contain a mapping at top level: {path}")
    return data


def _need(d: dict, *keys: str, where: str = "profile") -> Any:
    cur: Any = d
    trail = []
    for k in keys:
        trail.append(k)
        if not isinstance(cur, dict) or k not in cur:
            raise ProfileError(f"{where}: missing required field '{'.'.join(trail)}'")
        cur = cur[k]
    return cur


def _val(node: Any) -> Any:
    """Documented values are stored as {value: x, src: ...}; plain scalars are accepted too."""
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


# ------------------------------------------------------------------------------------------ robot
@dataclass
class RobotProfile:
    raw: dict
    id: str
    name: str
    d: list[float]
    a: list[float]
    alpha: list[float]
    joint_min_rad: list[float]
    joint_max_rad: list[float]
    joint_speed_max_rad_s: float
    joint_torque_max_nm: list[float]
    torque_usable_fraction: float
    link_masses: list[float]
    link_com: list[list[float]]
    payload_rated_kg: float
    payload_curve_mm_kg: list[tuple[float, float]]
    reach_m: float
    tcp_speed_max_m_s: float
    repeatability_mm: float
    ft_range_n: float
    ft_accuracy_n: float
    footprint_diameter_m: float
    segment_radius_m: list[float]
    table_clearance_margin_m: float
    tool_io: dict
    tool_power: dict
    conflicts: list[dict]

    @classmethod
    def from_dict(cls, data: dict) -> "RobotProfile":
        w = "robot profile"
        kin = _need(data, "kinematics", where=w)
        lim = _need(data, "limits", where=w)
        phys = _need(data, "physical", where=w)
        ranges = _need(lim, "joint_position_deg", where=w)
        if len(ranges) != 6:
            raise ProfileError(f"{w}: limits.joint_position_deg needs 6 [min,max] pairs")
        curve = [(float(x), float(y)) for x, y in _need(lim, "payload_vs_cog_offset", "points_mm_kg", where=w)]
        if sorted(curve) != curve:
            raise ProfileError(f"{w}: payload curve must be sorted by CoG offset")
        family = str(kin.get("family", "ur")).lower()
        if family != "ur":
            raise ProfileError(f"{w}: kinematics.family '{family}' is not supported yet - only UR-style arms (6 rotary joints, "
                               "spherical wrist, UR DH layout: UR3e/5e/10e/16e/20/30 and look-alikes). See README 'Using another robot'.")
        d = [float(v) for v in _need(kin, "d_m", where=w)]
        a = [float(v) for v in _need(kin, "a_m", where=w)]
        alpha = [float(v) for v in _need(kin, "alpha_rad", where=w)]
        if not (len(d) == len(a) == len(alpha) == 6):
            raise ProfileError(f"{w}: DH tables need 6 entries each")
        return cls(
            raw=data,
            id=str(_need(data, "robot", "id", where=w)),
            name=str(_need(data, "robot", "name", where=w)),
            d=d,
            a=a,
            alpha=alpha,
            joint_min_rad=[math.radians(float(r[0])) for r in ranges],
            joint_max_rad=[math.radians(float(r[1])) for r in ranges],
            joint_speed_max_rad_s=math.radians(float(_val(_need(lim, "joint_speed_max_deg_s", where=w)))),
            joint_torque_max_nm=[float(v) for v in _need(lim, "joint_torque_max_nm", where=w)],
            torque_usable_fraction=float(_need(lim, "torque_usable_fraction", where=w)),
            link_masses=[float(v) for v in _need(kin, "link_masses_kg", where=w)],
            link_com=[[float(c) for c in v] for v in _need(kin, "link_com_m", where=w)],
            payload_rated_kg=float(_val(_need(lim, "payload_rated_kg", where=w))),
            payload_curve_mm_kg=curve,
            reach_m=float(_val(_need(lim, "reach_m", where=w))),
            tcp_speed_max_m_s=float(_val(_need(lim, "tcp_speed_max_m_s", where=w))),
            repeatability_mm=float(_val(_need(lim, "pose_repeatability_mm", where=w))),
            ft_range_n=float(_need(lim, "force_torque_sensor", "range_n", where=w)),
            ft_accuracy_n=float(_need(lim, "force_torque_sensor", "accuracy_n", where=w)),
            footprint_diameter_m=float(_val(_need(phys, "footprint_diameter_m", where=w))),
            segment_radius_m=[float(v) for v in _need(phys, "collision_model", "segment_radius_m", where=w)],
            table_clearance_margin_m=float(_need(phys, "collision_model", "table_clearance_margin_m", where=w)),
            tool_io=dict(_need(data, "tool_interface", "io", where=w)),
            tool_power=dict(_need(data, "tool_interface", "power", where=w)),
            conflicts=list(data.get("conflicts", [])),
        )

    def kinematics(self) -> URKinematics:
        return URKinematics(
            d=self.d,
            a=self.a,
            alpha=self.alpha,
            joint_min=self.joint_min_rad,
            joint_max=self.joint_max_rad,
            link_mass=self.link_masses,
            link_com=self.link_com,
        )

    def rated_payload_kg(self, cog_offset_m: float) -> float:
        """Rated payload [kg] at a CoG offset [m] from the flange centre (linear interpolation of the
        manual's curve; beyond the plotted range the last point is NOT extrapolated -> 0 kg)."""
        mm = cog_offset_m * 1000.0
        xs = [p[0] for p in self.payload_curve_mm_kg]
        ys = [p[1] for p in self.payload_curve_mm_kg]
        if mm < 0:
            mm = abs(mm)
        if mm > xs[-1]:
            return 0.0
        return float(np.interp(mm, xs, ys))


# ------------------------------------------------------------------------------------------ tool
@dataclass
class ToolProfile:
    raw: dict
    id: str
    name: str
    actuated: bool
    actuation_kind: str
    opening_max_m: float | None
    opening_min_m: float | None
    grip_force_n: float | None
    control_interface: str | None
    passive_modes: list[str]
    mass_kg: float | None
    cog_offset_m: float | None
    tcp_offset_m: float | None
    tcp_offset_search_m: tuple[float, float]
    envelope_radius_m: float | None
    envelope_length_m: float | None

    @classmethod
    def from_dict(cls, data: dict) -> "ToolProfile":
        w = "tool profile"
        t = _need(data, "tool", where=w)
        act = _need(t, "actuation", where=w)
        search = t.get("tcp_offset_search_m") or [0.05, 0.30]
        env = t.get("envelope") or {}

        def opt(x):
            return None if x is None else float(x)

        return cls(
            raw=data,
            id=str(_need(t, "id", where=w)),
            name=str(t.get("name", t["id"])),
            actuated=bool(_need(act, "actuated", where=w)),
            actuation_kind=str(act.get("kind", "none")),
            opening_max_m=opt(act.get("opening_max_m")),
            opening_min_m=opt(act.get("opening_min_m")),
            grip_force_n=opt(act.get("grip_force_n")),
            control_interface=act.get("control_interface"),
            passive_modes=[str(m) for m in (t.get("passive_modes") or [])],
            mass_kg=opt(t.get("mass_kg")),
            cog_offset_m=opt(t.get("cog_offset_from_flange_m")),
            tcp_offset_m=opt(t.get("tcp_offset_m")),
            tcp_offset_search_m=(float(search[0]), float(search[1])),
            envelope_radius_m=opt(env.get("radius_m")),
            envelope_length_m=opt(env.get("length_m")),
        )


# ------------------------------------------------------------------------------------------ camera
# NOTE - TWO CAMERAS: the project has two Intel RealSense cameras.
#   * D415 (profiles/camera_realsense_d415.yaml) = computer vision in the execution cell -> `Profiles.camera`,
#     the only camera the capability verdict (DETECT / VERIFY coverage) is computed against.
#   * D435 (profiles/camera_realsense_d435_demo.yaml) = camera that recorded the demonstration used to make
#     skill.json -> `Profiles.demo_camera`, informational only.
# It is still UNSURE whether skill.json files will be created with both cameras or just one. Nothing in the
# checker depends on that; if it is decided, only the profile selection (CLI --camera / --demo-camera) changes.
@dataclass
class CameraProfile:
    raw: dict
    id: str
    name: str
    fov_h_deg: float
    fov_v_deg: float
    resolution_px: tuple[int, int]
    ideal_range_m: tuple[float, float]
    mount: dict | None
    role: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "CameraProfile":
        w = "camera profile"
        c = _need(data, "camera", where=w)
        dep = _need(c, "depth", where=w)
        rng = _need(dep, "ideal_range_m", where=w)
        res = _need(dep, "resolution_px", where=w)
        return cls(
            raw=data,
            id=str(_need(c, "id", where=w)),
            name=str(c.get("name", c["id"])),
            role=c.get("role"),
            fov_h_deg=float(_need(dep, "fov_deg", "horizontal", where=w)),
            fov_v_deg=float(_need(dep, "fov_deg", "vertical", where=w)),
            resolution_px=(int(res[0]), int(res[1])),
            ideal_range_m=(float(rng[0]), float(rng[1])),
            mount=c.get("mount"),
        )


# ------------------------------------------------------------------------------------------ cell
@dataclass
class CellProfile:
    raw: dict
    base_position_m: np.ndarray
    base_yaw_rad: float
    base_status: str
    mounting: str
    mounting_status: str
    table_z_m: float
    table_status: str
    reach_margin_m: float
    approach_clearance_m: float
    retreat_clearance_m: float
    transit_clearance_m: float
    sample_step_m: float
    max_joint_step_rad: float
    max_wrist_joint_step_rad: float
    sing_warn: float
    yaw_candidates_rad: list[float]
    scan_container_height_m: list[float]
    scan_object_size_m: list[float]

    @classmethod
    def from_dict(cls, data: dict) -> "CellProfile":
        w = "cell profile"
        c = _need(data, "cell", where=w)
        base = _need(c, "robot_base_in_world", where=w)
        md = _need(c, "motion_defaults", where=w)
        scan = c.get("unknown_dimension_scan", {})
        return cls(
            raw=data,
            base_position_m=np.array([float(v) for v in _need(base, "position_m", where=w)]),
            base_yaw_rad=math.radians(float(base.get("yaw_deg", 0.0))),
            base_status=str(base.get("status", "assumed")),
            mounting=str(_need(c, "mounting", "orientation", where=w)),
            mounting_status=str(c["mounting"].get("status", "assumed")),
            table_z_m=float(_need(c, "table", "surface_z_m", where=w)),
            table_status=str(c["table"].get("status", "assumed")),
            reach_margin_m=float(c.get("reachability_margin_m", 0.02)),
            approach_clearance_m=float(md["approach_clearance_m"]),
            retreat_clearance_m=float(md["retreat_clearance_m"]),
            transit_clearance_m=float(md["transit_clearance_m"]),
            sample_step_m=float(md["path_sample_step_m"]),
            max_joint_step_rad=float(md["max_joint_step_rad"]),
            max_wrist_joint_step_rad=float(md["max_wrist_joint_step_rad"]),
            sing_warn=float(md["singularity_inv_condition_warn"]),
            yaw_candidates_rad=[math.radians(float(v)) for v in md["default_yaw_candidates_deg"]],
            scan_container_height_m=[float(v) for v in scan.get("container_wall_height_m", [0.10])],
            scan_object_size_m=[float(v) for v in scan.get("object_size_m", [0.05])],
        )

    def world_to_base(self, p_world) -> np.ndarray:
        """Transform a world point into the robot base frame (base pose from the cell profile)."""
        c, s = math.cos(self.base_yaw_rad), math.sin(self.base_yaw_rad)
        R = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        return R.T @ (np.asarray(p_world, dtype=float) - self.base_position_m)

    @property
    def table_z_base_m(self) -> float:
        return float(self.table_z_m - self.base_position_m[2])


@dataclass
class Profiles:
    robot: RobotProfile
    tool: ToolProfile
    camera: CameraProfile                 # computer-vision camera of the execution cell (D415) - used for the verdict
    cell: CellProfile
    directory: Path
    demo_camera: CameraProfile | None = None   # camera that recorded the demonstration (D435) - informational only


def load_profiles(
    directory: str | Path | None = None,
    robot: str = "ur5e.yaml",
    tool: str = "tool_custom_3d_printed.yaml",
    camera: str = "camera_realsense_d415.yaml",
    cell: str = "cell_setup.yaml",
    demo_camera: str | None = "camera_realsense_d435_demo.yaml",
) -> Profiles:
    """Load the profile set. Two cameras exist (see the note above CameraProfile): `camera` is the one the
    capability check is computed against; `demo_camera` (None to disable) is only reported."""
    base = Path(directory) if directory else DEFAULT_PROFILE_DIR

    def p(name: str) -> Path:
        path = Path(name)
        if path.is_absolute() or path.exists():       # a file in the current folder wins over the built-in profile folder
            return path
        return base / name

    demo = None
    if demo_camera and p(demo_camera).exists():
        demo = CameraProfile.from_dict(_load_yaml(p(demo_camera)))
    return Profiles(
        robot=RobotProfile.from_dict(_load_yaml(p(robot))),
        tool=ToolProfile.from_dict(_load_yaml(p(tool))),
        camera=CameraProfile.from_dict(_load_yaml(p(camera))),
        cell=CellProfile.from_dict(_load_yaml(p(cell))),
        directory=base,
        demo_camera=demo,
    )
