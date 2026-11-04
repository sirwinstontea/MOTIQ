"""Regole deterministiche usate da MockGrok quando non ci sono risposte in coda.

Non "capiscono" le immagini: servono a far girare la pipeline offline (test, demo senza
chiave) con un comportamento plausibile e ripetibile.
"""
from __future__ import annotations

import re
from typing import Any

from .schemas import (
    ActionToken, LearnedFrom, MissingSkill, ParamSpec, PrimitiveStep, RepeatStep, SkillCall,
    SkillDef, SkillPlan, TokenChunkResponse, slugify,
)

_VERB_CANON = [
    (r"\b(prend|afferr|raccogl|pick|grab|grasp)", "grasp"),
    (r"\b(rilasc|lasc|pos[ao]|mett|appogg|deposit|release|place|put|drop)", "release"),
    (r"\b(svit|avvit|ruot|gir|unscrew|screw|rotat|turn|twist)", "rotate"),
    (r"\b(prem|sping|press|push)", "press"),
    (r"\b(vers|pour)", "pour"),
    (r"\b(inser|insert)", "insert"),
    (r"\b(alz|sollev|spost|lift|move)", "move"),
]


def canon_of(text: str) -> str:
    t = text.lower()
    for pat, c in _VERB_CANON:
        if re.search(pat, t):
            return c
    return "unknown"


def respond(task: str, ctx: dict[str, Any]) -> Any:
    if task == "tokenize":
        return _tokenize(ctx)
    if task == "plan":
        return _plan(ctx)
    if task == "synthesize":
        return _synthesize(ctx)
    raise ValueError(f"MockGrok: task sconosciuto {task!r}")


_STOP = {"il", "lo", "la", "i", "gli", "le", "l", "un", "uno", "una", "nel", "nella", "nello", "sul",
         "sulla", "dal", "dalla", "e", "ora", "adesso", "poi", "the", "a", "an", "in", "into", "on", "it"}


def _tokenize(ctx: dict[str, Any]) -> TokenChunkResponse:
    from .schemas import TokenObject
    kfs = ctx.get("keyframes", [])
    phrases = ctx.get("phrases", [])
    tokens: list[ActionToken] = []
    for ph in phrases:
        if not ph.get("action"):
            continue
        words = [w.strip(".,!?;").lower() for w in ph["text"].split()]
        vi = next((i for i, w in enumerate(words) if canon_of(w) != "unknown"), 0)
        verb = words[vi] if words else "azione"
        canon = canon_of(verb)
        nouns = [w for w in words[vi + 1:] if w and w not in _STOP]
        objs = [TokenObject(name=nouns[-1], role="dest" if canon == "release" else "target")] if nouns else []
        tokens.append(ActionToken(verb=verb, canonical=canon, t_start=ph["t_start"], t_end=ph["t_end"],
                                  speech=ph["text"], objects=objs, confidence=0.3,
                                  notes="mock da transcript"))
    if not tokens and kfs:
        tokens.append(ActionToken(verb="spostare", canonical="move", t_start=kfs[0]["t"],
                                  t_end=kfs[-1]["t"], frame_idx=[k["idx"] for k in kfs],
                                  confidence=0.1, notes="mock senza visione"))
    return TokenChunkResponse(tokens=tokens)


def _args_for(skill: SkillDef, target: str | None, dest: str | None) -> dict[str, Any]:
    refs = [n for n, p in skill.params.items() if p.type == "object_ref"]
    args: dict[str, Any] = {}
    vals = [v for v in (target, dest) if v]
    for n, v in zip(refs, vals):
        args[n] = v
    return args


def _plan(ctx: dict[str, Any]) -> SkillPlan:
    lib = ctx["library"]
    vt = ctx["tokens"]
    toks: list[ActionToken] = [t for t in vt.tokens]
    plan = SkillPlan(goal=vt.goal_hint or "riprodurre la dimostrazione")
    missing: dict[str, MissingSkill] = {}

    def name(t: ActionToken, role: str) -> str | None:
        o = t.primary(role)
        return o.name if o else None

    i = 0
    last_target: str | None = None
    while i < len(toks):
        t = toks[i]
        target, dest = name(t, "target"), name(t, "dest")
        if target == dest:  # unico oggetto con ruolo dest (es. "lo metto qui")
            target = None
        target = target or last_target or "oggetto"
        last_target = target
        known = lib.find_by_alias(t.verb)
        if known is not None and t.canonical not in ("grasp", "release", "move", "wait"):
            plan.calls.append(SkillCall(skill=known.id, args=_args_for(known, target, dest), token_ids=[t.id]))
        elif t.canonical == "grasp":
            nxt = next((x for x in toks[i + 1:] if x.canonical not in ("move", "wait")), None)
            merges = (nxt is not None and nxt.canonical not in ("grasp", "release")
                      and name(nxt, "target") == target)
            if not merges:
                plan.calls.append(SkillCall(skill="pick", args={"target": target}, token_ids=[t.id]))
        elif t.canonical == "release":
            plan.calls.append(SkillCall(skill="place", args={"target": dest or target}, token_ids=[t.id]))
        elif t.canonical in ("move", "wait"):
            pass
        else:
            sid = slugify(t.verb)
            if sid not in missing:
                missing[sid] = MissingSkill(
                    name=sid, description=f"Skill osservata nel video: '{t.verb}' ({t.canonical})",
                    token_ids=[t.id],
                    proposed_params={"target": ParamSpec(type="object_ref")},
                )
            plan.calls.append(SkillCall(skill=sid, args={"target": target}, token_ids=[t.id]))
        i += 1
    if not plan.calls or plan.calls[-1].skill != "home":
        plan.calls.append(SkillCall(skill="home"))
    plan.missing_skills = list(missing.values())
    return plan


def _synthesize(ctx: dict[str, Any]) -> SkillDef:
    ms: MissingSkill = ctx["missing"]
    toks: list[ActionToken] = ctx.get("tokens", [])
    t = toks[0] if toks else ActionToken(verb=ms.name)
    P = PrimitiveStep
    params: dict[str, ParamSpec] = {"target": ParamSpec(type="object_ref", description="oggetto")}
    unc = ["generata da MockGrok (regole), non da Grok reale"]
    approach = [
        P(primitive="MOVE_ABOVE", args={"target": "$target", "height_m": 0.15}),
        P(primitive="OPEN_GRIPPER"),
        P(primitive="MOVE_DOWN", args={"distance_m": 0.13, "speed_mps": 0.05}),
    ]
    if t.canonical == "rotate":
        angle = t.rotation.angle_deg if t.rotation else 360.0
        sign = 1 if angle >= 0 else -1
        params["turns"] = ParamSpec(type="integer", default=max(1, min(10, round(abs(angle) / 90))),
                                    min=1, max=10, description="quarti di giro")
        body = approach + [
            P(primitive="CLOSE_GRIPPER", args={"force_pct": 60}),
            RepeatStep(repeat="$turns", body=[
                P(primitive="ROTATE_YAW", args={"angle_deg": 90 * sign}),
                P(primitive="OPEN_GRIPPER"),
                P(primitive="ROTATE_YAW", args={"angle_deg": -90 * sign}),
                P(primitive="CLOSE_GRIPPER", args={"force_pct": 60}),
            ]),
            P(primitive="MOVE_UP", args={"distance_m": 0.13}),
        ]
        unc.append("verso di rotazione da verificare rispetto all'asse tool")
    elif t.canonical in ("press", "push"):
        body = [
            P(primitive="MOVE_ABOVE", args={"target": "$target", "height_m": 0.15}),
            P(primitive="CLOSE_GRIPPER"),
            P(primitive="MOVE_DOWN", args={"distance_m": 0.12, "speed_mps": 0.03}),
            P(primitive="WAIT", args={"seconds": 0.5}),
            P(primitive="MOVE_UP", args={"distance_m": 0.12}),
        ]
        unc.append("discesa a pinza chiusa: richiede validazione")
    elif t.canonical == "pour":
        body = [
            P(primitive="MOVE_ABOVE", args={"target": "$target", "height_m": 0.2}),
            P(primitive="ROTATE_ROLL", args={"angle_deg": 90}),
            P(primitive="WAIT", args={"seconds": 1.0}),
            P(primitive="ROTATE_ROLL", args={"angle_deg": -90}),
        ]
    else:
        body = approach + [P(primitive="MOVE_UP", args={"distance_m": 0.13})]
        unc.append("azione non riconosciuta: sequenza generica")
    return SkillDef(
        id=ms.id, name=ms.name.replace("_", " "), description=ms.description,
        aliases=[t.verb, ms.name], params=params, body=body,
        origin="learned", status="needs_validation",
        learned_from=LearnedFrom(token_ids=ms.token_ids), uncertainties=unc,
    )
