"""The free flange camera: PyBullet color and depth, with PyBullet's camera license out of the path."""
import numpy as np

from vision.eyes.capture.pybullet_cam import PyBulletCameraReader


def test_look_down_measures_the_screw():
    reader = PyBulletCameraReader(width=320, height=240, fps=5)
    try:
        ok, _, _, _ = reader.read_once()
        assert ok
        _, color, depth, _, _ = reader.get_frames()
        assert color.shape == (240, 320, 3)
        assert depth is not None and depth.shape == (240, 320)
        # Screw stands at its start pose; the top is 90 mm above the table.
        T = np.linalg.inv(reader.camera_in_base())
        cam = T @ np.array([-0.62, 0.08, 0.09, 1.0])
        assert cam[2] > 0.1
        k = reader.intrinsics
        u = int(round(cam[0] / cam[2] * k.fx + k.cx))
        v = int(round(cam[1] / cam[2] * k.fy + k.cy))
        assert 0 <= u < 320 and 0 <= v < 240
        measured = float(depth[v, u])
        assert measured > 0
        assert abs(measured - cam[2] * 1000.0) < 40.0
    finally:
        reader.stop()
