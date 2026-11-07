"""Read-only digital twin: physical (or URSim / fake) UR5e + calibrated RealSense -> PyBullet simulation.

MIRROR ONLY, NO PHYSICAL MOTION. One direction only: real world -> PyBullet. See docs/DIGITAL_TWIN_SYNC.md.

- UR side: `rtde_receive.RTDEReceiveInterface` only (telemetry.py). Nothing here can command the robot.
- Vision side: the computer-vision server's `POST /localization/snapshot_all` (flange eye-in-hand calibration,
  stationary-verified frame) or a replay fixture for offline simulation (vision_client.py).
- PyBullet side: `robot.setJoints()` on a SIMULATED robot and poses of `MOTIQ_Detected_*` markers, guarded before every
  batch (sim_writer.py).
"""

MODE = "mirror_only"
