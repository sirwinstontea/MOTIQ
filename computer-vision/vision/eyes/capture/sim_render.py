"""D435 simulata senza licenza RoboDK: render software (numpy + OpenCV) della stazione RoboDK.

RoboDK senza licenza dà snapshot da 100x75 e blocca la camera di profondità ("Invalid license"). Qui da RoboDK si
leggono solo le mesh degli oggetti (una volta) e le loro pose (a ogni frame); colore e profondità si disegnano qui,
con gli stessi intrinseci della D435 simulata. Il braccio stesso non viene disegnato (la camera guarda il tavolo).

Come funziona: i triangoli grandi (il tavolo) vengono suddivisi, poi disegnati dal più lontano al più vicino
(algoritmo del pittore) in un'immagine di indici; la profondità di ogni pixel è l'intersezione esatta del raggio con
il piano del suo triangolo, quindi è giusta anche sui piani inclinati. Uscita come la RealSense: BGR uint8 e Z in mm
uint16 (0 = nessuna misura).
"""
from __future__ import annotations

import time
from typing import Any

import cv2
import numpy as np

from .realsense import Intrinsics

try:  # i numeri veri da robolink; i fallback servono solo ai test senza il pacchetto
    from robodk.robolink import FEATURE_OBJECT_MESH, ITEM_TYPE_OBJECT  # type: ignore
except ImportError:  # pragma: no cover - dipende dall'ambiente
    ITEM_TYPE_OBJECT, FEATURE_OBJECT_MESH = 5, 4

BACKGROUND_BGR = (58, 52, 48)


def subdivide(tris: np.ndarray, max_edge: float) -> np.ndarray:
    """(n, 3, 3) -> triangoli grandi (lato > max_edge e area > max_edge²/4) suddivisi in 4 dai punti medi.
    Quelli lunghi e sottili (le pareti di un cilindro) restano come sono: per l'ordine del pittore non danno noia."""
    out, todo = [], np.asarray(tris, dtype=np.float64)
    for _ in range(12):
        if not len(todo):
            break
        edges = np.linalg.norm(todo - np.roll(todo, -1, axis=1), axis=2).max(axis=1)
        area = 0.5 * np.linalg.norm(np.cross(todo[:, 1] - todo[:, 0], todo[:, 2] - todo[:, 0]), axis=1)
        big = (edges > max_edge) & (area > max_edge ** 2 / 4)
        out.append(todo[~big])
        t = todo[big]
        a, b, c = t[:, 0], t[:, 1], t[:, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        todo = np.concatenate([np.stack(s, axis=1) for s in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))])
    out.append(todo)
    return np.concatenate(out)


class _Mesh:
    def __init__(self, item: Any, name: str, tris_m: np.ndarray, rgb: tuple[float, float, float]):
        self.item, self.name, self.tris, self.rgb = item, name, tris_m, rgb


def _mat_to_np_m(pose: Any) -> np.ndarray:
    T = np.array([[float(pose[i, j]) for j in range(4)] for i in range(4)])
    T[:3, 3] /= 1000.0
    return T


def load_meshes(rdk: Any, max_edge_m: float = 0.05, out=None) -> list[_Mesh]:
    """Mesh di tutti gli oggetti della stazione, in m nel frame dell'oggetto."""
    meshes = []
    for item in rdk.ItemList(ITEM_TYPE_OBJECT):
        pts = []
        for fid in range(10000):  # RoboDK divide una mesh in più feature
            try:
                fp, _ = item.GetPoints(FEATURE_OBJECT_MESH, fid)
            except Exception:  # noqa: BLE001
                break
            if not fp:
                break
            pts += [p[:3] for p in fp]
        if len(pts) < 3:
            continue
        tris = np.asarray(pts[: len(pts) // 3 * 3], dtype=np.float64).reshape(-1, 3, 3) / 1000.0
        rgb = tuple(float(c) for c in list(item.Color())[:3])
        meshes.append(_Mesh(item, item.Name(), subdivide(tris, max_edge_m), rgb))
        if out:
            out(f"  {item.Name()}: {len(tris)} triangoli -> {len(meshes[-1].tris)}")
    return meshes


class SoftwareD435:
    """Render colore + profondità della stazione RoboDK da una posa della camera (frame ottico: Z avanti, Y giù)."""

    def __init__(self, rdk: Any, intrinsics: Intrinsics, far_mm: float = 2000.0, min_depth_mm: float = 150.0,
                 depth_noise: bool = True, max_edge_m: float = 0.05, refresh_s: float = 5.0):
        self.rdk, self.K = rdk, intrinsics
        self.far_m, self.min_m = far_mm / 1000.0, min_depth_mm / 1000.0
        self.depth_noise, self.max_edge_m, self.refresh_s = depth_noise, max_edge_m, refresh_s
        self.meshes = load_meshes(rdk, max_edge_m)
        self._loaded_at = time.time()
        self._rng = np.random.default_rng()
        k = self.K
        vs, us = np.mgrid[0:k.height, 0:k.width]
        self._rays = np.stack([(us + 0.5 - k.cx) / k.fx, (vs + 0.5 - k.cy) / k.fy, np.ones_like(us, float)], axis=-1)

    def _maybe_reload(self) -> None:
        """Nuovi oggetti aggiunti nella stazione: ricarica le mesh ogni tanto."""
        if time.time() - self._loaded_at < self.refresh_s:
            return
        self._loaded_at = time.time()
        names = sorted(m.name for m in self.meshes)
        try:
            now = sorted(it.Name() for it in self.rdk.ItemList(ITEM_TYPE_OBJECT))
        except Exception:  # noqa: BLE001
            return
        if now != names:
            self.meshes = load_meshes(self.rdk, self.max_edge_m)

    def render(self, T_W_C: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """`T_W_C`: camera nel frame assoluto della stazione (m). -> (BGR uint8, Z mm uint16)."""
        self._maybe_reload()
        k = self.K
        T_C_W = np.linalg.inv(T_W_C)
        all_tris, all_rgb = [], []
        for m in self.meshes:
            try:
                if not m.item.Visible():
                    continue
                T = T_C_W @ _mat_to_np_m(m.item.PoseAbs())
            except Exception:  # noqa: BLE001 - oggetto cancellato nel frattempo
                continue
            tris = m.tris @ T[:3, :3].T + T[:3, 3]
            all_tris.append(tris)
            all_rgb.append(np.broadcast_to(np.asarray(m.rgb), (len(tris), 3)))
        color = np.empty((k.height, k.width, 3), np.uint8)
        color[:] = BACKGROUND_BGR
        depth = np.zeros((k.height, k.width), np.uint16)
        if not all_tris:
            return color, depth
        tris, rgb = np.concatenate(all_tris), np.concatenate(all_rgb)

        z = tris[:, :, 2]
        keep = (z > 0.01).all(axis=1) & (z.min(axis=1) < self.far_m)  # niente triangoli dietro la camera
        tris, rgb = tris[keep], rgb[keep]
        uv = np.empty(tris.shape[:2] + (2,))
        uv[..., 0] = tris[..., 0] / tris[..., 2] * k.fx + k.cx
        uv[..., 1] = tris[..., 1] / tris[..., 2] * k.fy + k.cy
        lo, hi = uv.min(axis=1), uv.max(axis=1)
        on = (hi[:, 0] >= 0) & (lo[:, 0] < k.width) & (hi[:, 1] >= 0) & (lo[:, 1] < k.height)
        tris, rgb, uv = tris[on], rgb[on], uv[on]
        n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        nn = np.linalg.norm(n, axis=1)
        ok = nn > 1e-12
        tris, rgb, uv, n = tris[ok], rgb[ok], uv[ok], n[ok] / nn[ok, None]
        if not len(tris):
            return color, depth

        order = np.argsort(-tris[:, :, 2].mean(axis=1))  # dal più lontano
        ids = np.full((k.height, k.width), -1, np.int32)
        poly = np.rint(uv * 16).astype(np.int32)       # coordinate a 4 bit di sotto-pixel
        for i in order:
            cv2.fillConvexPoly(ids, poly[i], int(i), lineType=cv2.LINE_8, shift=4)

        hit = ids >= 0
        tid = ids[hit]
        rays = self._rays[hit]
        nt, p0 = n[tid], tris[tid, 0]
        denom = (nt * rays).sum(axis=1)
        zz = (nt * p0).sum(axis=1) / np.where(np.abs(denom) < 1e-9, 1e-9, denom)  # Z, perché rays[:, 2] = 1
        zmean = tris[tid, :, 2].mean(axis=1)
        bad = ~np.isfinite(zz) | (zz <= 0) | (np.abs(zz - zmean) > 0.5)
        zz[bad] = zmean[bad]

        # luce "frontale" sulla camera, a due facce: forme leggibili senza ombre
        cosang = np.abs(denom) / np.linalg.norm(rays, axis=1)
        shade = 0.35 + 0.65 * cosang
        col = np.clip(rgb[tid] * shade[:, None] * 255.0, 0, 255)
        color[hit] = col[:, ::-1].astype(np.uint8)  # RGB -> BGR

        zmm = zz * 1000.0
        if self.depth_noise:  # rumore della D435: cresce col quadrato della distanza (~1 mm a 50 cm)
            zmm = zmm + self._rng.normal(0.0, 0.3 + 2.8 * zz ** 2, zmm.shape)
        zmm[(zz < self.min_m) | (zz > self.far_m)] = 0.0
        depth[hit] = np.clip(np.rint(zmm), 0, 65535).astype(np.uint16)
        return color, depth
