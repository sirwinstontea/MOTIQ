"""Show Motion with the local computer vision: run YOLO-World (computer-vision/yolov8s-worldv2.pt, open vocabulary:
it is told the names of the objects to look for) over the recorded demonstration, see which object moved where, and
turn that into action tokens. Grok never looks at the video: it only transcribes the speech (optional) and rewrites
the YOLO report into a clear task description (rewrite()).

Same idea as the app's live "watch" (groky-app/src/skills/fromVision.ts, Watcher + summarize), on a video file.
"""
from __future__ import annotations

import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import cv2

from pydantic import BaseModel

from vision.planner import frames as fr
from vision.planner.schemas import ActionToken, SceneObject, TokenObject, VideoTokens
from vision.planner.tokenizer import TokenizerConfig
from vision.planner.voice import DEFAULT_KEYTERMS

FPS = 2.0            # frames per second read from the video (YOLO-World is ~1.4 s/frame on this CPU)
WIDTH = 640          # frames are scaled to this width before detection
MOVE = 0.05          # a move smaller than this (fraction of the image) is tracker noise
NEAR = 0.2           # an object that ends up this close to one that stayed put went into / onto it
STT_WAIT_S = 45.0    # the transcript is optional: don't hold the skill for longer than this
# body parts: the oggetti_mani model detects braccio / mano / polso (arm, hand, wrist); never task objects
HANDS = {"mano", "mani", "polso", "braccio", "hand", "wrist", "person", "persona"}
COLORS = {"rosso": "red", "arancione": "orange", "giallo": "yellow", "verde": "green", "blu": "blue",
          "viola": "purple", "bianco": "white", "nero": "black", "grigio": "grey"}
NOUNS = {"tappo": "bucket lid", "anello": "ring", "vite": "screw", "secchio": "bucket"}

MODEL = Path(__file__).resolve().parent.parent / "yolov8s-worldv2.pt"
# what YOLO-World looks for: the four demo pieces first, then everyday things a person might use instead
CLASSES = ["orange screw", "blue bucket", "green ring", "white bucket lid", "cup", "bowl", "box", "bottle",
           "pen", "hand"]
CONF = 0.15          # YOLO-World scores are lower than a trained detector's
REWRITE_WAIT_S = 45.0  # Grok's rewrite is optional too: past this, the plain YOLO report is used

Log = Callable[[str, str], None]


@dataclass
class Seen:
    label: str
    color: Optional[str]
    conf: float
    bbox: tuple  # (x, y, w, h) normalised 0-1


class WorldDetector:
    """YOLO-World with the CLASSES vocabulary; loaded once (setting the classes runs CLIP, which is slow)."""
    _model = None
    _lock = threading.Lock()

    @classmethod
    def model(cls):
        with cls._lock:
            if cls._model is None:
                from ultralytics import YOLO
                m = YOLO(str(MODEL))
                m.set_classes(CLASSES)
                cls._model = m
            return cls._model

    def process_frame(self, img) -> list[Seen]:
        r = self.model().predict(img, conf=CONF, verbose=False)[0]
        best: dict[str, Seen] = {}  # one per class: the most confident
        for (x1, y1, x2, y2), c, k in zip(r.boxes.xyxyn.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist()):
            name = r.names[int(k)]
            if name not in best or c > best[name].conf:
                best[name] = Seen(name, None, float(c), (x1, y1, x2 - x1, y2 - y1))
        return list(best.values())


def warm_up(log: Log = lambda k, m: None) -> None:
    """Load YOLO-World in the background when the server starts, so the first video doesn't wait for it."""
    def run():
        try:
            WorldDetector.model()
            log("cv", f"YOLO-World ready ({MODEL.name})")
        except Exception as e:  # noqa: BLE001
            log("cv", f"YOLO-World failed to load: {e}")
    threading.Thread(target=run, daemon=True).start()


def english(label: str, color: Optional[str]) -> tuple[str, Optional[str]]:
    """"vite_arancione" -> ("screw", "orange")."""
    words = [w for w in label.lower().replace("_", " ").split() if w]
    col = color or next((w for w in words if w in COLORS), None)
    noun = " ".join(NOUNS.get(w, w) for w in words if w not in COLORS) or label
    return noun, (COLORS.get(col, col) if col else None)


@dataclass
class Track:
    label: str
    color: Optional[str]
    seen: list[tuple[float, float, float, tuple]] = field(default_factory=list)  # (t, x, y, bbox)

    def pos(self, part: list) -> tuple[float, float]:
        xs = sorted(p[1] for p in part)
        ys = sorted(p[2] for p in part)
        return xs[len(xs) // 2], ys[len(ys) // 2]

    @property
    def first(self):  # median of the first sightings: one bad box doesn't count as a move
        return self.pos(self.seen[:3])

    @property
    def last(self):
        return self.pos(self.seen[-3:])

    def moved(self) -> bool:
        (x0, y0), (x1, y1) = self.first, self.last
        return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 > MOVE

    def started_moving(self) -> float:
        x0, y0 = self.first
        for t, x, y, _ in self.seen:
            if ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5 > MOVE:
                return t
        return self.seen[0][0]


def read_frames(video: Path, out: Path) -> list[Path]:
    cmd = [fr.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
           "-vf", f"fps={FPS:g},scale={WIDTH}:-2", str(out / "f_%05d.jpg")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read the video: {r.stderr.strip()[-300:]}")
    return sorted(out.glob("f_*.jpg"))


def transcribe(grok, video: Path, tmp: Path, log: Log) -> str:
    """Grok speech-to-text of what the person said; '' if there is no audio, no key, or it takes too long."""
    if getattr(grok, "is_mock", False):
        return ""
    wav = fr.extract_audio(video, tmp / "audio.wav")
    if wav is None:
        return ""
    box: dict = {}

    def run():
        try:
            box["text"] = grok.transcribe(wav, TokenizerConfig().language, DEFAULT_KEYTERMS).text
        except Exception as e:  # noqa: BLE001 - the speech is a bonus, never a failure
            box["err"] = e

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(STT_WAIT_S)
    if th.is_alive():
        log("voice", f"speech-to-text took over {STT_WAIT_S:.0f} s, continuing without it")
        return ""
    if "err" in box:
        log("voice", f"speech-to-text skipped: {box['err']}")
        return ""
    return (box.get("text") or "").strip()


def analyze(video: Path, grok, goal: Optional[str] = None, log: Log = lambda k, m: None) -> VideoTokens:
    """Detect and track the objects in every sampled frame, then one token per object that changed place."""
    with tempfile.TemporaryDirectory(prefix="motiq-cv-") as d:
        tmp = Path(d)
        # the transcript runs on Grok while the detector works locally
        speech: dict = {}
        stt = threading.Thread(target=lambda: speech.update(text=transcribe(grok, video, tmp, log)), daemon=True)
        stt.start()

        files = read_frames(video, tmp)
        if len(files) < 2:
            raise RuntimeError("the video is too short or could not be read")
        tracker = WorldDetector()
        tracks: dict[str, Track] = {}
        hands = 0
        for i, f in enumerate(files):
            img = cv2.imread(str(f))
            t = i / FPS
            hand = False
            for o in tracker.process_frame(img):
                if o.label.lower() in HANDS:
                    hand = True
                    continue
                noun, col = english(o.label, o.color)
                key = f"{noun}|{col or ''}"
                x, y, w, h = o.bbox
                tracks.setdefault(key, Track(noun, col)).seen.append((t, x + w / 2, y + h / 2, o.bbox))
            hands += hand
        duration = len(files) / FPS
        stt.join(STT_WAIT_S + 5)
        transcript = speech.get("text", "")

    # flickers are not objects: seen in at least 3 frames or 10% of them
    min_seen = max(3, round(len(files) * 0.1))
    objs = [t for t in tracks.values() if len(t.seen) >= min_seen]
    movers = sorted((t for t in objs if t.moved()), key=lambda t: t.started_moving())
    still = [t for t in objs if not t.moved()]
    log("cv", f"{len(files)} frames, {hands} with a hand, objects: "
              + (", ".join(f"{(t.color + ' ') if t.color else ''}{t.label}{' (moved)' if t in movers else ''}"
                           for t in objs) or "none"))

    tokens: list[ActionToken] = []
    for m in movers:
        lx, ly = m.last
        dest, best = None, NEAR
        for s in still:
            sx, sy = s.last
            dd = ((lx - sx) ** 2 + (ly - sy) ** 2) ** 0.5
            if dd < best:
                dest, best = s, dd
        hidden = m.seen[-1][0] < duration - 1.5  # disappeared before the end: went into the destination
        objects = [TokenObject(name=m.label, color=m.color, role="target", bbox_norm=list(m.seen[0][3]))]
        if dest:
            objects.append(TokenObject(name=dest.label, color=dest.color, role="dest", bbox_norm=list(dest.seen[-1][3])))
        where = (f"into the {dest.label}" if hidden else f"onto / next to the {dest.label}") if dest else \
            f"from ({m.first[0]:.2f}, {m.first[1]:.2f}) to ({lx:.2f}, {ly:.2f}) of the image"
        tokens.append(ActionToken(
            verb=("pick and put into" if dest and hidden else "pick and place onto" if dest else "pick and move"),
            canonical="move", objects=objects, gripper="closed",
            motion=None, t_start=round(m.started_moving(), 2), t_end=round(m.seen[-1][0], 2),
            confidence=0.8, notes=f"computer vision: the {m.label} was moved {where}"))
    if not tokens and transcript:
        # nothing moved on camera, but the person explained the task: let the skill writer work from that
        tokens.append(ActionToken(verb="described the task", canonical="unknown", speech=transcript,
                                  objects=[TokenObject(name=t.label, color=t.color, role="other") for t in objs],
                                  t_start=0.0, t_end=round(duration, 2), confidence=0.4))
    if not tokens:
        if not objs:
            raise RuntimeError("YOLO did not recognise any objects in the video (it looks for: "
                               + ", ".join(c for c in CLASSES if c != "hand") + "). Check the camera shows the table.")
        seen = ", ".join(t.label for t in objs)
        raise RuntimeError(f"The computer vision saw {seen} but nothing changed place. Keep the objects in view "
                           "and move them clearly from one place to another.")
    for i, t in enumerate(tokens, 1):
        t.id = f"tok_{i:03d}"
    return VideoTokens(
        video=str(video), fps=FPS, duration_s=duration, transcript=transcript, goal_hint=goal, tokens=tokens,
        scene_objects=[SceneObject(name=t.label, color=t.color, source="video") for t in objs])


class _Task(BaseModel):
    task: str


REWRITE_SYSTEM = """You turn the report of a YOLO object detector, which watched a person demonstrate a task to a
robot arm, into a short, clear task description for the robot. Use only what the report and the person's words say;
don't invent objects or steps. Write numbered steps in plain English (e.g. "1. Pick up the orange screw. 2. Put it
into the blue bucket."). If the report is unsure, say what is most likely. Answer as JSON: {"task": "..."}"""


def rewrite(grok, report: str, log: Log = lambda k, m: None) -> Optional[str]:
    """Grok (text only, no images) rewrites the YOLO report as a clear task; None if it fails or is too slow."""
    if getattr(grok, "is_mock", False):
        return None
    box: dict = {}

    def run():
        try:
            box["task"] = grok.chat_json("rewrite_yolo", REWRITE_SYSTEM, report, _Task).task.strip()
        except Exception as e:  # noqa: BLE001
            box["err"] = e

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(REWRITE_WAIT_S)
    if th.is_alive() or "err" in box or not box.get("task"):
        log("rewrite", "Grok rewrite skipped (" + (str(box.get("err")) if "err" in box else "too slow") + "): using the YOLO report")
        return None
    log("rewrite", box["task"])
    return box["task"]
