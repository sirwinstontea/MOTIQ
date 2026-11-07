"""PlanningAgent: video/token -> piano di skill (Grok) -> skill mancanti sintetizzate (Grok)
-> espansione + validazione deterministica -> esecuzione step-by-step (PyBullet o mock).

Il run log JSON (`logs/runs/run_<id>.json`) viene aggiornato a ogni transizione.
"""
from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, Optional

from .executors.base import Executor
from .grok_client import GrokLike
from .library import ExpansionError, SkillLibrary, resolve_objects
from .prompts import PLAN_SYSTEM, plan_user, synthesize_system, synthesize_user
from .runlog import DEFAULT_RUNS_DIR, RunLog
from .schemas import (
    ConcretePrimitive, LearnedFrom, MissingSkill, PositionMM, RunInput, RunState, RunStep,
    SceneObject, SkillCall, SkillDef, SkillPlan, VideoTokens, norm_name, now_iso,
)
from .tokenizer import TokenizerConfig, VideoTokenizer
from .validator import validate_sequence


class PlanFailed(RuntimeError):
    def __init__(self, message: str, errors: Optional[list[str]] = None):
        super().__init__(message)
        self.errors = errors or []


class PlanningAgent:
    def __init__(self, grok: GrokLike, library: SkillLibrary, executor: Executor,
                 runs_dir: Path | str = DEFAULT_RUNS_DIR, tokenizer_cfg: Optional[TokenizerConfig] = None,
                 max_plan_iters: int = 3, max_synth_retries: int = 2, require_approved: bool = False,
                 replan_on_fail: bool = False, max_replans: int = 1, verbose: bool = False):
        self.grok = grok
        self.lib = library
        self.executor = executor
        self.runs_dir = Path(runs_dir)
        self.tokenizer_cfg = tokenizer_cfg or TokenizerConfig()
        self.max_plan_iters = max_plan_iters
        self.max_synth_retries = max_synth_retries
        self.require_approved = require_approved
        self.replan_on_fail = replan_on_fail
        self.max_replans = max_replans
        self.verbose = verbose
        self.log: Optional[RunLog] = None

    # ------------------------------------------------------------------
    def _event(self, kind: str, message: str = "", **data: Any) -> None:
        assert self.log is not None
        self.log.event(kind, message, **data)
        if self.verbose:
            print(f"[{kind}] {message}")

    def run(self, video: Optional[str | Path] = None, tokens: Optional[VideoTokens] = None,
            tokens_path: Optional[str] = None, goal: Optional[str] = None) -> RunState:
        self.log = RunLog.create(self.runs_dir,
                                 RunInput(video=str(video) if video else None, tokens_path=tokens_path, goal=goal),
                                 executor=getattr(self.executor, "name", type(self.executor).__name__),
                                 library_path=str(self.lib.path))
        self._event("run_created", f"run {self.log.state.run_id} -> {self.log.path}")
        try:
            self.executor.connect()
            self._event("executor_connected", self.log.state.executor)

            if video is not None:
                self.log.update(status="tokenizing")
                tok = VideoTokenizer(self.grok, self.tokenizer_cfg, log=lambda k, m: self._event(k, m))
                tokens = tok.tokenize(video, goal_hint=goal)
            if tokens is None:
                raise PlanFailed("nessun input: serve un video o un file di token")
            if goal and not tokens.goal_hint:
                tokens.goal_hint = goal
            self.log.update(tokens=tokens)
            self._event("tokens_ready", f"{len(tokens.tokens)} token, {len(tokens.keyframes)} keyframe")

            prims = self._plan_loop(tokens, goal)
            if self.require_approved:
                bad = sorted({s for c in self.log.state.plan.calls for s in self.lib.uses_unvalidated(c.skill)})
                if bad:
                    raise PlanFailed(f"skill non approvate nel piano: {bad} (--require-approved)")
            self._execute(prims, tokens, goal)
        except PlanFailed as e:
            self.log.state.validation_errors = e.errors
            self.log.fail(str(e))
        except KeyboardInterrupt:
            try:
                self.executor.stop()
            finally:
                self.log.fail("interrotto dall'operatore", status="stopped")
        except Exception as e:  # noqa: BLE001 — il run log deve sempre registrare l'errore
            self._event("exception", f"{type(e).__name__}: {e}", traceback=traceback.format_exc()[-3000:])
            self.log.fail(f"{type(e).__name__}: {e}")
        finally:
            try:
                self.executor.close()
            except Exception:
                pass
        return self.log.state

    # ------------------------------------------------------------------
    def _scene(self, tokens: VideoTokens) -> list[SceneObject]:
        scene = {norm_name(o.name): o for o in tokens.scene_objects}
        try:
            for o in self.executor.scene():  # gli oggetti della stazione hanno la priorità
                scene[norm_name(o.name)] = o
        except Exception as e:
            self._event("scene_unavailable", str(e))
        return list(scene.values())

    def _resolver(self, scene: list[SceneObject]):
        def resolve(name: str) -> Optional[PositionMM]:
            p = self.executor.object_pose(name)
            if p is not None:
                return p
            k = norm_name(name)
            for o in scene:
                names = [norm_name(o.name)] + [norm_name(a) for a in o.aliases]
                if k in names and o.position_mm is not None:
                    return o.position_mm
            return None
        return resolve

    def _plan_loop(self, tokens: VideoTokens, goal: Optional[str],
                   errors: Optional[list[str]] = None) -> list[ConcretePrimitive]:
        assert self.log is not None
        errors = list(errors or [])
        scene = self._scene(tokens)
        scene_dump = [o.model_dump(include={"name", "color", "label", "position_mm"}) for o in scene]
        tok_dump = [t.model_dump(exclude={"frame_idx"}) for t in tokens.tokens]
        tcp = self.executor.tcp_pose()
        home = self.executor.home_tcp()

        for it in range(1, self.max_plan_iters + 1):
            self.log.update(status="planning", plan_iterations=self.log.state.plan_iterations + 1)
            user = plan_user(tok_dump, tokens.transcript, self.lib.signatures(), scene_dump,
                             goal or tokens.goal_hint, errors)
            plan: SkillPlan = self.grok.chat_json(
                "plan", PLAN_SYSTEM, user, SkillPlan,
                context={"library": self.lib, "tokens": tokens, "scene": scene, "errors": errors})
            self.log.update(plan=plan)
            self._event("plan_ready", f"iterazione {it}: {[c.skill for c in plan.calls]}",
                        missing=[m.id for m in plan.missing_skills])

            # skill mancanti: dichiarate da Grok + chiamate a skill inesistenti
            missing: dict[str, MissingSkill] = {m.id: m for m in plan.missing_skills if m.id not in self.lib}
            for c in plan.calls:
                if c.skill not in self.lib and c.skill not in missing:
                    missing[c.skill] = MissingSkill(name=c.skill, description=c.reason or f"azione '{c.skill}'",
                                                    token_ids=c.token_ids)
            errors = []
            if missing:
                self.log.update(status="learning")
                for m in missing.values():
                    sd, errs = self._learn(m, tokens)
                    if sd is None:
                        errors += [f"skill '{m.id}' non sintetizzabile: {e}" for e in errs]
                if errors:
                    continue

            self.log.update(status="validating")
            try:
                prims = self.lib.expand_plan(plan.calls)
            except ExpansionError as e:
                errors = [str(e)]
                self._event("expansion_error", str(e))
                continue
            unresolved = resolve_objects(prims, self._resolver(scene), self.lib.catalog)
            if unresolved:
                names = [o.name for o in scene]
                errors = [f"oggetto '{n}' non trovato (needs_validation). Oggetti disponibili: {names}"
                          for n in unresolved]
                self._event("objects_unresolved", "; ".join(errors))
                continue
            rep = validate_sequence(
                prims, self.lib.catalog,
                initial_tcp_m=[c / 1000 for c in tcp] if tcp else None,
                home_tcp_m=[c / 1000 for c in home] if home else None,
                table_z_m=self.lib.data.table_z_m,
            )
            for w in rep.warnings:
                self._event("validation_warning", w)
            if not rep.ok:
                errors = rep.errors
                self._event("validation_failed", f"{len(rep.errors)} errori", errors=rep.errors)
                continue
            self.log.update(validation_errors=[])
            self._event("plan_validated", f"{len(prims)} primitive")
            return prims
        raise PlanFailed(f"piano non valido dopo {self.max_plan_iters} iterazioni", errors)

    def _learn(self, m: MissingSkill, tokens: VideoTokens) -> tuple[Optional[SkillDef], list[str]]:
        assert self.log is not None
        rel = [t for t in tokens.tokens if t.id in m.token_ids] or list(tokens.tokens)
        rel_dump = [t.model_dump(exclude={"frame_idx"}) for t in rel]
        errors: list[str] = []
        for attempt in range(self.max_synth_retries + 1):
            self._event("synthesize", f"skill '{m.id}' tentativo {attempt + 1}")
            sd: SkillDef = self.grok.chat_json(
                "synthesize", synthesize_system(self.lib.catalog),
                synthesize_user(m.model_dump(mode="json"), rel_dump, self.lib.signatures(), errors),
                SkillDef, context={"missing": m, "tokens": rel, "library": self.lib, "errors": errors})
            aliases = list(dict.fromkeys(sd.aliases + [m.name] + [t.verb for t in rel[:1]]))
            sd = sd.model_copy(update={
                "id": m.id, "aliases": aliases, "origin": "learned", "status": "needs_validation",
                "learned_from": LearnedFrom(video=tokens.video, token_ids=m.token_ids,
                                            run_id=self.log.state.run_id),
                "updated_at": now_iso(),
            })
            errors = self._check_learned(sd)
            if not errors:
                sd = self.lib.add_learned(sd, by="mock" if self.grok.is_mock else "grok")
                self.log.state.learned_skills.append(sd.id)
                self._event("skill_learned", f"'{sd.id}' v{sd.version} aggiunta alla libreria "
                                             f"(status {sd.status})", skill=sd.model_dump(mode="json"))
                return sd, []
            self._event("skill_rejected", f"'{m.id}': {len(errors)} errori", errors=errors)
        return None, errors

    def _check_learned(self, sd: SkillDef) -> list[str]:
        errors = self.lib.check_skill(sd)
        if errors:
            return errors
        # espansione di prova con oggetti fittizi davanti al robot + validazione fisica
        saved = dict(self.lib.data.skills)
        try:
            self.lib.data.skills[sd.id] = sd
            probe_args = {n: (f"__probe_{n}" if p.type == "object_ref" else None)
                          for n, p in sd.params.items() if p.required}
            probe_args = {k: v for k, v in probe_args.items() if v is not None}
            prims = self.lib.expand(SkillCall(skill=sd.id, args=probe_args))
            resolve_objects(prims, lambda _n: PositionMM(x=400, y=0, z=20), self.lib.catalog)
            rep = validate_sequence(prims, self.lib.catalog, initial_tcp_m=[0.4, 0.0, 0.4],
                                    table_z_m=self.lib.data.table_z_m)
            return rep.errors
        except ExpansionError as e:
            return [str(e)]
        finally:
            self.lib.data.skills = saved

    # ------------------------------------------------------------------
    def _execute(self, prims: list[ConcretePrimitive], tokens: VideoTokens, goal: Optional[str]) -> None:
        assert self.log is not None
        replans = 0
        while True:
            steps = [RunStep(idx=i, call_idx=p.call_idx, skill_path=p.skill_path,
                             primitive=p.primitive, args=p.args) for i, p in enumerate(prims)]
            self.log.set_steps(steps)
            self.log.update(status="executing")
            self._event("execution_started", f"{len(steps)} step")
            failed: Optional[str] = None
            for i, p in enumerate(prims):
                self.log.step(i, status="running", started_at=now_iso())
                fb = self.executor.execute(p)
                if fb.ok:
                    self.log.step(i, status="done", ended_at=now_iso(), feedback=fb)
                    continue
                self.log.step(i, status="failed", ended_at=now_iso(), feedback=fb, error=fb.message)
                for j in range(i + 1, len(prims)):
                    self.log.state.steps[j].status = "skipped"
                failed = (f"step {i + 1} {p.primitive} [{' > '.join(p.skill_path)}] fallito: "
                          f"{fb.message} (tcp={fb.tcp_mm}, pinza={fb.gripper}, oggetto={fb.attached})")
                self._event("step_failed", failed)
                break
            if failed is None:
                self.log.update(status="done", current_step=None)
                self._event("run_done", f"{len(prims)} step eseguiti")
                return
            if not self.replan_on_fail or replans >= self.max_replans:
                raise PlanFailed(failed, [failed])
            replans += 1
            self._event("replan", f"ripianificazione {replans}/{self.max_replans}")
            prims = self._plan_loop(tokens, goal, errors=[failed, "Il robot è nello stato indicato: "
                                                          "riparti da lì (eventualmente con home)."])
