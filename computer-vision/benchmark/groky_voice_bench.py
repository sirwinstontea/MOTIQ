"""Benchmark di Groky_Planner_Agent (video + audio) su RoboDK, pipeline nativa.

    cd computer-vision
    Groky_Planner_Agent/.venv/bin/python benchmark/groky_voice_bench.py \
        --video benchmark/videos/whatsapp_voice.mp4 --reps 3
    Groky_Planner_Agent/.venv/bin/python benchmark/groky_voice_bench.py --report benchmark/results/<dir>

Usa il codice di Groky senza modificarlo: PlanningAgent.run_pipeline (storyboard OpenCV + Grok Voice
Realtime -> token -> piano -> sintesi skill -> validazione -> RoboDKExecutor).
Interventi dell'harness, tutti documentati nel report:
  H1 libreria: copia fresca di Groky_Planner_Agent/data/skills/skill_library.json per ogni run
     (il DEFAULT_LIBRARY_PATH di Groky punta a computer-vision/data/skills, cioe' alla libreria di MOTIQ).
  H2 chiavi: chat -> XAI_API_KEY (OpenRouter); voce -> XAI_STT_API_KEY (xAI), perche' Grok Voice
     Realtime esiste solo su api.x.ai e Groky di suo userebbe la chiave OpenRouter.
  H3 telemetria: GrokClient.chat_json rifatto identico (stesso payload, timeout 60 s) solo per leggere
     `usage` e la latenza, che Groky scarta.
  H4 executor: le eccezioni di RoboDK (posa irraggiungibile) diventano step falliti invece di
     interrompere il processo. Il lookup dei nomi resta quello nativo (rdk.Item(nome)).
  H5 stazione: prima di ogni run si svuota la stazione (restano robot e pinza, robot in home). Gli
     oggetti NON vengono da un file preparato a mano: quando Groky si collega a RoboDK, l'harness crea
     come scatole gli oggetti che Grok ha elencato in scene_objects, con i suoi nomi. Le posizioni sono
     un layout fisso raggiungibile (il video non da' coordinate 3D): oggetti da prendere a destra
     del robot (y<0), contenitori a sinistra (y>0).
  H7 timeout: Groky usa 60 s fissi per chiamata; con grok-4.7 la tokenizzazione li supera. --timeout
     lo alza (il valore usato e' scritto nel report).
  H8 schema: i valori di `canonical` fuori dallo schema di Groky vengono convertiti (vedi coerce_canonical).
  H9 ragionamento: --reasoning aggiunge reasoning_effort alle chiamate chat (Groky non lo invia).
  H10 voce strutturata (--voice-steps): dopo la trascrizione di Grok Voice, una chiamata chat
     (reasoning --reasoning-voice) la converte in passi JSON ordinati; il risultato va nel campo
     intent_summary che Groky passa gia' al tokenizer ("Grok Voice Intent Summary"), di solito vuoto.
  H7b --timeout-video imposta un timeout diverso per la sola tokenizzazione (es. 150 s con medium).
  H8b schema del piano/skill: missing_skills con 'skill'/'params' al posto di 'name'/'proposed_params'
     e SkillDef senza id/name vengono rinominati (coerce_plan, coerce_skill).
  H6 rete: SSL_CERT_FILE=certifi (il Python di python.org non ha CA di sistema: websockets falliva).
     Se XAI_API_KEY e' una chiave xAI ma XAI_BASE_URL punta a OpenRouter, la chat va su api.x.ai.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import statistics as st
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

CV = Path(__file__).resolve().parents[1]          # computer-vision
GROKY = CV / "Groky_Planner_Agent"
BENCH = CV / "benchmark"

from dotenv import load_dotenv  # noqa: E402

load_dotenv(CV / ".env")                       # prima di importare planner: DEFAULT_MODEL si legge all'import
import certifi  # noqa: E402

os.environ.setdefault("SSL_CERT_FILE", certifi.where())                    # H6
if os.environ.get("XAI_API_KEY", "").startswith("xai-") and "openrouter" in os.environ.get("XAI_BASE_URL", ""):
    os.environ["XAI_BASE_URL"] = "https://api.x.ai/v1"
    os.environ["XAI_MODEL"] = os.environ.get("XAI_MODEL", "grok-4.7").split("/")[-1]
os.environ.setdefault("GROK_MOCK", "0")
if os.environ.get("GROK_MOCK") == "1":
    sys.exit("GROK_MOCK=1 nel .env: il benchmark richiede Grok reale.")

sys.path.insert(0, str(GROKY))
sys.path.insert(0, str(BENCH))
import httpx  # noqa: E402

from planner.agent import PlanningAgent  # noqa: E402
from planner.executors.robodk_exec import RoboDKExecutor  # noqa: E402
from planner.grok_client import GrokClient  # noqa: E402
from planner.library import SkillLibrary  # noqa: E402
from planner.schemas import StepFeedback  # noqa: E402
from planner.voice_extractor import GrokVoiceClient  # noqa: E402
from score import ACCEPT, iou, is_bucket  # noqa: E402

ORANGE = ("orange", "arancion", "cylinder", "cilindr", "screw", "vite", "molla", "spring")
CONTAINER_ROLES = ("container", "destination")
NOT_OBJECTS = ("table", "tavolo", "hand", "mano", "person", "persona", "operator", "human", "floor", "surface")
TIMEOUT_S = 60.0   # valore nativo di Groky; --timeout lo cambia (H7)
REASONING = None   # Groky non lo invia; --reasoning low|medium|high aggiunge reasoning_effort (H9)
TIMEOUT_BY_KIND: dict[str, float] = {}   # --timeout-video: timeout della sola tokenizzazione (H7)
REASONING_BY_KIND: dict[str, str] = {}   # --reasoning-video / --reasoning-voice: override per fase
VOICE_STEPS = False                      # --voice-steps: trascrizione -> passi JSON (H10)
VOICE_STEPS_SYSTEM = """You convert the spoken narration of a human task demonstration into an ordered list of robot-relevant steps.
Use ONLY what is said; do not invent steps. Output JSON:
{"steps": [{"n": 1, "action": "grasp|lift|move|lower|place|insert|release|rotate|other",
            "object": "object name as spoken (english snake_case)", "destination": "object or null",
            "direction": "up|down|left|right|forward|backward|null"}]}"""
MODEL = None       # --model sostituisce XAI_MODEL
NO_VOICE = False   # --no-voice: Grok riceve trascrizione vuota (test solo immagini)
SOURCE_SLOTS = [(400.0, -150.0, 0.0), (480.0, -300.0, 0.0), (300.0, -300.0, 0.0)]
CONTAINER_SLOTS = [(400.0, 200.0, 0.0), (250.0, 350.0, 0.0)]


VALID_CANON = {"grasp", "release", "move", "rotate", "insert", "press", "pour", "unknown"}
MOTION_WORDS = ("raise", "lift", "lower", "move", "transport", "carry", "reach", "approach", "translate",
                "descend", "ascend", "bring", "retract", "withdraw", "hold", "up", "down")


VALID_ROLES = {"source", "destination", "tool", "container", "obstacle", "other"}
SYNONYMS = {"release": ("place", "put", "drop", "deposit", "set", "lay", "release", "let go", "open"),
            "grasp": ("pick", "take", "grab", "seize", "hold", "close"),
            "insert": ("insert", "slide in", "fit")}


def coerce_canonical(out: dict) -> list[str]:
    """H8: il prompt di Groky non elenca i valori ammessi di `canonical` ne' di `role`, e Groky non fa retry:
    un valore fuori schema fa fallire VideoTokens. Qui: verbi -> azione equivalente (place->release,
    pick->grasp, lift/lower->move), altri -> unknown; ruoli fuori schema -> other."""
    changed = []
    for t in out.get("tokens") or []:
        c = str(t.get("canonical", "")).lower()
        if c not in VALID_CANON:
            new = next((k for k, ws in SYNONYMS.items() if any(w in c for w in ws)), None)
            new = new or ("move" if any(w in c for w in MOTION_WORDS) else "unknown")
            changed.append(f"{t.get('id')}: {c} -> {new}")
            t["canonical"] = new
    for o in (out.get("scene_objects") or []) + [o for t in out.get("tokens") or [] for o in t.get("objects") or []]:
        r = o.get("role")
        if r is not None and r not in VALID_ROLES:
            changed.append(f"role {o.get('name')}: {r} -> other")
            o["role"] = "other"
    return changed


PARAM_TYPES = {"number", "object_ref", "string", "enum"}


def _clean_params(params) -> dict:
    out = {}
    for k, v in (params or {}).items():
        if isinstance(v, dict):
            v = dict(v)
            if v.get("type") not in PARAM_TYPES:
                v["type"] = "string"
            out[k] = v
    return out


def coerce_plan(out: dict) -> list[str]:
    """H8 sul piano: il prompt di Groky non mostra il formato di missing_skills. Grok scrive 'skill'
    al posto di 'name' e 'params' al posto di 'proposed_params': qui vengono rinominati."""
    changed = []
    for m in out.get("missing_skills") or []:
        if "name" not in m and (m.get("skill") or m.get("id")):
            m["name"] = m.pop("skill", None) or m.get("id")
            changed.append(f"missing_skills: skill -> name ({m['name']})")
        if "description" not in m:
            m["description"] = ""
            changed.append(f"missing_skills {m.get('name')}: description vuota")
        if "proposed_params" not in m and "params" in m:
            m["proposed_params"] = m.pop("params")
            changed.append(f"missing_skills {m.get('name')}: params -> proposed_params")
        m["proposed_params"] = _clean_params(m.get("proposed_params"))
    return changed


def coerce_skill(out: dict) -> list[str]:
    """H8 sulla skill sintetizzata: campi obbligatori di SkillDef mancanti o con nomi diversi."""
    changed = []
    if "id" not in out and out.get("name"):
        out["id"] = str(out["name"]).lower().replace(" ", "_"); changed.append("id da name")
    if "name" not in out and out.get("id"):
        out["name"] = out["id"]; changed.append("name da id")
    out.setdefault("description", "")
    out["params"] = _clean_params(out.get("params"))
    return changed


# --------------------------------------------------------------------------- strumentazione
class MeteredGrok(GrokClient):
    """Stessa richiesta di GrokClient.chat_json, ma conserva usage e latenza (H3)."""

    def __init__(self):
        super().__init__()
        self.calls: list[dict] = []
        self.last_tokens: dict | None = None

    def chat_json(self, messages, temperature: float = 0.1, kind: str | None = None):
        n_img = sum(1 for m in messages if isinstance(m.get("content"), list)
                    for c in m["content"] if c.get("type") == "image_url")
        if MODEL:
            self.model = MODEL
        payload = {"model": self.model, "messages": messages,
                   "response_format": {"type": "json_object"}, "temperature": temperature}
        kind = kind or ("tokenize" if n_img else "plan" if "available_skills" in json.dumps(messages[-1]["content"])
                        else "synthesize")
        effort = REASONING_BY_KIND.get(kind) or REASONING
        if effort:
            payload["reasoning_effort"] = effort
        t0 = time.time()
        rec = {"kind": kind, "reasoning": effort, "n_images": n_img,
               "payload_kb": round(len(json.dumps(payload)) / 1024, 1)}
        try:
            rec["timeout_s"] = TIMEOUT_BY_KIND.get(kind, TIMEOUT_S)
            with httpx.Client(timeout=rec["timeout_s"]) as c:
                r = c.post(f"{self.base_url}/chat/completions", json=payload,
                           headers={"Authorization": f"Bearer {self.api_key}"})
                rec["latency_s"] = round(time.time() - t0, 2)
                rec["http"] = r.status_code
                r.raise_for_status()
                data = r.json()
            rec["usage"] = data.get("usage")
            content = data["choices"][0]["message"]["content"]
            rec["raw"] = content
            out = json.loads(content)
            if kind == "tokenize":
                rec["canonical_coerced"] = coerce_canonical(out)                  # H8
                self.last_tokens = out
            elif kind == "plan":
                rec["canonical_coerced"] = coerce_plan(out)                       # H8
            elif kind == "synthesize":
                rec["canonical_coerced"] = coerce_skill(out)                      # H8
            return out
        except Exception as e:
            rec.setdefault("latency_s", round(time.time() - t0, 2))
            rec["error"] = f"{type(e).__name__}: {e}"[:500]
            raise
        finally:
            self.calls.append(rec)


class MeteredVoice(GrokVoiceClient):
    def __init__(self, api_key: str | None, grok: "MeteredGrok | None" = None):
        super().__init__(api_key=api_key or "")
        self.grok = grok
        self.api_key = api_key or ""      # Groky ricadrebbe su XAI_API_KEY, che e' la chiave OpenRouter
        self.use_mock = False
        self.result: dict | None = None
        self.latency_s = None

    def analyze_video_audio(self, video_path, fallback_prompt=None):
        t0 = time.time()
        if NO_VOICE:
            res = {"has_audio": True, "source": "disabled_by_harness", "transcript": "", "intent_summary": ""}
        elif not self.api_key:
            # senza chiave Groky andrebbe in mock con una frase inventata: lo evitiamo
            res = {"has_audio": True, "source": "skipped_no_xai_key", "transcript": "", "intent_summary": ""}
        else:
            res = super().analyze_video_audio(video_path, fallback_prompt)
        self.latency_s = round(time.time() - t0, 2)
        if VOICE_STEPS and self.grok and res.get("transcript"):                     # H10
            t1 = time.time()
            steps = self.grok.chat_json([{"role": "system", "content": VOICE_STEPS_SYSTEM},
                                         {"role": "user", "content": res["transcript"]}], kind="voice_steps")
            res["intent_summary"] = json.dumps(steps, ensure_ascii=False)
            res["voice_steps_latency_s"] = round(time.time() - t1, 2)
        self.result = res
        return res


class BenchRoboDK(RoboDKExecutor):
    """RoboDKExecutor di Groky + creazione della scena dai token di Grok (H5) + eccezioni -> step falliti (H4)."""

    def __init__(self, grok: "MeteredGrok", scene_path: Path, **kw):
        super().__init__(**kw)
        self.grok, self.scene_path = grok, scene_path
        self.scene: list[dict] = []
        self.native_miss: list[str] = []
        self.errors: list[str] = []

    def connect(self) -> bool:
        ok = super().connect()
        self.scene = build_scene_from_tokens(self.grok.last_tokens or {}, self.scene_path)
        return ok

    def execute(self, primitive, args):
        if not self.rdk:
            self.connect()
        if primitive == "MOVE_ABOVE" and args.get("target") and not self.rdk.Item(str(args["target"])).Valid():
            self.native_miss.append(str(args["target"]))   # MOVE_ABOVE di Groky non fa nulla in silenzio
        try:
            return super().execute(primitive, args)
        except Exception as e:  # H4
            msg = f"{type(e).__name__}: {e}"[:300]
            self.errors.append(msg)
            return StepFeedback(success=False, message=msg)


# --------------------------------------------------------------------------- RoboDK
def robodk_up() -> bool:
    try:
        socket.create_connection(("localhost", int(os.environ.get("ROBODK_PORT", 20500))), 2).close()
        return True
    except OSError:
        return False


def clear_station() -> None:
    from robodk import robolink as R
    rdk = R.Robolink()
    for t in rdk.ItemList(R.ITEM_TYPE_TOOL):
        t.DetachAll()
    for o in rdk.ItemList(R.ITEM_TYPE_OBJECT):
        o.Delete()
    run_setup(None)


def run_setup(scene: Path | None) -> str:
    cmd = [str(CV / ".venv/bin/python"), "-m", "vision.planner.robodk_setup"]
    if scene is None:
        empty = BENCH / "results" / ".empty_scene.json"
        empty.parent.mkdir(parents=True, exist_ok=True)
        empty.write_text("[]")
        scene = empty
    out = subprocess.run(cmd + ["--scene", str(scene)], cwd=CV, capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise RuntimeError(f"robodk_setup fallito: {out.stdout}{out.stderr}")
    return out.stdout


def build_scene_from_tokens(tokens: dict, path: Path) -> list[dict]:
    """Oggetti di scene_objects (piu' quelli citati nei token) -> scatole in RoboDK con il nome di Grok."""
    seen: dict[str, str] = {}
    for o in (tokens.get("scene_objects") or []) + [o for t in tokens.get("tokens") or [] for o in t.get("objects") or []]:
        n, role = o.get("name"), o.get("role") or "other"
        if not n or role in ("obstacle", "tool") or any(k in n.lower() for k in NOT_OBJECTS):
            continue
        # contenitore se Grok lo usa come contenitore/destinazione in qualunque punto (es. secchio spostato e poi riempito)
        if n not in seen or role in CONTAINER_ROLES:
            seen[n] = role
    src, cont, scene = iter(SOURCE_SLOTS), iter(CONTAINER_SLOTS), []
    for n, role in seen.items():
        is_cont = role in CONTAINER_ROLES
        xyz = next(cont if is_cont else src, None)
        if xyz is None:
            continue
        scene.append({"name": n, "role": role, "label": "bucket" if is_cont else "part",
                      "position_mm": {"x": xyz[0], "y": xyz[1], "z": xyz[2]}, "source": "grok_scene_objects"})
    path.write_text(json.dumps(scene, indent=2, ensure_ascii=False))
    run_setup(path)
    return scene


def final_scene() -> dict:
    from robodk import robolink as R
    rdk = R.Robolink()
    objs = {}
    for o in rdk.ItemList(R.ITEM_TYPE_OBJECT):
        objs[o.Name()] = {"pos_mm": [round(v, 1) for v in o.PoseAbs().Pos()], "parent": o.Parent().Name()}
    robot = rdk.Item("UR5e", R.ITEM_TYPE_ROBOT)
    return {"objects": objs, "tcp_mm": [round(v, 1) for v in robot.Pose().Pos()]}


def task_success(scene: dict, built: list[dict]) -> tuple[bool, str]:
    """Riuscito se TUTTI gli oggetti non-contenitore finiscono (staccati dalla pinza) dentro il contenitore
    principale, nella sua posizione finale (il contenitore puo' essere stato spostato)."""
    o = scene["objects"]
    srcs = [b["name"] for b in built if b["role"] not in CONTAINER_ROLES and b["name"] in o]
    conts = [b["name"] for b in built if b["role"] in CONTAINER_ROLES and b["name"] in o]
    if not srcs or not conts:
        return False, f"scena incompleta da Grok: da prendere={srcs}, contenitori={conts}"
    main = next((c for c in conts if is_bucket(c)), conts[0])
    c = o[main]
    inside, detail = [], []
    for n in srcs:
        s = o[n]
        dx, dy = s["pos_mm"][0] - c["pos_mm"][0], s["pos_mm"][1] - c["pos_mm"][1]
        ok = s["parent"] != "Gripper" and abs(dx) <= 80 and abs(dy) <= 80     # contenitore 160x160 mm
        inside.append(ok)
        detail.append(f"{n}: {'dentro' if ok else 'fuori'} (dx={dx:.0f} dy={dy:.0f}"
                      + (", in pinza" if s["parent"] == "Gripper" else "") + ")")
    return all(inside), f"{sum(inside)}/{len(srcs)} in '{main}': " + "; ".join(detail)


# --------------------------------------------------------------------------- metriche token
def score_tokens(tokens: list[dict], gt: dict, duration: float) -> dict:
    toks = [{"t_start": t["t_start_s"], "t_end": t["t_end_s"], **t} for t in tokens]
    req = [a for a in gt["actions"] if not a.get("optional")]
    used, ious, matched = set(), [], 0
    for a in req:
        best, bi = 0.0, None
        for i, t in enumerate(toks):
            if i in used or t["canonical"] not in ACCEPT.get(a["canonical"], {a["canonical"]}):
                continue
            v = iou(a["t_start"], a["t_end"], t["t_start"], t["t_end"])
            if v > best:
                best, bi = v, i
        if bi is not None and best >= 0.3:
            used.add(bi); matched += 1; ious.append(best)
    fp = sum(1 for i, t in enumerate(toks) if i not in used and not any(
        a.get("optional") and t["canonical"] in a.get("accept_canonical", [])
        and iou(a["t_start"], a["t_end"], t["t_start"], t["t_end"]) > 0 for a in gt["actions"]))
    names = [o["name"] for t in toks for o in t.get("objects", [])]
    return {
        "n_tokens": len(toks), "recall": round(matched / len(req), 3),
        "precision": round(matched / len(toks), 3) if toks else 0.0, "false_pos": fp,
        "mean_iou": round(st.mean(ious), 3) if ious else 0.0,
        "coverage_s": round(max((t["t_end"] for t in toks), default=0.0), 2), "duration_s": duration,
        "release_detected": any(t["canonical"] in ("release", "insert") and t["t_end"] >= gt.get("release_after_s", 12.0) for t in toks),
        "bucket_named": any(is_bucket(n) for n in names),
        "orange_named": any(any(k in n.lower() for k in ORANGE) for n in names),
        "sequence": [t["canonical"] for t in toks],
    }


# --------------------------------------------------------------------------- run
def run_once(video: Path, gt: dict, rep_dir: Path) -> dict:
    rep_dir = rep_dir.resolve()
    rep_dir.mkdir(parents=True, exist_ok=True)
    lib_path = rep_dir / "skill_library.json"
    shutil.copy(GROKY / "data/skills/skill_library.json", lib_path)          # H1
    clear_station()                                                           # H5
    grok = MeteredGrok()
    voice = MeteredVoice(os.environ.get("XAI_STT_API_KEY", "").strip(), grok)  # H2/H3/H10
    ex = BenchRoboDK(grok, rep_dir / "scene_from_grok.json", robot_name="UR5e", tool_name="Gripper")
    agent = PlanningAgent(library=SkillLibrary(path=lib_path), client=grok, voice_client=voice)
    cwd = os.getcwd()
    os.chdir(rep_dir)                     # storyboard e wav di Groky finiscono in rep_dir/logs/
    t0, err, state = time.time(), None, None
    try:
        state = agent.run_pipeline(video_path=str(video), executor=ex, run_log_dir=rep_dir / "runs")
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:800]
        (rep_dir / "traceback.txt").write_text(traceback.format_exc())
    finally:
        os.chdir(cwd)
    wall = round(time.time() - t0, 1)
    if state is None:   # Groky persiste lo stato solo internamente: recupero l'ultimo run log
        logs = sorted((rep_dir / "runs").glob("run_*.json"))
        state_d = json.loads(logs[-1].read_text()) if logs else {}
    else:
        state_d = state.model_dump(mode="json")
    sc = final_scene()
    ok, why = task_success(sc, ex.scene)
    tokens = (state_d.get("tokens") or {}).get("tokens") or []
    steps = state_d.get("steps") or []
    usage = [c.get("usage") or {} for c in grok.calls]
    res = {
        "rep": rep_dir.name, "status": state_d.get("status"), "exception": err, "wall_s": wall,
        "voice": {**(voice.result or {}), "latency_s": voice.latency_s},
        "calls": [{k: v for k, v in c.items() if k != "raw"} for c in grok.calls],
        "n_calls": len(grok.calls),
        "n_images": sum(c["n_images"] for c in grok.calls),
        "prompt_tok": sum(u.get("prompt_tokens", 0) for u in usage),
        "compl_tok": sum(u.get("completion_tokens", 0) for u in usage),
        "cost_usd": round(sum(float(u.get("cost") or 0) or (u.get("cost_in_usd_ticks") or 0) / 1e10 for u in usage), 4),
        "reasoning_tok": sum((u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) for u in usage),
        "canonical_coerced": [x for c in grok.calls for x in c.get("canonical_coerced", [])],
        "tokens_metrics": score_tokens(tokens, gt, gt["duration_s"]),
        "plan": [f"{c['skill']}({', '.join(f'{k}={v}' for k, v in c['args'].items())})"
                 for c in (state_d.get("plan") or {}).get("calls", [])],
        "missing_skills": [m["name"] for m in (state_d.get("plan") or {}).get("missing_skills", [])],
        "learned": [s["id"] for s in state_d.get("learned_skills", [])],
        "steps_done": sum(s["status"] == "done" for s in steps), "steps_total": len(steps),
        "robodk_errors": ex.errors, "native_name_miss": ex.native_miss,
        "scene_from_grok": [(b["name"], b["role"]) for b in ex.scene],
        "task_success": ok, "task_detail": why, "final_scene": sc,
        "events": state_d.get("events", []),
    }
    (rep_dir / "state.json").write_text(json.dumps(state_d, indent=2, ensure_ascii=False))
    (rep_dir / "result.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    (rep_dir / "grok_calls.json").write_text(json.dumps(grok.calls, indent=2, ensure_ascii=False))
    return res


# --------------------------------------------------------------------------- report
def ms(xs, fmt="{:.2f}"):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "-"
    return fmt.format(xs[0]) if len(xs) == 1 else f"{fmt.format(st.mean(xs))} ± {fmt.format(st.stdev(xs))}"


def report(out: Path) -> Path:
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines() if l.strip()]
    tm = lambda k: [r["tokens_metrics"][k] for r in rows]  # noqa: E731
    lines = [f"# Benchmark Groky_Planner_Agent: video + audio su RoboDK", "",
             f"Video: `{rows[0].get('video')}`, {len(rows)} ripetizioni, modello `{os.environ.get('XAI_MODEL')}` "
             f"via `{os.environ.get('XAI_BASE_URL')}`, timeout {rows[0].get('timeout_s')} s per chiamata, voce Grok Voice Realtime (`{os.environ.get('XAI_VOICE_MODEL', 'grok-voice-latest')}`).", "",
             "## Riepilogo", "", "| metrica | valore |", "|---|---|"]
    summ = {
        "run completati (`completed`)": f"{sum(r['status'] == 'completed' for r in rows)}/{len(rows)}",
        "task riuscito (cilindro nel secchio)": f"{sum(r['task_success'] for r in rows)}/{len(rows)}",
        "voce trascritta (non vuota)": f"{sum(bool((r['voice'] or {}).get('transcript')) for r in rows)}/{len(rows)}",
        "rilascio finale rilevato nei token": f"{sum(r['tokens_metrics']['release_detected'] for r in rows)}/{len(rows)}",
        "recall azioni": ms(tm("recall")), "precision azioni": ms(tm("precision")),
        "IoU temporale medio": ms(tm("mean_iou")), "falsi positivi": ms(tm("false_pos"), "{:.1f}"),
        "copertura token (s)": ms(tm("coverage_s")) + f" su {rows[0]['tokens_metrics']['duration_s']}",
        "immagini inviate": ms([r["n_images"] for r in rows], "{:.0f}"),
        "chiamate Grok chat": ms([r["n_calls"] for r in rows], "{:.1f}"),
        "token prompt / completion / ragionamento": f"{ms([r['prompt_tok'] for r in rows], '{:.0f}')} / {ms([r['compl_tok'] for r in rows], '{:.0f}')} / {ms([r.get('reasoning_tok', 0) for r in rows], '{:.0f}')}",
        "costo API Grok (USD)": ms([r["cost_usd"] for r in rows], "{:.4f}"),
        "latenza voce (s)": ms([(r["voice"] or {}).get("latency_s") for r in rows]),
        "tempo totale run (s)": ms([r["wall_s"] for r in rows], "{:.1f}"),
        "step RoboDK completati": ", ".join(f"{r['steps_done']}/{r['steps_total']}" for r in rows),
    }
    lines += [f"| {k} | {v} |" for k, v in summ.items()]
    lines.insert(4, f"Modelli/ragionamento: {sorted({f"{r.get('model')} {r.get('reasoning')}" for r in rows})}.")
    lines += ["", "## Dettaglio per ripetizione", ""]
    for r in rows:
        v = r["voice"] or {}
        lines += [f"### {r['rep']} ({r.get('model')}, reasoning {r.get('reasoning')}): `{r['status']}`" + (f", eccezione `{r['exception']}`" if r["exception"] else ""), "",
                  f"- **Voce** [{v.get('source')}, {v.get('latency_s')} s]: trascrizione «{v.get('transcript') or '-'}»; "
                  f"intent «{(v.get('intent_summary') or '-')[:300]}»",
                  f"- **Token** ({r['tokens_metrics']['n_tokens']}): {' → '.join(r['tokens_metrics']['sequence']) or '-'}"
                  + (f" (convertiti H8: {r['canonical_coerced']})" if r.get("canonical_coerced") else ""),
                  f"- **Piano:** {' ; '.join(r['plan']) or '-'}" + (f"; skill mancanti: {r['missing_skills']}" if r["missing_skills"] else ""),
                  f"- **Skill apprese:** {r['learned'] or '-'}",
                  f"- **RoboDK:** {r['steps_done']}/{r['steps_total']} step; task {'OK' if r['task_success'] else 'FALLITO'} ({r['task_detail']})"
                  + (f"; errori: {r['robodk_errors']}" if r["robodk_errors"] else "")
                  + (f"; nomi non trovati: {r['native_name_miss']}" if r["native_name_miss"] else ""),
                  f"- **Scena creata dai token:** {r['scene_from_grok']}",
                  f"- **Chiamate:** " + ", ".join(f"{c['kind']} {c.get('latency_s')} s" + (f" ERR {c['error'][:80]}" if c.get('error') else "") for c in r["calls"]),
                  ""]
    p = out / "report.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(BENCH / "videos/whatsapp_voice.mp4"))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--out")
    ap.add_argument("--report", help="rigenera solo report.md da una cartella di risultati")
    ap.add_argument("--allow-no-voice", action="store_true")
    ap.add_argument("--timeout", type=float, default=100.0, help="timeout per chiamata Grok (nativo Groky: 60)")
    ap.add_argument("--reasoning", choices=["low", "medium", "high"], default="low", help="default per tutte le chiamate")
    ap.add_argument("--reasoning-video", choices=["low", "medium", "high"], help="solo tokenizzazione video")
    ap.add_argument("--timeout-video", type=float, help="timeout (s) della sola tokenizzazione video")
    ap.add_argument("--reasoning-voice", choices=["low", "medium", "high"], help="solo passi dalla voce (H10)")
    ap.add_argument("--voice-steps", action="store_true", help="trascrizione -> passi JSON in intent_summary")
    ap.add_argument("--model", default="grok-4.7")
    ap.add_argument("--no-voice", action="store_true", help="non trascrive l'audio (solo immagini)")
    a = ap.parse_args()
    global TIMEOUT_S, REASONING, MODEL, NO_VOICE, VOICE_STEPS
    TIMEOUT_S, REASONING, MODEL, NO_VOICE, VOICE_STEPS = a.timeout, a.reasoning, a.model, a.no_voice, a.voice_steps
    if a.timeout_video:
        TIMEOUT_BY_KIND["tokenize"] = a.timeout_video
    REASONING_BY_KIND.update({k: v for k, v in (("tokenize", a.reasoning_video), ("voice_steps", a.reasoning_voice)) if v})
    if a.report:
        print(report(Path(a.report)))
        return
    if not os.environ.get("XAI_API_KEY"):
        sys.exit("XAI_API_KEY mancante in computer-vision/.env")
    if not os.environ.get("XAI_STT_API_KEY", "").strip() and not (a.allow_no_voice or a.no_voice):
        sys.exit("XAI_STT_API_KEY (chiave xAI per Grok Voice) mancante in computer-vision/.env")
    if not robodk_up():
        sys.exit("RoboDK non risponde su localhost:20500: apri RoboDK con la stazione UR5e.")
    video = Path(a.video).resolve()
    vid = video.stem
    gt = json.loads((BENCH / f"videos/{vid}.gt.json").read_text())
    out = Path(a.out) if a.out else BENCH / "results" / f"{datetime.now():%Y%m%d_%H%M}_groky_voice"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "results.jsonl", "a", encoding="utf-8") as f:
        for i in range(1, a.reps + 1):
            print(f"[rep {i}/{a.reps}] ...", flush=True)
            r = run_once(video, gt, out / f"rep{i}")
            r["video"] = str(video.relative_to(CV))
            r["timeout_s"] = TIMEOUT_S
            r["model"] = MODEL or os.environ.get("XAI_MODEL")
            r["reasoning"] = {"default": REASONING or "default", **REASONING_BY_KIND}
            r["voice_enabled"] = not NO_VOICE
            f.write(json.dumps(r, ensure_ascii=False) + "\n"); f.flush()
            print(f"  status={r['status']} task={r['task_success']} voice={r['voice'].get('source')} "
                  f"tokens={r['tokens_metrics']['sequence']} plan={r['plan']} cost=${r['cost_usd']}", flush=True)
    print(report(out))


if __name__ == "__main__":
    main()
