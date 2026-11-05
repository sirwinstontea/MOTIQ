"""Test della Parte C (vision/planner). Offline: MockGrok + MockExecutor.

    GROK_MOCK=1 python -m pytest tests/test_planner.py -v
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from vision.planner import frames as fr
from vision.planner.agent import PlanningAgent
from vision.planner.executors import MockExecutor
from vision.planner.frames import Phrase
from vision.planner.grok_client import GrokClient, GrokError, MockGrok, STTResult, STTWord
from vision.planner.library import ExpansionError, SkillLibrary, resolve_objects
from vision.planner.motion import (adaptive_threshold, add_voice_keyframes, motion_energy, segment,
                                   select_keyframes)
from vision.planner.runlog import RunLog
from vision.planner.schemas import (ConcretePrimitive, PositionMM, PrimitiveStep, RepeatStep,
                                    RunInput, SkillCall, SkillDef, SkillStep, StepFeedback, VideoTokens)
from vision.planner.tokenizer import TokenizerConfig, VideoTokenizer, merge_tokens
from vision.planner.validator import validate_sequence
from vision.planner.voice import words_to_phrases

FIXTURES = Path(__file__).parent / "fixtures" / "planner"


@pytest.fixture
def lib(tmp_path: Path) -> SkillLibrary:
    return SkillLibrary(tmp_path / "skill_library.json")


@pytest.fixture
def tokens() -> VideoTokens:
    return VideoTokens.model_validate_json((FIXTURES / "tokens_unscrew.json").read_text())


def _executor(tokens: VideoTokens, **kw) -> MockExecutor:
    return MockExecutor(objects=[o for o in tokens.scene_objects if o.position_mm], **kw)


# ---------------------------------------------------------------------------
# Libreria ed espansione
# ---------------------------------------------------------------------------

def test_seed_library_is_persisted(lib: SkillLibrary):
    assert lib.path.exists()
    data = json.loads(lib.path.read_text())
    assert set(data["skills"]) >= {"home", "pick", "place", "pick_and_place"}
    assert data["history"][0]["event"] == "seeded"


def test_expand_recursive_with_params_and_repeat(lib: SkillLibrary):
    lib.add_learned(SkillDef(
        id="wiggle", params={"n": {"type": "integer", "default": 2, "min": 0, "max": 5},
                             "target": {"type": "object_ref"}},
        body=[SkillStep(skill="pick", args={"target": "$target"}),
              RepeatStep(repeat="$n", body=[PrimitiveStep(primitive="ROTATE_YAW", args={"angle_deg": 30}),
                                            PrimitiveStep(primitive="ROTATE_YAW", args={"angle_deg": -30})])],
    ))
    prims = lib.expand(SkillCall(skill="wiggle", args={"target": "cube", "n": 3}))
    names = [p.primitive for p in prims]
    assert names[:5] == ["OPEN_GRIPPER", "MOVE_ABOVE", "MOVE_DOWN", "CLOSE_GRIPPER", "MOVE_UP"]
    assert names[5:] == ["ROTATE_YAW", "ROTATE_YAW"] * 3
    assert prims[1].args["target"] == "cube"
    assert prims[0].skill_path == ["wiggle", "pick"]
    assert prims[2].args["speed_mps"] == 0.05  # dal body di pick
    assert prims[4].args["speed_mps"] == 0.1  # default del catalogo


def test_cycle_is_rejected(lib: SkillLibrary):
    lib.add_learned(SkillDef(id="a", body=[SkillStep(skill="b")]))
    lib.add_learned(SkillDef(id="b", body=[SkillStep(skill="a")]))
    with pytest.raises(ExpansionError, match="ciclo"):
        lib.expand(SkillCall(skill="a"))


@pytest.mark.parametrize("body,match", [
    ([PrimitiveStep(primitive="LASER_BEAM")], "primitiva sconosciuta"),
    ([PrimitiveStep(primitive="MOVE_DOWN", args={"distance_m": 0.5})], "max"),
    ([PrimitiveStep(primitive="MOVE_DOWN", args={"speed": 1})], "non accetta"),
    ([PrimitiveStep(primitive="MOVE_ABOVE", args={"target": "$nope"})], "parametro inesistente"),
    ([RepeatStep(repeat=50, body=[PrimitiveStep(primitive="WAIT")])], "repeat"),
])
def test_invalid_bodies_are_rejected(lib: SkillLibrary, body, match):
    sd = SkillDef(id="bad", body=body)
    errs = lib.check_skill(sd)
    assert errs and match in errs[0]
    assert "bad" not in lib  # check_skill non modifica la libreria


def test_builtin_never_overwritten(lib: SkillLibrary):
    sd = lib.add_learned(SkillDef(id="pick", body=[PrimitiveStep(primitive="HOME")]))
    assert sd.id == "pick_v2"
    assert lib.get("pick").origin == "builtin"


def test_relearn_bumps_version(lib: SkillLibrary):
    lib.add_learned(SkillDef(id="x", body=[PrimitiveStep(primitive="HOME")]))
    sd = lib.add_learned(SkillDef(id="x", body=[PrimitiveStep(primitive="WAIT")]))
    assert sd.version == 2
    assert [e.event for e in lib.data.history][-2:] == ["skill_added", "skill_updated"]
    assert SkillLibrary(lib.path).get("x").version == 2  # persistito


# ---------------------------------------------------------------------------
# Validatore
# ---------------------------------------------------------------------------

def _prims(lib: SkillLibrary, *calls: SkillCall, pos=(400.0, 0.0, 20.0)) -> list[ConcretePrimitive]:
    prims = lib.expand_plan(list(calls))
    resolve_objects(prims, lambda _n: PositionMM(x=pos[0], y=pos[1], z=pos[2]))
    return prims


def test_builtin_pick_and_place_is_valid(lib: SkillLibrary):
    prims = _prims(lib, SkillCall(skill="pick_and_place", args={"obj": "a", "dest": "b"}))
    rep = validate_sequence(prims, initial_tcp_m=[0.3, 0, 0.4], home_tcp_m=[0.3, 0, 0.4])
    assert rep.ok, rep.errors


def test_fork_pick_drop_is_valid_and_never_closes_gripper(lib: SkillLibrary):
    prims = _prims(lib, SkillCall(skill="fork_pick_drop", args={"target": "a", "dest": "b"}))
    names = [p.primitive for p in prims]
    assert "CLOSE_GRIPPER" not in names
    assert names.index("MOVE_DOWN") < names.index("MOVE_UP") < names.index("ROTATE_ROLL") < names.index("HOME")
    assert prims[names.index("ROTATE_ROLL")].args["angle_deg"] == 90
    rep = validate_sequence(prims, initial_tcp_m=[0.3, 0, 0.4], home_tcp_m=[0.3, 0, 0.4])
    assert rep.ok, rep.errors
    assert lib.get("fork_pick_drop").status == "needs_validation"  # non provata sul robot


def test_validator_rejects_closed_gripper_descent(lib: SkillLibrary):
    prims = [ConcretePrimitive(primitive="CLOSE_GRIPPER", args={"force_pct": 70}),
             ConcretePrimitive(primitive="OPEN_GRIPPER"),
             ConcretePrimitive(primitive="CLOSE_GRIPPER"),
             ConcretePrimitive(primitive="MOVE_DOWN", args={"distance_m": 0.05, "speed_mps": 0.05})]
    # dopo CLOSE la pinza "tiene" qualcosa: la discesa è lecita (posa)
    assert validate_sequence(prims, initial_tcp_m=[0.4, 0, 0.3]).ok
    prims = [ConcretePrimitive(primitive="OPEN_GRIPPER"),
             ConcretePrimitive(primitive="MOVE_ABOVE", args={"target": "a", "target_mm": [400, 0, 20], "height_m": 0.15}),
             ConcretePrimitive(primitive="OPEN_GRIPPER"),
             ConcretePrimitive(primitive="CLOSE_GRIPPER"), ConcretePrimitive(primitive="OPEN_GRIPPER")]
    assert validate_sequence(prims).ok


def test_validator_errors(lib: SkillLibrary):
    above = ConcretePrimitive(primitive="MOVE_ABOVE", args={"target": "a", "target_mm": [400, 0, 0], "height_m": 0.15})
    down = lambda d: ConcretePrimitive(primitive="MOVE_DOWN", args={"distance_m": d, "speed_mps": 0.05})  # noqa: E731
    # sotto il tavolo
    rep = validate_sequence([ConcretePrimitive(primitive="OPEN_GRIPPER"), above, down(0.15)])
    assert any("sotto il minimo" in e for e in rep.errors)
    # discesa con pinza chiusa e vuota: modellata con stato iniziale "closed" e niente in mano
    rep = validate_sequence([above, down(0.05)], initial_gripper="closed")
    assert any("OPEN_GRIPPER" in e for e in rep.errors)
    # traslazione orizzontale a quota bassa
    rep = validate_sequence([ConcretePrimitive(primitive="OPEN_GRIPPER"), above, down(0.13),
                             ConcretePrimitive(primitive="MOVE_LEFT", args={"distance_m": 0.1, "speed_mps": 0.1})])
    assert any("risalire" in e for e in rep.errors)
    # velocità oltre il limite operativo
    rep = validate_sequence([ConcretePrimitive(primitive="MOVE_UP", args={"distance_m": 0.05, "speed_mps": 0.9})])
    assert any("speed_mps" in e for e in rep.errors)
    # fuori sbraccio
    far = ConcretePrimitive(primitive="MOVE_ABOVE", args={"target": "a", "target_mm": [2000, 0, 0], "height_m": 0.15})
    assert any("sbraccio" in e for e in validate_sequence([far]).errors)


# ---------------------------------------------------------------------------
# Agente end-to-end (mock)
# ---------------------------------------------------------------------------

def test_agent_learns_missing_skill_and_executes(tmp_path: Path, lib: SkillLibrary, tokens: VideoTokens):
    ex = _executor(tokens)
    grok = MockGrok()
    st = PlanningAgent(grok, lib, ex, runs_dir=tmp_path / "runs").run(tokens=tokens)

    assert st.status == "done", st.error
    assert st.learned_skills == ["svitare"]
    learned = SkillLibrary(lib.path).get("svitare")  # riletta da disco
    assert learned.origin == "learned" and learned.status == "needs_validation"
    assert learned.learned_from.token_ids == ["tok_002"]
    assert [c.skill for c in st.plan.calls] == ["svitare", "place", "home"]
    assert st.steps and all(s.status == "done" for s in st.steps)
    assert sum(1 for s in st.steps if s.primitive == "ROTATE_YAW") == 8  # 4 quarti di giro x 2

    # il tappo è finito nel secchio
    cap, bucket = ex.objects["white cap"], ex.objects["blue bucket"]
    assert abs(cap[0] - bucket[0]) < 1 and abs(cap[1] - bucket[1]) < 1

    # run log su disco coerente con lo stato
    run_files = list((tmp_path / "runs").glob("run_*.json"))
    assert len(run_files) == 1
    saved = json.loads(run_files[0].read_text())
    assert saved["status"] == "done" and len(saved["steps"]) == len(st.steps)
    assert json.loads((tmp_path / "runs" / "latest.json").read_text())["run_id"] == st.run_id
    kinds = [e["kind"] for e in saved["events"]]
    assert kinds.index("skill_learned") < kinds.index("plan_validated") < kinds.index("run_done")


def test_second_run_reuses_learned_skill(tmp_path: Path, lib: SkillLibrary, tokens: VideoTokens):
    PlanningAgent(MockGrok(), lib, _executor(tokens), runs_dir=tmp_path / "r1").run(tokens=tokens)
    grok = MockGrok()
    st = PlanningAgent(grok, SkillLibrary(lib.path), _executor(tokens), runs_dir=tmp_path / "r2").run(tokens=tokens)
    assert st.status == "done"
    assert st.learned_skills == []
    assert not any(c["task"] == "synthesize" for c in grok.calls)


def test_synthesis_retries_with_validation_feedback(tmp_path: Path, lib: SkillLibrary, tokens: VideoTokens):
    bad = {"id": "svitare", "params": {"target": {"type": "object_ref"}},
           "body": [{"primitive": "UNSCREW_MAGIC", "args": {}}]}
    seen: list[str] = []

    def good(system, user, ctx):
        seen.append(user)
        from vision.planner import mock_rules
        return mock_rules.respond("synthesize", ctx)

    grok = MockGrok(responses={"synthesize": [bad, good]})
    st = PlanningAgent(grok, lib, _executor(tokens), runs_dir=tmp_path).run(tokens=tokens)
    assert st.status == "done", st.error
    assert sum(1 for c in grok.calls if c["task"] == "synthesize") == 2
    assert "UNSCREW_MAGIC" in seen[0]  # l'errore è stato rimandato a Grok
    assert any(e.kind == "skill_rejected" for e in st.events)


def test_require_approved_blocks_unvalidated_skills(tmp_path: Path, lib: SkillLibrary, tokens: VideoTokens):
    st = PlanningAgent(MockGrok(), lib, _executor(tokens), runs_dir=tmp_path,
                       require_approved=True).run(tokens=tokens)
    assert st.status == "failed" and "non approvate" in st.error
    assert not st.steps


def test_unresolved_objects_fail_with_needs_validation(tmp_path: Path, lib: SkillLibrary, tokens: VideoTokens):
    ex = MockExecutor(objects=[], auto_place=False)
    tokens.scene_objects = []
    st = PlanningAgent(MockGrok(), lib, ex, runs_dir=tmp_path).run(tokens=tokens)
    assert st.status == "failed"
    assert any("needs_validation" in e for e in st.validation_errors)
    assert st.plan_iterations == 3


def test_validation_errors_are_fed_back_to_planner(tmp_path: Path, lib: SkillLibrary, tokens: VideoTokens):
    first = {"goal": "x", "calls": [{"skill": "pick", "args": {"target": "white_cap", "grasp_depth_m": 0.2}}]}
    seen: list[str] = []

    def second(system, user, ctx):
        seen.append(user)
        return {"goal": "x", "calls": [{"skill": "pick", "args": {"target": "white_cap"}}, {"skill": "home"}]}

    ex = _executor(tokens)
    ex.objects["white cap"][2] = 0.0  # oggetto a quota tavolo: grasp_depth 0.2 porta sotto il minimo
    st = PlanningAgent(MockGrok(responses={"plan": [first, second]}), lib, ex, runs_dir=tmp_path).run(tokens=tokens)
    assert st.status == "done", st.error
    assert "sotto il minimo" in seen[0]


class _FailingExecutor(MockExecutor):
    def execute(self, prim):
        if prim.primitive == "CLOSE_GRIPPER":
            return StepFeedback(ok=False, message="presa fallita")
        return super().execute(prim)


def test_step_failure_stops_and_skips_rest(tmp_path: Path, lib: SkillLibrary, tokens: VideoTokens):
    ex = _FailingExecutor(objects=[o for o in tokens.scene_objects if o.position_mm])
    st = PlanningAgent(MockGrok(), lib, ex, runs_dir=tmp_path).run(tokens=tokens)
    assert st.status == "failed" and "presa fallita" in st.error
    statuses = [s.status for s in st.steps]
    i = statuses.index("failed")
    assert all(s == "done" for s in statuses[:i]) and all(s == "skipped" for s in statuses[i + 1:])


def test_replan_on_fail(tmp_path: Path, lib: SkillLibrary, tokens: VideoTokens):
    class FailOnce(MockExecutor):
        failed = False

        def execute(self, prim):
            if prim.primitive == "CLOSE_GRIPPER" and not self.failed:
                self.failed = True
                return StepFeedback(ok=False, message="slittamento")
            return super().execute(prim)

    ex = FailOnce(objects=[o for o in tokens.scene_objects if o.position_mm])
    st = PlanningAgent(MockGrok(), lib, ex, runs_dir=tmp_path, replan_on_fail=True).run(tokens=tokens)
    assert st.status == "done", st.error
    assert any(e.kind == "replan" for e in st.events)


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------

def test_runlog_atomic_writes(tmp_path: Path):
    log = RunLog.create(tmp_path, RunInput(goal="g"))
    for i in range(20):
        log.event("tick", str(i))
    assert not list(tmp_path.glob(".*.tmp"))
    data = json.loads(log.path.read_text())
    assert len(data["events"]) == 20 and data["input"]["goal"] == "g"


# ---------------------------------------------------------------------------
# Movimento / keyframe / voce
# ---------------------------------------------------------------------------

def _synthetic_frames(n=180, fps=30.0, moves=((30, 60), (105, 135))):
    frames = np.full((n, 36, 64), 230, np.uint8)
    for i in range(n):
        x = 5
        for s, e in moves:
            if s <= i < e:
                x = 5 + (i - s)  # il quadrato si sposta durante il movimento
                break
            if i >= e:
                x = 5 + (e - s) if moves.index((s, e)) == 0 else 5
        frames[i, 10:20, x % 50:x % 50 + 8] = 30
    return frames, [i / fps for i in range(n)]


def test_motion_segments_and_keyframes():
    frames, times = _synthetic_frames()
    m = motion_energy(frames)
    thr = adaptive_threshold(m)
    segs = segment(m, times, thr)
    assert len(segs) == 2
    assert 25 <= segs[0].start <= 33 and 57 <= segs[0].end <= 65
    kfs = select_keyframes(segs, times)
    reasons = [k.reason for k in kfs]
    assert reasons[0] == "scene_start" and reasons[-1] == "scene_end"
    assert reasons.count("motion_start") == 2 and reasons.count("motion_peak") == 2
    assert reasons.count("motion_end") == 2
    assert len(kfs) <= 10  # molto meno dei 180 frame


def test_static_video_falls_back_to_uniform():
    frames = np.full((90, 36, 64), 128, np.uint8)
    times = [i / 30 for i in range(90)]
    kfs = select_keyframes(segment(motion_energy(frames), times, 0.002), times)
    assert [k.idx for k in kfs] == [0, 30, 60, 89]


def test_voice_phrases_align_and_boost_keyframes():
    times = [i / 30 for i in range(300)]
    words = [STTWord(text=w, start=s, end=s + 0.3) for w, s in
             [("adesso", 1.0), ("prendo", 1.3), ("il", 1.6), ("tappo.", 1.8),
              ("bello", 5.0), ("vero", 5.3)]]
    ph = words_to_phrases(words, times)
    assert [p.text for p in ph] == ["adesso prendo il tappo.", "bello vero"]
    assert ph[0].action and not ph[1].action
    assert ph[0].idx_start == 30
    from vision.planner.motion import Keyframe
    kfs = add_voice_keyframes([Keyframe(0, 0.0, "scene_start")], ph, times)
    assert any(k.reason == "voice" and abs(k.t - 1.55) < 0.05 for k in kfs)
    assert len(kfs) == 2  # la frase senza verbo d'azione non aggiunge keyframe


def test_merge_adjacent_tokens():
    from vision.planner.schemas import ActionToken, TokenObject
    o = [TokenObject(name="cap")]
    ts = [ActionToken(verb="svitare", canonical="rotate", t_start=0, t_end=1, objects=o, frame_idx=[1]),
          ActionToken(verb="svitare", canonical="rotate", t_start=1, t_end=2, objects=o, frame_idx=[2]),
          ActionToken(verb="sollevare", canonical="move", t_start=2, t_end=3, objects=o)]
    out = merge_tokens(ts)
    assert len(out) == 2 and out[0].t_end == 2 and out[0].frame_idx == [1, 2]


# ---------------------------------------------------------------------------
# Grok client (HTTP finto)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status: int, payload: dict):
        self.status_code, self._p, self.text = status, payload, json.dumps(payload)

    def json(self):
        return self._p


def test_grok_client_parses_fenced_json_and_retries(monkeypatch):
    from vision.planner.schemas import SkillPlan
    replies = iter(["non è json", "```json\n{\"goal\": \"g\", \"calls\": [{\"skill\": \"Home\"}]}\n```"])
    sent: list[dict] = []

    def fake_post(url, headers=None, timeout=None, json=None, files=None):
        sent.append(json)
        return _Resp(200, {"choices": [{"message": {"content": next(replies)}}]})

    monkeypatch.setattr("vision.planner.grok_client.httpx.post", fake_post)
    c = GrokClient(api_key="k", base_url="https://openrouter.ai/api/v1", reasoning="minimal")
    plan = c.chat_json("plan", "sys", "user", SkillPlan, images=[b"\xff\xd8jpeg"])
    assert plan.calls[0].skill == "home"
    assert len(sent) == 2 and sent[1]["messages"][-1]["role"] == "user"
    assert sent[0]["reasoning"] == {"effort": "minimal"}
    assert sent[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_grok_stt_requires_xai_key_and_puts_file_last(monkeypatch, tmp_path: Path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF0000WAVE")
    assert GrokClient(api_key="or", base_url="https://openrouter.ai/api/v1").stt_api_key is None
    with pytest.raises(GrokError, match="XAI_STT_API_KEY"):
        GrokClient(api_key="or", base_url="https://openrouter.ai/api/v1").transcribe(wav)

    captured: dict = {}

    def fake_post(url, headers=None, timeout=None, json=None, files=None):
        captured.update(url=url, names=[f[0] for f in files])
        return _Resp(200, {"text": "ciao", "words": [{"text": "ciao", "start": 0.1, "end": 0.4}]})

    monkeypatch.setattr("vision.planner.grok_client.httpx.post", fake_post)
    r = GrokClient(api_key="xai").transcribe(wav, language="it", keyterms=["tappo"])
    assert captured["url"] == "https://api.x.ai/v1/stt"
    assert captured["names"][-1] == "file" and captured["names"][0] == "model"
    assert r.words[0].text == "ciao"


# ---------------------------------------------------------------------------
# ffmpeg: doppio flusso LQ/HQ su un video sintetico
# ---------------------------------------------------------------------------

def _has_ffmpeg() -> bool:
    try:
        fr.ffmpeg_exe()
        return True
    except fr.FFmpegError:
        return False


@pytest.fixture(scope="module")
def moving_video(tmp_path_factory) -> Path:
    if not _has_ffmpeg():
        pytest.skip("ffmpeg non disponibile")
    out = tmp_path_factory.mktemp("video") / "moving.mp4"
    x = "if(lt(t,1),100,if(lt(t,2),100+(t-1)*300,if(lt(t,3.5),400,if(lt(t,4.5),400-(t-3.5)*300,100))))"
    cmd = [fr.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", "color=c=white:s=640x360:d=6:r=30",
           "-f", "lavfi", "-i", "color=c=red:s=60x60:d=6:r=30",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
           "-filter_complex", f"[0][1]overlay=x='{x}':y=150[v]", "-map", "[v]", "-map", "2:a",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)]
    subprocess.run(cmd, check=True)
    return out


def _decode_gray(path: Path) -> np.ndarray:
    r = subprocess.run([fr.ffmpeg_exe(), "-loglevel", "error", "-i", str(path), "-vf",
                        "scale=64:36,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(36, 64).astype(np.int16)


@pytest.mark.parametrize("hq_mode", ["all", "on-demand"])
def test_dual_stream_index_alignment(moving_video: Path, tmp_path: Path, hq_mode: str):
    # frase d'azione in un istante senza movimento né altri keyframe (~2.75 s)
    stt = STTResult(words=[STTWord(text="prendo", start=2.5, end=2.7), STTWord(text="il", start=2.7, end=2.8),
                           STTWord(text="cubo", start=2.8, end=3.0)])
    cfg = TokenizerConfig(hq_mode=hq_mode, cache_dir=tmp_path, debug=True)
    tok = VideoTokenizer(MockGrok(stt=stt), cfg)
    idx, out = tok.build_index(moving_video)

    assert idx.n_frames == 180 and idx.hq_mode == hq_mode
    assert idx.voice_status.startswith("ok")
    reasons = [k.reason for k in idx.keyframes]
    assert reasons.count("motion_peak") == 2 and "voice" in reasons
    assert len(list((out / "hq").glob("*.jpg"))) == len(idx.keyframes)  # HQ non selezionati rimossi
    assert (out / "motion.svg").exists()

    lq, times = fr.extract_lq(moving_video)
    assert [round(f.t, 4) for f in idx.frames] == [round(t, 4) for t in times]
    for k in idx.keyframes:
        hq = _decode_gray(out / k.hq)
        same = np.abs(hq - lq[k.idx].astype(np.int16)).mean()
        assert same < 6, f"HQ {k.idx} non corrisponde al frame LQ {k.idx} ({same:.1f})"
        if k.reason == "motion_peak":  # durante il moto i frame vicini sono diversi: allineamento esatto
            for j in (k.idx - 3, k.idx + 3):
                assert np.abs(hq - lq[j].astype(np.int16)).mean() > same

    # seconda chiamata: indice dalla cache
    idx2, out2 = tok.build_index(moving_video)
    assert out2 == out and [k.idx for k in idx2.keyframes] == [k.idx for k in idx.keyframes]


def test_video_pipeline_end_to_end_mock(moving_video: Path, tmp_path: Path, lib: SkillLibrary):
    stt = STTResult(words=[STTWord(text="prendo", start=0.9, end=1.1), STTWord(text="il", start=1.1, end=1.2),
                           STTWord(text="cubo.", start=1.2, end=1.4), STTWord(text="lo", start=3.4, end=3.5),
                           STTWord(text="metto", start=3.5, end=3.7), STTWord(text="qui.", start=3.7, end=3.9)])
    grok = MockGrok(stt=stt)
    agent = PlanningAgent(grok, lib, MockExecutor(), runs_dir=tmp_path / "runs",
                          tokenizer_cfg=TokenizerConfig(cache_dir=tmp_path / "frames"))
    st = agent.run(video=moving_video)
    assert st.tokens is not None and st.tokens.keyframes
    assert any(c["task"] == "tokenize" and c["n_images"] > 0 for c in grok.calls)
    assert [t.canonical for t in st.tokens.tokens] == ["grasp", "release"]
    assert st.status == "done", st.error


# ---------------------------------------------------------------------------
# Executor RoboDK: mapping delle primitive con un robot finto (senza RoboDK)
# ---------------------------------------------------------------------------

class _FakeItem:
    def __init__(self, name="obj"):
        self.name = name

    def Valid(self):
        return True

    def Name(self):
        return self.name


class _FakeRobot:
    def __init__(self, rm, reachable=True):
        self.rm = rm
        self.pose = rm.transl(400, 0, 300) * rm.rotx(3.14159265)
        self.moves: list = []
        self.joints = [0, -90, 90, -90, -90, 0]
        self.reachable = reachable

    def Pose(self):
        return self.pose

    def PoseTool(self):
        return self.rm.eye(4)

    def PoseFrame(self):
        return self.rm.eye(4)

    def SolveIK(self, pose, joints_approx=None, tool=None, reference=None):
        self._ik_pose = pose
        return self.rm.Mat([[0.0]] * 6) if self.reachable else self.rm.Mat([[]])

    def MoveL(self, pose):
        self.moves.append(("L", pose.Pos()))
        self.pose = pose

    def MoveJ(self, joints):
        self.moves.append(("J", list(joints)))
        self.joints = list(joints)
        if getattr(self, "_ik_pose", None) is not None:  # MoveJ verso una posa risolta con IK
            self.pose, self._ik_pose = self._ik_pose, None

    def Joints(self):
        return self.rm.Mat([[float(j)] for j in self.joints])

    def setSpeed(self, *a):
        pass

    def Stop(self):
        self.moves.append(("STOP",))


class _FakeTool:
    def __init__(self):
        self.attached = None

    def AttachClosest(self, keyword="", tolerance_mm=-1):
        self.attached = _FakeItem("white_cap")
        return self.attached

    def DetachAll(self, parent=0):
        self.attached = None


def _robodk_exec(reachable=True):
    pytest.importorskip("robodk")
    from robodk import robolink, robomath
    from vision.planner.executors.robodk_exec import RoboDKExecutor
    ex = RoboDKExecutor()
    ex._rl, ex._rm = robolink, robomath
    ex.robot, ex.tool = _FakeRobot(robomath, reachable), _FakeTool()
    return ex


def test_robodk_mapping_units_and_frames():
    ex = _robodk_exec()
    P = ConcretePrimitive
    assert ex.execute(P(primitive="MOVE_DOWN", args={"distance_m": 0.05, "speed_mps": 0.05})).ok
    assert [round(c, 3) for c in ex.robot.moves[-1][1]] == [400, 0, 250]  # metri -> mm, frame base
    assert ex.execute(P(primitive="MOVE_ABOVE", args={"target": "c", "target_mm": [420, -120, 20],
                                                     "height_m": 0.15})).ok
    assert ex.robot.moves[-1][0] == "J"  # trasferimento in spazio giunti
    assert [round(c, 3) for c in ex.robot.pose.Pos()] == [420, -120, 170]
    assert ex.execute(P(primitive="ROTATE_YAW", args={"angle_deg": 90})).ok
    assert [round(c, 3) for c in ex.robot.moves[-1][1]] == [420, -120, 170]  # ruota nel frame tool
    fb = ex.execute(P(primitive="CLOSE_GRIPPER", args={"force_pct": 70}))
    assert fb.ok and fb.attached == "white_cap" and fb.gripper == "closed"
    assert ex.execute(P(primitive="OPEN_GRIPPER")).attached is None
    assert ex.execute(P(primitive="HOME")).ok
    assert ex.robot.moves[-1] == ("J", [0.0, -90.0, 90.0, -90.0, -90.0, 0.0])


def test_robodk_ik_failure_is_reported():
    ex = _robodk_exec(reachable=False)
    fb = ex.execute(ConcretePrimitive(primitive="MOVE_UP", args={"distance_m": 0.05, "speed_mps": 0.1}))
    assert not fb.ok and "IK" in fb.message
    assert ex.robot.moves == []


def test_merge_keeps_distinct_moves():
    from vision.planner.schemas import ActionToken, TokenMotion, TokenObject
    o = [TokenObject(name="red_square")]
    ts = [ActionToken(verb="spostare", canonical="move", t_start=0.9, t_end=2.1, objects=o,
                      motion=TokenMotion(direction="right")),
          ActionToken(verb="spostare", canonical="move", t_start=3.4, t_end=4.6, objects=o,
                      motion=TokenMotion(direction="left")),
          ActionToken(verb="spostare", canonical="move", t_start=4.6, t_end=5.0, objects=o,
                      motion=TokenMotion(direction="left"))]
    out = merge_tokens(ts)
    assert [t.motion.direction for t in out] == ["right", "left"] and out[1].t_end == 5.0
