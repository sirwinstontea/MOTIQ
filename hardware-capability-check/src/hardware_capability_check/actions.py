"""What each skill verb means for the hardware.

Skills come from a language model, so verbs vary (PICK / GRASP / PICK_UP ...). Every verb is mapped to
a small set of canonical actions with the hardware capabilities it needs. Unknown verbs are not
guessed: they are reported as needing validation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    canonical: str
    needs_motion: bool = False
    needs_vision: bool = False
    needs_hold: bool = False      # must be able to hold an object afterwards (grasp / scoop ...)
    needs_release: bool = False   # must be able to let go of a held object
    needs_force: bool = False
    description: str = ""


CANONICAL: dict[str, ActionSpec] = {
    "DETECT": ActionSpec("DETECT", needs_vision=True, description="locate / identify objects with the camera"),
    "VERIFY": ActionSpec("VERIFY", needs_vision=True, description="check a result with the camera"),
    "PICK": ActionSpec("PICK", needs_motion=True, needs_hold=True, description="approach, grasp and lift an object"),
    "PLACE": ActionSpec("PLACE", needs_motion=True, needs_release=True, description="carry a held object to a target and put it down"),
    "MOVE": ActionSpec("MOVE", needs_motion=True, description="move the tool to a position"),
    "LIFT": ActionSpec("LIFT", needs_motion=True, description="raise the tool"),
    "LOWER": ActionSpec("LOWER", needs_motion=True, description="lower the tool"),
    "RETREAT": ActionSpec("RETREAT", needs_motion=True, description="back away upward"),
    "PUSH": ActionSpec("PUSH", needs_motion=True, needs_force=True, description="push / press / slide an object with the tool"),
    "ROTATE": ActionSpec("ROTATE", needs_motion=True, description="rotate the tool about its own axis"),
    "TILT": ActionSpec("TILT", needs_motion=True, description="tilt the tool (e.g. pour)"),
    "RELEASE": ActionSpec("RELEASE", needs_release=True, description="let go of the held object"),
    "GRIP": ActionSpec("GRIP", needs_hold=True, description="close the tool on an object"),
    "HOME": ActionSpec("HOME", description="return to a safe pose (no pose given in the skill)"),
    "WAIT": ActionSpec("WAIT", description="pause"),
}

_ALIASES: dict[str, str] = {}


def _alias(canon: str, *names: str) -> None:
    for n in names:
        _ALIASES[n] = canon


_alias("DETECT", "DETECT", "LOCATE", "FIND", "OBSERVE", "SCAN", "IDENTIFY", "RECOGNIZE", "LOOK", "SEARCH", "PERCEIVE", "SEE", "TRACK", "REIDENTIFY", "RE_IDENTIFY", "LOOK_AT", "INSPECT_SCENE")
_alias("VERIFY", "VERIFY", "CHECK", "INSPECT", "CONFIRM", "VALIDATE", "ASSESS", "EVALUATE", "TEST")
_alias("PICK", "PICK", "PICK_UP", "PICKUP", "GRASP", "GRAB", "TAKE", "GET", "FETCH", "COLLECT", "SEIZE", "SCOOP")
_alias("PLACE", "PLACE", "PUT", "PUT_DOWN", "DROP", "DEPOSIT", "SET", "SET_DOWN", "STORE", "STACK", "INSERT_INTO", "PUT_IN", "PUT_INTO")
_alias("MOVE", "MOVE", "MOVE_TO", "GOTO", "GO_TO", "REACH", "APPROACH", "NAVIGATE", "TRANSPORT", "CARRY", "TRANSFER", "POSITION", "TRAVEL")
_alias("LIFT", "LIFT", "RAISE", "ELEVATE", "UP")
_alias("LOWER", "LOWER", "DESCEND", "DOWN")
_alias("RETREAT", "RETREAT", "RETRACT", "BACK_OFF", "WITHDRAW")
_alias("PUSH", "PUSH", "SLIDE", "SWEEP", "NUDGE", "PRESS", "POKE", "WIPE", "SHOVE")
_alias("ROTATE", "ROTATE", "TURN", "TWIST", "SPIN", "UNSCREW", "SCREW")
_alias("TILT", "TILT", "POUR", "FLIP", "INVERT")
_alias("RELEASE", "RELEASE", "LET_GO", "OPEN_GRIPPER", "OPEN", "OPEN_TOOL", "UNGRIP")
_alias("GRIP", "GRIP", "CLOSE_GRIPPER", "CLOSE", "CLAMP", "SQUEEZE", "CLOSE_TOOL")
_alias("HOME", "HOME", "GO_HOME", "PARK", "RESET", "RETURN_HOME", "RETURN_TO_HOME", "STOW")
_alias("WAIT", "WAIT", "PAUSE", "SLEEP", "DELAY", "HOLD", "IDLE", "DWELL")


def resolve_action(verb: str) -> ActionSpec | None:
    """Canonical spec for a skill verb, or None if the verb is not understood."""
    v = verb.strip().upper().replace(" ", "_").replace("-", "_")
    canon = _ALIASES.get(v)
    return CANONICAL[canon] if canon else None


# Grasp styles that need the tool to close / actuate.
CLOSING_GRASPS = {
    "top_grasp", "side_grasp", "front_grasp", "lateral", "lateral_grasp", "pinch", "pinch_grasp", "parallel",
    "parallel_grasp", "power", "power_grasp", "precision", "precision_grasp", "wrap", "tripod", "two_finger",
    "two_finger_grasp", "three_finger", "form_closure", "enveloping", "grasp", "grip", "gripper",
}
# Holding styles that rely on the tool's shape or physics instead of moving fingers.
PASSIVE_GRASPS = {"scoop", "hook", "push", "press_fit", "cradle", "magnetic_passive", "magnetic", "suction_passive"}
# Grasps that need vacuum / magnet (an actuator of a different kind).
SUCTION_GRASPS = {"suction", "vacuum", "suction_cup"}
