"""Digital twin service: single owner of the receive-only UR stream and the PyBullet writer (spec sections 3, 9, 10).

States: DISCONNECTED (nothing checked yet / inputs unreachable), READY (checks passed, idle), RUNNING (live loops),
PAUSED_STALE (running, but telemetry froze: the scene is frozen and markers are greyed), ERROR (a guard refused or a
loop failed: loops stopped, operator must Start again), STOPPED (operator pressed Stop).

`stop()` only ends the mirroring threads. It never sends anything to the robot (there is no channel that could).
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import MODE
from .config import TwinConfig
from .frames import joints_rad_to_deg, point_B_to_R, pose_delta, tabletop_contact_B, ur5e_fk_flange
from .sim_writer import GuardError, PyBulletWriter
from .scene_mapper import map_frame
from .telemetry import JointSample, StaleTracker, TelemetryError, make_source
from .vision_client import VisionUnavailable, make_observation_source

LABEL = "MIRROR ONLY — NO PHYSICAL MOTION"
DEFAULT_LOCK = Path(__file__).resolve().parents[2] / "runs" / "digital_twin.lock"
DEFAULT_LOG = Path(__file__).resolve().parents[2] / "runs" / "digital_twin" / "diagnostics.jsonl"
LOG_MAX_BYTES = 5 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class WriterLock:
    """Advisory lock: at most one process writes the twin into PyBullet (service, CLI run or CLI snapshot)."""

    def __init__(self, path: Path = DEFAULT_LOCK):
        self.path, self._fd = Path(path), None

    def acquire(self) -> bool:
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        return True

    def release(self):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


class TwinService:
    def __init__(self, cfg: TwinConfig, writer: PyBulletWriter | None = None, joint_source=None, obs_source=None,
                 api_key: str = "", lock: WriterLock | None = None, log_path: Path | None = DEFAULT_LOG,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] | None = None):
        self.cfg = cfg
        self.writer = writer or PyBulletWriter(cfg.sim)
        self.joints = joint_source or make_source(cfg)
        self.obs = obs_source if obs_source is not None else make_observation_source(cfg, api_key)
        self.lock = lock or WriterLock()
        self.log_path = log_path
        self.clock = clock
        self._stop = threading.Event()
        self._sleep = sleep or (lambda s: self._stop.wait(s))
        self._life = threading.Lock()          # start/stop/snapshot are serialized
        self._tel_lock = threading.Lock()      # one reader of the RTDE stream at a time
        self._threads: list[threading.Thread] = []
        self.stale = StaleTracker(cfg.sync.stale_after_ms, clock)
        self.state = "DISCONNECTED"
        self.objects_enabled = False
        self.last_error: dict | None = None
        self.last_sample: JointSample | None = None
        self.last_q_deg: list[float] | None = None
        self.last_fk: dict | None = None
        self._fk_at = -1e9
        self._written_q: list[float] | None = None
        self._written_at = -1e9
        self.sim_info: dict = {}
        self.vision_info: dict = {"source": cfg.vision.source}
        self.hz = 0.0
        self.started_at: str | None = None
        self.counts = {"updated": 0, "stale": 0, "skipped": 0, "not_visible": 0, "unknown": 0}
        self.objects: dict[str, dict] = {oid: {"state": "never_seen", "reason": None, "position_base_m": None,
                                              "updated_at": None, "_mono": None} for oid in cfg.objects}
        self.recent: deque = deque(maxlen=50)

    # ---- helpers ------------------------------------------------------------------------------------------
    def _err(self, status: str, reason: str):
        self.last_error = {"status": status, "reason": reason, "at": utc_now()}

    def _log(self, kind: str, data: dict):
        rec = {"at": utc_now(), "kind": kind, **data}
        self.recent.append(rec)
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > LOG_MAX_BYTES:
                self.log_path.replace(self.log_path.with_suffix(".1.jsonl"))
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass

    def _read_joints(self) -> JointSample | None:
        with self._tel_lock:
            try:
                s = self.joints.read()
            except TelemetryError as e:
                self._err(e.status, e.reason)
                s = None
        fresh = self.stale.update(s)
        if s is not None:
            self.last_sample = s
        return s if fresh else None

    def _fresh_q(self) -> tuple[float, ...] | None:
        s = self.stale.last_good
        if s is None or (self.stale.age_ms() or 0) > self.cfg.sync.stale_after_ms:
            return None
        return s.q_rad

    def objects_blockers(self) -> list[str]:
        out = []
        if self.cfg.vision.source == "none" or self.obs is None:
            out.append("vision.source is 'none': no object observations configured")
        ok, why = self.cfg.alignment_ok()
        if not ok:
            out.append(why)
        return out

    # ---- robot --------------------------------------------------------------------------------------------
    def _write_robot(self, s: JointSample) -> None:
        q_deg = joints_rad_to_deg(s.q_rad)
        now = self.clock()
        # Each setJoints re-renders PyBullet (~35-90 ms, and the single-threaded API waits for it), so identical joints
        # are not re-sent; the pose is still refreshed (and the guard re-run) at least once a second.
        if (self._written_q is not None and now - self._written_at < 1.0
                and max(abs(a - b) for a, b in zip(q_deg, self._written_q)) < 0.01):
            return
        with_fk = now - self._fk_at > 1.0                   # diagnostic only: once a second is plenty
        T_R_F_sim = self.writer.write_joints(q_deg, with_fk=with_fk)
        self._written_q, self._written_at = q_deg, now
        self.last_q_deg = [round(v, 3) for v in q_deg]
        self.sim_info = dict(self.writer.last_guard)
        if T_R_F_sim is None:
            return
        self._fk_at = self.clock()
        # like-for-like: PyBullet flange FK vs nominal UR5e DH flange FK, same joints, each in its own robot base.
        # (The UR's active TCP is NOT a flange and is never compared to the flange.)
        dmm, ddeg = pose_delta(ur5e_fk_flange(s.q_rad), T_R_F_sim)
        self.last_fk = {"sim_flange_vs_ur_nominal_dh_flange_mm": round(dmm, 2), "deg": round(ddeg, 3),
                        "note": "model/base-convention consistency only; not a measurement error"}

    def _robot_tick(self) -> bool:
        s = self._read_joints()
        if s is None:
            if self.state == "RUNNING":
                self.state = "PAUSED_STALE"
                self._log("stale", {"reason": (self.last_error or {}).get("reason")})
                self._grey_all("stale")
            return False
        self._write_robot(s)
        if self.state == "PAUSED_STALE":
            self.state = "RUNNING"
        return True

    # ---- objects ------------------------------------------------------------------------------------------
    def _grey_all(self, why: str):
        fresh = [oid for oid, o in self.objects.items() if o["state"] == "fresh"]
        for oid in fresh:
            self.objects[oid].update(state="stale", reason=why)
        if fresh:
            try:
                with self.writer.batch():
                    for oid in fresh:
                        self._safe_mark(oid, "stale")
            except GuardError as e:
                self._err(e.status, e.reason)

    def _safe_mark(self, oid: str, state: str):
        try:
            self.writer.mark(self.cfg.objects[oid].marker_name, state)
            self.objects[oid]["_shown"] = state
        except GuardError as e:
            self._err(e.status, e.reason)

    def _age_markers(self):
        """Markers not re-confirmed within stale_after_ms turn grey: never shown as current when they are not.
        Also recolours any marker whose colour lags its state (e.g. aged by a status() read)."""
        self._age_counts_only()
        todo = [oid for oid, o in self.objects.items()
                if o["state"] in ("stale", "not_visible") and o.get("_shown") not in (None, o["state"])]
        if todo:
            with self.writer.batch():
                for oid in todo:
                    self._safe_mark(oid, self.objects[oid]["state"])

    def _objects_round(self) -> dict:
        """One vision observation -> marker updates. Raises GuardError (unsafe PyBullet) to the caller."""
        self._age_markers()
        s = self.stale.last_good
        q = self._fresh_q()
        if q is None:
            self.vision_info.update(last_reason="robot_offline")
            return {"ok": False, "reason": "robot_offline"}
        if s is not None and s.moving(self.cfg.physical_robot.moving_joint_speed_rad_s):
            self.vision_info.update(last_reason="moving")
            return {"ok": False, "reason": "moving"}
        try:
            obs = self.obs.snapshot()
        except VisionUnavailable as e:
            self.vision_info.update(last_reason=e.status, last_detail=e.reason)
            return {"ok": False, "reason": e.status, "detail": e.reason}
        res = map_frame(obs, self.cfg, self._fresh_q())
        self.vision_info.update(calibration_id=obs.calibration_id, last_frame_id=obs.frame_id,
                                last_frame_ts=obs.ts, simulation=obs.simulation, flange_check=res.flange_check,
                                last_reason=res.frame_reason, last_detail=None)
        if not res.frame_ok:
            self._log("frame_rejected", {"reason": res.frame_reason, "frame": obs.frame_id, "check": res.flange_check})
            return {"ok": False, "reason": res.frame_reason, "flange_check": res.flange_check}

        T_R_B = self.cfg.sim.base_alignment.T
        updated, skipped = [], []
        with self.writer.batch():                      # one guard, then all marker writes of this observation
            self._apply_decisions(res, T_R_B, updated, skipped)
        self.counts = {"updated": len(updated), "skipped": len(skipped) + len(res.unknown_labels),
                       "stale": sum(1 for o in self.objects.values() if o["state"] == "stale"),
                       "not_visible": sum(1 for o in self.objects.values() if o["state"] == "not_visible"),
                       "unknown": len(res.unknown_labels)}
        out = {"ok": True, "frame": obs.frame_id, "calibration_id": obs.calibration_id, "updated": updated,
               "skipped": {oid: res.decisions[oid].reason for oid in skipped}, "unknown_labels": res.unknown_labels}
        self._log("objects", out)
        return out

    def _apply_decisions(self, res, T_R_B, updated: list, skipped: list) -> None:
        for oid, d in res.decisions.items():
            entry, spec = self.objects[oid], self.cfg.objects[oid]
            if d.state == "valid":
                p_R = point_B_to_R(T_R_B, d.position_base_m)
                self.writer.update_marker(spec.marker_name, p_R)
                cad = None
                if (spec.pose_policy == "tabletop_contact_preserve_orientation"
                        and self.cfg.sim.allow_cad_model_reposition and spec.tabletop_policy_validated):
                    p_contact_R = point_B_to_R(T_R_B, tabletop_contact_B(d.position_base_m, spec.table_z_base_m))
                    cad = self.writer.place_cad_origin(spec.pybullet_item_name, p_contact_R)
                entry.update(state="fresh", reason=None, position_base_m=[round(v, 4) for v in d.position_base_m],
                             confidence=d.confidence, updated_at=utc_now(), _mono=self.clock(),
                             cad_moved=bool(cad), point_semantics="visible_surface_centroid_3d", _shown="fresh")
                updated.append(oid)
            elif d.state == "not_visible":
                if entry["state"] in ("fresh", "stale"):
                    entry.update(state="not_visible", reason="not_visible")
                    self._safe_mark(oid, "not_visible")
            else:                                  # invalid / ambiguous: keep the last marker, greyed, with a reason
                skipped.append(oid)
                if entry["state"] in ("fresh", "stale", "not_visible"):
                    entry.update(state="stale", reason=d.reason)
                    self._safe_mark(oid, "stale")
                else:
                    entry.update(reason=d.reason)

    # ---- public API ---------------------------------------------------------------------------------------
    def check(self) -> dict:
        """Every prerequisite, read-only (no PyBullet write, nothing to the robot)."""
        out: dict[str, Any] = {"ok": True, "mode": MODE, "target": self.cfg.target, "label": LABEL,
                               "physical_motion_enabled": False}
        sample = self._read_joints() if self.state not in ("RUNNING", "PAUSED_STALE") else self.stale.last_good
        r: dict[str, Any] = {"source": self.joints.kind, "host": None if self.joints.kind == "fake"
                             else self.cfg.physical_robot.host}
        if sample is None:
            r.update(connected=False, reason=(self.last_error or {}).get("reason"))
        else:
            r.update(connected=True, q_deg=[round(v, 2) for v in joints_rad_to_deg(sample.q_rad)],
                     moving=sample.moving(self.cfg.physical_robot.moving_joint_speed_rad_s),
                     active_tcp_pose_m_rad=None if sample.tcp_pose is None else [round(v, 4) for v in sample.tcp_pose],
                     active_tcp_note="active TCP, not the flange: informational only")
        out["robot"] = r
        try:
            g = self.writer.guard()
            out["sim"] = {**g, "ok": True}
            if sample is not None:
                T = self.writer.flange_fk(joints_rad_to_deg(sample.q_rad))
                dmm, ddeg = pose_delta(ur5e_fk_flange(sample.q_rad), T)
                out["sim"]["fk_consistency"] = {"sim_flange_vs_ur_nominal_dh_flange_mm": round(dmm, 2),
                                                   "deg": round(ddeg, 3)}
        except GuardError as e:
            out["sim"] = {"ok": False, "status": e.status, "reason": e.reason, **self.writer.last_guard}
        v: dict[str, Any] = {"source": self.cfg.vision.source}
        if self.cfg.vision.source == "vision_server" and hasattr(self.obs, "get_status"):
            try:
                st = self.obs.get_status()
                ac = st.get("active_calibration") or {}
                rec = ac.get("record") or {}
                v.update(connected=True, calibration_valid=bool(ac.get("valid")),
                         calibration_id=rec.get("calibration_id"), calibration_reason=ac.get("reason"),
                         vision_mode=st.get("mode_label"), camera=(st.get("camera") or {}).get("connected"))
            except VisionUnavailable as e:
                v.update(connected=False, reason=e.reason)
        elif self.cfg.vision.source == "replay":
            v.update(connected=True, calibration_valid=None, note="replay fixture (simulation only, no calibration)")
        out["vision"] = v
        a = self.cfg.sim.base_alignment
        out["alignment"] = {"validated": a.validated, "validated_for": a.validated_for, "validated_by": a.validated_by,
                            "T_R_B": a.T_R_B}
        robot_ok = r.get("connected") and out["sim"]["ok"]
        obj_block = self.objects_blockers()
        if self.cfg.vision.source == "vision_server" and not v.get("calibration_valid"):
            obj_block.append("no valid active eye-in-hand calibration on the vision server: "
                             f"{v.get('calibration_reason') or v.get('reason') or 'unknown'}")
        out["can_start_robot_only"] = bool(robot_ok)
        out["can_start_with_objects"] = bool(robot_ok) and not obj_block
        out["object_blockers"] = obj_block
        out["ok"] = bool(robot_ok)
        if self.state in ("DISCONNECTED", "READY", "STOPPED", "ERROR") and not self._threads_alive():
            self.state = "READY" if robot_ok else "DISCONNECTED"
        self._log("check", {k: out[k] for k in ("ok", "can_start_robot_only", "can_start_with_objects",
                                               "object_blockers")})
        return out

    def _require_lock(self):
        if not self.lock.acquire():
            raise GuardError("writer_busy", "another digital-twin writer (CLI run/snapshot or service) owns the "
                             "PyBullet station; stop it first")

    def snapshot(self, objects: bool = True) -> dict:
        with self._life:
            if self._threads_alive():
                return {"ok": False, "status": "already_running", "reason": "live sync is running; Stop it first"}
            if objects and self.objects_blockers():
                return {"ok": False, "status": "objects_blocked", "reason": "; ".join(self.objects_blockers()),
                        "hint": "send {\"objects\": false} for a robot-only snapshot"}
            try:
                self._require_lock()
                s = self._read_joints()
                if s is None:
                    return {"ok": False, "status": "robot_offline", "reason": (self.last_error or {}).get("reason")}
                self._fk_at = self._written_at = -1e9      # a snapshot always writes and reports FK
                self._write_robot(s)
                res: dict[str, Any] = {"ok": True, "q_deg": self.last_q_deg, "fk_consistency": self.last_fk,
                                       "objects": None if not objects else self._objects_round(),
                                       "objects_mode": "with_objects" if objects else
                                       "robot_only (objects not updated: explicit partial mode)"}
                self.state = "READY"
                self._log("snapshot", {"q_deg": self.last_q_deg, "objects": res["objects"]})
                return res
            except GuardError as e:
                self._err(e.status, e.reason)
                self.state = "ERROR"
                return {"ok": False, "status": e.status, "reason": e.reason}
            finally:
                if not self._threads_alive():
                    self.lock.release()

    def _threads_alive(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def start(self, objects: bool = True) -> dict:
        with self._life:
            if self._threads_alive():                  # idempotent: never a second loop
                return {"ok": True, "status": "already_running", "objects": self.objects_enabled,
                        "state": self.state}
            if objects and self.objects_blockers():
                return {"ok": False, "status": "objects_blocked", "reason": "; ".join(self.objects_blockers())}
            try:
                self._require_lock()
                self.writer.guard()
            except GuardError as e:
                self.lock.release()
                self._err(e.status, e.reason)
                self.state = "ERROR"
                return {"ok": False, "status": e.status, "reason": e.reason}
            self._stop.clear()
            self.objects_enabled = objects
            self.last_error = None
            self.state = "RUNNING"
            self.started_at = utc_now()
            self._threads = [threading.Thread(target=self._joint_loop, name="twin-joints", daemon=True)]
            if objects:
                self._threads.append(threading.Thread(target=self._object_loop, name="twin-objects", daemon=True))
            for t in self._threads:
                t.start()
            self._log("start", {"objects": objects})
            return {"ok": True, "status": "started", "objects": objects, "state": self.state}

    def stop(self) -> dict:
        """Stop MIRRORING only. Nothing is sent to the robot (the twin has no command channel)."""
        with self._life:
            was = self._threads_alive()
            self._stop.set()
            for t in self._threads:
                t.join(timeout=5.0)
            self._threads = []
            self.lock.release()
            if self.state != "ERROR":
                self.state = "STOPPED"
            self._log("stop", {"was_running": was})
            return {"ok": True, "status": "stopped" if was else "not_running", "state": self.state,
                    "robot_command_sent": False}

    def _fail(self, status: str, reason: str):
        self._err(status, reason)
        self.state = "ERROR"
        self._stop.set()
        self._log("error", {"status": status, "reason": reason})

    def _joint_loop(self):
        period = 1.0 / self.cfg.sync.joints_hz
        last = None
        while not self._stop.is_set():
            t0 = self.clock()
            try:
                if self._robot_tick():
                    if last is not None:
                        dt = t0 - last
                        self.hz = round(0.8 * self.hz + 0.2 * (1.0 / dt), 2) if dt > 0 else self.hz
                    last = t0
            except GuardError as e:                    # unsafe PyBullet state: fail closed, operator restarts
                self._fail(e.status, e.reason)
                break
            except Exception as e:  # noqa: BLE001 - never die silently
                self._fail("loop_error", f"{type(e).__name__}: {e}")
                break
            self._sleep(max(0.0, period - (self.clock() - t0)))

    def _object_loop(self):
        period = 1.0 / self.cfg.sync.objects_hz
        while not self._stop.is_set():
            t0 = self.clock()
            try:
                if self.state == "RUNNING":
                    self._objects_round()
                else:
                    self._age_markers()
            except GuardError as e:
                self._fail(e.status, e.reason)
                break
            except Exception as e:  # noqa: BLE001
                self._fail("loop_error", f"{type(e).__name__}: {e}")
                break
            self._sleep(max(0.0, period - (self.clock() - t0)))

    def status(self) -> dict:
        s = self.stale.last_good
        age = self.stale.age_ms()
        return {
            "ok": True, "mode": MODE, "label": LABEL, "target": self.cfg.target, "state": self.state,
            "physical_motion_enabled": False, "objects_enabled": self.objects_enabled, "started_at": self.started_at,
            "robot": {"source": self.joints.kind, "connected": s is not None and age is not None
                      and age <= self.cfg.sync.stale_after_ms,
                      "last_joint_sample_age_ms": None if age is None else round(age, 1),
                      "hz": self.hz, "target_hz": self.cfg.sync.joints_hz, "q_deg": self.last_q_deg,
                      "moving": None if s is None else s.moving(self.cfg.physical_robot.moving_joint_speed_rad_s),
                      "fk_consistency": self.last_fk},
            "camera": {**{k: v for k, v in self.vision_info.items() if k != "last_frame_ts"},
                       "last_frame_age_ms": None if not self.vision_info.get("last_frame_ts") else
                       round((time.time() - self.vision_info["last_frame_ts"]) * 1000, 1)},
            "sim": {"connected": bool(self.sim_info), **self.sim_info,
                       "robot_item": self.cfg.sim.robot_item_name},
            "alignment": {"validated": self.cfg.sim.base_alignment.validated,
                          "validated_for": self.cfg.sim.base_alignment.validated_for},
            # fresh/stale/not_visible are live (never overstated); skipped/unknown are from the last observation
            "objects": {**self.counts, **self._live_counts(), "per_object": {oid: {k: v for k, v in o.items() if not k.startswith("_")}
                                                      for oid, o in self.objects.items()}},
            "last_error": self.last_error,
        }

    def _live_counts(self) -> dict:
        self._age_counts_only()
        st = [o["state"] for o in self.objects.values()]
        return {"updated": st.count("fresh"), "stale": st.count("stale"), "not_visible": st.count("not_visible")}

    def _age_counts_only(self):
        """Status view of ageing (no PyBullet call): a marker older than stale_after_ms is reported stale."""
        lim = self.cfg.sync.stale_after_ms / 1000.0
        now = self.clock()
        for o in self.objects.values():
            if o["state"] == "fresh" and o["_mono"] is not None and now - o["_mono"] > lim:
                o.update(state="stale", reason="not_reconfirmed")

    def close(self):
        self.stop()
        try:
            self.joints.close()
        finally:
            self.writer.close()
