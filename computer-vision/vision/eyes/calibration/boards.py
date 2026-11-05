"""Calibration targets for the flange hand-eye: chessboard (inner corners, square size from the setup JSON) or the
existing 4-marker ArUco sheet (`aruco_4`, see `hand_eye.BOARD_TOPLEFT_M`).

- Chessboard object points: `P(i, j) = [i * s, j * s, 0]` metres for inner corner column i, row j, in the order
  `cv2.findChessboardCornersSB` returns them for `patternSize = (columns, rows)` (row-major, x along columns).
- A board whose inner-corner counts have the same parity looks identical after a 180° rotation, so the detector may
  return the corners in reversed order. For such boards the order is canonicalized per image (first corner = the
  one nearer the image top-left); a sample still flipped relative to the others is caught at solve time
  (`flange_calibration.solve`). An odd x even inner-corner board is unambiguous.
- PnP uses the real colour intrinsics and distortion (`capture.realsense.opencv_distortion`); unsupported distortion
  models fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from ..capture.realsense import UnsupportedDistortion, opencv_distortion
from .aruco_calib import detect_aruco_corners
from .hand_eye import BOARD_SIDE_M, BOARD_TOPLEFT_M, camera_matrix
from .setup_config import BoardSetup, orientation_ambiguous


@dataclass(frozen=True)
class BoardObservation:
    T_camera_board: np.ndarray       # board -> camera optical frame, metres
    image_points: np.ndarray         # (N, 2) pixels, in object-point order
    object_points: np.ndarray        # (N, 3) metres, board frame
    rmse_px: float
    board_type: str
    orientation_ambiguous: bool = False
    canonicalized: bool = False


class BoardRejected(Exception):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


def chessboard_object_points(cols: int, rows: int, square_m: float) -> np.ndarray:
    jj, ii = np.mgrid[0:rows, 0:cols]
    return np.column_stack([ii.ravel() * square_m, jj.ravel() * square_m, np.zeros(cols * rows)]).astype(np.float64)


def detect_chessboard(frame_bgr: np.ndarray, cols: int, rows: int) -> np.ndarray | None:
    """(cols*rows, 2) inner corners in detector order, or None. The whole board must be visible."""
    gray = frame_bgr if frame_bgr.ndim == 2 else cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    flags = cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
    ok, corners = cv2.findChessboardCornersSB(gray, (cols, rows), flags=flags)
    if not ok or corners is None or len(corners) != cols * rows:
        return None
    return corners.reshape(-1, 2).astype(np.float64)


def canonical_order(corners: np.ndarray) -> tuple[np.ndarray, bool]:
    """For 180°-symmetric boards: reverse the order if the last corner is nearer the image top-left than the first."""
    if corners[-1].sum() < corners[0].sum():
        return corners[::-1].copy(), True
    return corners, False


def solve_board_pose(obj: np.ndarray, img: np.ndarray, K: np.ndarray, dist: np.ndarray) -> tuple[np.ndarray, float]:
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        raise BoardRejected("board_not_detected", "PnP failed for the detected board")
    rvec, tvec = cv2.solvePnPRefineLM(obj, img, K, dist, rvec, tvec)
    proj, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
    err = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img) ** 2, axis=1))))
    T = np.eye(4)
    T[:3, :3] = cv2.Rodrigues(rvec)[0]
    T[:3, 3] = tvec.ravel()
    if T[2, 3] <= 0:
        raise BoardRejected("board_not_detected", "board solved behind the camera")
    return T, err


def reprojection_rmse(T_camera_board: np.ndarray, obj: np.ndarray, img: np.ndarray, K: np.ndarray,
                      dist: np.ndarray) -> float:
    rvec = cv2.Rodrigues(np.ascontiguousarray(T_camera_board[:3, :3]))[0]
    proj, _ = cv2.projectPoints(obj, rvec, T_camera_board[:3, 3].astype(np.float64), K, dist)
    return float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img) ** 2, axis=1))))


def observe_board(frame_bgr: np.ndarray, intrinsics: Any, board: BoardSetup) -> BoardObservation:
    """Board pose in the camera optical frame from one colour image. Raises `BoardRejected` with a typed status."""
    if intrinsics is None:
        raise BoardRejected("camera_offline", "camera intrinsics unavailable")
    if frame_bgr is None or frame_bgr.shape[1] != intrinsics.width or frame_bgr.shape[0] != intrinsics.height:
        raise BoardRejected("camera_mismatch", "image size does not match the camera intrinsics")
    try:
        dist = opencv_distortion(intrinsics)
    except UnsupportedDistortion as exc:
        raise BoardRejected("camera_mismatch", str(exc)) from exc
    K = camera_matrix(intrinsics)
    if board.type == "chessboard":
        cols, rows = board.inner_corners_columns, board.inner_corners_rows
        if cols is None or rows is None:
            raise BoardRejected("board_geometry_incomplete", "inner corner counts are not set")
        corners = detect_chessboard(frame_bgr, cols, rows)
        if corners is None:
            raise BoardRejected("board_not_detected", f"chessboard with {cols}x{rows} inner corners not found: whole "
                                "board in view, flat, well lit, and the corner counts right?")
        ambiguous = orientation_ambiguous(cols, rows)
        flipped = False
        if ambiguous:
            corners, flipped = canonical_order(corners)
        obj = chessboard_object_points(cols, rows, board.square_size_mm / 1000.0)
        T, err = solve_board_pose(obj, corners, K, dist)
        return BoardObservation(T, corners, obj, err, "chessboard", ambiguous, flipped)
    # aruco_4: the printed MOTIQ sheet (DICT_4X4_50, IDs 0-3, 50.8 mm markers); at least 2 markers
    found = {m: c for m, c in detect_aruco_corners(frame_bgr).items() if m in BOARD_TOPLEFT_M}
    if len(found) < 2:
        raise BoardRejected("board_not_detected", "ArUco sheet not seen: at least 2 whole markers needed")
    s = BOARD_SIDE_M
    obj, img = [], []
    for m_id in sorted(found):
        x0, y0 = BOARD_TOPLEFT_M[m_id]
        obj += [(x0, y0, 0.0), (x0 + s, y0, 0.0), (x0 + s, y0 + s, 0.0), (x0, y0 + s, 0.0)]
        img += [tuple(p) for p in found[m_id]]
    obj_a, img_a = np.array(obj, dtype=np.float64), np.array(img, dtype=np.float64)
    T, err = solve_board_pose(obj_a, img_a, K, dist)
    return BoardObservation(T, img_a, obj_a, err, "aruco_4")
