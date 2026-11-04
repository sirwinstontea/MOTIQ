"""Tavolo vuoto di riferimento: colore e profondità mediani su qualche frame.

La camera del braccio è fissa sopra il tavolo, quindi un oggetto è "ciò che è cambiato" rispetto al tavolo vuoto:
più vicino alla camera (profondità) o di colore diverso. Va registrato di nuovo se la camera si sposta.

    bg = TableBackground()
    while not bg.add_frame(color, depth):   # 15 frame, senza oggetti sul tavolo
        color, depth = leggi()
    bg.save("data/calibration/table_empty_arm.npz")
"""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np


class TableBackground:
    def __init__(self, n_frames: int = 15):
        self.n_frames = max(1, int(n_frames))
        self.color: np.ndarray | None = None   # BGR uint8
        self.depth: np.ndarray | None = None   # mm uint16, 0 = nessuna misura
        self._colors: list[np.ndarray] = []
        self._depths: list[np.ndarray] = []
        self.version = 0  # cresce a ogni nuovo riferimento: chi tiene cache sa quando buttarle

    @property
    def ready(self) -> bool:
        return self.color is not None

    @property
    def has_depth(self) -> bool:
        return self.depth is not None

    @property
    def collecting(self) -> bool:
        return bool(self._colors)

    def reset(self) -> None:
        self.color = self.depth = None
        self.version += 1
        self._colors, self._depths = [], []

    def add_frame(self, color: np.ndarray, depth: np.ndarray | None = None) -> bool:
        """Aggiunge un frame del tavolo vuoto. Restituisce True quando il riferimento è pronto."""
        if self._colors and color.shape != self._colors[0].shape:
            self._colors, self._depths = [], []  # risoluzione cambiata: si riparte
        self._colors.append(color.copy())
        if depth is not None:
            self._depths.append(depth.copy())
        if len(self._colors) < self.n_frames:
            return False
        self.color = np.median(np.stack(self._colors), axis=0).astype(np.uint8)
        self.depth = _median_depth(self._depths) if len(self._depths) == len(self._colors) else None
        self._colors, self._depths = [], []
        self.version += 1
        return True

    def matches(self, frame: np.ndarray) -> bool:
        """Il riferimento vale solo per frame della stessa risoluzione."""
        return self.ready and frame.shape[:2] == self.color.shape[:2]

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"color": self.color}
        if self.depth is not None:
            data["depth"] = self.depth
        np.savez_compressed(path, **data)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "TableBackground":
        bg = cls()
        with np.load(Path(path)) as data:
            bg.color = data["color"].astype(np.uint8)
            bg.depth = data["depth"].astype(np.uint16) if "depth" in data.files else None
        bg.version = 1
        return bg


def _median_depth(depths: list[np.ndarray]) -> np.ndarray:
    """Mediana per pixel ignorando gli zeri (pixel senza misura). Resta 0 dove non c'è mai stata una misura."""
    stack = np.stack(depths).astype(np.float32)
    stack[stack <= 0] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)  # "All-NaN slice" dove non c'è mai misura
        med = np.nanmedian(stack, axis=0)
    return np.nan_to_num(med, nan=0.0).astype(np.uint16)
