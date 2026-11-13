from types import SimpleNamespace
import pytest

from robot_control.backends.robodk_sim import RoboDKSimController
from robot_control.models import RobotControlError


class Item:
    def __init__(self, name, kind=5): self.name, self.name_calls, self.kind = name, 0, kind
    def Name(self):
        self.name_calls += 1
        return self.name
    def Valid(self): return True
    def Type(self): return self.kind


def backend(scene_cfg):
    c = object.__new__(RoboDKSimController)
    c.scene = scene_cfg.model_copy(deep=True)
    c.scene.collision_policy = 'table_and_tagged'
    c.robot, c.tool, table = Item('robot'), Item('tool'), Item('table')
    bucket = Item(c.scene.objects['blue_bucket_01'].station_item)
    tagged = Item(c.scene.objects['orange_screw_03'].station_item)
    c.scene.objects['orange_screw_03'].avoid_collision = True
    obstacle, other_robot = Item('unknown_obstacle'), Item('other_robot')
    pairs = [(c.robot, table, 2, 0), (c.robot, bucket, 5, 0), (c.tool, bucket, 0, 0),
             (bucket, table, 0, 0), (bucket, tagged, 0, 0), (bucket, obstacle, 0, 0),
             (c.robot, c.robot, 2, 4), (other_robot, bucket, 2, 0)]
    c.rl = SimpleNamespace(ITEM_TYPE_OBJECT=5, COLLISION_OFF=0, COLLISION_ON=1)
    c.changes = []
    c.pairs = pairs
    c.batch_calls = []
    def set_pair(state, a, b, ia, ib):
        c.changes.append((state, a.name, b.name, ia, ib))
        pair = (a, b, ia, ib)
        if state == 0 and pair in pairs:
            pairs.remove(pair)
        if state == 1 and pair not in pairs:
            pairs.append(pair)
        return 1
    def set_batch(states, aa, bb, ia, ib):
        c.batch_calls.append(len(states))
        for args in zip(states, aa, bb, ia, ib): set_pair(*args)
        return len(states)
    c.RDK = SimpleNamespace(Item=lambda *args: table, CollisionActivePairList=lambda: pairs,
                            setCollisionActivePair=set_pair, setCollisionActivePairList=set_batch)
    return c


def test_only_contact_object_pairs_are_temporarily_disabled(scene_cfg):
    c = backend(scene_cfg)
    with c._contact_policy():
        off = [x for x in c.changes if x[0] == 0]
        assert len(off) == 2
        assert {p[1] for p in off} == {'robot', 'tool'}
    assert len([x for x in c.changes if x[0] == 1]) == 2


def test_delivery_marker_is_visual_but_tagged_geometry_is_protected(scene_cfg):
    c = backend(scene_cfg)
    pad = Item('delivery_pad')
    c.pairs.append((c.robot, pad, 2, 0))
    with c._contact_policy(): pass
    assert any(state == 0 and name == 'robot' and other == 'delivery_pad'
               for state, name, other, _, _ in c.changes)
    c.changes.clear()
    c.scene.station_geometry['delivery_pad'].avoid_collision = True
    with c._contact_policy(): pass
    assert not any(other == 'delivery_pad' for _, _, other, _, _ in c.changes)


def test_map_is_restored_after_a_path_exception(scene_cfg):
    c = backend(scene_cfg)
    with pytest.raises(RuntimeError):
        with c._contact_policy():
            raise RuntimeError('path test failed')
    assert len([x for x in c.changes if x[0] == 1]) == 2


def test_missing_table_robot_pairs_fail_closed(scene_cfg):
    c = backend(scene_cfg)
    c.RDK.CollisionActivePairList = lambda: []
    with pytest.raises(RobotControlError, match='not active'):
        with c._contact_policy(): pass


def test_contact_policy_never_enables_hardware_backend(scene_cfg):
    c = backend(scene_cfg)
    c.backend_name = 'ursim_sync'
    with pytest.raises(RobotControlError, match='RoboDK-only'):
        with c._contact_policy(): pass


def test_policy_batches_updates_and_resolves_each_item_name_once(scene_cfg):
    c = backend(scene_cfg)
    items = {item for a, b, _, _ in c.pairs for item in (a, b)}
    original = set(c.pairs)
    with c._contact_policy(): pass
    assert c.batch_calls == [2, 2]
    assert set(c.pairs) == original
    assert all(item.name_calls <= 1 for item in items)


def test_policy_restores_all_pairs_after_partial_batch_failure(scene_cfg):
    c = backend(scene_cfg)
    original = set(c.pairs)
    batch = c.RDK.setCollisionActivePairList
    def broken(states, aa, bb, ia, ib):
        if states[0] == 0:
            c.RDK.setCollisionActivePair(states[0], aa[0], bb[0], ia[0], ib[0])
            raise RuntimeError('partial failure')
        return batch(states, aa, bb, ia, ib)
    c.RDK.setCollisionActivePairList = broken
    with pytest.raises(RobotControlError):
        with c._contact_policy(): pytest.fail('must not enter movement after partial batch failure')
    assert set(c.pairs) == original


def test_policy_verifies_batch_effect_instead_of_trusting_return_value(scene_cfg):
    c = backend(scene_cfg)
    original = set(c.pairs)
    c.RDK.setCollisionActivePairList = lambda *args: 1  # claims success but changes nothing
    with pytest.raises(RobotControlError):
        with c._contact_policy(): pytest.fail('unverified collision map')
    assert set(c.pairs) == original


def test_older_api_individual_update_fallback(scene_cfg):
    c = backend(scene_cfg)
    del c.RDK.setCollisionActivePairList
    original = set(c.pairs)
    with c._contact_policy(): pass
    assert len(c.changes) == 4
    assert set(c.pairs) == original


def test_failed_batch_restore_recovers_individually(scene_cfg):
    c = backend(scene_cfg)
    original = set(c.pairs)
    batch = c.RDK.setCollisionActivePairList
    def fail_restore(states, *args):
        if states[0] == 1:
            raise RuntimeError('restore batch interrupted')
        return batch(states, *args)
    c.RDK.setCollisionActivePairList = fail_restore
    with c._contact_policy(): pass
    assert set(c.pairs) == original


def test_unrecoverable_restore_fails_closed_after_attempting_every_pair(scene_cfg):
    c = backend(scene_cfg)
    batch = c.RDK.setCollisionActivePairList
    attempted = []
    def fail_restore(states, *args):
        if states[0] == 1:
            raise RuntimeError('restore failed')
        return batch(states, *args)
    c.RDK.setCollisionActivePairList = fail_restore
    c.RDK.setCollisionActivePair = lambda state, *args: attempted.append(args) or 0
    with pytest.raises(RobotControlError, match='restore collision map'):
        with c._contact_policy(): pass
    assert len(attempted) == 2


def test_metadata_is_not_reused_across_policy_operations(scene_cfg):
    c = backend(scene_cfg)
    bucket = next(b for a, b, _, _ in c.pairs if b.name == 'Blue_Bucket_01')
    with c._contact_policy(): pass
    c.changes.clear()
    bucket.name = 'new_unknown_obstacle'
    with c._contact_policy(): pass
    assert not c.changes
    assert bucket.name_calls == 2


def test_robot_self_collision_and_same_name_other_robot_stay_protected(scene_cfg):
    c = backend(scene_cfg)
    duplicate_robot = Item('Blue_Bucket_01', kind=2)
    c.pairs.append((c.robot, duplicate_robot, 2, 1))
    # Even a mistaken registered name must never disable robot self-collision.
    c.scene.objects['white_bucket_lid_04'].station_item = c.robot.name
    original = set(c.pairs)
    with c._contact_policy():
        assert (c.robot, duplicate_robot, 2, 1) in c.pairs
        assert any(a == b == c.robot for a, b, _, _ in c.pairs)
    assert set(c.pairs) == original


@pytest.mark.parametrize('pair_count', [1, 10, 50])
def test_batch_api_call_budget_for_synthetic_collision_maps(scene_cfg, pair_count):
    from robot_control.performance import record_task
    results = []
    for batch in (False, True):
        c = backend(scene_cfg)
        template = c.scene.objects['blue_bucket_01']
        c.scene.objects.clear()
        c.pairs[:] = c.pairs[:1]  # retain the protected robot/table pair
        for index in range(pair_count):
            item = Item(f'contact_{index}')
            c.scene.objects[f'contact_{index}'] = template.model_copy(update={'station_item': item.name})
            c.pairs.append((c.robot, item, 2, 0))
        if not batch:
            del c.RDK.setCollisionActivePairList
        original = set(c.pairs)
        with record_task() as perf:
            with c._contact_policy(): pass
        assert set(c.pairs) == original
        counters = perf.snapshot()['counters']
        assert counters['collision_map_reads'] == 3
        assert counters['item_name_queries'] == pair_count + 2
        results.append(counters)
    assert results[0]['collision_pair_individual_calls'] == 2 * pair_count
    assert results[1]['collision_pair_batch_calls'] == 2
    assert results[1].get('collision_pair_individual_calls', 0) == 0


def test_directional_push_uses_current_object_pose_without_editing_taught_targets(scene_cfg):
    c = backend(scene_cfg)
    c._transient_poses = {}
    c.object_position = lambda oid: [-0.45, 0.0, 0.0]
    c._target_item = lambda tid: Item(tid)
    original = c.scene.model_dump()
    c.prepare_push('blue_bucket_01', 'right', 0.10)
    push = c.scene.objects['blue_bucket_01'].push
    start, end = c._transient_poses[push.start_target], c._transient_poses[push.end_target]
    assert end.y - start.y == pytest.approx(-0.10)
    assert end.x == start.x
    assert c.scene.model_dump() == original
    c.clear_transient_plans()
    assert not c._transient_poses


def test_directional_push_refuses_overhang(scene_cfg):
    c = backend(scene_cfg)
    c._transient_poses = {}
    c.object_position = lambda oid: [-0.45, 0.0, 0.0]
    with pytest.raises(RobotControlError, match='beyond the table'):
        c.prepare_push('blue_bucket_01', 'left', 0.5)
    assert not c._transient_poses


@pytest.mark.parametrize('dof', [4, 5, 7])
@pytest.mark.parametrize('residual', ['none', 'position', 'orientation'])
def test_pose_ik_reconciliation_for_different_dof(dof, residual):
    import math
    from robot_control.models import Pose
    class Matrix:
        def __init__(self, angle=0, dx=0):
            self.angle, self.dx = angle, dx
        def Pos(self): return [10 + self.dx, 0, 300]
        def __getitem__(self, ij):
            return [[math.cos(self.angle), -math.sin(self.angle), 0],
                    [math.sin(self.angle), math.cos(self.angle), 0], [0, 0, 1]][ij[0]][ij[1]]
    c = object.__new__(RoboDKSimController)
    c._require = lambda: None
    c.robot_cfg = SimpleNamespace(dof=dof)
    c.tool = SimpleNamespace(PoseTool=lambda: None)
    actual = Matrix(angle=0.01 if residual == 'orientation' else 0,
                    dx=1 if residual == 'position' else 0)
    c.robot = SimpleNamespace(SolveIK=lambda *args: SimpleNamespace(list=lambda: [0] * dof),
                             SolveFK=lambda *args: actual)
    c._mat_from_pose = lambda pose: Matrix()
    pose = Pose.from_list([0.01, 0, 0.3, 0, 0, 0])
    if residual == 'none':
        assert len(c.plan_pose(pose, [0] * dof).end_joints) == dof
    else:
        with pytest.raises(RobotControlError, match='does not preserve'):
            c.plan_pose(pose, [0] * dof)
