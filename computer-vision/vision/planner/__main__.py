"""CLI del planner.

  python -m vision.planner index    --video data/replays/sample_arm.mp4 --debug   (solo keyframe, senza Grok)
  python -m vision.planner tokenize --video data/replays/sample_arm.mp4 --out logs/tokens.json
  python -m vision.planner run      --tokens tests/fixtures/planner/tokens_unscrew.json --executor mock
  python -m vision.planner run      --video <mp4> --executor pybullet
  python -m vision.planner skills   [--show svitare] [--approve svitare]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .grok_client import GrokClient, MockGrok
from .library import DEFAULT_LIBRARY_PATH, SkillLibrary
from .runlog import DEFAULT_RUNS_DIR
from .schemas import SceneObject, VideoTokens
from .tokenizer import DEFAULT_CACHE_DIR, TokenizerConfig, VideoTokenizer


def _tok_cfg(a: argparse.Namespace) -> TokenizerConfig:
    return TokenizerConfig(hq_mode=a.hq, k=a.k, chunk_size=a.chunk, use_voice=not a.no_voice,
                           language=a.lang or None, keep_frames=a.keep_frames, debug=a.debug,
                           max_seg_s=a.max_seg, cache_dir=Path(a.cache_dir))


def _add_tok_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--hq", choices=["auto", "all", "on-demand"], default="auto")
    p.add_argument("--k", type=float, default=3.0, help="soglia movimento: mediana + k*MAD")
    p.add_argument("--max-seg", type=float, default=1.5, help="keyframe extra ogni N s nei segmenti lunghi")
    p.add_argument("--chunk", type=int, default=8, help="immagini per chiamata Grok vision")
    p.add_argument("--no-voice", action="store_true")
    p.add_argument("--lang", default="it")
    p.add_argument("--keep-frames", action="store_true")
    p.add_argument("--debug", action="store_true", help="salva motion.svg")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))


def _grok(force_mock: bool):
    g = MockGrok() if force_mock else GrokClient.from_env()
    if g.is_mock:
        print("[grok] MODALITÀ MOCK (GROK_MOCK=1 o XAI_API_KEY assente): risposte deterministiche offline",
              file=sys.stderr)
    return g


def cmd_index(a: argparse.Namespace) -> int:
    # Grok serve qui solo per l'STT: con --no-voice nessuna chiamata di rete.
    tok = VideoTokenizer(MockGrok() if a.no_voice else _grok(a.mock), _tok_cfg(a),
                         log=lambda k, m: print(f"[{k}] {m}"))
    idx, out = tok.build_index(a.video)
    print(f"\n{idx.n_frames} frame @ {idx.fps:.2f} fps, durata {idx.duration_s:.2f}s, HQ={idx.hq_mode}")
    for k in idx.keyframes:
        print(f"  frame {k.idx:5d}  t={k.t:7.3f}s  motion={k.motion:.4f}  {k.reason:13s} -> {out / k.hq}")
    print(f"\nindice: {out / 'frames_index.json'}")
    return 0


def cmd_tokenize(a: argparse.Namespace) -> int:
    tok = VideoTokenizer(_grok(a.mock), _tok_cfg(a), log=lambda k, m: print(f"[{k}] {m}"))
    vt = tok.tokenize(a.video, goal_hint=a.goal)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(vt.model_dump_json(indent=2), encoding="utf-8")
    for t in vt.tokens:
        objs = ", ".join(f"{o.name}({o.role})" for o in t.objects)
        print(f"  {t.id} {t.t_start:6.2f}-{t.t_end:6.2f}s  {t.verb:12s} [{t.canonical}] {objs}")
    print(f"\n{len(vt.tokens)} token -> {out}")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    from .agent import PlanningAgent
    from .executors import make_executor

    lib = SkillLibrary(a.library)
    tokens = None
    if a.tokens:
        tokens = VideoTokens.model_validate_json(Path(a.tokens).read_text(encoding="utf-8"))
    objects = None
    if a.mock_scene:
        objects = [SceneObject.model_validate(o) for o in json.loads(Path(a.mock_scene).read_text())]
    elif tokens is not None:
        objects = [o for o in tokens.scene_objects if o.position_mm is not None]
    ex = make_executor(a.executor, objects=objects, robot_name=a.robot, tool_name=a.tool,
                       gripper_name=a.gripper, record_program=a.record_program,
                       check_collisions=a.collisions, step_delay_s=a.step_delay)
    agent = PlanningAgent(_grok(a.mock), lib, ex, runs_dir=a.runs_dir, tokenizer_cfg=_tok_cfg(a),
                          require_approved=a.require_approved, replan_on_fail=a.replan_on_fail,
                          verbose=True)
    st = agent.run(video=a.video, tokens=tokens, tokens_path=a.tokens, goal=a.goal)
    print(f"\nstato: {st.status}" + (f" — {st.error}" if st.error else ""))
    if st.plan:
        print("piano:", " -> ".join(f"{c.skill}({', '.join(f'{k}={v}' for k, v in c.args.items())})"
                                     for c in st.plan.calls))
    if st.learned_skills:
        print("skill apprese:", st.learned_skills, f"(libreria: {lib.path})")
    done = sum(1 for s in st.steps if s.status == "done")
    print(f"step eseguiti: {done}/{len(st.steps)}")
    print(f"run log: {agent.log.path if agent.log else '-'}")
    return 0 if st.status == "done" else 1


def cmd_skills(a: argparse.Namespace) -> int:
    lib = SkillLibrary(a.library)
    if a.approve:
        lib.set_status(a.approve, "approved")
        print(f"'{a.approve}' -> approved")
        return 0
    if a.show:
        s = lib.get(a.show)
        if s is None:
            print(f"skill '{a.show}' non trovata", file=sys.stderr)
            return 1
        print(s.model_dump_json(indent=2))
        return 0
    for s in lib.skills.values():
        params = ", ".join(f"{n}:{p.type}" for n, p in s.params.items())
        print(f"  {s.id:18s} v{s.version} {s.origin:8s} {s.status:17s} ({params})  {s.description[:60]}")
    print(f"\n{len(lib.skills)} skill — {lib.path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vision.planner", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true", help="forza MockGrok")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="keyframe LQ->HQ (+voce) senza Grok vision")
    p.add_argument("--video", required=True)
    _add_tok_args(p)
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("tokenize", help="video -> token d'azione (Grok vision)")
    p.add_argument("--video", required=True)
    p.add_argument("--out", default="logs/tokens.json")
    p.add_argument("--goal")
    _add_tok_args(p)
    p.set_defaults(fn=cmd_tokenize)

    p = sub.add_parser("run", help="pipeline completa: token -> piano -> skill -> esecuzione")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--video")
    src.add_argument("--tokens")
    p.add_argument("--goal")
    p.add_argument("--executor", choices=["mock", "pybullet"], default="mock")
    p.add_argument("--robot", help=argparse.SUPPRESS)
    p.add_argument("--tool", help=argparse.SUPPRESS)
    p.add_argument("--gripper", help=argparse.SUPPRESS)
    p.add_argument("--record-program", help=argparse.SUPPRESS)
    p.add_argument("--collisions", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--mock-scene", help="JSON lista di SceneObject per l'executor mock")
    p.add_argument("--step-delay", type=float, default=0.0, help="ritardo per step (mock)")
    p.add_argument("--library", default=str(DEFAULT_LIBRARY_PATH))
    p.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    p.add_argument("--require-approved", action="store_true")
    p.add_argument("--replan-on-fail", action="store_true")
    _add_tok_args(p)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("skills", help="elenca / mostra / approva skill della libreria")
    p.add_argument("--show")
    p.add_argument("--approve")
    p.add_argument("--library", default=str(DEFAULT_LIBRARY_PATH))
    p.set_defaults(fn=cmd_skills)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
