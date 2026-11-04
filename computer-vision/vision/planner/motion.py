"""Energia di movimento sul flusso LQ e scelta dei keyframe.

Per ogni segmento di movimento si tengono 3 keyframe: inizio (frame prima del moto),
picco, fine/pausa (dove avvengono presa e rilascio). Più il primo e l'ultimo frame.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

import numpy as np

from .frames import Phrase


@dataclass(frozen=True)
class Segment:
    start: int  # primo frame sopra soglia
    peak: int
    end: int  # ultimo frame sopra soglia


@dataclass(frozen=True)
class Keyframe:
    idx: int
    t: float
    reason: str


def motion_energy(frames: np.ndarray, smooth: int = 5, pix_delta: int = 12) -> np.ndarray:
    """Frazione di pixel LQ cambiati di più di `pix_delta` livelli di grigio rispetto al frame
    precedente, smussata con media mobile. motion[0] = 0.

    Rispetto alla media delle differenze è robusta al rumore del sensore (che sul frame 64x36
    resta sotto pix_delta) e non penalizza oggetti/mani piccoli nell'inquadratura.
    """
    n = len(frames)
    if n < 2:
        return np.zeros(n, dtype=np.float64)
    f = frames.astype(np.int16)
    changed = np.abs(f[1:] - f[:-1]).reshape(n - 1, -1) > pix_delta
    m = np.concatenate([[0.0], changed.mean(axis=1)])
    if smooth > 1:
        k = np.ones(smooth) / smooth
        m = np.convolve(m, k, mode="same")
    return m


def adaptive_threshold(motion: np.ndarray, k: float = 3.0, floor: float = 0.002) -> float:
    """mediana + k * MAD (scalata), con un minimo per i video quasi statici."""
    if len(motion) == 0:
        return floor
    med = float(np.median(motion))
    mad = float(np.median(np.abs(motion - med))) * 1.4826
    return max(med + k * mad, floor)


def segment(motion: np.ndarray, times: list[float], threshold: float,
            min_seg_s: float = 0.15, merge_gap_s: float = 0.25) -> list[Segment]:
    active = motion > threshold
    runs: list[list[int]] = []
    i, n = 0, len(motion)
    while i < n:
        if active[i]:
            j = i
            while j + 1 < n and active[j + 1]:
                j += 1
            runs.append([i, j])
            i = j + 1
        else:
            i += 1
    merged: list[list[int]] = []
    for r in runs:
        if merged and times[r[0]] - times[merged[-1][1]] < merge_gap_s:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    out = []
    for s, e in merged:
        if times[e] - times[s] < min_seg_s:
            continue
        peak = s + int(np.argmax(motion[s:e + 1]))
        out.append(Segment(s, peak, e))
    return out


def select_keyframes(segments: list[Segment], times: list[float], max_seg_s: float = 1.5,
                     uniform_s: float = 1.0) -> list[Keyframe]:
    n = len(times)
    if n == 0:
        return []
    picked: dict[int, str] = {}

    def add(i: int, reason: str) -> None:
        i = min(max(i, 0), n - 1)
        picked.setdefault(i, reason)

    add(0, "scene_start")
    if not segments:  # nessun movimento rilevato: campionamento uniforme
        t, i = 0.0, 0
        while i < n:
            add(i, "uniform")
            t += uniform_s
            i = bisect_left(times, t)
    for sg in segments:
        add(sg.start - 1, "motion_start")
        add(sg.peak, "motion_peak")
        add(sg.end + 1, "motion_end")
        t = times[sg.start] + max_seg_s
        while t < times[sg.end] - max_seg_s / 2:
            add(bisect_left(times, t), "motion_mid")
            t += max_seg_s
    add(n - 1, "scene_end")
    return [Keyframe(i, times[i], r) for i, r in sorted(picked.items())]


def nearest_idx(times: list[float], t: float) -> int:
    i = bisect_left(times, t)
    if i <= 0:
        return 0
    if i >= len(times):
        return len(times) - 1
    return i if times[i] - t < t - times[i - 1] else i - 1


def add_voice_keyframes(keyframes: list[Keyframe], phrases: list[Phrase], times: list[float],
                        tol_s: float = 0.3) -> list[Keyframe]:
    """Aggiunge un keyframe al centro di ogni frase d'azione non già coperta (± tol_s)."""
    out = list(keyframes)
    for ph in phrases:
        if not ph.action:
            continue
        tc = (ph.t_start + ph.t_end) / 2
        if any(abs(k.t - tc) <= tol_s for k in out):
            continue
        i = nearest_idx(times, tc)
        if all(k.idx != i for k in out):
            out.append(Keyframe(i, times[i], "voice"))
    return sorted(out, key=lambda k: k.idx)
