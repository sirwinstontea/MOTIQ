"""Tokenizzazione video: frame LQ -> keyframe -> frame HQ (+ voce) -> Grok vision -> token."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import frames as fr
from .grok_client import GrokError, GrokLike
from .library import atomic_write_json
from .motion import (adaptive_threshold, add_voice_keyframes, motion_energy, segment,
                     select_keyframes)
from .primitives import ROOT
from .prompts import TOKENIZE_SYSTEM, tokenize_user
from .schemas import ActionToken, SceneObject, TokenChunkResponse, VideoTokens, norm_name
from .voice import DEFAULT_KEYTERMS, words_to_phrases

DEFAULT_CACHE_DIR = ROOT / "logs" / "frames"
INDEX_NAME = "frames_index.json"
MERGE_GAP_S = 0.3  # token separati da una pausa più lunga restano azioni distinte
ALGO_VERSION = 2  # incrementare quando cambia l'algoritmo di movimento/keyframe (invalida la cache)


@dataclass
class TokenizerConfig:
    lq_size: tuple[int, int] = (64, 36)
    hq_mode: str = "auto"  # auto | all | on-demand
    hq_height: int = 720
    auto_all_max_s: float = 20.0
    k: float = 3.0  # soglia = mediana + k * MAD
    min_seg_s: float = 0.15
    merge_gap_s: float = 0.25
    max_seg_s: float = 1.5
    chunk_size: int = 8
    use_voice: bool = True
    language: Optional[str] = "it"
    keep_frames: bool = False
    debug: bool = False
    cache_dir: Path = field(default_factory=lambda: DEFAULT_CACHE_DIR)

    def key(self) -> str:
        d = asdict(self)
        for k in ("chunk_size", "keep_frames", "debug", "cache_dir"):
            d.pop(k)
        d["algo"] = ALGO_VERSION
        return hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:8]


Log = Callable[[str, str], None]


class VideoTokenizer:
    def __init__(self, grok: GrokLike, cfg: Optional[TokenizerConfig] = None, log: Optional[Log] = None):
        self.grok = grok
        self.cfg = cfg or TokenizerConfig()
        self.log: Log = log or (lambda kind, msg: None)

    # ------------------------------------------------------------------
    def build_index(self, video: Path | str) -> tuple[fr.FramesIndex, Path]:
        video = Path(video)
        if not video.exists():
            raise FileNotFoundError(video)
        cfg = self.cfg
        sha = fr.file_sha1(video)
        out = Path(cfg.cache_dir) / f"{sha[:12]}_{cfg.key()}"
        idx_path = out / INDEX_NAME
        if idx_path.exists():
            idx = fr.FramesIndex.model_validate_json(idx_path.read_text(encoding="utf-8"))
            if all(k.hq and (out / k.hq).exists() for k in idx.keyframes):
                self.log("frames_cached", f"indice in cache: {idx_path}")
                return idx, out
        out.mkdir(parents=True, exist_ok=True)

        info = fr.probe(video)
        mode = cfg.hq_mode
        if mode == "auto":
            mode = "all" if info.duration_s <= cfg.auto_all_max_s else "on-demand"
        hq_dir = out / "hq"
        proc = fr.start_hq_all(video, hq_dir, cfg.hq_height) if mode == "all" else None

        lq, times = fr.extract_lq(video, cfg.lq_size)
        motion = motion_energy(lq)
        thr = adaptive_threshold(motion, cfg.k)
        segs = segment(motion, times, thr, cfg.min_seg_s, cfg.merge_gap_s)
        kfs = select_keyframes(segs, times, cfg.max_seg_s)
        self.log("motion", f"{len(lq)} frame LQ, {len(segs)} segmenti di movimento, "
                           f"{len(kfs)} keyframe (soglia {thr:.4f})")

        phrases: list[fr.Phrase] = []
        voice_status = "disabled"
        if cfg.use_voice and info.has_audio:
            wav = fr.extract_audio(video, out / "audio.wav")
            if wav is None:
                voice_status = "skipped: audio non estraibile"
            else:
                try:
                    stt = self.grok.transcribe(wav, cfg.language, DEFAULT_KEYTERMS)
                    phrases = words_to_phrases(stt.words, times)
                    voice_status = f"ok: {len(stt.words)} parole, {len(phrases)} frasi"
                    kfs = add_voice_keyframes(kfs, phrases, times)
                except (GrokError, OSError, ValueError) as e:
                    voice_status = f"skipped: {e}"
        elif cfg.use_voice:
            voice_status = "skipped: video senza audio"
        self.log("voice", voice_status)

        if proc is not None:
            fr.wait_hq(proc)
        selected = {k.idx: k for k in kfs}
        infos: list[fr.FrameInfo] = []
        for i, t in enumerate(times):
            fi = fr.FrameInfo(idx=i, t=round(t, 5), motion=round(float(motion[i]), 6))
            if i in selected:
                fi.selected, fi.reason = True, selected[i].reason
                rel = f"hq/{i:06d}.jpg"
                if mode == "on-demand":
                    fr.extract_hq_at(video, t, out / rel, cfg.hq_height)
                if not (out / rel).exists():
                    raise fr.FFmpegError(f"frame HQ {rel} mancante (LQ e HQ disallineati?)")
                fi.hq = rel
            infos.append(fi)

        if mode == "all" and not cfg.keep_frames:
            keep = {f"{i:06d}.jpg" for i in selected}
            for p in hq_dir.glob("*.jpg"):
                if p.name not in keep:
                    p.unlink()

        idx = fr.FramesIndex(
            video=str(video), sha1=sha, params=asdict(cfg) | {"cache_dir": str(cfg.cache_dir)},
            fps=info.fps, duration_s=info.duration_s or (times[-1] if times else 0.0),
            width=info.width, height=info.height, n_frames=len(times), lq_size=cfg.lq_size,
            hq_mode=mode, has_audio=info.has_audio, voice_status=voice_status, threshold=thr,
            frames=infos, transcript=phrases,
        )
        atomic_write_json(idx_path, idx.model_dump(mode="json"))
        if cfg.debug:
            fr.write_motion_svg(out / "motion.svg", motion, times, thr, idx.keyframes)
            self.log("debug", f"grafico movimento: {out / 'motion.svg'}")
        return idx, out

    # ------------------------------------------------------------------
    def tokenize(self, video: Path | str, goal_hint: Optional[str] = None) -> VideoTokens:
        idx, out = self.build_index(video)
        kfs = idx.keyframes
        tokens: list[ActionToken] = []
        scene: dict[str, SceneObject] = {}
        cs = max(1, self.cfg.chunk_size)
        for c in range(0, len(kfs), cs):
            chunk = kfs[c:c + cs]
            t0, t1 = chunk[0].t - 0.5, chunk[-1].t + 0.5
            phr = [p.model_dump() for p in idx.transcript if p.t_end >= t0 and p.t_start <= t1]
            fdicts = [{"idx": k.idx, "t": k.t, "reason": k.reason} for k in chunk]
            prev = [t.model_dump(exclude={"frame_idx"}) for t in tokens[-3:]]
            images = [(out / k.hq).read_bytes() for k in chunk if k.hq]
            user = tokenize_user(fdicts, phr, prev, goal_hint)
            self.log("grok_tokenize", f"chunk {c // cs + 1}: {len(images)} immagini")
            resp = self.grok.chat_json("tokenize", TOKENIZE_SYSTEM, user, TokenChunkResponse,
                                       images=images, context={"keyframes": fdicts, "phrases": phr})
            for t in resp.tokens:
                if not t.frame_idx:
                    t.frame_idx = [k.idx for k in chunk if t.t_start - 0.05 <= k.t <= t.t_end + 0.05]
                tokens.append(t)
            for o in resp.scene_objects:
                scene.setdefault(norm_name(o.name), o)
        tokens = merge_tokens(tokens)
        for i, t in enumerate(tokens, 1):
            t.id = f"tok_{i:03d}"
            for o in t.objects:
                scene.setdefault(norm_name(o.name), SceneObject(name=o.name, color=o.color))
        return VideoTokens(
            video=str(video), fps=idx.fps, duration_s=idx.duration_s,
            keyframes=[k.idx for k in kfs],
            transcript=" ".join(p.text for p in idx.transcript),
            goal_hint=goal_hint, tokens=tokens, scene_objects=list(scene.values()),
        )


def merge_tokens(tokens: list[ActionToken]) -> list[ActionToken]:
    """Unisce token adiacenti con stesso verbo/canonical e stesso oggetto target."""
    out: list[ActionToken] = []
    for t in sorted(tokens, key=lambda x: (x.t_start, x.t_end)):
        if out:
            p = out[-1]
            pt, tt = p.primary("target"), t.primary("target")
            same_obj = (pt is None and tt is None) or (pt and tt and norm_name(pt.name) == norm_name(tt.name))
            pd = p.motion.direction if p.motion else "none"
            td = t.motion.direction if t.motion else "none"
            same_dir = pd == td or "none" in (pd, td)
            contiguous = t.t_start - p.t_end <= MERGE_GAP_S
            if (p.canonical == t.canonical and norm_name(p.verb) == norm_name(t.verb) and same_obj
                    and same_dir and contiguous):
                p.t_end = max(p.t_end, t.t_end)
                p.frame_idx = sorted(set(p.frame_idx) | set(t.frame_idx))
                p.confidence = max(p.confidence, t.confidence)
                if t.rotation and not p.rotation:
                    p.rotation = t.rotation
                if t.speech and t.speech not in (p.speech or ""):
                    p.speech = f"{p.speech} {t.speech}".strip() if p.speech else t.speech
                if t.gripper != "unknown":
                    p.gripper = t.gripper
                continue
        out.append(t.model_copy(deep=True))
    return out
