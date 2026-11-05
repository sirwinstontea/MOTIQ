"""Synthetic camera images of a chessboard for calibration tests (no hardware)."""
from __future__ import annotations

import cv2
import numpy as np

PX_PER_SQUARE = 40
MARGIN_PX = 60


def board_texture(cols: int, rows: int, mark: bool = False) -> np.ndarray:
    """White sheet with (cols+1) x (rows+1) squares; inner corner (0, 0) at (MARGIN+PX, MARGIN+PX)."""
    w = (cols + 1) * PX_PER_SQUARE + 2 * MARGIN_PX
    h = (rows + 1) * PX_PER_SQUARE + 2 * MARGIN_PX
    tex = np.full((h, w), 255, np.uint8)
    for r in range(rows + 1):
        for c in range(cols + 1):
            if (r + c) % 2 == 1:  # OpenCV starts the inner-corner order next to a white corner square
                y0, x0 = MARGIN_PX + r * PX_PER_SQUARE, MARGIN_PX + c * PX_PER_SQUARE
                tex[y0:y0 + PX_PER_SQUARE, x0:x0 + PX_PER_SQUARE] = 0
    return tex


def render(T_camera_board: np.ndarray, K: np.ndarray, cols: int, rows: int, square_m: float,
           size=(640, 480), dist: np.ndarray | None = None) -> np.ndarray | None:
    """BGR image of the board seen from `T_camera_board` (board -> camera). None if the board is behind the camera."""
    tex = board_texture(cols, rows)
    off = MARGIN_PX + PX_PER_SQUARE - 0.5  # pixel-centre convention: the corner sits between texels
    S_inv = np.array([[square_m / PX_PER_SQUARE, 0, -off * square_m / PX_PER_SQUARE],
                      [0, square_m / PX_PER_SQUARE, -off * square_m / PX_PER_SQUARE], [0, 0, 1]])
    if T_camera_board[2, 3] <= 0:
        return None
    H = K @ np.column_stack([T_camera_board[:3, 0], T_camera_board[:3, 1], T_camera_board[:3, 3]]) @ S_inv
    img = cv2.warpPerspective(tex, H, size, flags=cv2.INTER_AREA, borderValue=180)
    if dist is not None and np.any(dist):
        # apply lens distortion: for each output (distorted) pixel find its undistorted source
        w, h = size
        uu, vv = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        und = cv2.undistortPoints(np.stack([uu.ravel(), vv.ravel()], 1).reshape(-1, 1, 2), K, dist, P=K)
        mx, my = und[:, 0, 0].reshape(h, w), und[:, 0, 1].reshape(h, w)
        img = cv2.remap(img, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_LINEAR, borderValue=180)
    img = cv2.GaussianBlur(img, (3, 3), 0.6)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def look_at(eye: np.ndarray, target: np.ndarray, up=np.array([0.0, 1.0, 0.0])) -> np.ndarray:
    """T_world_camera for an optical camera (z forward, y down) at `eye` looking at `target`."""
    z = target - eye
    z = z / np.linalg.norm(z)
    x = np.cross(-up, z)
    if np.linalg.norm(x) < 1e-6:
        x = np.array([1.0, 0, 0])
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    T = np.eye(4)
    T[:3, :3] = np.column_stack([x, y, z])
    T[:3, 3] = eye
    return T
