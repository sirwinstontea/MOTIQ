"""Approved high-level AI tools and request validation.

The AI (Grok planner) only ever sees these tool names and their parameter
schemas. There is no raw XYZ, joint array, moveJ/moveL, IP or script tool.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from .models import (CONTROL_ACTIONS, PARAMS_BY_ACTION, ActionName, ErrorCode, ErrorInfo,
                     RobotControlError, TaskRequest)

DESCRIPTIONS: dict[ActionName, str] = {
    ActionName.GET_ROBOT_STATE: "Current robot state, backend, mode and data freshness.",
    ActionName.GET_SCENE: "Approved object IDs, destinations, targets, patterns and latest detections.",
    ActionName.IDENTIFY_OBJECT: "Request a detection of a registered object. May return NOT_AVAILABLE; mock detections are labelled.",
    ActionName.PICK_OBJECT: "Approach, grasp, verify grip and lift a registered, freshly identified object.",
    ActionName.MOVE_OBJECT: "Transport the currently held, verified object to the approach of a registered destination.",
    ActionName.PLACE_OBJECT: "Approach (if needed), place, release, verify and retreat at a registered destination.",
    ActionName.RELEASE_OBJECT: "Open the gripper. Only allowed at a destination place target or when holding nothing.",
    ActionName.MOVE_TO_TARGET: "Move to a registered named target.",
    ActionName.MOVE_RELATIVE: "Straight-line TCP translation in base, tool, or world frame; orientation is preserved.",
    ActionName.MOVE_JOINT: "Move one robot joint to a relative or absolute angle after full trajectory validation.",
    ActionName.SHAKE_OBJECT: "Run an approved bounded shake pattern while holding a verified object.",
    ActionName.PERFORM_MOTION: "Run a pre-validated named motion macro.",
    ActionName.PUSH_OBJECT: "Push a registered, freshly identified object along its taught push path (gripper closed, empty): above -> beside it -> push -> lift. Only objects with a push definition.",
    ActionName.GET_TASK_STATUS: "Progress and events of a task (API call, not a task step).",
    ActionName.CANCEL_TASK: "Controlled cancellation of a task (not an emergency stop).",
    ActionName.STOP_ROBOT: "Request a controlled stop (not an emergency stop).",
}


def tool_specs() -> list[dict[str, Any]]:
    """Function-calling style tool list for the planner."""
    specs = []
    for name, model in PARAMS_BY_ACTION.items():
        schema = model.model_json_schema()
        schema.pop("title", None)
        schema["additionalProperties"] = False
        specs.append({"name": name.value, "description": DESCRIPTIONS[name],
                      "parameters": schema, "task_step": name not in CONTROL_ACTIONS})
    return specs


def _reject_constant(c: str):
    raise ValueError(f"non-finite number {c} is not allowed")


def validate_task(data: Any) -> TaskRequest:
    """Strictly validate an AI task. Raises RobotControlError(VALIDATION_ERROR/UNKNOWN_ACTION)."""
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data, parse_constant=_reject_constant)
        except ValueError as e:
            raise RobotControlError(ErrorCode.VALIDATION_ERROR, f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise RobotControlError(ErrorCode.VALIDATION_ERROR, "task must be a JSON object")
    known = {a.value for a in ActionName}
    for a in data.get("actions", []) or []:
        if isinstance(a, dict) and a.get("action") not in known:
            raise RobotControlError(ErrorCode.UNKNOWN_ACTION, f"unknown action '{a.get('action')}'")
    try:
        return TaskRequest.model_validate(data)
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        raise RobotControlError(ErrorCode.VALIDATION_ERROR, msgs) from e


def error_info(e: RobotControlError) -> ErrorInfo:
    return ErrorInfo(code=e.code, message=e.message, recoverable=e.recoverable)
