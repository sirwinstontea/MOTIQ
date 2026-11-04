"""Test della maschera di segmentazione (vision/eyes/segmentation/) su scene sintetiche, senza camera."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from vision.eyes.calibration import estimate_grasp_angle
from vision.eyes.schemas import EyeObject
from vision.eyes.segmentation import (
    MaskCache,
    TableBackground,
    attach_masks,
    clean_mask,
    compute_mask,
    mask_from_color,
    mask_from_depth,
    mask_geometry,
    mask_to_polygon,
)
from vision.eyes.segmentation.masks import bbox_to_px
from vision.grok.schemas import parse_objects

H, W = 480, 640
TABLE_MM = 600
TABLE_BGR = (205, 200, 195)

CAP = dict(center=(200, 200), r=40)                 # tappo bianco alto 20 mm: si vede solo in profondità
RING = dict(center=(460, 300), r_out=30, r_in=18)   # anello verde alto 3 mm: si vede solo nel colore


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a > 0, b > 0
    return float((a & b).sum() / max(1, (a | b).sum()))


def _disk(center, r) -> np.ndarray:
    m = np.zeros((H, W), np.uint8)
    cv2.circle(m, center, r, 255, -1)
    return m


def _ring() -> np.ndarray:
    m = _disk(RING["center"], RING["r_out"])
    cv2.circle(m, RING["center"], RING["r_in"], 0, -1)
    return m


def _bbox_of(mask: np.ndarray, pad: int = 0) -> tuple[float, float, float, float]:
    x, y, w, h = cv2.boundingRect((mask > 0).astype(np.uint8))
    return ((x - pad) / W, (y - pad) / H, (w + 2 * pad) / W, (h + 2 * pad) / H)


def _empty_table(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    color = np.clip(np.array(TABLE_BGR, np.float32) + rng.normal(0, 2, (H, W, 3)), 0, 255).astype(np.uint8)
    depth = (TABLE_MM + rng.normal(0, 1.0, (H, W))).astype(np.uint16)
    depth[rng.random((H, W)) < 0.02] = 0  # pixel senza misura, come la D435
    return color, depth


def _scene(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    color, depth = _empty_table(rng)
    cap, ring = _disk(CAP["center"], CAP["r"]) > 0, _ring() > 0
    color[cap] = (212, 210, 208)            # bianco quasi come il tavolo
    depth[cap] = TABLE_MM - 20
    color[ring] = (40, 170, 40)             # verde
    depth[ring] = TABLE_MM - 3
    return color, depth


@pytest.fixture(scope="module")
def table() -> TableBackground:
    rng = np.random.default_rng(0)
    bg = TableBackground(n_frames=5)
    done = False
    for _ in range(5):
        done = bg.add_frame(*_empty_table(rng))
    assert done and bg.ready and bg.has_depth
    return bg


@pytest.fixture()
def scene() -> tuple[np.ndarray, np.ndarray]:
    return _scene(np.random.default_rng(1))


# --- tavolo vuoto -----------------------------------------------------------------------------------------------

def test_background_median_ignores_zeros_and_roundtrips(tmp_path):
    bg = TableBackground(n_frames=3)
    color = np.full((4, 4, 3), 100, np.uint8)
    d1, d2, d3 = (np.full((4, 4), v, np.uint16) for v in (600, 0, 602))
    d1[0, 0] = d2[0, 0] = d3[0, 0] = 0          # mai misurato: resta 0
    assert not bg.add_frame(color, d1) and bg.collecting
    assert not bg.add_frame(color, d2)
    assert bg.add_frame(color, d3)
    assert bg.depth[1, 1] == 601                # mediana di 600 e 602, lo zero non conta
    assert bg.depth[0, 0] == 0

    loaded = TableBackground.load(bg.save(tmp_path / "table_empty.npz"))
    assert np.array_equal(loaded.color, bg.color) and np.array_equal(loaded.depth, bg.depth)
    assert loaded.matches(color) and not loaded.matches(np.zeros((8, 8, 3), np.uint8))


def test_background_without_depth():
    bg = TableBackground(n_frames=2)
    bg.add_frame(np.zeros((4, 4, 3), np.uint8))
    assert bg.add_frame(np.zeros((4, 4, 3), np.uint8))
    assert bg.ready and not bg.has_depth


# --- sorgenti della maschera ------------------------------------------------------------------------------------

def test_depth_mask_finds_tall_object(table, scene):
    color, depth = scene
    truth = _disk(CAP["center"], CAP["r"])
    box = bbox_to_px(_bbox_of(truth), W, H)

    m = mask_from_depth(depth, table, box)
    assert m is not None and m.shape == (H, W)
    assert _iou(m, truth) > 0.9
    assert mask_from_color(color, table, box) is None  # bianco su bianco: il colore da solo non basta

    res = compute_mask(color, _bbox_of(truth), depth=depth, bg=table)
    assert res is not None and res.source == "depth"
    assert _iou(res.mask, truth) > 0.9


def test_low_ring_found_only_by_color(table, scene):
    color, depth = scene
    truth = _ring()
    box = bbox_to_px(_bbox_of(truth), W, H)

    assert mask_from_depth(depth, table, box) is None   # 3 mm < 6 mm
    m = mask_from_color(color, table, box)
    assert m is not None and _iou(m, truth) > 0.85
    assert not m[RING["center"][1], RING["center"][0]]  # il buco dell'anello resta vuoto

    res = compute_mask(color, _bbox_of(truth), depth=depth, bg=table, label="vite")
    assert res is not None and res.source == "color"


def test_shadow_is_not_an_object(table):
    color, depth = _empty_table(np.random.default_rng(2))
    shadow = _disk((320, 240), 50) > 0
    color[shadow] = (np.array(TABLE_BGR) * 0.7).astype(np.uint8)   # più scuro, stessa tinta
    box = bbox_to_px(_bbox_of(shadow.astype(np.uint8)), W, H)
    assert mask_from_color(color, table, box) is None
    assert mask_from_depth(depth, table, box) is None


def test_neighbour_in_margin_is_not_chosen():
    big = _disk((100, 100), 60)
    small = _disk((200, 100), 15)
    m = clean_mask(big | small, box_px=(185, 85, 216, 116))
    assert m is not None and _iou(m, small) > 0.9


def test_grabcut_without_background():
    frame = np.full((H, W, 3), 230, np.uint8)
    truth = _disk((320, 240), 60)
    frame[truth > 0] = (30, 30, 180)
    res = compute_mask(frame, _bbox_of(truth, pad=10))
    assert res is not None and res.source == "grabcut"
    assert _iou(res.mask, truth) > 0.85


# --- geometria --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("angle", [30.0, -30.0, 75.0])
def test_mask_geometry_angle_follows_grasp_convention(angle):
    canvas = np.full((300, 300, 3), 240, np.uint8)
    rect = ((150.0, 150.0), (120.0, 24.0), angle)
    pts = np.int32(cv2.boxPoints(rect))
    cv2.fillPoly(canvas, [pts], (20, 20, 200))
    mask = np.zeros((300, 300), np.uint8)
    cv2.fillPoly(mask, [pts], 255)

    g = mask_geometry(mask)
    assert g is not None
    assert abs(g.angle_deg - angle) <= 2.0
    otsu = estimate_grasp_angle(canvas, (0.0, 0.0, 1.0, 1.0))
    assert np.sign(g.angle_deg) == np.sign(otsu)       # stesso segno della convenzione attuale
    assert abs(g.long_px - 120) <= 3 and abs(g.short_px - 24) <= 3
    assert abs(g.centroid[0] - 150) <= 1 and abs(g.centroid[1] - 150) <= 1
    assert not g.border and not g.long_clipped


def test_mask_geometry_border_clipping():
    mask = np.zeros((100, 200), np.uint8)
    mask[40:60, 0:80] = 255          # barra orizzontale tagliata dal bordo sinistro
    g = mask_geometry(mask)
    assert g.border == frozenset({"left"})
    assert g.long_clipped and not g.short_clipped
    assert mask_geometry(np.zeros((10, 10), np.uint8)) is None


def test_polygon_is_normalized_and_short():
    star = np.zeros((H, W), np.uint8)
    angles = np.linspace(0, 2 * np.pi, 120, endpoint=False)
    radii = np.where(np.arange(120) % 2 == 0, 150, 60)
    pts = np.stack([320 + radii * np.cos(angles), 240 + radii * np.sin(angles)], axis=1).astype(np.int32)
    cv2.fillPoly(star, [pts], 255)

    poly = mask_to_polygon(star)
    assert poly is not None and 3 <= len(poly) <= 32
    assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in poly)
    assert mask_to_polygon(np.zeros((H, W), np.uint8)) is None

    obj = EyeObject(id=1, label="x", conf=0.5, bbox=(0.1, 0.1, 0.2, 0.2), mask=poly, mask_source="color")
    assert obj.mask == poly


# --- attach_masks -----------------------------------------------------------------------------------------------

def test_attach_keeps_input_type_and_contract(table, scene):
    color, depth = scene
    cap_box = _bbox_of(_disk(CAP["center"], CAP["r"]))
    ring_box = _bbox_of(_ring())
    objs = [
        EyeObject(id=1, label="tappo", conf=0.9, bbox=cap_box),
        {"id": 2, "label": "anello", "conf": 0.8, "bbox": list(ring_box)},
    ]
    out = attach_masks(objs, color, depth=depth, bg=table)

    assert isinstance(out[0], EyeObject) and isinstance(out[1], dict)
    assert out[0].mask_source == "depth" and out[1]["mask_source"] == "color"
    assert 3 <= len(out[0].mask) <= 32 and 3 <= len(out[1]["mask"]) <= 32
    assert objs[0].mask is None and "mask" not in objs[1]   # gli ingressi non vengono modificati

    again = EyeObject.model_validate(out[0].model_dump())
    assert again.mask == out[0].mask
    assert len(parse_objects([out[0].model_dump(), out[1]])) == 2   # Visione B accetta i campi nuovi


def test_attach_without_background_uses_grabcut():
    frame = np.full((H, W, 3), 230, np.uint8)
    truth = _disk((320, 240), 60)
    frame[truth > 0] = (30, 30, 180)
    out = attach_masks([{"id": 7, "label": "palla", "conf": 0.7, "bbox": _bbox_of(truth, pad=10)}], frame)
    assert out[0]["mask_source"] == "grabcut"


def test_attach_error_on_one_object_does_not_stop_the_others(table, scene):
    color, depth = scene
    broken = {"id": 9, "label": "rotto", "conf": 0.5}       # manca bbox
    good = {"id": 1, "label": "tappo", "conf": 0.9, "bbox": _bbox_of(_disk(CAP["center"], CAP["r"]))}
    out = attach_masks([broken, good], color, depth=depth, bg=table)
    assert out[0] is broken
    assert out[1]["mask_source"] == "depth"


def test_attach_no_mask_clears_fields(table):
    color, depth = _empty_table(np.random.default_rng(3))
    flat = np.full((H, W, 3), 128, np.uint8)        # niente da separare, nemmeno per GrabCut
    obj = EyeObject(id=1, label="x", conf=0.5, bbox=(0.4, 0.4, 0.1, 0.1),
                    mask=[(0.1, 0.1), (0.2, 0.1), (0.2, 0.2)], mask_source="grabcut")
    out = attach_masks([obj], flat)
    assert out[0].mask is None and out[0].mask_source is None


def test_cache_recomputes_only_when_box_moves_or_ages(table, scene):
    color, depth = scene
    cache = MaskCache(refresh_every=5)
    box = _bbox_of(_disk(CAP["center"], CAP["r"]))
    obj = {"id": 1, "label": "tappo", "conf": 0.9, "bbox": box}

    first = attach_masks([obj], color, depth=depth, bg=table, cache=cache)
    for _ in range(3):
        same = attach_masks([obj], color, depth=depth, bg=table, cache=cache)
    assert cache.computed == 1 and same[0]["mask"] == first[0]["mask"]

    moved = {**obj, "bbox": (box[0] + 0.05, box[1], box[2], box[3])}
    attach_masks([moved], color, depth=depth, bg=table, cache=cache)
    assert cache.computed == 2

    for _ in range(5):
        attach_masks([moved], color, depth=depth, bg=table, cache=cache)
    assert cache.computed == 3            # ricalcolata dopo 5 frame anche se ferma

    attach_masks([moved], color, depth=None, bg=table, cache=cache)
    assert cache.computed == 4            # cambiata la sorgente (niente profondità): si riparte


def test_cache_resets_when_table_is_registered_again(scene):
    color, depth = scene
    rng = np.random.default_rng(4)
    bg = TableBackground(n_frames=2)
    for _ in range(2):
        bg.add_frame(*_empty_table(rng))
    cache = MaskCache()
    obj = {"id": 1, "label": "tappo", "conf": 0.9, "bbox": _bbox_of(_disk(CAP["center"], CAP["r"]))}
    attach_masks([obj], color, depth=depth, bg=bg, cache=cache)
    for _ in range(2):                    # tasto T: stesso oggetto TableBackground, nuovo riferimento
        bg.add_frame(*_empty_table(rng))
    attach_masks([obj], color, depth=depth, bg=bg, cache=cache)
    assert cache.computed == 2


def test_cache_forgets_old_ids(table, scene):
    color, depth = scene
    cache = MaskCache(forget_after=2)
    attach_masks([{"id": 1, "label": "tappo", "conf": 0.9, "bbox": _bbox_of(_disk(CAP["center"], CAP["r"]))}],
                 color, depth=depth, bg=table, cache=cache)
    assert len(cache) == 1
    for _ in range(3):
        attach_masks([], color, depth=depth, bg=table, cache=cache)
    assert len(cache) == 0


# --- misure dalla maschera (passo 4) ----------------------------------------------------------------------------

FX = FY = 600.0
INTR = (FX, FY, W / 2, H / 2)


def _bar(center, size, angle) -> np.ndarray:
    m = np.zeros((H, W), np.uint8)
    cv2.fillPoly(m, [np.int32(cv2.boxPoints((center, size, angle)))], 255)
    return m


def _scene_with(mask: np.ndarray, height_mm: int, bgr=(60, 60, 200), seed: int = 5):
    color, depth = _empty_table(np.random.default_rng(seed))
    color[mask > 0] = bgr
    depth[mask > 0] = TABLE_MM - height_mm
    return color, depth


def test_depth_distance_and_size_from_mask(table, scene):
    color, depth = scene
    cap = _disk(CAP["center"], CAP["r"])
    obj = EyeObject(id=1, label="tappo", conf=0.9, bbox=_bbox_of(cap), distance_m=9.9)
    out = attach_masks([obj], color, depth=depth, bg=table, intrinsics=INTR)[0]

    assert out.mask_source == "depth"
    assert abs(out.distance_m - 0.580) <= 0.002                  # Z vera, non la stima dal box
    expected_mm = 2 * CAP["r"] * 580 / FX                        # diametro 80 px a 580 mm
    assert out.size_mm is not None
    assert all(abs(s - expected_mm) <= 4.0 for s in out.size_mm)
    # Punto 3D nel frame camera: centro del tappo deproiettato a 580 mm; tappo rotondo, quindi niente asse
    u, v = CAP["center"]
    x, y, z = out.xyz_cam_m
    assert abs(x - (u - W / 2) * 0.58 / FX) <= 0.002 and abs(y - (v - H / 2) * 0.58 / FY) <= 0.002
    assert abs(z - 0.580) <= 0.002 and out.axis_cam is None


def test_depth_ignores_flying_pixels_and_holes(table):
    cap = _disk(CAP["center"], CAP["r"])
    color, depth = _scene_with(cap, 20, bgr=(212, 210, 208))
    edge = cap - cv2.erode(cap, np.ones((3, 3), np.uint8))
    depth[edge > 0] = 2000                                       # bordo volante, come la D435
    holes = (cap > 0) & (np.random.default_rng(6).random((H, W)) < 0.3)
    depth[holes] = 0                                             # 30% di pixel senza misura
    out = attach_masks([{"id": 1, "label": "tappo", "conf": 0.9, "bbox": _bbox_of(cap)}],
                       color, depth=depth, bg=table)[0]
    assert abs(out["distance_m"] - 0.580) <= 0.002
    assert out["size_mm"] is None                                # niente intrinseci, niente mm


def test_rotated_bar_size_and_grasp_angle_replace_otsu(table):
    bar = _bar((320.0, 240.0), (160.0, 30.0), 30.0)
    color, depth = _scene_with(bar, 15)
    objs = [
        EyeObject(id=1, label="vite", conf=0.9, bbox=_bbox_of(bar), coords_mm={"x": 10.0, "y": 20.0, "angle_deg": 0}),
        {"id": 2, "label": "vite", "conf": 0.9, "bbox": _bbox_of(bar), "coords_mm": {"x": 1.0, "y": 2.0, "angle_deg": 0}},
    ]
    a, b = attach_masks(objs, color, depth=depth, bg=table, intrinsics=INTR)

    assert abs(a.coords_mm.angle_deg - 30.0) <= 2.0 and (a.coords_mm.x, a.coords_mm.y) == (10.0, 20.0)
    assert abs(b["coords_mm"]["angle_deg"] - 30.0) <= 2.0 and b["coords_mm"]["x"] == 1.0
    long_mm, short_mm = a.size_mm
    assert abs(long_mm - 160 * 585 / FX) <= 5.0 and abs(short_mm - 30 * 585 / FX) <= 3.0
    assert abs(a.distance_m - 0.585) <= 0.002


def test_round_object_keeps_its_angle(table, scene):
    color, depth = scene
    obj = {"id": 1, "label": "tappo", "conf": 0.9, "bbox": _bbox_of(_disk(CAP["center"], CAP["r"])),
           "coords_mm": {"x": 0.0, "y": 0.0, "angle_deg": 12.0}}
    out = attach_masks([obj], color, depth=depth, bg=table)[0]
    assert out["coords_mm"]["angle_deg"] == 12.0                 # rotondo: l'asse lungo non esiste


def test_distance_rolling_median_and_jump_reset(table, scene):
    color, depth = scene
    cap = _disk(CAP["center"], CAP["r"]) > 0
    cache = MaskCache(refresh_every=100)
    obj = {"id": 1, "label": "tappo", "conf": 0.9, "bbox": _bbox_of(cap.astype(np.uint8))}

    def frame_at(z_mm):
        d = depth.copy()
        d[cap] = z_mm
        return attach_masks([obj], color, depth=d, bg=table, cache=cache)[0]["distance_m"]

    for _ in range(3):
        assert frame_at(580) == 0.58
    assert frame_at(590) == 0.58                                 # un frame rumoroso non sposta la mediana
    assert frame_at(450) == 0.45                                 # sollevato: salto > 10%, la storia si butta


def test_moved_box_resets_distance_history():
    cache = MaskCache(smooth_frames=10)
    cache.begin_frame(("ctx",))
    cache.store(1, (0.1, 0.1, 0.2, 0.2), None)
    for v in (0.60, 0.60, 0.60):
        cache.smooth(1, v)
    cache.store(1, (0.1, 0.1, 0.2, 0.2), None)                   # ricalcolo per età: storia tenuta
    assert cache.smooth(1, 0.65) == 0.6
    cache.store(1, (0.5, 0.5, 0.2, 0.2), None)                   # box spostato: si riparte
    assert cache.smooth(1, 0.65) == 0.65


def test_monocular_distance_uses_rotated_side_not_box():
    from vision.eyes.depth import estimate_distance_m
    from vision.eyes.segmentation import monocular_distance_m

    bar = _bar((320.0, 240.0), (240.0, 40.0), 30.0)              # "bottle": 24 cm, a ~55 cm dalla webcam
    g = mask_geometry(bar)
    d = monocular_distance_m("bottle", g, (H, W))
    assert abs(d - 0.55) <= 0.02
    assert abs(d - 0.55) < abs(estimate_distance_m("bottle", _bbox_of(bar)) - 0.55)


def test_monocular_distance_skips_clipped_side_and_people():
    from vision.eyes.segmentation import monocular_distance_m

    bar = np.zeros((H, W), np.uint8)
    bar[200:270, 0:200] = 255                                    # lato lungo tagliato dal bordo sinistro
    g = mask_geometry(bar)
    assert g.long_clipped and not g.short_clipped
    assert abs(monocular_distance_m("bottle", g, (H, W)) - 0.07 * 1.15 * H / 70) <= 0.02   # dal lato corto
    assert monocular_distance_m("person", mask_geometry(_disk((320, 240), 60)), (H, W)) is None
    corner = np.zeros((H, W), np.uint8)
    corner[0:100, 0:100] = 255                                   # tagliato su entrambi gli assi
    assert monocular_distance_m("bottle", mask_geometry(corner), (H, W)) is None


def test_webcam_grabcut_sets_monocular_distance():
    frame = np.full((H, W, 3), 230, np.uint8)
    bar = _bar((320.0, 240.0), (240.0, 40.0), 30.0)
    frame[bar > 0] = (30, 30, 180)
    out = attach_masks([EyeObject(id=3, label="bottle", conf=0.8, bbox=_bbox_of(bar, pad=10), distance_m=9.9)],
                       frame)[0]
    assert out.mask_source == "grabcut"
    assert abs(out.distance_m - 0.55) <= 0.03 and out.size_mm is None


def test_no_mask_keeps_box_distance():
    flat = np.full((H, W, 3), 128, np.uint8)
    obj = EyeObject(id=1, label="x", conf=0.5, bbox=(0.4, 0.4, 0.1, 0.1), distance_m=1.23, size_mm=(5.0, 5.0))
    out = attach_masks([obj], flat)[0]
    assert out.distance_m == 1.23 and out.size_mm is None and out.mask is None


def test_demo_labels_have_priors_and_low_threshold():
    from vision.eyes.depth import CLASS_PRIORS_CM
    from vision.eyes.segmentation.attach import min_height_for
    from vision.eyes.segmentation.masks import MIN_HEIGHT_MM

    for label in ("tappo_bianco", "anello_verde", "vite_arancione", "secchio_blu"):
        assert label in CLASS_PRIORS_CM
    assert min_height_for("Anello_Verde") < MIN_HEIGHT_MM <= min_height_for("secchio_blu")


# --- contratto dello schema (passo 1) ---------------------------------------------------------------------------

def test_schema_without_mask_has_new_fields_none_and_parse_objects_accepts():
    obj = EyeObject(id=1, label="x", conf=0.5, bbox=(0.1, 0.1, 0.2, 0.2))
    dumped = obj.model_dump()
    assert dumped["mask"] is None and dumped["mask_source"] is None and dumped["size_mm"] is None
    assert len(parse_objects([dumped])) == 1


def test_schema_mask_validator_clamps_and_drops_degenerate():
    obj = EyeObject(id=1, label="x", conf=0.5, bbox=(0.1, 0.1, 0.2, 0.2),
                    mask=[(-0.2, 0.5), (1.4, 0.5), (0.5, 0.123456)], mask_source="depth")
    assert obj.mask == [(0.0, 0.5), (1.0, 0.5), (0.5, 0.1235)]
    flat = EyeObject(id=1, label="x", conf=0.5, bbox=(0.1, 0.1, 0.2, 0.2), mask=[(0.1, 0.1), (0.2, 0.2)])
    assert flat.mask is None
    with pytest.raises(ValueError):
        EyeObject(id=1, label="x", conf=0.5, bbox=(0.1, 0.1, 0.2, 0.2), mask_source="magia")


# --- lettore RealSense finto attraverso EyePipeline.step (passo 5) -------------------------------------------------

def _fake_realsense():
    from vision.eyes.capture.realsense import Intrinsics, RealSenseReader

    class FakeRealSense(RealSenseReader):
        """Tavolo a 600 mm; con `has_obj` un parallelepipedo verde alto 20 mm al centro."""

        def __init__(self):
            self._running = False
            self.intrinsics = Intrinsics(FX, FY, W / 2, H / 2, W, H)
            self.has_obj = False
            self.n = 0

        def read_once(self):
            self.n += 1
            self.color = np.full((H, W, 3), 120, np.uint8)
            self.depth = np.full((H, W), TABLE_MM, np.uint16)
            if self.has_obj:
                self.color[200:260, 300:380] = (30, 160, 30)
                self.depth[200:260, 300:380] = TABLE_MM - 20
            return True, self.color, self.n, 0.0

        def get_depth(self):
            return self.depth

        def stop(self):
            pass

    return FakeRealSense()


def _fake_pipeline():
    from vision.eyes.pipeline import EyePipeline

    pl = EyePipeline()
    # "synthetic", non "realsense": il lettore vero viene subito sostituito dal finto, e aprirlo
    # richiede pyrealsense2 (non in requirements) facendo fallire il test su chi non ce l'ha.
    node = pl.add_source("arm", mode="synthetic", use_mock_tracker=True)
    node.reader = _fake_realsense()
    obj = EyeObject(id=1, label="anello_verde", conf=0.9, bbox=(300 / W, 200 / H, 80 / W, 60 / H), distance_m=9.9)
    node.tracker.process_frame = lambda f, apply_calibration=False: [obj] if node.reader.has_obj else []
    return pl, node


def test_fake_realsense_through_pipeline_step_gives_depth_mask_and_true_distance():
    pl, node = _fake_pipeline()
    node.record_empty_table()
    for _ in range(10):                                       # BG_FRAMES: tavolo vuoto
        assert pl.step("arm").objects == []
    assert node.bg.ready and not node.recording_bg

    node.reader.has_obj = True
    o = pl.step("arm").objects[0]
    assert o.mask_source == "depth" and o.mask is not None and 3 <= len(o.mask) <= 32
    assert abs(o.distance_m - 0.580) <= 0.002                 # non il 9.9 del tracker
    assert o.size_mm is not None and abs(o.size_mm[0] - 80 * 580 / FX) <= 5.0


def test_pipeline_without_empty_table_does_not_use_depth_mask():
    pl, node = _fake_pipeline()
    node.reader.has_obj = True
    o = pl.step("arm").objects[0]
    assert o.mask_source in (None, "grabcut")                 # nessun tavolo vuoto: niente depth/color


def test_draw_marks_show_masks_false_skips_outline():
    from vision.eyes.marks.som import draw_marks

    frame = np.full((H, W, 3), 120, np.uint8)
    box = (0.3, 0.3, 0.3, 0.3)
    poly = [(0.32, 0.32), (0.58, 0.34), (0.56, 0.58), (0.34, 0.56)]
    with_mask = EyeObject(id=1, label="x", conf=0.9, bbox=box, mask=poly, mask_source="depth")
    without = EyeObject(id=1, label="x", conf=0.9, bbox=box)

    shown = draw_marks(frame, [with_mask], show_masks=True)
    hidden = draw_marks(frame, [with_mask], show_masks=False)
    plain = draw_marks(frame, [without])
    assert not np.array_equal(shown, plain)                   # la sagoma si disegna
    assert np.array_equal(hidden, plain)                      # e con show_masks=False sparisce
    assert np.array_equal(frame, np.full((H, W, 3), 120, np.uint8))  # il frame in ingresso non cambia
