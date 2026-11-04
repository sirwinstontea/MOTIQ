"""Pydantic v2 schemas for the Vision-to-Robot planning pipeline."""
from __future__ import annotations
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    ymin: float = Field(..., ge=0.0, le=1.0)
    xmin: float = Field(..., ge=0.0, le=1.0)
    ymax: float = Field(..., ge=0.0, le=1.0)
    xmax: float = Field(..., ge=0.0, le=1.0)


class DetectedObject(BaseModel):
    name: str
    role: Optional[Literal["source", "destination", "tool", "container", "obstacle", "other"]] = "other"
    bbox: Optional[BoundingBox] = None
    color: Optional[str] = None
    estimated_xyz: Optional[List[float]] = None  # [x, y, z] in meters if available


class ActionToken(BaseModel):
    """An action gesture detected from human video demonstration frames."""
    id: str
    t_start_s: float
    t_end_s: float
    verb: str
    canonical: Literal["grasp", "release", "move", "rotate", "insert", "press", "pour", "unknown"]
    objects: List[DetectedObject] = Field(default_factory=list)
    gripper: Literal["open", "closed", "unknown"] = "unknown"
    motion_direction: Optional[str] = None  # up, down, left, right, forward, backward
    distance_estimate_cm: Optional[float] = None
    rotation_axis: Optional[str] = None
    rotation_deg: Optional[float] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    notes: Optional[str] = None


class VideoTokens(BaseModel):
    """Aggregated token sequence produced from sampling video frames + Grok Voice audio."""
    video_path: Optional[str] = None
    fps_sampled: float = 2.0
    storyboard_path: Optional[str] = None
    voice_transcript: Optional[str] = None
    scene_objects: List[DetectedObject] = Field(default_factory=list)
    tokens: List[ActionToken] = Field(default_factory=list)


# --- Skill Definitions & Compositions ---

class ParamDef(BaseModel):
    type: Literal["number", "object_ref", "string", "enum"]
    default: Optional[Any] = None
    min: Optional[float] = None
    max: Optional[float] = None
    options: Optional[List[str]] = None
    description: Optional[str] = None


class PrimitiveCall(BaseModel):
    primitive: str
    args: Dict[str, Any] = Field(default_factory=dict)


class SkillCall(BaseModel):
    skill: str
    args: Dict[str, Any] = Field(default_factory=dict)
    token_ids: List[str] = Field(default_factory=list)


class RepeatCall(BaseModel):
    repeat: int = Field(..., ge=1, le=20)
    body: List[Union[PrimitiveCall, SkillCall]]


SkillBodyItem = Union[PrimitiveCall, SkillCall, RepeatCall]


class SkillDef(BaseModel):
    id: str
    name: str
    description: str
    params: Dict[str, ParamDef] = Field(default_factory=dict)
    body: List[Any] = Field(default_factory=list)  # List of SkillBodyItem
    origin: Literal["builtin", "learned"] = "learned"
    status: Literal["approved", "needs_validation"] = "needs_validation"
    version: str = "1.0.0"
    uncertainties: List[str] = Field(default_factory=list)


class MissingSkillRequest(BaseModel):
    name: str
    description: str
    token_ids: List[str] = Field(default_factory=list)
    proposed_params: Dict[str, ParamDef] = Field(default_factory=dict)


class SkillPlan(BaseModel):
    goal: str
    calls: List[SkillCall] = Field(default_factory=list)
    missing_skills: List[MissingSkillRequest] = Field(default_factory=list)


# --- Runtime Tracking ---

class StepFeedback(BaseModel):
    success: bool
    tcp_pose: Optional[List[float]] = None
    gripper_closed: Optional[bool] = None
    message: Optional[str] = None


class StepRecord(BaseModel):
    step_id: int
    primitive: str
    args: Dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "running", "done", "failed", "skipped"] = "pending"
    feedback: Optional[StepFeedback] = None
    error: Optional[str] = None


class RunState(BaseModel):
    run_id: str
    status: Literal["created", "tokenizing", "planning", "synthesizing", "validating", "executing", "completed", "failed"] = "created"
    tokens: Optional[VideoTokens] = None
    plan: Optional[SkillPlan] = None
    learned_skills: List[SkillDef] = Field(default_factory=list)
    steps: List[StepRecord] = Field(default_factory=list)
    events: List[str] = Field(default_factory=list)
