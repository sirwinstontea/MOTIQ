"""Modalità "Cosa è cambiato": scena di riferimento contro scena attuale, camera ferma.

    ref = frame.copy()                                    # tasto P
    changes, problem = find_changes(ref, frame, ignore=[o["bbox"] for o in mani])   # tasto X
    # changes: [{"n": 1, "kind": "added"|"removed"|"moved", "bbox": [x, y, w, h], "from_bbox": [...] | None}]

Si confrontano i pixel, non le detection: trova anche oggetti che nessun modello conosce.
Poi Grok (tasks.explain_changes) dà il nome agli oggetti e scarta le ombre e i riflessi.
Soglie da regolare sul tavolo vero: sono le manopole contro i falsi allarmi.
"""
from __future__ import annotations

import cv2
import numpy as np

DIFF_THRESH = 28    # differenza di colore (LAB, 0-255) per dire "cambiato": alzala se luce/ombre fanno falsi allarmi
MIN_AREA = 0.0015   # regioni più piccole di questa frazione dell'immagine = rumore
MAX_CHANGED = 0.35  # se cambia più di così, si è mossa la camera (o il braccio): referenza da rifare
MOVED_DIST = 25     # distanza massima tra i colori (LAB) per accoppiare tolto + aggiunto = spostato
HAND_MARGIN = 0.03  # margine attorno alle mani, che non sono mai un cambiamento


def _lab(img: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2LAB), (7, 7), 0).astype(np.int16)


def change_mask(ref: np.ndarray, cur: np.ndarray, ignore=()) -> np.ndarray:
    """Maschera 0/255 dei pixel cambiati, senza le zone in `ignore` (bbox normalizzati, es. mani)."""
    d = np.abs(_lab(ref) - _lab(cur))
    d = d - np.median(d, axis=(0, 1))  # la luce cambiata in modo uniforme non conta
    mask = (d.max(axis=2) > DIFF_THRESH).astype(np.uint8) * 255
    h, w = mask.shape
    for x, y, bw, bh in ignore:
        m = HAND_MARGIN
        cv2.rectangle(mask, (int((x - m) * w), int((y - m) * h)), (int((x + bw + m) * w), int((y + bh + m) * h)), 0, -1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))


def _color(lab: np.ndarray, m: np.ndarray, table: np.ndarray) -> np.ndarray:
    """Colore mediano dei pixel della zona che non sono tavolo (= l'oggetto)."""
    px = lab[m]
    obj = px[np.abs(px - table).max(axis=1) > DIFF_THRESH]
    return np.median(obj if len(obj) else px, axis=0)


def find_changes(ref: np.ndarray, cur: np.ndarray, ignore=()) -> tuple[list[dict], str | None]:
    """Cambiamenti tra ref e cur. Restituisce (cambiamenti, problema): problema != None = non fidarsi."""
    if ref is None or cur is None or ref.shape != cur.shape:
        return [], "manca la scena di riferimento (premi P) o la risoluzione è cambiata"
    mask = change_mask(ref, cur, ignore)
    h, w = mask.shape
    if np.count_nonzero(mask) > MAX_CHANGED * h * w:
        return [], "è cambiata quasi tutta l'immagine: camera o braccio mossi, rifai la referenza (P)"

    # Colore del tavolo = mediana di ciò che non è cambiato. "Quanto è oggetto" una zona = distanza dal tavolo.
    lab_ref, lab_cur = _lab(ref), _lab(cur)
    table = np.median(lab_ref[mask == 0], axis=0)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    regions = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < MIN_AREA * h * w:
            continue
        m = (labels == i)
        obj_ref = float(np.abs(lab_ref[m] - table).max(axis=1).mean())
        obj_cur = float(np.abs(lab_cur[m] - table).max(axis=1).mean())
        # ponytail: euristica "tavolo uniforme"; con un piano molto texturizzato chiedere a Grok il tipo
        if min(obj_ref, obj_cur) > 0.7 * max(obj_ref, obj_cur):
            kind = "moved"  # oggetto sia prima sia dopo, nella stessa zona: spostato di poco
        else:
            kind = "added" if obj_cur > obj_ref else "removed"
        regions.append({"kind": kind, "bbox": [float(x / w), float(y / h), float(bw / w), float(bh / h)],
                        "from_bbox": None, "area": int(area),
                        "color": _color(lab_cur if kind == "added" else lab_ref, m, table)})

    # Tolto qui + aggiunto là con lo stesso colore e dimensione simile = spostato
    added = [r for r in regions if r["kind"] == "added"]
    removed = [r for r in regions if r["kind"] == "removed"]
    pairs = sorted((float(np.linalg.norm(a["color"] - r["color"])), ai, ri)
                   for ai, a in enumerate(added) for ri, r in enumerate(removed)
                   if 0.4 < a["area"] / r["area"] < 2.5)
    used_a, used_r = set(), set()
    for dist, ai, ri in pairs:
        if dist > MOVED_DIST or ai in used_a or ri in used_r:
            continue
        used_a.add(ai)
        used_r.add(ri)
        added[ai].update(kind="moved", from_bbox=removed[ri]["bbox"])
    regions = ([r for i, r in enumerate(removed) if i not in used_r] + added
               + [r for r in regions if r["kind"] == "moved" and r["from_bbox"] is None])

    out = sorted(({k: r[k] for k in ("kind", "bbox", "from_bbox")} for r in regions),
                 key=lambda c: (c["bbox"][1], c["bbox"][0]))
    for i, c in enumerate(out, 1):
        c["n"] = i
    return out, None


KIND_IT = {"added": "aggiunto", "removed": "tolto", "moved": "spostato"}
KIND_COLOR = {"added": (90, 220, 90), "removed": (70, 70, 255), "moved": (0, 220, 255)}  # BGR


def draw_changes(img: np.ndarray, changes: list[dict]) -> np.ndarray:
    """Riquadri numerati: verde aggiunto, rosso tolto, giallo spostato (con freccia da dove veniva)."""
    out = img.copy()
    h, w = out.shape[:2]

    def px(b):
        return int(b[0] * w), int(b[1] * h), int((b[0] + b[2]) * w), int((b[1] + b[3]) * h)

    for c in changes:
        color = KIND_COLOR[c["kind"]]
        x1, y1, x2, y2 = px(c["bbox"])
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 3)
        if c.get("from_bbox"):
            f1, g1, f2, g2 = px(c["from_bbox"])
            cv2.rectangle(out, (f1, g1), (f2, g2), color, 1)
            cv2.arrowedLine(out, ((f1 + f2) // 2, (g1 + g2) // 2), ((x1 + x2) // 2, (y1 + y2) // 2), color, 2,
                            tipLength=0.08)
        text = f"{c['n']} {c.get('name') or KIND_IT[c['kind']]}"
        cv2.putText(out, text, (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(out, text, (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
    return out


if __name__ == "__main__":
    # Self-check su un tavolo finto: un oggetto aggiunto, uno tolto, uno spostato, una mano da ignorare.
    rng = np.random.default_rng(0)
    table = np.full((480, 640, 3), (200, 200, 205), np.uint8)
    ref, cur = table.copy(), table.copy()
    cv2.rectangle(ref, (50, 50), (110, 110), (30, 30, 200), -1)     # rosso: verrà tolto
    cv2.rectangle(ref, (300, 300), (360, 380), (200, 60, 20), -1)   # blu: verrà spostato
    cv2.rectangle(cur, (480, 80), (560, 140), (20, 160, 20), -1)    # verde: aggiunto
    cv2.rectangle(cur, (420, 320), (480, 400), (200, 60, 20), -1)   # blu: nuova posizione
    cv2.rectangle(cur, (100, 380), (200, 470), (90, 120, 180), -1)  # "mano": ignorata
    cur = np.clip(cur.astype(int) + rng.integers(-6, 7, cur.shape) + 8, 0, 255).astype(np.uint8)  # rumore + luce
    hand = [(100 / 640, 380 / 480, 100 / 640, 90 / 480)]
    ch, problem = find_changes(ref, cur, ignore=hand)
    kinds = sorted(c["kind"] for c in ch)
    assert problem is None and kinds == ["added", "moved", "removed"], (problem, ch)
    moved = next(c for c in ch if c["kind"] == "moved")
    assert moved["from_bbox"][0] < moved["bbox"][0]                 # si è spostato verso destra
    assert find_changes(ref, ref)[0] == []                          # niente cambiato = zero falsi allarmi
    busy = cv2.resize(rng.integers(0, 255, (24, 32, 3), dtype=np.uint8), (640, 480), interpolation=cv2.INTER_NEAREST)
    assert find_changes(busy, np.roll(busy, 60, axis=1))[1] is not None  # inquadratura spostata = camera mossa
    assert find_changes(ref, np.clip(ref.astype(int) - 40, 0, 255).astype(np.uint8))[0] == []  # luce più bassa
    print("ok:", [(c["n"], c["kind"]) for c in ch])
