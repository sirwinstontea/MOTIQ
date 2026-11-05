"""Generic hardware components.

A hardware TYPE (gripper, camera, force/torque sensor, mobile base, ...) is a YAML file in
``robot-chooser/hardware_types/``: its fields (with units and limits), where it may be attached, which
drivers can connect it and what role it plays. Adding a new kind of hardware = adding one file; no code.

A COMPONENT is one physical device in ``hardware.yaml``: ``{id, type, name, attached_to, driver, status, spec}``.
``attached_to`` is ``cell`` (fixed in the cell), ``arm`` (the active arm's flange) or another component id,
so chains like arm -> tool changer -> gripper -> wrist camera are expressed directly.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ID_RE = r"^[a-z0-9][a-z0-9_]*$"
ROOTS = ("cell", "arm")                       # attachment roots that are not components


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldDef(_M):
    type: Literal["number", "integer", "string", "bool", "enum", "vector"]
    required: bool = False
    unit: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    values: Optional[list[str]] = None        # enum
    length: Optional[int] = Field(default=None, ge=1, le=64)   # vector
    default: Any = None
    description: Optional[str] = None

    @model_validator(mode="after")
    def _check(self):
        if self.type == "enum" and not self.values:
            raise ValueError("enum fields need `values`")
        return self


class DriverDef(_M):
    id: str = Field(pattern=ID_RE)
    description: str
    connects_via: str                         # which service/code talks to the device
    physical: bool = False                    # talks to a real device (vs. simulation)
    actuates: bool = False                    # can make the device MOVE / act


class TypeDef(_M):
    type: str = Field(pattern=ID_RE)
    label: str
    description: str = ""
    attach_to: list[str] = Field(default_factory=lambda: ["cell", "arm"])   # roots, type names or "*"
    roles: list[Literal["end_effector", "sensor", "camera", "mobility", "other"]] = Field(default_factory=list)
    payload_field: Optional[str] = "mass_kg"  # mass counted against the arm payload when mounted on it
    allow_extra_fields: bool = False          # `generic` type: record anything
    fields: dict[str, FieldDef] = Field(default_factory=dict)
    drivers: list[DriverDef] = Field(default_factory=list)

    def driver(self, did: Optional[str]) -> Optional[DriverDef]:
        return next((d for d in self.drivers if d.id == did), None)


class Component(_M):
    id: str = Field(pattern=ID_RE)
    type: str = Field(pattern=ID_RE)
    name: str = Field(min_length=1)
    attached_to: str = "cell"
    driver: Optional[str] = None
    status: Literal["placeholder", "datasheet", "measured"] = "placeholder"
    notes: Optional[str] = None
    spec: dict[str, Any] = Field(default_factory=dict)


class TypeRegistry:
    def __init__(self, types: dict[str, TypeDef]):
        self.types = types

    @classmethod
    def load(cls, folder: Path) -> "TypeRegistry":
        types: dict[str, TypeDef] = {}
        for p in sorted(folder.glob("*.yaml")):
            try:
                t = TypeDef.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
            except (ValidationError, yaml.YAMLError) as e:
                raise ValueError(f"hardware type {p.name}: {e}") from e
            if t.type in types:
                raise ValueError(f"hardware type '{t.type}' defined twice")
            types[t.type] = t
        return cls(types)

    def get(self, name: str) -> Optional[TypeDef]:
        return self.types.get(name)

    def dump(self) -> dict:
        return {k: v.model_dump(mode="json") for k, v in self.types.items()}

    # ------------------------------------------------------------------ validation of one component
    def validate_spec(self, c: Component) -> dict:
        """Return the spec with defaults filled in, or raise ValueError with every problem."""
        t = self.get(c.type)
        if t is None:
            raise ValueError(f"{c.id}: unknown hardware type '{c.type}' (known: {', '.join(sorted(self.types))}; "
                             "add a file in robot-chooser/hardware_types/)")
        problems, out = [], {}
        for key in c.spec:
            if key not in t.fields and not t.allow_extra_fields:
                problems.append(f"{c.id}.spec.{key}: not a field of type '{t.type}' ({', '.join(t.fields) or 'none'})")
        for key, f in t.fields.items():
            v = c.spec.get(key, f.default)
            if v is None:
                if f.required:
                    problems.append(f"{c.id}.spec.{key}: required" + (f" ({f.unit})" if f.unit else ""))
                out[key] = None
                continue
            err = _check_value(v, f)
            if err:
                problems.append(f"{c.id}.spec.{key}: {err}")
            out[key] = v
        if t.allow_extra_fields:
            for key, v in c.spec.items():
                if key not in t.fields:
                    if not re.match(ID_RE, key):
                        problems.append(f"{c.id}.spec.{key}: field names must be lowercase_with_underscores")
                    out[key] = v
        if c.driver is not None and t.driver(c.driver) is None:
            problems.append(f"{c.id}.driver: '{c.driver}' is not a driver of type '{t.type}' "
                            f"(known: {', '.join(d.id for d in t.drivers) or 'none'})")
        if problems:
            raise ValueError("; ".join(problems))
        return out

    def validate_all(self, comps: list[Component]) -> None:
        """Cross-component checks: unique ids, valid attachments, no cycles."""
        ids = [c.id for c in comps]
        problems = []
        if len(ids) != len(set(ids)):
            problems.append("component ids must be unique")
        if set(ids) & set(ROOTS):
            problems.append(f"component ids cannot be {ROOTS}")
        by_id = {c.id: c for c in comps}
        for c in comps:
            try:
                self.validate_spec(c)
            except ValueError as e:
                problems.append(str(e))
                continue
            t = self.get(c.type)
            parent = c.attached_to
            if parent not in ROOTS and parent not in by_id:
                problems.append(f"{c.id}.attached_to: '{parent}' is not 'cell', 'arm' or a component id")
                continue
            ptype = parent if parent in ROOTS else by_id[parent].type
            if "*" not in t.attach_to and ptype not in t.attach_to:
                problems.append(f"{c.id}: a {t.type} cannot be attached to {parent} ({ptype}); "
                                f"allowed: {', '.join(t.attach_to)}")
        for c in comps:                       # cycle check
            seen, cur = set(), c
            while cur is not None and cur.attached_to not in ROOTS:
                if cur.id in seen:
                    problems.append(f"attachment cycle through '{c.id}'")
                    break
                seen.add(cur.id)
                cur = by_id.get(cur.attached_to)
        if problems:
            raise ValueError("; ".join(dict.fromkeys(problems)))


def _check_value(v, f: FieldDef) -> Optional[str]:
    def num(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

    if f.type in ("number", "integer"):
        if not num(v) or (f.type == "integer" and int(v) != v):
            return f"must be a finite {f.type}"
        if f.min is not None and v < f.min:
            return f"must be >= {f.min:g}"
        if f.max is not None and v > f.max:
            return f"must be <= {f.max:g}"
    elif f.type == "bool" and not isinstance(v, bool):
        return "must be true/false"
    elif f.type == "string" and not isinstance(v, str):
        return "must be text"
    elif f.type == "enum" and v not in (f.values or []):
        return f"must be one of {f.values}"
    elif f.type == "vector":
        if not isinstance(v, list) or not all(num(x) for x in v):
            return "must be a list of finite numbers"
        if f.length is not None and len(v) != f.length:
            return f"needs {f.length} values"
    return None


# ---------------------------------------------------------------------------------------------- chain helpers

def on_arm(comps: list[Component]) -> list[Component]:
    """Components carried by the arm (directly or through other components)."""
    by_id = {c.id: c for c in comps}

    def carried(c, depth=0):
        if depth > len(comps):
            return False
        if c.attached_to == "arm":
            return True
        p = by_id.get(c.attached_to)
        return p is not None and carried(p, depth + 1)

    return [c for c in comps if carried(c)]
