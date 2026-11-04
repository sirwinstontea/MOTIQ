"""Skill library: file JSON vivo con skill builtin + skill apprese da Grok.

Una skill è SOLO una composizione di primitive del catalogo (o di altre skill):
nessun codice generato viene mai eseguito.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .primitives import ROOT, Catalog, default_catalog
from .schemas import (
    BodyStep, ConcretePrimitive, LibraryEvent, ParamSpec, PositionMM, PrimitiveStep,
    RepeatStep, SkillCall, SkillDef, SkillLibraryFile, SkillStep, now_iso,
)

DEFAULT_LIBRARY_PATH = ROOT / "data" / "skills" / "skill_library.json"
MAX_DEPTH = 5
MAX_REPEAT = 10
MAX_PRIMITIVES = 400

ObjectResolver = Callable[[str], Optional[PositionMM]]


class ExpansionError(ValueError):
    """Errore leggibile (viene rimandato a Grok come feedback)."""


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------------------
# Skill builtin (dalle primitive_sequence di step_002/step_003 del JSON UR5e)
# ---------------------------------------------------------------------------

def builtin_skills() -> list[SkillDef]:
    P, S = PrimitiveStep, SkillStep
    common = dict(origin="builtin", status="approved")
    return [
        SkillDef(
            id="home", name="Home", description="Ritorno alla configurazione sicura home.",
            aliases=["home", "torna a casa", "riposo"], body=[P(primitive="HOME")], **common,
        ),
        SkillDef(
            id="pick", name="Pick",
            description="Presa dall'alto: apre la pinza, va sopra l'oggetto, scende, chiude, risale.",
            aliases=["prendere", "afferrare", "raccogliere", "pick", "grab", "grasp"],
            params={
                "target": ParamSpec(type="object_ref", description="oggetto da prendere"),
                "approach_height_m": ParamSpec(default=0.15, min=0.05, max=0.3),
                "grasp_depth_m": ParamSpec(default=0.13, min=0.0, max=0.2),
                "force_pct": ParamSpec(default=70, min=0, max=100),
            },
            body=[
                P(primitive="OPEN_GRIPPER"),
                P(primitive="MOVE_ABOVE", args={"target": "$target", "height_m": "$approach_height_m"}),
                P(primitive="MOVE_DOWN", args={"distance_m": "$grasp_depth_m", "speed_mps": 0.05}),
                P(primitive="CLOSE_GRIPPER", args={"force_pct": "$force_pct"}),
                P(primitive="MOVE_UP", args={"distance_m": "$grasp_depth_m"}),
            ],
            preconditions=["target rilevato", "target raggiungibile"],
            postconditions=["oggetto in pinza"],
            **common,
        ),
        SkillDef(
            id="grasp", name="Grasp",
            description="Presa SENZA sollevare: apre, va sopra l'oggetto, scende, chiude e resta giù. "
                        "Da usare prima di azioni sul posto (svitare, ruotare, premere, spingere).",
            aliases=["afferrare sul posto", "stringere", "grasp in place"],
            params={
                "target": ParamSpec(type="object_ref", description="oggetto da afferrare"),
                "approach_height_m": ParamSpec(default=0.15, min=0.05, max=0.3),
                "grasp_depth_m": ParamSpec(default=0.13, min=0.0, max=0.2),
                "force_pct": ParamSpec(default=70, min=0, max=100),
            },
            body=[
                P(primitive="OPEN_GRIPPER"),
                P(primitive="MOVE_ABOVE", args={"target": "$target", "height_m": "$approach_height_m"}),
                P(primitive="MOVE_DOWN", args={"distance_m": "$grasp_depth_m", "speed_mps": 0.05}),
                P(primitive="CLOSE_GRIPPER", args={"force_pct": "$force_pct"}),
            ],
            preconditions=["target rilevato", "target raggiungibile"],
            postconditions=["oggetto in pinza, TCP a quota di presa"],
            **common,
        ),
        SkillDef(
            id="place", name="Place",
            description="Deposito: va sopra la destinazione, scende, apre la pinza, risale.",
            aliases=["posare", "mettere", "appoggiare", "rilasciare", "depositare", "place", "put", "drop"],
            params={
                "target": ParamSpec(type="object_ref", description="destinazione (contenitore o area)"),
                "approach_height_m": ParamSpec(default=0.15, min=0.05, max=0.3),
                "release_depth_m": ParamSpec(default=0.08, min=0.0, max=0.2),
            },
            body=[
                P(primitive="MOVE_ABOVE", args={"target": "$target", "height_m": "$approach_height_m"}),
                P(primitive="MOVE_DOWN", args={"distance_m": "$release_depth_m", "speed_mps": 0.05}),
                P(primitive="OPEN_GRIPPER"),
                P(primitive="MOVE_UP", args={"distance_m": "$release_depth_m"}),
            ],
            preconditions=["oggetto in pinza", "destinazione rilevata"],
            postconditions=["oggetto rilasciato nella destinazione"],
            **common,
        ),
        SkillDef(
            id="pick_and_place", name="Pick and place",
            description="Prende un oggetto e lo deposita nella destinazione, poi torna in home.",
            aliases=["spostare", "trasferire", "pick and place"],
            params={
                "obj": ParamSpec(type="object_ref", description="oggetto da spostare"),
                "dest": ParamSpec(type="object_ref", description="destinazione"),
            },
            body=[
                S(skill="pick", args={"target": "$obj"}),
                S(skill="place", args={"target": "$dest"}),
                S(skill="home"),
            ],
            **common,
        ),
    ]


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

class SkillLibrary:
    def __init__(self, path: Path | str = DEFAULT_LIBRARY_PATH, catalog: Optional[Catalog] = None):
        self.path = Path(path)
        self.catalog = catalog or default_catalog()
        self.data = self._load()

    # ---- persistenza -----------------------------------------------------
    def _load(self) -> SkillLibraryFile:
        if self.path.exists():
            data = SkillLibraryFile.model_validate_json(self.path.read_text(encoding="utf-8"))
            new = [b for b in builtin_skills() if b.id not in data.skills]
            if new:  # libreria creata con una versione precedente: aggiunge le builtin mancanti
                for b in new:
                    data.skills[b.id] = b
                    data.history.append(LibraryEvent(event="seeded", skill_id=b.id, note="nuova builtin"))
                self.data = data
                self.save()
            return data
        data = SkillLibraryFile(skills={s.id: s for s in builtin_skills()})
        data.history.append(LibraryEvent(event="seeded", note="skill builtin da skill_pick_place_ur5e.json"))
        self.data = data
        self.save()
        return data

    def save(self) -> None:
        self.data.updated_at = now_iso()
        atomic_write_json(self.path, self.data.model_dump(mode="json"))

    def reload(self) -> None:
        self.data = self._load()

    # ---- accesso ---------------------------------------------------------
    @property
    def skills(self) -> dict[str, SkillDef]:
        return self.data.skills

    def get(self, skill_id: str) -> Optional[SkillDef]:
        return self.data.skills.get(skill_id)

    def __contains__(self, skill_id: str) -> bool:
        return skill_id in self.data.skills

    def find_by_alias(self, verb: str) -> Optional[SkillDef]:
        from .schemas import norm_name, slugify
        v = norm_name(verb)
        if slugify(verb) in self.data.skills:
            return self.data.skills[slugify(verb)]
        for s in self.data.skills.values():
            if any(norm_name(a) == v for a in s.aliases):
                return s
        return None

    def signatures(self) -> list[dict[str, Any]]:
        """Riassunto compatto delle skill da passare a Grok nel prompt di pianificazione."""
        out = []
        for s in self.data.skills.values():
            params = {}
            for n, p in s.params.items():
                d: dict[str, Any] = {"type": p.type}
                if p.default is not None:
                    d["default"] = p.default
                if p.min is not None:
                    d["min"] = p.min
                if p.max is not None:
                    d["max"] = p.max
                if p.values:
                    d["values"] = p.values
                params[n] = d
            out.append({
                "id": s.id, "description": s.description, "aliases": s.aliases,
                "params": params, "status": s.status, "origin": s.origin,
            })
        return out

    def add_learned(self, skill: SkillDef, by: str = "grok") -> SkillDef:
        """Aggiunge (o aggiorna) una skill appresa. Non sovrascrive mai una builtin."""
        existing = self.data.skills.get(skill.id)
        if existing is not None and existing.origin == "builtin":
            base, n = skill.id, 2
            while f"{base}_v{n}" in self.data.skills:
                n += 1
            skill = skill.model_copy(update={"id": f"{base}_v{n}"})
            existing = None
        if existing is not None:
            skill = skill.model_copy(update={
                "version": existing.version + 1,
                "created_at": existing.created_at,
                "updated_at": now_iso(),
            })
            event = "skill_updated"
        else:
            event = "skill_added"
        self.data.skills[skill.id] = skill
        self.data.history.append(LibraryEvent(event=event, skill_id=skill.id, version=skill.version, by=by))
        self.save()
        return skill

    def set_status(self, skill_id: str, status: str, by: str = "operator") -> None:
        s = self.data.skills[skill_id]
        self.data.skills[skill_id] = s.model_copy(update={"status": status, "updated_at": now_iso()})
        self.data.history.append(LibraryEvent(event="skill_status_changed", skill_id=skill_id,
                                              version=s.version, by=by, note=status))
        self.save()

    def uses_unvalidated(self, skill_id: str, _seen: Optional[set[str]] = None) -> list[str]:
        """Skill (ricorsive) con status diverso da approved usate da skill_id."""
        seen = _seen if _seen is not None else set()
        if skill_id in seen:
            return []
        seen.add(skill_id)
        s = self.get(skill_id)
        if s is None:
            return [skill_id]
        out = [] if s.status == "approved" else [skill_id]
        for child in _iter_skill_refs(s.body):
            out += self.uses_unvalidated(child, seen)
        return out

    # ---- espansione ------------------------------------------------------
    def expand(self, call: SkillCall, call_idx: int = 0) -> list[ConcretePrimitive]:
        skill = self.get(call.skill)
        if skill is None:
            raise ExpansionError(f"skill sconosciuta '{call.skill}'")
        env = self.bind_args(skill, call.args, path=[skill.id])
        out: list[ConcretePrimitive] = []
        self._expand_body(skill.body, env, [skill.id], call_idx, out)
        return out

    def expand_plan(self, calls: Iterable[SkillCall]) -> list[ConcretePrimitive]:
        out: list[ConcretePrimitive] = []
        for i, c in enumerate(calls):
            out += self.expand(c, call_idx=i)
        if len(out) > MAX_PRIMITIVES:
            raise ExpansionError(f"piano troppo lungo: {len(out)} primitive (max {MAX_PRIMITIVES})")
        return out

    def check_skill(self, skill: SkillDef) -> list[str]:
        """Espansione di prova con argomenti di default/placeholder. Ritorna errori."""
        args: dict[str, Any] = {}
        for n, p in skill.params.items():
            if p.default is not None:
                continue
            if p.type == "object_ref":
                args[n] = f"__probe_{n}"
            elif p.type == "enum" and p.values:
                args[n] = p.values[0]
            elif p.type in ("number", "integer"):
                args[n] = p.min if p.min is not None else 1
            elif not p.optional:
                args[n] = "x"
        tmp_skills = dict(self.data.skills)
        tmp_skills[skill.id] = skill
        saved = self.data.skills
        try:
            self.data.skills = tmp_skills
            prims = self.expand(SkillCall(skill=skill.id, args=args))
            if not prims:
                return ["la skill non produce nessuna primitiva"]
            return []
        except ExpansionError as e:
            return [str(e)]
        finally:
            self.data.skills = saved

    def bind_args(self, skill: SkillDef, args: dict[str, Any], path: list[str]) -> dict[str, Any]:
        where = " > ".join(path)
        unknown = set(args) - set(skill.params)
        if unknown:
            raise ExpansionError(f"{where}: argomenti sconosciuti {sorted(unknown)} "
                                 f"(ammessi: {sorted(skill.params)})")
        env: dict[str, Any] = {}
        for name, spec in skill.params.items():
            if name in args and args[name] is not None:
                env[name] = _coerce(args[name], spec, f"{where}.{name}")
            elif spec.default is not None:
                env[name] = spec.default
            elif spec.optional:
                env[name] = None
            else:
                raise ExpansionError(f"{where}: argomento obbligatorio mancante '{name}' ({spec.type})")
        return env

    def _expand_body(self, body: list[BodyStep], env: dict[str, Any], path: list[str],
                     call_idx: int, out: list[ConcretePrimitive]) -> None:
        where = " > ".join(path)
        if len(path) > MAX_DEPTH:
            raise ExpansionError(f"{where}: profondità di composizione > {MAX_DEPTH}")
        for step in body:
            if isinstance(step, PrimitiveStep):
                spec = self.catalog.get(step.primitive)
                if spec is None:
                    raise ExpansionError(f"{where}: primitiva sconosciuta '{step.primitive}' "
                                         f"(ammesse: {sorted(self.catalog.primitives)})")
                raw = {k: _subst(v, env, where) for k, v in step.args.items()}
                unknown = set(raw) - set(spec.params)
                if unknown:
                    raise ExpansionError(f"{where}: {step.primitive} non accetta {sorted(unknown)} "
                                         f"(ammessi: {sorted(spec.params)})")
                args: dict[str, Any] = {}
                for n, p in spec.params.items():
                    if raw.get(n) is not None:
                        args[n] = _coerce(raw[n], p, f"{where}.{step.primitive}.{n}")
                    elif p.default is not None:
                        args[n] = p.default
                    elif not p.optional:
                        raise ExpansionError(f"{where}: {step.primitive} richiede '{n}'")
                out.append(ConcretePrimitive(primitive=step.primitive, args=args,
                                             call_idx=call_idx, skill_path=list(path)))
                if len(out) > MAX_PRIMITIVES:
                    raise ExpansionError(f"{where}: troppe primitive (max {MAX_PRIMITIVES})")
            elif isinstance(step, SkillStep):
                child = self.get(step.skill)
                if child is None:
                    raise ExpansionError(f"{where}: skill sconosciuta '{step.skill}'")
                if child.id in path:
                    raise ExpansionError(f"{where}: ciclo di composizione su '{child.id}'")
                child_args = {k: _subst(v, env, where) for k, v in step.args.items()}
                child_env = self.bind_args(child, child_args, path + [child.id])
                self._expand_body(child.body, child_env, path + [child.id], call_idx, out)
            elif isinstance(step, RepeatStep):
                n = _subst(step.repeat, env, where)
                try:
                    n = int(n)
                except (TypeError, ValueError):
                    raise ExpansionError(f"{where}: repeat non intero ({step.repeat!r})")
                if not 0 <= n <= MAX_REPEAT:
                    raise ExpansionError(f"{where}: repeat={n} fuori da [0, {MAX_REPEAT}]")
                for _ in range(n):
                    self._expand_body(step.body, env, path, call_idx, out)


def resolve_objects(prims: list[ConcretePrimitive], resolver: ObjectResolver,
                    catalog: Optional[Catalog] = None) -> list[str]:
    """Aggiunge `<param>_mm` agli argomenti object_ref. Ritorna i nomi non risolti."""
    cat = catalog or default_catalog()
    missing: list[str] = []
    for p in prims:
        spec = cat.get(p.primitive)
        if spec is None:
            continue
        for n, ps in spec.params.items():
            if ps.type != "object_ref" or n not in p.args:
                continue
            pos = resolver(str(p.args[n]))
            if pos is None:
                if p.args[n] not in missing:
                    missing.append(p.args[n])
            else:
                p.args[f"{n}_mm"] = [pos.x, pos.y, pos.z]
    return missing


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------

def _iter_skill_refs(body: list[BodyStep]) -> Iterable[str]:
    for st in body:
        if isinstance(st, SkillStep):
            yield st.skill
        elif isinstance(st, RepeatStep):
            yield from _iter_skill_refs(st.body)


def _subst(value: Any, env: dict[str, Any], where: str) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        key = value[1:]
        if key not in env:
            raise ExpansionError(f"{where}: riferimento a parametro inesistente '{value}' "
                                 f"(parametri: {sorted(env)})")
        return env[key]
    if isinstance(value, list):
        return [_subst(v, env, where) for v in value]
    return value


def _coerce(value: Any, spec: ParamSpec, where: str) -> Any:
    t = spec.type
    if t in ("number", "integer"):
        if isinstance(value, bool):
            raise ExpansionError(f"{where}: atteso numero, ricevuto booleano")
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ExpansionError(f"{where}: atteso numero, ricevuto {value!r}")
        if t == "integer":
            if v != int(v):
                raise ExpansionError(f"{where}: atteso intero, ricevuto {value!r}")
            v = int(v)
        if spec.min is not None and v < spec.min:
            raise ExpansionError(f"{where}: {v} < min {spec.min}")
        if spec.max is not None and v > spec.max:
            raise ExpansionError(f"{where}: {v} > max {spec.max}")
        return v
    if t == "enum":
        if spec.values and value not in spec.values:
            raise ExpansionError(f"{where}: valore {value!r} non in {spec.values}")
        return value
    if t == "array":
        if not isinstance(value, list):
            raise ExpansionError(f"{where}: attesa lista, ricevuto {value!r}")
        if spec.length is not None and len(value) != spec.length:
            raise ExpansionError(f"{where}: attesa lista di {spec.length} elementi, ricevuti {len(value)}")
        try:
            return [float(x) for x in value]
        except (TypeError, ValueError):
            raise ExpansionError(f"{where}: lista con valori non numerici {value!r}")
    if t == "object_ref":
        if not isinstance(value, str) or not value.strip():
            raise ExpansionError(f"{where}: atteso nome oggetto, ricevuto {value!r}")
        return value.strip()
    if t == "boolean":
        return bool(value)
    return str(value)
