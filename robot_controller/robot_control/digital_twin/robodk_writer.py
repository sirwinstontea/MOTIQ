"""RoboDK writer: SIMULATION ONLY, guarded before every batch (spec section 7, "RoboDK write-side guards").

Allowed writes: `robot.setJoints()` on the simulated robot item, and `setPose`/`setColor` on `MOTIQ_Detected_*` marker
objects (created once, never re-imported in the loop). Optionally `setPose` on a registered CAD object when its
explicit, validated tabletop policy is enabled. Nothing else: no MoveJ/MoveL, no programs, no run-mode change, no
driver connection. The guard refuses (and writes nothing) when RoboDK is not in simulate mode, the robot item is
connected to a driver, a program is moving it, or the station/robot is not the configured one.
"""
from __future__ import annotations

import importlib
import itertools
import socket
import threading
import time
from contextlib import contextmanager
from typing import Any

import numpy as np

from .config import MARKER_PREFIX
from .frames import T_m_to_mm, T_mm_to_m, marker_T_R, pose_in_parent, rigid_inverse

COLOR_FRESH = [1.0, 0.0, 1.0, 1.0]          # magenta: "measured just now", unlike any CAD colour
COLOR_STALE = [0.6, 0.6, 0.6, 0.6]          # grey: last known position, not current
COLOR_NOT_VISIBLE = [0.35, 0.35, 0.35, 0.3]


class GuardError(RuntimeError):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


def sphere_triangles(radius_mm: float) -> list[list[float]]:
    """Small icosphere-like marker (octahedron subdivided twice), vertices in mm, 3 per triangle."""
    v = [np.array(p, float) for p in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]
    tris = [(v[0], v[2], v[4]), (v[2], v[1], v[4]), (v[1], v[3], v[4]), (v[3], v[0], v[4]),
            (v[2], v[0], v[5]), (v[1], v[2], v[5]), (v[3], v[1], v[5]), (v[0], v[3], v[5])]
    for _ in range(2):
        nxt = []
        for a, b, c in tris:
            ab, bc, ca = [(x + y) / np.linalg.norm(x + y) for x, y in ((a, b), (b, c), (c, a))]
            nxt += [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
        tris = nxt
    return [list(p * radius_mm) for p in itertools.chain.from_iterable(tris)]


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class RoboDKWriter:
    def __init__(self, cfg_robodk, link: Any = None, rl: Any = None, rm: Any = None):
        self.cfg = cfg_robodk
        self._rdk = link
        self._rl = rl
        self._rm = rm
        self._robot = None
        self._markers: dict[str, Any] = {}
        self.lock = threading.RLock()
        self.last_guard: dict = {}
        self._in_batch = 0
        self._station: str | None = None
        self._station_at = -1e9
        self._marker_T: dict[str, np.ndarray] = {}
        self._marker_color: dict[str, list] = {}

    # ---- connection ---------------------------------------------------------------------------------------
    def _modules(self):
        if self._rl is None:
            try:
                self._rl = importlib.import_module("robodk.robolink")
                self._rm = importlib.import_module("robodk.robomath")
            except ImportError as e:
                raise GuardError("robodk_offline", "robodk package missing: pip install robodk") from e
        return self._rl, self._rm

    def connect(self):
        rl, _ = self._modules()
        if self._rdk is not None:
            return self._rdk
        # never let Robolink auto-launch RoboDK: only attach to one that is already running
        if not _port_open(self.cfg.host, self.cfg.port):
            raise GuardError("robodk_offline", f"RoboDK API not reachable on {self.cfg.host}:{self.cfg.port}. "
                             "Start RoboDK with the station open, then retry.")
        try:
            self._rdk = rl.Robolink(robodk_ip=self.cfg.host, port=self.cfg.port)
            try:
                self._rdk.COM.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:  # noqa: BLE001 - only slower
                pass
        except Exception as e:  # noqa: BLE001
            self._rdk = None
            raise GuardError("robodk_offline", f"cannot connect to RoboDK: {e}") from e
        return self._rdk

    def close(self):
        with self.lock:
            self._rdk, self._robot, self._markers = None, None, {}
            self._station, self._marker_T, self._marker_color = None, {}, {}

    def _c(self, name: str, default: int) -> int:
        return getattr(self._rl, name, default)

    # ---- guard --------------------------------------------------------------------------------------------
    # Measured on RoboDK 6.x with TCP_NODELAY: RunMode 0.1 ms, Busy 3.5 ms, ConnectedState ~30 ms,
    # ActiveStation().Name ~40 ms, and every scene write (setJoints/setPose/setColor/setVisible) 30-45 ms because it
    # re-renders. So: ONE guard per batch (run mode, driver state, busy, robot item: every batch; the station name at
    # most once per second), and scene writes only when something actually changed.
    STATION_RECHECK_S = 1.0

    def guard(self) -> dict:
        """Check every precondition for writing. Raises GuardError; returns details when safe."""
        with self.lock:
            rdk = self.connect()
            try:
                now = time.monotonic()
                if self._station is None or now - self._station_at > self.STATION_RECHECK_S:
                    self._station, self._station_at = rdk.ActiveStation().Name(), now
                station = self._station
                run_mode = rdk.RunMode()
            except Exception as e:  # noqa: BLE001 - RoboDK closed meanwhile
                self.close()
                raise GuardError("robodk_offline", f"RoboDK connection lost: {e}") from e
            info = {"connected": True, "station": station, "run_mode": run_mode}
            self.last_guard = info
            if self.cfg.station_name and station != self.cfg.station_name:
                raise GuardError("wrong_station", f"active station is {station!r}, config expects "
                                 f"{self.cfg.station_name!r}")
            if run_mode != self._c("RUNMODE_SIMULATE", 1):
                raise GuardError("robodk_not_simulating", f"RoboDK run mode is {run_mode}, not simulate (1): refusing "
                                 "to write. The twin never changes the run mode itself.")
            robot = self._robot
            if robot is None or not robot.Valid():
                robot = rdk.Item(self.cfg.robot_item_name, self._c("ITEM_TYPE_ROBOT", 2))
                if not robot.Valid() or robot.Name() != self.cfg.robot_item_name:
                    raise GuardError("robot_not_found", f"robot {self.cfg.robot_item_name!r} not found in station "
                                     f"{station!r}")
                self._robot = robot
            try:
                state = robot.ConnectedState()
                busy = robot.Busy()
            except Exception as e:  # noqa: BLE001
                self.close()
                raise GuardError("robodk_offline", f"RoboDK connection lost: {e}") from e
            code = state[0] if isinstance(state, (list, tuple)) else state
            safe = {self._c("ROBOTCOM_NOT_CONNECTED", -1), self._c("ROBOTCOM_DISCONNECTED", -2)}
            info["driver_state"] = code
            if code not in safe:
                raise GuardError("robot_driver_connected", f"RoboDK robot {self.cfg.robot_item_name!r} has driver "
                                 f"state {code} (connected/connecting to a real controller): refusing all writes")
            if busy:
                raise GuardError("robodk_robot_busy", "a program or move is running on this RoboDK robot item "
                                 "(e.g. the MOTIQ controller simulation): refusing to fight it. Use a dedicated "
                                 "twin station/robot item or stop that simulation.")
            info["safe"] = True
            return info

    @contextmanager
    def batch(self):
        """Guard once, then several writes with no other thread in between (one 'update batch')."""
        with self.lock:
            self.guard()
            self._in_batch += 1
            try:
                yield self
            finally:
                self._in_batch -= 1

    def _guarded(self):
        if self._in_batch == 0:
            self.guard()

    # ---- robot --------------------------------------------------------------------------------------------
    def write_joints(self, q_deg: list[float], with_fk: bool = False) -> np.ndarray | None:
        """Guard, then setJoints on the SIMULATED robot. With `with_fk`, also RoboDK flange FK ^R T_F (m)."""
        with self.lock:
            self._guarded()
            self._robot.setJoints(list(q_deg))
            return T_mm_to_m(self._robot.SolveFK(list(q_deg))) if with_fk else None

    def flange_fk(self, q_deg: list[float]) -> np.ndarray:
        """RoboDK flange FK ^R T_F (m) for q. Read-only computation (no joint write)."""
        with self.lock:
            self._guarded()
            return T_mm_to_m(self._robot.SolveFK(list(q_deg)))

    def robot_base_abs(self) -> np.ndarray:
        """^S T_R (m). For a robot item RoboDK documents PoseAbs() as the robot BASE w.r.t. the station."""
        return T_mm_to_m(self._robot.PoseAbs())

    # ---- markers ------------------------------------------------------------------------------------------
    def _parent_frame(self):
        rdk = self._rdk
        name = self.cfg.object_parent_frame
        if name:
            fr = [it for it in rdk.ItemList(self._c("ITEM_TYPE_FRAME", 3)) if it.Name() == name]
            if fr:
                return fr[0]
        return rdk.ActiveStation()

    def _abs(self, item) -> np.ndarray:
        """^S T_item (m); the station root itself is the identity."""
        if item.Type() == self._c("ITEM_TYPE_STATION", 1):
            return np.eye(4)
        return T_mm_to_m(item.PoseAbs())

    def _marker(self, name: str, create: bool):
        if not name.startswith(MARKER_PREFIX):
            raise GuardError("bad_marker_name", f"refusing to touch {name!r}: not a {MARKER_PREFIX}* marker")
        it = self._markers.get(name)
        if it is not None and it.Valid():
            return it
        self._marker_T.pop(name, None)
        self._marker_color.pop(name, None)
        rdk = self._rdk
        same = [x for x in rdk.ItemList() if x.Name() == name]
        objs = [x for x in same if x.Type() == self._c("ITEM_TYPE_OBJECT", 5)]
        if len(same) != len(objs):
            raise GuardError("marker_name_collision", f"an item named {name!r} exists and is not a marker object")
        if len(objs) > 1:
            raise GuardError("marker_name_collision", f"{len(objs)} objects named {name!r}: delete the extras")
        if objs:
            it = objs[0]
        elif not create:
            return None
        else:
            rm = self._rm
            it = rdk.AddShape(rm.Mat(sphere_triangles(self.cfg.marker_radius_mm)).tr())
            if not it.Valid():
                raise GuardError("marker_create_failed", f"RoboDK did not create marker {name!r}")
            it.setName(name)
            it.setParent(self._parent_frame())
            for other in rdk.ItemList(self._c("ITEM_TYPE_OBJECT", 5)):   # markers must never trigger collisions
                try:
                    rdk.setCollisionActivePair(self._c("COLLISION_OFF", 0), it, other)
                except Exception:  # noqa: BLE001
                    break
        it.setVisible(True)
        self._markers[name] = it
        return it

    def _color(self, name: str, it, color: list):
        if self._marker_color.get(name) != color:
            it.setColor(color)
            self._marker_color[name] = color

    def update_marker(self, name: str, p_R_m) -> dict:
        """Put marker `name` at visible point ^R p (m), display-only identity orientation, fresh colour."""
        with self.lock:
            self._guarded()
            it = self._marker(name, create=self.cfg.create_visible_point_markers)
            if it is None:
                raise GuardError("marker_missing", f"marker {name!r} not in station and creation is disabled")
            parent = it.Parent()
            T_P_M = pose_in_parent(self._abs(parent), self.robot_base_abs(), marker_T_R(p_R_m))
            last = self._marker_T.get(name)
            moved = last is None or np.linalg.norm(last[:3, 3] - T_P_M[:3, 3]) > 0.0005 or \
                not np.allclose(last[:3, :3], T_P_M[:3, :3], atol=1e-6)
            if moved:                                   # same point (< 0.5 mm): no re-render
                it.setPose(self._rm.Mat(T_m_to_mm(T_P_M)))
                self._marker_T[name] = T_P_M
            self._color(name, it, COLOR_FRESH)
            return {"parent": parent.Name(), "moved": bool(moved),
                    "T_P_marker_mm": np.round(np.asarray(T_m_to_mm(T_P_M)), 3).tolist()}

    def mark(self, name: str, state: str):
        """Recolour an existing marker (stale / not visible). Never moves it, never creates it."""
        with self.lock:
            self._guarded()
            it = self._marker(name, create=False)
            if it is not None:
                self._color(name, it, COLOR_NOT_VISIBLE if state == "not_visible" else COLOR_STALE)

    # ---- optional CAD policy ------------------------------------------------------------------------------
    def place_cad_origin(self, item_name: str, p_R_origin_m) -> dict:
        """Move a registered CAD object's ORIGIN to ^R p, keeping its current orientation (tabletop policy)."""
        with self.lock:
            self._guarded()
            objs = [x for x in self._rdk.ItemList(self._c("ITEM_TYPE_OBJECT", 5)) if x.Name() == item_name]
            if len(objs) != 1:
                raise GuardError("cad_item_missing", f"expected exactly one object {item_name!r}, found {len(objs)}")
            it = objs[0]
            T_S_R = self.robot_base_abs()
            T_S_O = self._abs(it)
            T_S_O[:3, 3] = (T_S_R @ np.array([*p_R_origin_m, 1.0]))[:3]
            T_P_O = rigid_inverse(self._abs(it.Parent())) @ T_S_O
            it.setPose(self._rm.Mat(T_m_to_mm(T_P_O)))
            return {"item": item_name, "approximate": True}
