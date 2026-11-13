"""Regression geometry only: no Robolink connection and no robot moves."""
import math
from types import SimpleNamespace
import pytest

from robot_control.backends.robodk_sim import RoboDKSimController
from robot_control.models import Pose, RobotControlError
from robot_control.planning import table_surface
from robot_control.scene_registry import SceneRegistry


@pytest.fixture
def sim(scene_cfg):
    rm = pytest.importorskip('robodk.robomath')
    class Node:
        def __init__(self, name, mat, parent=None):
            self.name, self.mat, self.parent = name, mat, parent
            self.reparents = 0
        def PoseAbs(self): return self.mat
        def setPoseAbs(self, mat): self.mat = mat
        def Valid(self): return True
        def Name(self): return self.name
        def Parent(self): return self.parent
        def setParentStatic(self, parent):
            self.parent = parent
            self.reparents += 1
    c = object.__new__(RoboDKSimController)
    c.scene, c.rm = scene_cfg.model_copy(deep=True), rm
    c._require = lambda: None
    # Nontrivial base/world transform catches accidental use of world positions as base positions.
    c.base = Node('base', rm.transl(100, 200, 300) * rm.rotz(0.4))
    c.tool = Node('tool', rm.eye(4))
    nodes = {}
    table = c.scene.station_geometry['table']
    nodes['table'] = Node('table', c.base.mat * rm.UR_2_Pose([v*1000 for v in table.pose_m_rad[:3]] + table.pose_m_rad[3:]))
    for oid, obj in c.scene.objects.items():
        if obj.station_item:
            xyz = c.scene.targets[obj.grasp_target].pose_m_rad[:2]
            nodes[obj.station_item] = Node(obj.station_item, c.base.mat * rm.transl(xyz[0]*1000, xyz[1]*1000, 0))
    c.RDK = SimpleNamespace(Item=lambda name, *args: nodes[name])
    c.rl = SimpleNamespace(ITEM_TYPE_OBJECT=5)
    c.nodes, c._transient_poses = nodes, {}
    c._holding, c._held_item = None, None
    c._target_item = lambda tid: SimpleNamespace()  # deliberately has no station-target setters
    c.tcp = Pose.from_list([-0.5, 0, 0.295, math.pi, 0, 0])
    c.get_tcp_pose = lambda: c.tcp
    c.set_object = lambda oid, xyz: setattr(nodes[c.scene.objects[oid].station_item], 'mat',
        c.base.mat * rm.transl(*(v*1000 for v in xyz)))
    return c


@pytest.fixture
def maintenance(sim):
    sim.rl.RUNMODE_SIMULATE = 1
    sim.rl.ROBOTCOM_DISCONNECTED = -2
    sim.rl.ITEM_TYPE_ROBOT = 2
    sim.rl.ITEM_TYPE_TOOL = 4
    sim.rl.COLLISION_ON = 1
    sim.RDK.RunMode = lambda: 1
    sim.robot = SimpleNamespace(ConnectedState=lambda: (-2, 'disconnected'), Busy=lambda: False)
    sim.RDK.ItemList = lambda kind: list(sim.nodes.values())
    for node in sim.nodes.values():
        node.parent = SimpleNamespace(Valid=lambda: False)
    sim.pairs = []
    sim.RDK.CollisionActivePairList = lambda: list(sim.pairs)
    def enable(state, a, b, ia, ib):
        assert state == 1  # maintenance must NEVER disable protection
        sim.pairs.append((a, b, ia, ib))
        return 1
    sim.RDK.setCollisionActivePair = enable
    sim.RDK.Collision = lambda item, table: int((sim.base.mat.inv() * item.mat).Pos()[2] < 0.5)
    return sim


def test_startup_restores_required_table_pairs_without_touching_other_pairs_or_poses(maintenance):
    sim = maintenance
    untouched = (sim.nodes['Blue_Bucket_01'], sim.nodes['Orange_Screw_03'], 0, 0)
    sim.pairs.append(untouched)
    before = {name: node.PoseAbs().list() for name, node in sim.nodes.items()}
    assert sim.ensure_table_collision_pairs() == 4
    assert len(sim.pairs) == 5 and untouched in sim.pairs
    assert sim.ensure_table_collision_pairs() == 0
    assert {name: node.PoseAbs().list() for name, node in sim.nodes.items()} == before
    # Opening an old saved map does not rely on the previous in-memory repair.
    sim.pairs.clear()
    assert sim.ensure_table_collision_pairs() == 4


@pytest.mark.parametrize('condition', ['mode', 'driver', 'busy'])
def test_station_preparation_is_simulation_only_and_stationary(maintenance, condition):
    sim = maintenance
    if condition == 'mode': sim.RDK.RunMode = lambda: 6
    if condition == 'driver': sim.robot.ConnectedState = lambda: (0, 'ready')
    if condition == 'busy': sim.robot.Busy = lambda: True
    with pytest.raises(RobotControlError): sim.ensure_table_collision_pairs()
    with pytest.raises(RobotControlError): sim.repair_resting_contacts()
    assert sim.pairs == []


def test_resting_contact_repair_preserves_xy_rotation_parent_and_is_idempotent(maintenance):
    sim = maintenance
    sim.set_object('blue_bucket_01', [-0.45, 0, 0])
    sim.nodes['Blue_Bucket_01'].mat *= sim.rm.rotz(0.5)
    before = {name: (node.mat, node.parent) for name, node in sim.nodes.items()}
    assert len(sim.repair_resting_contacts()) == 4
    for oid, obj in sim.scene.objects.items():
        if not obj.station_item: continue
        node = sim.nodes[obj.station_item]
        old, parent = before[obj.station_item]
        assert node.parent is parent
        assert node.mat.Pos()[:2] == pytest.approx(old.Pos()[:2])
        assert node.mat[0, 0] == pytest.approx(old[0, 0])
        assert sim.object_position(oid)[2] == pytest.approx(0.001)
    assert sim.repair_resting_contacts() == []


@pytest.mark.parametrize('condition', ['penetrating', 'attached', 'still_colliding'])
def test_resting_repair_refuses_invalid_scene_without_leaving_partial_changes(maintenance, condition):
    sim = maintenance
    if condition == 'penetrating': sim.set_object('white_bucket_lid_04', [-0.62, 0.24, -0.03])
    if condition == 'attached':
        sim.nodes['White_Bucket_Lid_04'].parent = SimpleNamespace(Valid=lambda: True, Type=lambda: 4)
    if condition == 'still_colliding': sim.RDK.Collision = lambda *args: 1
    before = {name: node.mat.list() for name, node in sim.nodes.items()}
    with pytest.raises(RobotControlError): sim.repair_resting_contacts()
    assert {name: node.mat.list() for name, node in sim.nodes.items()} == before


def test_startup_pair_failure_is_not_silently_accepted(maintenance):
    maintenance.RDK.setCollisionActivePair = lambda *args: 0
    with pytest.raises(RobotControlError): maintenance.ensure_table_collision_pairs()


def test_left_push_10cm_uses_bucket_new_table_center_position(sim):
    sim.set_object('blue_bucket_01', [-0.45, 0, 0.001])
    sim.prepare_push('blue_bucket_01', 'left', 0.10)
    push = sim.scene.objects['blue_bucket_01'].push
    start, end = (sim._transient_poses[t] for t in (push.start_target, push.end_target))
    assert start.x == pytest.approx(-0.45)
    assert end.y - start.y == pytest.approx(0.10)
    assert end.z == pytest.approx(start.z)
    assert [start.rx, start.ry, start.rz] == pytest.approx([end.rx, end.ry, end.rz])


def test_push_object_table_protection_is_required_during_stroke(sim):
    sim._pushed_item = sim.nodes['Blue_Bucket_01']
    sim.RDK.CollisionActivePairList = lambda: []
    with pytest.raises(RobotControlError, match='collision pair is disabled'):
        sim._require_held_table_pair()


def test_container_destination_follows_bucket_after_it_moves(sim):
    sim.set_object('blue_bucket_01', [-0.45, 0.20, 0.001])
    sim.set_object('orange_screw_03', [-0.5, 0, 0.25])
    sim._holding = 'orange_screw_03'
    assert sim.retarget_place('orange_screw_03', 'blue_bucket_01_inside')
    target = sim._transient_poses['blue_bucket_01_drop']
    assert [target.x, target.y] == pytest.approx([-0.45, 0.20])
    assert target.z - 0.045 == pytest.approx(0.117)
    sim.set_object('blue_bucket_01', [-0.4, 0.10, 0.001])
    sim.retarget_place('orange_screw_03', 'blue_bucket_01_inside')
    assert sim._transient_poses['blue_bucket_01_drop'].y == pytest.approx(0.10)


def test_named_delivery_accounts_for_bucket_grasp_offset(sim):
    sim._holding = 'blue_bucket_01'
    sim.set_object('blue_bucket_01', [-0.5, 0, 0.2])
    sim.tcp = Pose.from_list([-0.5, 0, 0.2575, math.pi, 0, 0])
    sim.retarget_place('blue_bucket_01', 'delivery_zone')
    pose = sim._transient_poses['delivery_place']
    assert pose.z == pytest.approx(0.0585)
    assert pose.z - 0.0575 == pytest.approx(0.001)


def test_release_below_table_is_refused_without_detaching(sim):
    sim._holding = 'blue_bucket_01'
    sim._held_item = sim.nodes['Blue_Bucket_01']
    sim.set_object('blue_bucket_01', [-0.45, 0.2, -0.0295])
    with pytest.raises(RobotControlError, match='penetrate the table'):
        sim.open_gripper()
    assert sim._holding == 'blue_bucket_01'
    assert sim._held_item.reparents == 0


def test_container_already_intersecting_table_is_not_a_valid_destination(sim):
    sim.set_object('blue_bucket_01', [-0.45, 0.20, -0.0295])
    with pytest.raises(RobotControlError, match='already intersects'):
        sim._destination_bottom('orange_screw_03', 'blue_bucket_01_inside')


def test_table_center_and_bounds_are_explicit_not_corner_interpretations(sim):
    table = SceneRegistry(sim.scene).snapshot()['table']
    assert table['center_xy_m'] == [-0.45, 0]
    assert table['bounds_x_m'] == pytest.approx([-0.70, -0.20])
    assert table['origin_semantics'] == 'bottom_center'
    assert sim.table_center() == (-0.45, 0)
    with pytest.raises(RobotControlError, match='beyond the table'):
        sim.table_placement_pose('blue_bucket_01', -0.20, 0)


def test_live_scene_positions_refresh_and_are_not_camera_claims(sim):
    sim.set_object('blue_bucket_01', [-0.45, 0.2, 0.001])
    assert sim.scene_observation()['objects']['blue_bucket_01']['position_m'] == pytest.approx([-0.45, 0.2, 0.001])
    sim.set_object('blue_bucket_01', [-0.5, 0, 0.001])
    observation = sim.scene_observation()
    assert observation['objects']['blue_bucket_01']['position_m'][0] == pytest.approx(-0.5)
    assert observation['camera_verified'] is False


def test_verification_rejects_wrong_position_even_with_empty_gripper(sim):
    sim.set_object('blue_bucket_01', [-0.62, -0.24, 0.001])
    assert sim.verify_placement('blue_bucket_01', 'delivery_zone')['ok'] is False
    sim.set_object('blue_bucket_01', [-0.45, 0.2, 0.001])
    result = sim.verify_placement('blue_bucket_01', 'delivery_zone')
    assert result['ok'] and result['camera_verified'] is False


def test_pick_retargeting_is_transient_not_a_station_edit(sim):
    sim.set_object('orange_screw_03', [-0.4, 0.1, 0.001])
    sim.retarget_pick('orange_screw_03')
    assert sim._transient_poses['orange_screw_03_grasp'].x == pytest.approx(-0.4)
    assert sim.scene.targets['orange_screw_03_grasp'].pose_m_rad[0] != -0.4
    sim.clear_transient_plans()
    assert not sim._transient_poses


def test_table_pose_mismatch_fails_closed(sim):
    sim.nodes['table'].mat = sim.base.mat * sim.rm.transl(-200, 0, -20)
    with pytest.raises(RobotControlError, match='does not match'):
        sim.table_center()


def test_axis_aligned_bucket_footprint_is_not_inflated_to_a_circle(scene_cfg):
    assert table_surface(scene_cfg, -0.62, -0.24, [0.139, 0.139, 0.115]) == pytest.approx(0)
    with pytest.raises(RobotControlError):
        table_surface(scene_cfg, -0.62, -0.24, [0.139, 0.139, 0.115], math.pi/4)


def test_carried_object_table_pair_must_be_enabled(sim):
    sim._held_item = sim.nodes['Blue_Bucket_01']
    sim.RDK.CollisionActivePairList = lambda: []
    with pytest.raises(RobotControlError, match='collision pair is disabled'):
        sim._require_held_table_pair()
    sim.RDK.CollisionActivePairList = lambda: [(sim.nodes['table'], sim._held_item, 0, 0)]
    sim._require_held_table_pair()


def test_pick_requires_table_pair_before_attaching_object(sim):
    sim.RDK.CollisionActivePairList = lambda: []
    with pytest.raises(RobotControlError, match='Blue_Bucket_01.*collision pair is disabled'):
        sim.validate_pick('blue_bucket_01')
    assert sim._holding is None
    assert sim.nodes['Blue_Bucket_01'].reparents == 0
    sim.RDK.CollisionActivePairList = lambda: [(sim.nodes['table'], sim.nodes['Blue_Bucket_01'], 0, 0)]
    sim.validate_pick('blue_bucket_01')


def test_pick_configuration_refusal_happens_before_any_motion(make_executor):
    from robot_control.models import ErrorCode
    ex = make_executor()
    def refuse(oid):
        raise RobotControlError(ErrorCode.CONFIG_ERROR, 'object/table collision pair is disabled')
    ex.c.validate_pick = refuse
    result = ex.submit({'task_id': 'invalid-pick-map', 'actions': [
        {'action_id': 'identify', 'action': 'identify_object', 'parameters': {'object_id': 'test_cube'}},
        {'action_id': 'pick', 'action': 'pick_object', 'parameters': {'object_id': 'test_cube'}}
    ]}, wait=True)
    assert result.error.code == ErrorCode.CONFIG_ERROR
    assert ex.c.history == []
    assert ex.c.get_state().holding_object_id is None


def test_object_import_policy_never_disables_protected_table():
    from robot_control.sim_objects.robodk_import import _configure_object_collisions
    table, pad, obj = object(), object(), object()
    calls = []
    rdk = SimpleNamespace(setCollisionActivePair=lambda state, a, b: calls.append((state, a, b)) or 1)
    rl = SimpleNamespace(COLLISION_OFF=0, COLLISION_ON=1)
    assert _configure_object_collisions(rdk, rl, obj, [table, pad], [table])
    assert (0, obj, table) not in calls
    assert (1, obj, table) in calls
