# eyes/capture

Video sources. Every reader offers the same interface: `read_once`, `start_background`, `get_latest`, `stop`.

| File | Purpose |
|---|---|
| `reader.py` | `VideoReader`: `webcam`, stream, replay and `synthetic` modes. Also makes the disconnected placeholder frame and sample videos. |
| `realsense.py` | `RealSenseReader`: aligned colour and depth from a D415 or D435, plus intrinsics. `pyrealsense2` loads lazily. |
| `pybullet.py` | **Read only**, never moves the robot. `PyBulletFlange` gives the flange pose, `PyBulletCameraReader` gives simulated colour and depth. |
| `sim_render.py` | `SoftwareD435`: renders the PyBullet station with numpy and OpenCV, because PyBullet without a licence limits snapshots and blocks the depth camera |
