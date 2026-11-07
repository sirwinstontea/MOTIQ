"""Typed, validated configuration for the digital twin (spec section 6).

Loading order: `--config` file (e.g. `config/digital_twin.local.json`, git-ignored). `${VAR}` placeholders in string
values are replaced from the environment (e.g. `"host": "${MOTIQ_UR_HOST}"`), so the robot IP never lives in Git.

`target` is chosen by the operator and checked against the telemetry source:
    simulation  -> telemetry "fake" (scripted joints, no robot at all) or "rtde_receive_only" on loopback (URSim)
    physical    -> telemetry "rtde_receive_only" on a non-loopback host, `allow_non_loopback: true`
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .frames import rigid_problems

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MARKER_PREFIX = "MOTIQ_Detected_"
OBJECT_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
SIM_NAME_RE = re.compile(r"^[A-Za-z0-9_\- .]{1,80}$")
_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PhysicalRobot(Strict):
    model: Literal["UR5e"] = "UR5e"
    telemetry: Literal["rtde_receive_only", "fake"] = "rtde_receive_only"
    host: str = "127.0.0.1"
    allow_non_loopback: bool = False
    # Always true: the twin never moves the robot, motion only comes from the pendant / URSim / the fake script.
    require_manual_robot_motion: Literal[True] = True
    moving_joint_speed_rad_s: float = Field(default=0.002, gt=0, le=0.5)
    reconnect_backoff_max_s: float = Field(default=5.0, ge=0.5, le=60)

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK


class Vision(Strict):
    source: Literal["none", "vision_server", "replay"] = "none"
    base_url: str = "http://127.0.0.1:8000"
    api_key_env: str = "BRIDGE_KEY"
    replay_file: str | None = None
    require_validated_eye_in_hand: Literal[True] = True
    require_pose_reference: Literal["flange"] = "flange"
    require_matching_robot_identity: bool = True
    expected_calibration_id: str | None = None
    expected_robot_serial: str | None = None
    min_confidence: float = Field(default=0.65, ge=0, le=1)
    min_depth_valid_ratio: float = Field(default=0.60, ge=0, le=1)
    min_depth_points: int = Field(default=30, ge=1)
    max_detection_age_ms: float = Field(default=750, gt=0)
    max_flange_mismatch_mm: float = Field(default=10.0, gt=0)
    max_flange_mismatch_deg: float = Field(default=3.0, gt=0)
    update_only_when_stationary: Literal[True] = True
    request_timeout_s: float = Field(default=8.0, gt=0, le=30)

    @field_validator("base_url")
    @classmethod
    def _local_only(cls, v: str) -> str:
        m = re.match(r"^http://([^/:]+)(:\d+)?/?$", v)
        if not m or m.group(1) not in LOOPBACK:
            raise ValueError("vision.base_url must be a local http://127.0.0.1:<port> URL")
        return v.rstrip("/")


class BaseAlignment(Strict):
    frame_from: Literal["ur_physical_base"] = "ur_physical_base"
    frame_to: Literal["sim_robot_base"] = "sim_robot_base"
    validated: bool = False
    validated_for: Literal["simulation", "physical"] | None = None
    validated_by: str = ""
    T_R_B: list[list[float]] = Field(default_factory=lambda: np.eye(4).tolist())
    translation_units: Literal["m"] = "m"

    @field_validator("T_R_B")
    @classmethod
    def _rigid(cls, v):
        probs = rigid_problems(np.asarray(v, dtype=float), "T_R_B")
        if probs:
            raise ValueError("; ".join(probs))
        return v

    @model_validator(mode="after")
    def _who(self):
        if self.validated and (self.validated_for is None or not self.validated_by.strip()):
            raise ValueError("base_alignment.validated needs validated_for and validated_by (who/how it was checked)")
        return self

    @property
    def T(self) -> np.ndarray:
        return np.asarray(self.T_R_B, dtype=float)


class PyBullet(Strict):
    host: Literal["127.0.0.1", "localhost"] = "127.0.0.1"
    port: int = Field(default=20500, ge=1, le=65535)
    station_name: str | None = None
    robot_item_name: str = "UR5e"
    require_simulation_mode: Literal[True] = True
    reject_connected_robot_driver: Literal[True] = True
    base_alignment: BaseAlignment = Field(default_factory=BaseAlignment)
    object_parent_frame: str | None = "Objects_Frame"
    create_visible_point_markers: bool = True
    marker_radius_mm: float = Field(default=12.0, gt=1, le=100)
    allow_cad_model_reposition: bool = False

    @field_validator("robot_item_name", "object_parent_frame", "station_name")
    @classmethod
    def _name(cls, v):
        if v is not None and not SIM_NAME_RE.match(v):
            raise ValueError(f"invalid PyBullet item name {v!r}")
        return v


class Sync(Strict):
    joints_hz: float = Field(default=10.0, ge=1, le=30)
    objects_hz: float = Field(default=2.0, ge=0.1, le=5)
    stale_after_ms: float = Field(default=1500, ge=100, le=60000)
    on_stale: Literal["freeze_and_mark_stale"] = "freeze_and_mark_stale"
    auto_start: bool = False


class ObjectEntry(Strict):
    pybullet_item_name: str
    marker_name: str
    class_labels: list[str] = Field(min_length=1)
    pose_policy: Literal["visible_surface_marker", "tabletop_contact_preserve_orientation"] = "visible_surface_marker"
    # tabletop policy only: table height in the PHYSICAL UR base frame (m), measured, and an operator sign-off
    table_z_base_m: float | None = None
    tabletop_policy_validated: bool = False

    @field_validator("pybullet_item_name")
    @classmethod
    def _item(cls, v):
        if not SIM_NAME_RE.match(v):
            raise ValueError(f"invalid PyBullet item name {v!r}")
        return v

    @field_validator("marker_name")
    @classmethod
    def _marker(cls, v):
        if not v.startswith(MARKER_PREFIX) or not SIM_NAME_RE.match(v):
            raise ValueError(f"marker names must start with {MARKER_PREFIX!r} (got {v!r}) so they never collide "
                             "with station items")
        return v

    @model_validator(mode="after")
    def _policy(self):
        if self.pose_policy == "tabletop_contact_preserve_orientation" and self.table_z_base_m is None:
            raise ValueError("tabletop_contact_preserve_orientation needs table_z_base_m")
        return self


class TwinConfig(Strict):
    schema_version: Literal["1.0.0"] = "1.0.0"
    mode: Literal["mirror_only"] = "mirror_only"
    target: Literal["simulation", "physical"]
    physical_robot: PhysicalRobot = Field(default_factory=PhysicalRobot)
    vision: Vision = Field(default_factory=Vision)
    sim: PyBullet = Field(default_factory=PyBullet)
    sync: Sync = Field(default_factory=Sync)
    service: dict = Field(default_factory=lambda: {"host": "127.0.0.1", "port": 8770})
    objects: dict[str, ObjectEntry] = Field(default_factory=dict)

    @field_validator("service")
    @classmethod
    def _service(cls, v):
        if v.get("host", "127.0.0.1") not in LOOPBACK:
            raise ValueError("service.host must be loopback; the app reaches it through the authenticated bridge")
        port = int(v.get("port", 8770))
        if not 1 <= port <= 65535:
            raise ValueError("service.port out of range")
        return {"host": v.get("host", "127.0.0.1"), "port": port}

    @field_validator("objects")
    @classmethod
    def _ids(cls, v):
        bad = [k for k in v if not OBJECT_ID_RE.match(k)]
        if bad:
            raise ValueError(f"object ids must match {OBJECT_ID_RE.pattern}: {bad}")
        labels: dict[str, str] = {}
        for oid, e in v.items():
            for lab in e.class_labels:
                k = lab.strip().lower()
                if k in labels and labels[k] != oid:
                    raise ValueError(f"class label {lab!r} is mapped to both {labels[k]} and {oid}")
                labels[k] = oid
        markers = [e.marker_name for e in v.values()]
        if len(set(markers)) != len(markers):
            raise ValueError("marker names must be unique")
        return v

    @model_validator(mode="after")
    def _target_consistency(self):
        r = self.physical_robot
        if self.target == "physical":
            if r.telemetry != "rtde_receive_only":
                raise ValueError("target physical needs telemetry rtde_receive_only")
            if r.is_loopback:
                raise ValueError("target physical with a loopback host: that is URSim, use target simulation")
            if not r.allow_non_loopback:
                raise ValueError("target physical needs physical_robot.allow_non_loopback: true (explicit opt-in)")
            if self.vision.source == "replay":
                raise ValueError("replay observations are synthetic: not allowed with target physical")
        else:
            if r.telemetry == "rtde_receive_only" and not r.is_loopback:
                raise ValueError("target simulation reads a non-loopback robot: set target physical deliberately")
        if self.vision.source == "replay" and not self.vision.replay_file:
            raise ValueError("vision.source replay needs vision.replay_file")
        return self

    # ---- derived ----------------------------------------------------------------------------------------------
    def label_map(self) -> dict[str, str]:
        return {lab.strip().lower(): oid for oid, e in self.objects.items() for lab in e.class_labels}

    def alignment_ok(self) -> tuple[bool, str | None]:
        a = self.sim.base_alignment
        if not a.validated:
            return False, "base alignment ^R T_B not validated (sim.base_alignment.validated is false)"
        if a.validated_for != self.target:
            return False, f"base alignment validated for {a.validated_for}, not for this {self.target} target"
        return True, None


def _expand(v):
    if isinstance(v, str):
        def sub(m):
            val = os.environ.get(m.group(1))
            if val is None:
                raise ValueError(f"environment variable {m.group(1)} is not set (used in the twin config)")
            return val
        return _ENV_RE.sub(sub, v)
    if isinstance(v, dict):
        return {k: _expand(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_expand(x) for x in v]
    return v


def load_config(path: str | Path) -> TwinConfig:
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw.pop("_comment", None)
    cfg = TwinConfig.model_validate(_expand(raw))
    if cfg.vision.replay_file and not Path(cfg.vision.replay_file).is_absolute():
        cfg.vision.replay_file = str((p.parent / cfg.vision.replay_file).resolve())
    return cfg
