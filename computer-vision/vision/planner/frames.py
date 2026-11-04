"""Estrazione frame a doppio flusso con ffmpeg (nessuna dipendenza da OpenCV).

- Flusso LQ: tutti i frame a fps nativo, 64x36 in scala di grigi, letti da pipe rawvideo
  come array numpy (N, H, W) -> usato per l'energia di movimento.
- Flusso HQ: gli stessi frame (stesso indice, stesso PTS) in JPEG ~720p su disco, oppure
  estratti on-demand solo per i keyframe selezionati.
Il binario ffmpeg è quello di sistema se presente, altrimenti quello di `imageio-ffmpeg`.
"""
from __future__ import annotations

import hashlib
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from pydantic import BaseModel, Field


class FFmpegError(RuntimeError):
    pass


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise FFmpegError("ffmpeg non trovato: installa con `brew install ffmpeg` "
                          "oppure `pip install imageio-ffmpeg`")


# ---------------------------------------------------------------------------
# Indice condiviso LQ/HQ
# ---------------------------------------------------------------------------

class FrameInfo(BaseModel):
    idx: int
    t: float
    motion: float = 0.0
    hq: Optional[str] = None  # path relativo alla cartella dell'indice
    selected: bool = False
    reason: Optional[str] = None


class Phrase(BaseModel):
    text: str
    t_start: float
    t_end: float
    idx_start: int = 0
    idx_end: int = 0
    action: bool = False


class FramesIndex(BaseModel):
    video: str
    sha1: str
    params: dict = Field(default_factory=dict)
    fps: float
    duration_s: float
    width: int
    height: int
    n_frames: int
    lq_size: tuple[int, int] = (64, 36)
    hq_mode: str = "all"
    has_audio: bool = False
    voice_status: str = "not_run"
    threshold: float = 0.0
    frames: list[FrameInfo] = Field(default_factory=list)
    transcript: list[Phrase] = Field(default_factory=list)

    @property
    def keyframes(self) -> list[FrameInfo]:
        return [f for f in self.frames if f.selected]


@dataclass
class VideoInfo:
    duration_s: float
    fps: float
    width: int
    height: int
    has_audio: bool


def file_sha1(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def probe(video: Path) -> VideoInfo:
    """Legge durata/fps/dimensioni da `ffmpeg -i` (ffprobe non è garantito)."""
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(video)],
                       capture_output=True, text=True)
    err = r.stderr
    if "Invalid data" in err or "No such file" in err:
        raise FFmpegError(f"video non leggibile: {video}\n{err[-400:]}")
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    vline = next((ln for ln in err.splitlines() if "Video:" in ln), "")
    size = re.search(r"\b(\d{2,5})x(\d{2,5})\b", vline)
    fps = re.search(r"([\d.]+)\s*fps", vline) or re.search(r"([\d.]+)\s*tbr", vline)
    return VideoInfo(
        duration_s=dur,
        fps=float(fps.group(1)) if fps else 30.0,
        width=int(size.group(1)) if size else 0,
        height=int(size.group(2)) if size else 0,
        has_audio=any("Audio:" in ln for ln in err.splitlines()),
    )


_SHOWINFO = re.compile(r"\bn:\s*(\d+)\b.*?\bpts_time:\s*([-\d.eE+]+)")


def extract_lq(video: Path, size: tuple[int, int] = (64, 36)) -> tuple[np.ndarray, list[float]]:
    """Tutti i frame decodificati, grigi e minuscoli, con il loro PTS reale (s)."""
    w, h = size
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostats", "-i", str(video), "-an",
           "-vf", f"scale={w}:{h},format=gray,showinfo", "-fps_mode", "passthrough",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise FFmpegError(f"estrazione LQ fallita: {r.stderr.decode(errors='replace')[-500:]}")
    n = len(r.stdout) // (w * h)
    frames = np.frombuffer(r.stdout[: n * w * h], dtype=np.uint8).reshape(n, h, w)
    pts = {int(a): float(b) for a, b in _SHOWINFO.findall(r.stderr.decode(errors="replace"))}
    times = [pts.get(i, float("nan")) for i in range(n)]
    # Fallback se showinfo non ha dato tutti i PTS: interpolazione lineare.
    if any(math.isnan(t) for t in times):
        known = [(i, t) for i, t in enumerate(times) if not math.isnan(t)]
        fps = probe(video).fps or 30.0
        times = [t if not math.isnan(t) else (known[0][1] if known else 0.0) + i / fps
                 for i, t in enumerate(times)]
    return frames, times


def _hq_scale(height: int) -> str:
    return f"scale=-2:'min({height},ih)'"


def start_hq_all(video: Path, out_dir: Path, height: int = 720) -> subprocess.Popen:
    """Avvia in parallelo l'estrazione di TUTTI i frame HQ: out_dir/000000.jpg, ..."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostats", "-loglevel", "error", "-y", "-i", str(video),
           "-an", "-vf", _hq_scale(height), "-fps_mode", "passthrough", "-q:v", "3",
           "-start_number", "0", str(out_dir / "%06d.jpg")]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def wait_hq(proc: subprocess.Popen) -> None:
    _, err = proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(f"estrazione HQ fallita: {(err or b'').decode(errors='replace')[-500:]}")


def extract_hq_at(video: Path, t: float, out_path: Path, height: int = 720) -> Path:
    """Estrae un solo frame HQ al PTS t (seek accurato; t arrotondato per difetto)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ts = math.floor(max(t, 0.0) * 10000) / 10000
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostats", "-loglevel", "error", "-y",
           "-ss", f"{ts:.4f}", "-i", str(video), "-frames:v", "1", "-an",
           "-vf", _hq_scale(height), "-q:v", "3", str(out_path)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not out_path.exists():
        raise FFmpegError(f"estrazione HQ a t={t} fallita: {r.stderr.decode(errors='replace')[-300:]}")
    return out_path


def extract_audio(video: Path, out_wav: Path) -> Optional[Path]:
    """Audio mono 16 kHz in WAV; None se il video non ha audio."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostats", "-loglevel", "error", "-y", "-i", str(video),
           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not out_wav.exists() or out_wav.stat().st_size < 1000:
        return None
    return out_wav


def write_motion_svg(path: Path, motion: np.ndarray, times: list[float], threshold: float,
                     keyframes: list[FrameInfo], width: int = 1000, height: int = 260) -> None:
    """Grafico di debug: energia di movimento, soglia, keyframe scelti."""
    n = len(motion)
    if n == 0:
        return
    top = float(max(motion.max(), threshold) * 1.1 or 1.0)
    t_end = times[-1] if times[-1] > 0 else 1.0
    px = lambda t: 40 + (width - 60) * t / t_end  # noqa: E731
    py = lambda v: height - 30 - (height - 50) * v / top  # noqa: E731
    pts = " ".join(f"{px(times[i]):.1f},{py(float(motion[i])):.1f}" for i in range(n))
    colors = {"motion_start": "#2a9d8f", "motion_peak": "#e76f51", "motion_end": "#264653",
              "motion_mid": "#8ab17d", "voice": "#9b5de5", "scene_start": "#888", "scene_end": "#888",
              "uniform": "#888"}
    marks = "".join(
        f'<line x1="{px(k.t):.1f}" y1="20" x2="{px(k.t):.1f}" y2="{height - 30}" '
        f'stroke="{colors.get(k.reason or "", "#888")}" stroke-width="1.5"/>'
        f'<text x="{px(k.t) + 2:.1f}" y="16" font-size="9" fill="#333">{k.idx}</text>'
        for k in keyframes)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'style="background:#fff;font-family:sans-serif">'
           f'<line x1="40" y1="{py(threshold):.1f}" x2="{width - 20}" y2="{py(threshold):.1f}" '
           f'stroke="#d62828" stroke-dasharray="4 3"/>{marks}'
           f'<polyline fill="none" stroke="#1d3557" stroke-width="1.5" points="{pts}"/>'
           f'<text x="40" y="{height - 10}" font-size="11">0 s</text>'
           f'<text x="{width - 60}" y="{height - 10}" font-size="11">{t_end:.2f} s</text>'
           f'<text x="{width - 260}" y="{height - 10}" font-size="11" fill="#d62828">'
           f'soglia {threshold:.4f}</text></svg>')
    path.write_text(svg, encoding="utf-8")
