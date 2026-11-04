"""Skill library persistence, signature summarization and recursive expansion."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

from .schemas import SkillDef, PrimitiveCall, SkillCall, RepeatCall
from .primitives import is_valid_primitive

DEFAULT_LIBRARY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "skills" / "skill_library.json"


class SkillLibrary:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_LIBRARY_PATH
        self.skills: Dict[str, Dict[str, Any]] = {}
        self.version: str = "1.0.0"
        self.history: List[Dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.version = data.get("version", "1.0.0")
        self.skills = data.get("skills", {})
        self.history = data.get("history", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        data = {
            "version": self.version,
            "skills": self.skills,
            "history": self.history
        }
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_path, self.path)

    def signatures(self) -> List[Dict[str, Any]]:
        """Compact summary of available skills to pass in Grok prompt."""
        sigs = []
        for sid, s in self.skills.items():
            sigs.append({
                "id": sid,
                "name": s.get("name", sid),
                "description": s.get("description", ""),
                "params": s.get("params", {}),
                "origin": s.get("origin", "learned"),
                "status": s.get("status", "needs_validation")
            })
        return sigs

    def add_learned(self, skill: SkillDef) -> None:
        skill_dict = skill.model_dump()
        skill_dict["origin"] = "learned"
        skill_dict["status"] = "needs_validation"
        self.skills[skill.id] = skill_dict
        self.history.append({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "action": "add_learned_skill",
            "skill_id": skill.id,
            "name": skill.name
        })
        self.save()

    def expand(self, call: Dict[str, Any], depth: int = 0, call_stack: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Recursively expand a skill call into a list of concrete primitives."""
        if call_stack is None:
            call_stack = []

        skill_id = call.get("skill")
        if not skill_id or skill_id not in self.skills:
            raise ValueError(f"Unknown skill: '{skill_id}'")

        if skill_id in call_stack:
            raise RecursionError(f"Circular dependency detected in skill expansion: {' -> '.join(call_stack + [skill_id])}")

        if depth > 5:
            raise RecursionError(f"Maximum expansion depth exceeded (5) for skill '{skill_id}'")

        skill_def = self.skills[skill_id]
        args = call.get("args", {})
        current_stack = call_stack + [skill_id]
        expanded_primitives: List[Dict[str, Any]] = []

        for item in skill_def.get("body", []):
            if "repeat" in item:
                repeat_count = int(item["repeat"])
                sub_body = item.get("body", [])
                for _ in range(repeat_count):
                    for sub_item in sub_body:
                        expanded_primitives.extend(self._resolve_item(sub_item, args, depth + 1, current_stack, skill_def))
            else:
                expanded_primitives.extend(self._resolve_item(item, args, depth + 1, current_stack, skill_def))

        return expanded_primitives

    def _resolve_item(self, item: Dict[str, Any], parent_args: Dict[str, Any], depth: int, call_stack: List[str], skill_def: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        # Substitute $variables and defaults from skill_def
        resolved_item = copy.deepcopy(item)
        item_args = resolved_item.get("args", {})
        param_defs = (skill_def or {}).get("params", {})

        for k, v in item_args.items():
            if isinstance(v, str) and v.startswith("$"):
                var_name = v[1:]
                if var_name in parent_args:
                    item_args[k] = parent_args[var_name]
                elif var_name in param_defs and "default" in param_defs[var_name]:
                    item_args[k] = param_defs[var_name]["default"]

        if "primitive" in resolved_item:
            prim_name = resolved_item["primitive"]
            if not is_valid_primitive(prim_name):
                raise ValueError(f"Unknown primitive in skill body: '{prim_name}'")
            return [{"primitive": prim_name, "args": item_args}]
        elif "skill" in resolved_item:
            return self.expand(resolved_item, depth=depth, call_stack=call_stack)
        else:
            raise ValueError(f"Invalid body item: {resolved_item}")
