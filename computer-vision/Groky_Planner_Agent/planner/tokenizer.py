"""Video frame tokenizer using OpenCV to sample frames, burn timestamps, and create a visual Storyboard Grid."""
from __future__ import annotations
import base64
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import cv2
import numpy as np


def resize_frame(frame: np.ndarray, max_side: int = 640) -> np.ndarray:
    h, w = frame.shape[:2]
    if max(h, w) <= max_side:
        return frame
    if w >= h:
        new_w = max_side
        new_h = int(h * (max_side / w))
    else:
        new_h = max_side
        new_w = int(w * (max_side / h))
    return cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)


def draw_frame_badge(frame: np.ndarray, step_num: int, timestamp_s: float) -> np.ndarray:
    """Draws a high-contrast badge (#1 | 0.5s) on the top-left corner of the frame for Grok Vision."""
    annotated = frame.copy()
    label = f"#{step_num} | {timestamp_s:.1f}s"
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.65
    thickness = 2
    (tw, th), _ = cv2.getTextSize(label, font, scale, thickness)
    cv2.rectangle(annotated, (8, 8), (20 + tw, 22 + th), (0, 0, 0), -1)
    cv2.rectangle(annotated, (8, 8), (20 + tw, 22 + th), (0, 255, 128), 1)
    cv2.putText(annotated, label, (14, 16 + th), font, scale, (0, 255, 128), thickness, cv2.LINE_AA)
    return annotated


def encode_frame_to_base64(frame: np.ndarray, quality: int = 85) -> str:
    success, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        raise RuntimeError("Failed to encode frame to JPEG")
    return base64.b64encode(buffer).decode("utf-8")


def build_storyboard_grid(
    bgr_frames: List[np.ndarray],
    cols: int = 3,
    tile_w: int = 420,
    tile_h: int = 280,
    save_path: Optional[Path] = None
) -> Tuple[np.ndarray, str]:
    """
    Combines up to 6-9 sampled frames into a single comic-strip Storyboard Grid JPEG.
    Makes temporal action recognition for Grok Vision significantly more accurate and easy to inspect.
    """
    if not bgr_frames:
        raise ValueError("No frames provided to build storyboard grid.")

    # Select up to 6 evenly spaced key frames if there are many
    max_tiles = cols * 2
    if len(bgr_frames) > max_tiles:
        indices = np.linspace(0, len(bgr_frames) - 1, max_tiles, dtype=int)
        selected = [bgr_frames[i] for i in indices]
    else:
        selected = list(bgr_frames)

    rows = math.ceil(len(selected) / cols)
    grid_rows = []

    for r in range(rows):
        row_tiles = []
        for c in range(cols):
            idx = r * cols + c
            if idx < len(selected):
                tile = cv2.resize(selected[idx], (tile_w, tile_h), interpolation=cv2.INTER_AREA)
            else:
                tile = np.zeros((tile_h, tile_w, 3), dtype=np.uint8)
            # Add subtle border around each tile
            cv2.rectangle(tile, (0, 0), (tile_w - 1, tile_h - 1), (60, 60, 60), 2)
            row_tiles.append(tile)
        grid_rows.append(cv2.hconcat(row_tiles))

    grid_img = cv2.vconcat(grid_rows)

    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(save_path), grid_img)

    b64 = encode_frame_to_base64(grid_img, quality=85)
    return grid_img, f"data:image/jpeg;base64,{b64}"


def sample_video_frames(
    video_path: str,
    fps: float = 2.0,
    max_side: int = 640,
    storyboard_out: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Extracts frames at a regular sampled rate, burns frame numbers + timestamps,
    and builds a single Storyboard Grid image for Grok Vision.
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    video_native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_step = max(1, int(round(video_native_fps / fps)))

    frames = []
    annotated_bgr_frames = []
    frame_idx = 0
    step_num = 1

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_step == 0:
            timestamp_s = frame_idx / video_native_fps
            resized = resize_frame(frame, max_side=max_side)
            badged = draw_frame_badge(resized, step_num=step_num, timestamp_s=timestamp_s)
            b64 = encode_frame_to_base64(badged)
            annotated_bgr_frames.append(badged)
            frames.append({
                "step_num": step_num,
                "timestamp_s": round(timestamp_s, 2),
                "frame_idx": frame_idx,
                "data_url": f"data:image/jpeg;base64,{b64}",
                "base64": b64
            })
            step_num += 1

        frame_idx += 1

    cap.release()

    if storyboard_out is None:
        storyboard_out = Path("logs/storyboard.jpg")

    _, storyboard_data_url = build_storyboard_grid(
        annotated_bgr_frames,
        cols=3,
        save_path=storyboard_out
    )

    return {
        "frames": frames,
        "storyboard_path": str(storyboard_out),
        "storyboard_data_url": storyboard_data_url
    }
