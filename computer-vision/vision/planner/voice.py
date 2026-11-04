"""Voce come supporto: Grok STT -> parole con timestamp -> frasi allineate agli indici frame."""
from __future__ import annotations

import re
from typing import Sequence

from .frames import Phrase
from .grok_client import STTWord
from .mock_rules import canon_of
from .motion import nearest_idx

PHRASE_GAP_S = 0.6
_END_PUNCT = re.compile(r"[.!?;]$")

# Termini utili per l'STT (keyterm) in una dimostrazione di manipolazione.
DEFAULT_KEYTERMS = ["pinza", "tappo", "svitare", "avvitare", "secchio", "bullone", "anello", "vite"]


def words_to_phrases(words: Sequence[STTWord], times: list[float]) -> list[Phrase]:
    phrases: list[Phrase] = []
    cur: list[STTWord] = []

    def flush() -> None:
        if not cur:
            return
        text = " ".join(w.text for w in cur).strip()
        t0, t1 = cur[0].start, cur[-1].end
        phrases.append(Phrase(
            text=text, t_start=round(t0, 3), t_end=round(t1, 3),
            idx_start=nearest_idx(times, t0) if times else 0,
            idx_end=nearest_idx(times, t1) if times else 0,
            action=any(canon_of(w.text) != "unknown" for w in cur),
        ))
        cur.clear()

    for w in words:
        if cur and w.start - cur[-1].end > PHRASE_GAP_S:
            flush()
        cur.append(w)
        if _END_PUNCT.search(w.text):
            flush()
    flush()
    return phrases
