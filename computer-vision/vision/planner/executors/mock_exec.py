"""Executor simulato in memoria: per i test e per lavorare senza PyBullet."""
from __future__ import annotations

import math
import time
from typing import Optional

from ..primitives import Catalog, default_catalog
from ..schemas import ConcretePrimitive, PositionMM, SceneObject, StepFeedback, norm_name


class MockExecutor:
    name = "mock"
    INIT_KEYS = {"objects", "auto_place", "attach_tol_mm", "step_delay_s", "catalog", "home_tcp_mm"}

    def __init__(self, objects: Optional[list[SceneObject]] = None, auto_place: bool = True,
                 attach_tol_mm: float = 60.0, step_delay_s: float = 0.0,
                 catalog: Optional[Catalog] = None, home_tcp_mm: tuple[float, float, float] = (300.0, 0.0, 400.0)):
        self.cat = catalog or default_catalog()
        self.auto_place = auto_place
        self.attach_tol_mm = attach_tol_mm
        self.step_delay_s = step_delay_s
        self._home = list(home_tcp_mm)
        self.tcp = list(home_tcp_mm)
        self.rot_deg = {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}
        self.joints = list(self.cat.robot.home_joints_deg)
        self.gripper = "open"
        self.attached: Optional[str] = None
        self._offset = [0.0, 0.0, 0.0]
        self.objects: dict[str, list[float]] = {}
        self.display: dict[str, str] = {}
        self.aliases: dict[str, str] = {}
        self.auto_placed: list[str] = []
        self.log: list[str] = []
        for o in objects or []:
            if o.position_mm is not None:
                self.add_object(o.name, [o.position_mm.x, o.position_mm.y, o.position_mm.z], o.aliases)

    # ---- scena -----------------------------------------------------------
    def add_object(self, name: str, pos_mm: list[float], aliases: Optional[list[str]] = None) -> None:
        key = norm_name(name)
        self.objects[key] = list(pos_mm)
        self.display[key] = name
        for a in aliases or []:
            self.aliases[norm_name(a)] = key

    def _key(self, name: str) -> Optional[str]:
        k = norm_name(name)
        if k in self.objects:
            return k
        if k in self.aliases:
            return self.aliases[k]
        for key in self.objects:
            if k and (k in key or key in k):
                return key
        return None

    def object_pose(self, name: str) -> Optional[PositionMM]:
        key = self._key(name)
        if key is None and self.auto_place and name and not name.startswith("__probe"):
            i = len(self.auto_placed)
            pos = [400.0 + 80.0 * (i // 3), -150.0 + 150.0 * (i % 3), 20.0]
            self.add_object(name, pos)
            self.auto_placed.append(name)
            key = norm_name(name)
        if key is None:
            return None
        x, y, z = self.objects[key]
        return PositionMM(x=x, y=y, z=z)

    def scene(self) -> list[SceneObject]:
        return [SceneObject(name=self.display[k], position_mm=PositionMM(x=p[0], y=p[1], z=p[2]), source="mock")
                for k, p in self.objects.items()]

    # ---- ciclo di vita ---------------------------------------------------
    def connect(self) -> None:
        self.log.append("connect")

    def close(self) -> None:
        pass

    def stop(self) -> None:
        self.log.append("STOP")

    def tcp_pose(self) -> Optional[list[float]]:
        return list(self.tcp)

    def home_tcp(self) -> Optional[list[float]]:
        return list(self._home)

    # ---- esecuzione ------------------------------------------------------
    def _fb(self, ok: bool, msg: str = "", t0: float = 0.0) -> StepFeedback:
        return StepFeedback(ok=ok, message=msg, tcp_mm=[round(c, 2) for c in self.tcp],
                            joints_deg=list(self.joints), gripper=self.gripper,
                            attached=self.display.get(self.attached) if self.attached else None,
                            duration_s=round(time.time() - t0, 4))

    def _move_to(self, target: list[float]) -> Optional[str]:
        if math.sqrt(sum(c * c for c in target)) > self.cat.robot.reach_m * 1000:
            return f"IK non risolvibile: target {target} fuori sbraccio"
        if target[2] < 0:
            return f"collisione con il tavolo: z={target[2]:.1f} mm"
        self.tcp = list(target)
        if self.attached:
            self.objects[self.attached] = [self.tcp[k] + self._offset[k] for k in range(3)]
        return None

    def execute(self, prim: ConcretePrimitive) -> StepFeedback:
        t0 = time.time()
        if self.step_delay_s:
            time.sleep(self.step_delay_s)
        a, pid = prim.args, prim.primitive
        self.log.append(pid)
        spec = self.cat.get(pid)
        if spec is None:
            return self._fb(False, f"primitiva sconosciuta {pid}", t0)
        err: Optional[str] = None
        if spec.direction:
            vec = self.cat.robot.axis_vector(spec.direction)
            d = float(a.get("distance_m", 0.05)) * 1000
            err = self._move_to([self.tcp[k] + vec[k] * d for k in range(3)])
        elif pid == "MOVE_ABOVE":
            tgt = a.get("target_mm")
            if tgt is None:
                pos = self.object_pose(str(a.get("target", "")))
                tgt = [pos.x, pos.y, pos.z] if pos else None
            if tgt is None:
                return self._fb(False, f"oggetto '{a.get('target')}' non trovato", t0)
            err = self._move_to([tgt[0], tgt[1], tgt[2] + float(a.get("height_m", 0.15)) * 1000])
        elif pid == "MOVE_TO_POSE":
            err = self._move_to([float(a["x"]) * 1000, float(a["y"]) * 1000, float(a["z"]) * 1000])
        elif pid in ("ROTATE_ROLL", "ROTATE_PITCH", "ROTATE_YAW"):
            self.rot_deg[pid.split("_")[1].lower()] += float(a.get("angle_deg", 0))
        elif pid == "HOME":
            self.joints = list(self.cat.robot.home_joints_deg)
            err = self._move_to(list(self._home))
        elif pid == "MOVE_JOINTS":
            self.joints = [float(x) for x in a["joints_deg"]]
        elif pid == "MOVE_JOINT":
            i = self.cat.robot.joint_ids.index(a["joint"])
            self.joints[i] = float(a["target_deg"]) if a.get("target_deg") is not None \
                else self.joints[i] + float(a.get("delta_deg", 0))
        elif pid == "OPEN_GRIPPER":
            self.gripper, self.attached = "open", None
        elif pid == "CLOSE_GRIPPER":
            self.gripper = "closed"
            best, best_d = None, self.attach_tol_mm
            for k, p in self.objects.items():
                d = math.dist(p, self.tcp)
                if d <= best_d:
                    best, best_d = k, d
            if best is None:
                return self._fb(False, "nessun oggetto rilevato tra le dita", t0)
            self.attached = best
            self._offset = [self.objects[best][k] - self.tcp[k] for k in range(3)]
        elif pid == "WAIT":
            pass
        elif pid == "STOP":
            self.stop()
        if err:
            return self._fb(False, err, t0)
        return self._fb(True, "ok", t0)
