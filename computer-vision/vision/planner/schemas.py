"""Contratti Pydantic del planner (Parte C).

Unità: le skill e le primitive usano metri e gradi (come skill_pick_place_ur5e.json).
Le posizioni degli oggetti in scena e la posa TCP restituita dagli executor sono in mm.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str) -> str:
    """'Svitare il tappo' -> 'svitare_il_tappo'."""
    s = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return s or "skill"


def norm_name(text: str) -> str:
    """Normalizza nomi oggetto: 'Red Cube' == 'red_cube' == 'red-cube'."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# ---------------------------------------------------------------------------
# Tokenizzazione video
# ---------------------------------------------------------------------------

Canonical = Literal[
    "grasp", "release", "move", "rotate", "insert", "press", "push", "pour", "wait", "unknown"
]


class TokenObject(BaseModel):
    name: str
    color: Optional[str] = None
    role: Literal["target", "dest", "tool", "other"] = "target"
    bbox_norm: Optional[list[float]] = None  # [x, y, w, h] normalizzati, top-left


class TokenMotion(BaseModel):
    direction: str = "none"  # up|down|left|right|forward|backward|toward_dest|none
    distance_cm: Optional[float] = None


class TokenRotation(BaseModel):
    axis: Literal["roll", "pitch", "yaw"] = "yaw"
    angle_deg: float = 0.0  # positivo = antiorario visto dall'alto (regola mano destra)


class ActionToken(BaseModel):
    id: str = ""
    t_start: float = 0.0
    t_end: float = 0.0
    frame_idx: list[int] = Field(default_factory=list)
    verb: str
    canonical: Canonical = "unknown"
    objects: list[TokenObject] = Field(default_factory=list)
    gripper: Literal["open", "closed", "unknown"] = "unknown"
    motion: Optional[TokenMotion] = None
    rotation: Optional[TokenRotation] = None
    speech: Optional[str] = None
    confidence: float = 0.5
    notes: str = ""

    @field_validator("canonical", mode="before")
    @classmethod
    def _canon(cls, v: Any) -> Any:
        allowed = {"grasp", "release", "move", "rotate", "insert", "press", "push", "pour", "wait"}
        return v if v in allowed else "unknown"

    def primary(self, role: str = "target") -> Optional[TokenObject]:
        for o in self.objects:
            if o.role == role:
                return o
        return self.objects[0] if (self.objects and role == "target") else None


class PositionMM(BaseModel):
    x: float
    y: float
    z: float = 0.0


class SceneObject(BaseModel):
    name: str
    color: Optional[str] = None
    label: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    position_mm: Optional[PositionMM] = None  # nel frame base del robot
    source: str = "video"  # video|sim|mock|manual


class VideoTokens(BaseModel):
    video: Optional[str] = None
    fps: Optional[float] = None
    duration_s: Optional[float] = None
    keyframes: list[int] = Field(default_factory=list)
    transcript: str = ""
    goal_hint: Optional[str] = None
    tokens: list[ActionToken] = Field(default_factory=list)
    scene_objects: list[SceneObject] = Field(default_factory=list)


class TokenChunkResponse(BaseModel):
    """Risposta di Grok vision per un chunk di keyframe."""
    tokens: list[ActionToken] = Field(default_factory=list)
    scene_objects: list[SceneObject] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Skill library
# ---------------------------------------------------------------------------

class ParamSpec(BaseModel):
    type: Literal["number", "integer", "object_ref", "enum", "array", "string", "boolean"] = "number"
    default: Any = None
    min: Optional[float] = None
    max: Optional[float] = None
    values: Optional[list[str]] = None
    length: Optional[int] = None
    optional: bool = False
    description: str = ""

    @field_validator("type", mode="before")
    @classmethod
    def _map_type(cls, v: Any) -> Any:
        return {"object_id": "object_ref", "object": "object_ref", "int": "integer",
                "float": "number", "str": "string", "bool": "boolean"}.get(v, v)

    @property
    def required(self) -> bool:
        return self.default is None and not self.optional


class PrimitiveStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primitive: str
    args: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class SkillStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill: str
    args: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class RepeatStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: Union[int, str]  # intero oppure "$param"
    body: list["BodyStep"]
    note: str = ""


BodyStep = Union[PrimitiveStep, SkillStep, RepeatStep]
RepeatStep.model_rebuild()


class LearnedFrom(BaseModel):
    video: Optional[str] = None
    token_ids: list[str] = Field(default_factory=list)
    run_id: Optional[str] = None


class SkillDef(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    aliases: list[str] = Field(default_factory=list)  # verbi che la richiamano (it/en)
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    body: list[BodyStep]
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    origin: Literal["builtin", "learned", "manual"] = "learned"
    status: Literal["approved", "needs_validation", "rejected"] = "needs_validation"
    learned_from: Optional[LearnedFrom] = None
    uncertainties: list[str] = Field(default_factory=list)
    version: int = 1
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @field_validator("id", mode="before")
    @classmethod
    def _slug(cls, v: Any) -> Any:
        return slugify(str(v)) if v else v


class LibraryEvent(BaseModel):
    ts: str = Field(default_factory=now_iso)
    event: Literal["seeded", "skill_added", "skill_updated", "skill_status_changed"]
    skill_id: Optional[str] = None
    version: Optional[int] = None
    by: str = "system"
    note: str = ""


class SkillLibraryFile(BaseModel):
    schema_version: str = "2.0.0"
    robot_ref: str = "skill_pick_place_ur5e.json"
    table_z_m: float = 0.0  # quota del tavolo nel frame base del robot
    updated_at: str = Field(default_factory=now_iso)
    skills: dict[str, SkillDef] = Field(default_factory=dict)
    history: list[LibraryEvent] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Piano
# ---------------------------------------------------------------------------

class SkillCall(BaseModel):
    skill: str
    args: dict[str, Any] = Field(default_factory=dict)
    token_ids: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("skill", mode="before")
    @classmethod
    def _slug(cls, v: Any) -> Any:
        return slugify(str(v))


class MissingSkill(BaseModel):
    name: str
    description: str = ""
    token_ids: list[str] = Field(default_factory=list)
    proposed_params: dict[str, ParamSpec] = Field(default_factory=dict)

    @property
    def id(self) -> str:
        return slugify(self.name)


class SkillPlan(BaseModel):
    goal: str = ""
    calls: list[SkillCall] = Field(default_factory=list)
    missing_skills: list[MissingSkill] = Field(default_factory=list)
    notes: str = ""


class ConcretePrimitive(BaseModel):
    """Primitiva pronta per l'executor: parametri risolti, oggetti risolti in mm."""
    primitive: str
    args: dict[str, Any] = Field(default_factory=dict)
    call_idx: int = 0
    skill_path: list[str] = Field(default_factory=list)  # es. ["pick_and_place", "pick"]


# ---------------------------------------------------------------------------
# Esecuzione / run log
# ---------------------------------------------------------------------------

class StepFeedback(BaseModel):
    ok: bool
    message: str = ""
    tcp_mm: Optional[list[float]] = None
    joints_deg: Optional[list[float]] = None
    gripper: Optional[Literal["open", "closed"]] = None
    attached: Optional[str] = None
    duration_s: float = 0.0


StepStatus = Literal["pending", "running", "done", "failed", "skipped"]


class RunStep(BaseModel):
    idx: int
    call_idx: int
    skill_path: list[str]
    primitive: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: StepStatus = "pending"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    feedback: Optional[StepFeedback] = None
    error: Optional[str] = None


class RunEvent(BaseModel):
    ts: str = Field(default_factory=now_iso)
    t: float = Field(default_factory=time.time)
    kind: str
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


RunStatus = Literal[
    "created", "tokenizing", "planning", "learning", "validating", "executing",
    "done", "failed", "stopped",
]


class RunInput(BaseModel):
    video: Optional[str] = None
    tokens_path: Optional[str] = None
    goal: Optional[str] = None


class RunState(BaseModel):
    run_id: str
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    status: RunStatus = "created"
    input: RunInput = Field(default_factory=RunInput)
    executor: str = ""
    library_path: Optional[str] = None
    tokens: Optional[VideoTokens] = None
    plan: Optional[SkillPlan] = None
    plan_iterations: int = 0
    learned_skills: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    steps: list[RunStep] = Field(default_factory=list)
    current_step: Optional[int] = None
    events: list[RunEvent] = Field(default_factory=list)
    error: Optional[str] = None
