"""Fake PyBullet station + fake receive-only UR for the digital twin tests (no PyBullet, no robot, no network)."""
from __future__ import annotations

import math
import time
from types import SimpleNamespace

import numpy as np

from robot_control.digital_twin.frames import T_m_to_mm, rotz, transl, ur5e_fk_flange
from robot_control.digital_twin.telemetry import JointSample

rl = SimpleNamespace(RUNMODE_SIMULATE=1, RUNMODE_RUN_ROBOT=6, ITEM_TYPE_STATION=1, ITEM_TYPE_ROBOT=2,
                     ITEM_TYPE_FRAME=3, ITEM_TYPE_OBJECT=5, ROBOTCOM_NOT_CONNECTED=-1, ROBOTCOM_DISCONNECTED=-2,
                     ROBOTCOM_READY=0, ROBOTCOM_WORKING=1, COLLISION_OFF=0)


class Mat:
    """Minimal robomath.Mat stand-in: row-major, m[i, j], .tr()."""

    def __init__(self, rows):
        self.a = np.array(rows, dtype=float)

    def __getitem__(self, ij):
        return self.a[ij]

    def tr(self):
        return Mat(self.a.T)


rm = SimpleNamespace(Mat=Mat)


class Item:
    def __init__(self, rdk, name, typ, parent=None, pose_m=None):
        self.rdk, self.name, self.typ, self.parent = rdk, name, typ, parent
        self.T = np.eye(4) if pose_m is None else np.array(pose_m, float)   # local pose in parent, m
        self.valid, self.color, self.visible = True, None, True
        self.joints = [0.0] * 6
        self.writes: list = []

    def Valid(self):
        return self.valid

    def Name(self):
        return self.name

    def Type(self):
        return self.typ

    def Parent(self):
        return self.parent or self.rdk.station

    def abs_m(self):
        if self.typ == rl.ITEM_TYPE_STATION:
            return np.eye(4)
        return self.Parent().abs_m() @ self.T

    def PoseAbs(self):
        return Mat(T_m_to_mm(self.abs_m()))

    def Pose(self):
        return Mat(T_m_to_mm(self.T))

    def setPose(self, M):
        self.rdk.log.append(("setPose", self.name))
        T = np.array(M.a, float)
        T[:3, 3] /= 1000.0
        self.T = T

    def setParent(self, p):
        self.parent = p

    def setName(self, n):
        self.name = n

    def setColor(self, c):
        self.rdk.log.append(("setColor", self.name))
        self.color = list(c)

    def setVisible(self, v, *_):
        self.visible = bool(v)

    # robot
    def setJoints(self, q):
        self.rdk.log.append(("setJoints", self.name))
        self.joints = list(q)

    def Joints(self):
        return list(self.joints)

    def SolveFK(self, q_deg):
        return Mat(T_m_to_mm(ur5e_fk_flange([math.radians(v) for v in q_deg])))

    def ConnectedState(self):
        return (self.rdk.driver_state, "msg")

    def Busy(self):
        return self.rdk.busy


class FakeRDK:
    """Demo-like station: robot base at `T_S_R`, Objects_Frame under the robot base frame at (-620, 0, 0) mm, rz 90."""

    def __init__(self, T_S_R=None, with_objects_frame=True):
        self.log: list = []
        self.run_mode, self.driver_state, self.busy = rl.RUNMODE_SIMULATE, rl.ROBOTCOM_NOT_CONNECTED, False
        self.station = Item(self, "Demo", rl.ITEM_TYPE_STATION)
        self.base_frame = Item(self, "UR5e Base", rl.ITEM_TYPE_FRAME, self.station,
                               np.eye(4) if T_S_R is None else T_S_R)
        self.robot = Item(self, "UR5e", rl.ITEM_TYPE_ROBOT, self.base_frame)
        self.items = [self.base_frame, self.robot]
        if with_objects_frame:
            self.objects_frame = Item(self, "Objects_Frame", rl.ITEM_TYPE_FRAME, self.base_frame,
                                      transl(-0.62, 0, 0) @ rotz(90))
            self.bucket = Item(self, "Blue_Bucket_01", rl.ITEM_TYPE_OBJECT, self.objects_frame,
                               transl(-0.24, 0, 0))
            self.items += [self.objects_frame, self.bucket]

    def ActiveStation(self):
        return self.station

    def RunMode(self):
        return self.run_mode

    def Item(self, name, typ=None):
        for it in self.items:
            if it.name == name and (typ is None or it.typ == typ):
                return it
        dead = Item(self, "", -1)
        dead.valid = False
        return dead

    def ItemList(self, typ=None, *_):
        return [it for it in self.items if typ is None or it.typ == typ]

    def AddShape(self, M):
        it = Item(self, "shape", rl.ITEM_TYPE_OBJECT, self.station)
        it.n_vertices = M.a.shape[1]
        self.items.append(it)
        self.log.append(("AddShape", None))
        return it

    def setCollisionActivePair(self, *a):
        pass

    def writes(self, kind=None):
        return [x for x in self.log if kind is None or x[0] == kind]


class FakeJoints:
    """Receive-only fake: only read()/close() exist; there is no method that could command anything."""

    kind = "rtde_receive"

    def __init__(self, q=(0.1, -1.5, 1.4, -1.2, -1.57, 0.3)):
        self.q, self.qd, self.ts, self.fail, self.freeze = list(q), [0.0] * 6, 0.0, False, False
        self.reads = 0

    def read(self):
        from robot_control.digital_twin.telemetry import TelemetryError
        self.reads += 1
        if self.fail:
            raise TelemetryError("robot_offline", "unplugged")
        if not self.freeze:
            self.ts += 0.002
        return JointSample(tuple(self.q), tuple(self.qd), self.ts, time.monotonic(), time.time(), None)

    def close(self):
        pass


class FakeObs:
    kind = "vision_server"

    def __init__(self, frame=None, error=None):
        self.frame, self.error, self.calls = frame, error, 0

    def get_status(self):
        return {"mode_label": "REAL ROBOT", "camera": {"connected": True},
                "active_calibration": {"valid": True, "record": {"calibration_id": "hef-abc"}}}

    def snapshot(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.frame() if callable(self.frame) else self.frame


class NoLock:
    def acquire(self):
        return True

    def release(self):
        pass
