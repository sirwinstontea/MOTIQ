"""Executor RoboDK (simulazione). Richiede RoboDK aperto in locale e `pip install robodk`.

Unità: le primitive usano metri/gradi, RoboDK usa mm/gradi (rotazioni robomath in radianti).
Il robot reale non viene mai pilotato: si forza RUNMODE_SIMULATE.
"""
from __future__ import annotations

import math
import os
import socket
import time
from typing import Any, Optional

from ..primitives import Catalog, default_catalog
from ..schemas import ConcretePrimitive, PositionMM, SceneObject, StepFeedback, norm_name
from .base import ExecutorError


def _rotvec_to_mat3(rx: float, ry: float, rz: float) -> list[list[float]]:
    """Rodrigues: vettore di rotazione (rad) -> matrice 3x3."""
    th = math.sqrt(rx * rx + ry * ry + rz * rz)
    if th < 1e-12:
        return [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    kx, ky, kz = rx / th, ry / th, rz / th
    c, s, v = math.cos(th), math.sin(th), 1 - math.cos(th)
    return [
        [kx * kx * v + c, kx * ky * v - kz * s, kx * kz * v + ky * s],
        [ky * kx * v + kz * s, ky * ky * v + c, ky * kz * v - kx * s],
        [kz * kx * v - ky * s, kz * ky * v + kx * s, kz * kz * v + c],
    ]


class RoboDKExecutor:
    name = "robodk"
    INIT_KEYS = {"robot_name", "tool_name", "gripper_name", "record_program", "attach_tol_mm",
                 "check_collisions", "catalog"}

    def __init__(self, robot_name: Optional[str] = None, tool_name: Optional[str] = None,
                 gripper_name: Optional[str] = None, record_program: Optional[str] = None,
                 attach_tol_mm: float = 60.0, check_collisions: bool = False,
                 catalog: Optional[Catalog] = None):
        self.cat = catalog or default_catalog()
        self.robot_name = robot_name
        self.tool_name = tool_name
        self.gripper_name = gripper_name
        self.record_program = record_program
        self.attach_tol_mm = attach_tol_mm
        self.check_collisions = check_collisions
        self.rdk: Any = None
        self.robot: Any = None
        self.tool: Any = None
        self.gripper: Any = None
        self.program: Any = None
        self.gripper_state: Optional[str] = None
        self.attached: Optional[str] = None
        self._home_tcp: Optional[list[float]] = None

    # ---- connessione -----------------------------------------------------
    def connect(self) -> None:
        try:
            from robodk import robolink, robomath  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise ExecutorError("pacchetto 'robodk' non installato: pip install robodk") from e
        self._rl, self._rm = robolink, robomath
        # Controllo rapido: senza RoboDK aperto Robolink() prova ad avviarlo e resta in attesa.
        host = os.getenv("ROBODK_HOST", "localhost")
        port = int(os.getenv("ROBODK_PORT", "20500"))
        try:
            socket.create_connection((host, port), timeout=2.0).close()
        except OSError:
            raise ExecutorError(f"RoboDK non raggiungibile su {host}:{port}: apri RoboDK con la stazione "
                                f"UR5e (API attiva di default sulla porta 20500)") from None
        try:
            self.rdk = robolink.Robolink(robodk_ip=host, port=port)
        except Exception as e:
            raise ExecutorError(f"RoboDK non raggiungibile ({e}). Apri RoboDK con la stazione UR5e.") from e

        R = robolink
        self.robot = self.rdk.Item(self.robot_name or "", R.ITEM_TYPE_ROBOT)
        if not self.robot.Valid():
            raise ExecutorError(f"robot '{self.robot_name or '(primo disponibile)'}' non trovato nella stazione")
        if self.tool_name:
            self.tool = self.rdk.Item(self.tool_name, R.ITEM_TYPE_TOOL)
        else:
            self.tool = self.robot.getLink(R.ITEM_TYPE_TOOL)
        if self.tool is None or not self.tool.Valid():
            raise ExecutorError("nessun tool attivo sul robot: aggiungi una pinza in RoboDK")
        if self.gripper_name:
            g = self.rdk.Item(self.gripper_name, R.ITEM_TYPE_ROBOT)
            self.gripper = g if g.Valid() else None

        self.rdk.setRunMode(R.RUNMODE_SIMULATE)
        self.rdk.setCollisionActive(R.COLLISION_ON if self.check_collisions else R.COLLISION_OFF)
        # Riferimento = base del robot: tutte le pose sono nel frame base.
        base = self.robot.Parent()
        self.robot.setPoseFrame(base)
        self.robot.setPoseTool(self.tool)
        self._base_abs = base.PoseAbs()

        if self.record_program:
            self.program = self.rdk.AddProgram(self.record_program, self.robot)
            self.program.setPoseFrame(base)
            self.program.setPoseTool(self.tool)

        # Si parte sempre da home (simulazione): MOVE_ABOVE mantiene l'orientamento corrente,
        # quindi serve la pinza rivolta verso il basso.
        home = list(self.cat.robot.home_joints_deg)
        self.robot.setJoints(home)
        try:
            self._home_tcp = [float(c) for c in self.robot.SolveFK(home).Pos()]
        except Exception:
            self._home_tcp = None

    def close(self) -> None:
        self.rdk = None

    def stop(self) -> None:
        if self.robot is not None:
            self.robot.Stop()

    # ---- stato -----------------------------------------------------------
    def tcp_pose(self) -> Optional[list[float]]:
        if self.robot is None:
            return None
        return [float(c) for c in self.robot.Pose().Pos()]

    def home_tcp(self) -> Optional[list[float]]:
        return self._home_tcp

    def _objects(self) -> list[Any]:
        return self.rdk.ItemList(self._rl.ITEM_TYPE_OBJECT, False) if self.rdk else []

    def _find_object(self, name: str) -> Any:
        k = norm_name(name)
        items = self._objects()
        for it in items:
            if norm_name(it.Name()) == k:
                return it
        for it in items:
            n = norm_name(it.Name())
            if k and (k in n or n in k):
                return it
        return None

    def object_pose(self, name: str) -> Optional[PositionMM]:
        if self.rdk is None:
            return None
        it = self._find_object(name)
        if it is None:
            return None
        rel = self._rm.invH(self._base_abs) * it.PoseAbs()
        x, y, z = (float(c) for c in rel.Pos())
        return PositionMM(x=x, y=y, z=z)

    def scene(self) -> list[SceneObject]:
        out = []
        for it in self._objects():
            p = self.object_pose(it.Name())
            out.append(SceneObject(name=it.Name(), position_mm=p, source="robodk"))
        return out

    # ---- esecuzione ------------------------------------------------------
    def _fb(self, ok: bool, msg: str, t0: float) -> StepFeedback:
        try:
            tcp = [round(float(c), 2) for c in self.robot.Pose().Pos()]
            joints = [round(float(j), 3) for j in self.robot.Joints().list()]
        except Exception:
            tcp, joints = None, None
        return StepFeedback(ok=ok, message=msg, tcp_mm=tcp, joints_deg=joints,
                            gripper=self.gripper_state, attached=self.attached,
                            duration_s=round(time.time() - t0, 3))

    def _ik(self, pose: Any) -> Any:
        """IK della posa del TCP (tool e riferimento attivi). Senza questi argomenti SolveIK
        interpreta la posa come flangia rispetto alla base."""
        return self.robot.SolveIK(pose, None, self.robot.PoseTool(), self.robot.PoseFrame())

    def _reachable(self, pose: Any) -> bool:
        return len(self._ik(pose).list()) >= 6

    def _movel(self, pose: Any, speed_mps: float) -> Optional[str]:
        if not self._reachable(pose):
            return f"IK non risolvibile per la posa {[round(c, 1) for c in pose.Pos()]} mm"
        if self.check_collisions:
            code = self.robot.MoveL_Test(self.robot.Joints(), pose)
            if code != 0:
                return {-1: "movimento lineare non eseguibile (singolarità/limiti giunti)",
                        -2: "posa non raggiungibile"}.get(code, f"collisione rilevata ({code})")
        self.robot.setSpeed(speed_mps * 1000.0)
        try:
            self.robot.MoveL(pose)
        except self._rl.TargetReachError as e:
            return f"target non raggiungibile: {e}"
        if self.program is not None:
            self.program.setSpeed(speed_mps * 1000.0)
            self.program.MoveL(pose)
        return None

    def _movej_pose(self, pose: Any, speed_dps: float) -> Optional[str]:
        joints = self._ik(pose)
        if len(joints.list()) < 6:
            return f"IK non risolvibile per la posa {[round(c, 1) for c in pose.Pos()]} mm"
        if self.check_collisions and self.robot.MoveJ_Test(self.robot.Joints(), joints) != 0:
            return "collisione rilevata nel movimento giunti"
        return self._movej(joints.list()[:6], speed_dps)

    def _movej(self, joints: list[float], speed_dps: float) -> Optional[str]:
        self.robot.setSpeed(-1, speed_dps)
        try:
            self.robot.MoveJ(joints)
        except self._rl.TargetReachError as e:
            return f"giunti non raggiungibili: {e}"
        if self.program is not None:
            self.program.MoveJ(joints)
        return None

    def _comment(self, text: str) -> None:
        if self.program is not None:
            self.program.RunInstruction(text, self._rl.INSTRUCTION_COMMENT)

    def execute(self, prim: ConcretePrimitive) -> StepFeedback:
        if self.robot is None:
            raise ExecutorError("executor non connesso")
        rm, t0 = self._rm, time.time()
        a, pid = prim.args, prim.primitive
        spec = self.cat.get(pid)
        if spec is None:
            return self._fb(False, f"primitiva sconosciuta {pid}", t0)
        lim = self.cat.robot.limits
        err: Optional[str] = None
        try:
            if spec.direction:  # MOVE_UP/DOWN/LEFT/RIGHT/FORWARD/BACKWARD nel frame base
                vec = self.cat.robot.axis_vector(spec.direction)
                d = float(a.get("distance_m", 0.05)) * 1000.0
                pose = rm.transl(vec[0] * d, vec[1] * d, vec[2] * d) * self.robot.Pose()
                err = self._movel(pose, float(a.get("speed_mps", 0.1)))
            elif pid in ("ROTATE_ROLL", "ROTATE_PITCH", "ROTATE_YAW"):  # frame tool
                ang = math.radians(float(a.get("angle_deg", 15)))
                rot = {"ROTATE_ROLL": rm.rotx, "ROTATE_PITCH": rm.roty, "ROTATE_YAW": rm.rotz}[pid](ang)
                err = self._movel(self.robot.Pose() * rot, 0.1)
            elif pid == "MOVE_ABOVE":
                tgt = a.get("target_mm")
                if tgt is None:
                    p = self.object_pose(str(a.get("target", "")))
                    tgt = [p.x, p.y, p.z] if p else None
                if tgt is None:
                    return self._fb(False, f"oggetto '{a.get('target')}' non trovato nella stazione", t0)
                pose = self.robot.Pose()
                pose.setPos([tgt[0], tgt[1], tgt[2] + float(a.get("height_m", 0.15)) * 1000.0])
                # Trasferimento a quota di sicurezza: movimento giunti verso la posa (un MoveL
                # lungo può attraversare la zona della base / singolarità).
                err = self._movej_pose(pose, lim.get("max_joint_speed_dps", 60))
            elif pid == "MOVE_TO_POSE":
                pos = [float(a["x"]) * 1000, float(a["y"]) * 1000, float(a["z"]) * 1000]
                if any(a.get(k) is not None for k in ("rx", "ry", "rz")):
                    m = _rotvec_to_mat3(*(float(a.get(k) or 0.0) for k in ("rx", "ry", "rz")))
                    pose = rm.Mat([[m[0][0], m[0][1], m[0][2], pos[0]],
                                   [m[1][0], m[1][1], m[1][2], pos[1]],
                                   [m[2][0], m[2][1], m[2][2], pos[2]],
                                   [0, 0, 0, 1]])
                else:
                    pose = self.robot.Pose()
                    pose.setPos(pos)
                err = self._movel(pose, float(a.get("speed_mps", 0.1)))
            elif pid == "HOME":
                err = self._movej(list(self.cat.robot.home_joints_deg), lim.get("max_joint_speed_dps", 60))
            elif pid == "MOVE_JOINTS":
                err = self._movej([float(x) for x in a["joints_deg"]], float(a.get("speed_dps", 30)))
            elif pid == "MOVE_JOINT":
                q = self.robot.Joints().list()
                i = self.cat.robot.joint_ids.index(a["joint"])
                q[i] = float(a["target_deg"]) if a.get("target_deg") is not None else q[i] + float(a.get("delta_deg", 10))
                err = self._movej(q, float(a.get("speed_dps", 30)))
            elif pid == "OPEN_GRIPPER":
                if self.gripper is not None:
                    self.gripper.MoveJ([float(a.get("width_m", self.cat.robot.gripper_stroke_m)) * 1000.0])
                self.tool.DetachAll()
                self.gripper_state, self.attached = "open", None
                self._comment("OPEN_GRIPPER")
            elif pid == "CLOSE_GRIPPER":
                if self.gripper is not None:
                    self.gripper.MoveJ([0.0])
                self.gripper_state = "closed"
                self._comment("CLOSE_GRIPPER")
                item = self.tool.AttachClosest("", self.attach_tol_mm)
                if item is None or not item.Valid():
                    return self._fb(False, "nessun oggetto rilevato tra le dita (AttachClosest)", t0)
                self.attached = item.Name()
            elif pid == "WAIT":
                time.sleep(float(a.get("seconds", 0.5)))
                if self.program is not None:
                    self.program.Pause(float(a.get("seconds", 0.5)) * 1000)
            elif pid == "STOP":
                self.stop()
        except ExecutorError:
            raise
        except Exception as e:  # errori di comunicazione / API RoboDK
            return self._fb(False, f"errore RoboDK: {type(e).__name__}: {e}", t0)
        if err:
            return self._fb(False, err, t0)
        return self._fb(True, "ok", t0)
