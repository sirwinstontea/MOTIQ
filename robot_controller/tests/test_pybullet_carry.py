"""A part parented to the flange is not an obstacle, and a drop clears the bucket."""
import pytest


def test_carried_screw_can_reach_the_bucket(robot_cfg, scene_cfg):
    pytest.importorskip("pybullet")
    from robot_control.backends.pybullet_sim import PyBulletSimController

    arm = PyBulletSimController(robot_cfg, scene_cfg)
    arm.connect()
    try:
        world = arm.world
        grasp = scene_cfg.targets["orange_screw_03_grasp"].pose_m_rad
        q = world.ik(grasp, world.joints())
        assert q is not None
        world.set_joints(q)
        assert world.grab("Orange_Screw_03")
        lift = scene_cfg.targets["orange_screw_03_lift"].pose_m_rad
        q = world.ik(lift, q)
        assert q is not None
        world.set_joints(q)

        to_bucket = arm.plan_segment("blue_bucket_01_above", "joint")
        world._held = None
        blocked = arm.test_segment(to_bucket)
        world._held = "Orange_Screw_03"
        assert blocked.ok is False and "Orange_Screw_03" in blocked.reason

        carried = arm.test_segment(to_bucket)
        assert carried.ok, carried.reason
        world.set_joints(to_bucket.end_joints)

        drop = arm.plan_segment("blue_bucket_01_drop", "linear")
        placed = arm.test_segment(drop)
        assert placed.ok, placed.reason
    finally:
        arm.disconnect()


def test_a_released_screw_rests_in_the_bucket_and_the_bucket_can_be_pushed(robot_cfg, scene_cfg):
    pytest.importorskip("pybullet")
    from robot_control.backends.pybullet_sim import PyBulletSimController

    arm = PyBulletSimController(robot_cfg, scene_cfg)
    arm.connect()
    try:
        world = arm.world
        grasp = scene_cfg.targets["orange_screw_03_grasp"].pose_m_rad
        q = world.ik(grasp, world.joints())
        assert q is not None
        world.set_joints(q)
        assert world.grab("Orange_Screw_03")
        drop = scene_cfg.targets["blue_bucket_01_drop"].pose_m_rad
        q = world.ik(drop, q)
        assert q is not None
        world.set_joints(q)
        world.release()
        bottom = world.object_bottom("Orange_Screw_03")
        assert bottom is not None and bottom[2] == pytest.approx(0.0, abs=1e-4)

        start = scene_cfg.targets["blue_bucket_01_push_start"].pose_m_rad
        q = world.ik(start, world.joints())
        assert q is not None
        world.set_joints(q)
        push = arm.plan_segment("blue_bucket_01_push_end", "linear")
        result = arm.test_segment(push)
        assert result.ok, result.reason
    finally:
        arm.disconnect()


def test_a_push_follows_where_the_camera_saw_the_bucket(robot_cfg, scene_cfg):
    pytest.importorskip("pybullet")
    import pybullet as p
    from robot_control.backends.pybullet_sim import PyBulletSimController

    arm = PyBulletSimController(robot_cfg, scene_cfg)
    arm.connect()
    try:
        world = arm.world
        seen = [-0.62, -0.29, 0.05]   # the bucket is no longer on its saved spot
        p.resetBasePositionAndOrientation(
            world.bodies["Blue_Bucket_01"], [seen[0], seen[1], 0.0], [0, 0, 0, 1],
            physicsClientId=world.client)
        assert arm.retarget_push("blue_bucket_01", seen) is not None
        start = arm.plan_segment("blue_bucket_01_push_start", "joint")
        assert start.end_joints is not None, "push start over the camera point is unreachable"
        world.set_joints(start.end_joints)
        arm._gripper = "closed"
        assert arm.begin_push("blue_bucket_01") is True
        moved = arm.retarget_pick("green_ring_02", [-0.50, 0.0, 0.0])
        assert moved[:2] == [-0.5, 0.0]
        assert arm._targets["green_ring_02_grasp"][2] == pytest.approx(
            scene_cfg.targets["green_ring_02_grasp"].pose_m_rad[2])
    finally:
        arm.disconnect()


def test_reset_sends_the_arm_home_and_puts_a_held_part_back(robot_cfg, scene_cfg):
    pytest.importorskip("pybullet")
    from robot_control.backends.pybullet_sim import PyBulletSimController

    arm = PyBulletSimController(robot_cfg, scene_cfg)
    arm.connect()
    try:
        world = arm.world
        grasp = scene_cfg.targets["orange_screw_03_grasp"].pose_m_rad
        q = world.ik(grasp, world.joints())
        assert q is not None
        world.set_joints(q)
        assert world.grab("Orange_Screw_03")
        arm._holding = "orange_screw_03"
        arm._gripper = "closed"
        arm.reset_to_start()
        home = scene_cfg.targets["home"].joints_rad
        assert home is not None
        assert world.joints() == pytest.approx(home, abs=1e-6)
        assert world._held is None and arm._holding is None and arm._gripper == "open"
        bottom = world.object_bottom("Orange_Screw_03")
        assert bottom is not None
        assert list(bottom) == pytest.approx([-0.62, 0.08, 0.0], abs=1e-3)
    finally:
        arm.disconnect()
