"""MOTIQ arm bridge: groky-app  <->  robot_controller  <->  computer-vision.

The app (EXPO_PUBLIC_ARM_URL) speaks the "bridge" protocol:
    GET  /health          -> status of the bridge, the controller and the vision server
    GET  /camera.jpg      -> latest annotated frame from the vision server (arm camera)
    POST /tools/stop      -> cancel the active task + controlled stop
    GET  /speed           -> current arm speed (% of the UR5e maximums)
    POST /speed           -> {"percent": 25}: change it (refused while a task runs)
    POST /tools/{name}    -> run one tool, wait for it, return {ok, did, error, ...}

The controller only accepts allowlisted high-level tasks (AI_ACTION_CONTRACT.md) and only on
127.0.0.1, so this bridge runs on the same PC, translates tool calls into task JSON and is the
only thing exposed on the LAN (protected by X-Bridge-Key).

Vision: before every task that needs an object, the bridge reads GET {vision}/state/arm, maps the
tracked objects to the controller's *registered* object IDs (scene `class_labels`) and writes them
to --vision-file, which the controller reads with `--detector vision_file`. If the object isn't
visible the bridge refuses up front, so a missing object never latches a controller FAULT.
Camera coordinates (coords_mm) are not forwarded: picks use the taught named targets.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

log = logging.getLogger("motiq_bridge")

HERE = Path(__file__).resolve().parent
DEFAULT_SCENE = HERE.parent / "robot_controller" / "config" / "scene.example.yaml"
DEFAULT_VISION_FILE = HERE.parent / "robot_controller" / "runs" / "vision_bridge.json"

TERMINAL = {"SUCCEEDED", "FAILED", "REJECTED", "CANCELLED"}

# Italian colour names the vision server reports -> English, so "red cube" matches "cube" + "rosso"
COLORS_IT_EN = {"rosso": "red", "blu": "blue", "verde": "green", "giallo": "yellow", "nero": "black",
                "bianco": "white", "arancione": "orange", "viola": "purple", "grigio": "gray", "rosa": "pink",
                "marrone": "brown", "azzurro": "light blue"}

# ids used in the app's skill template (environment.objects) -> controller scene ids
APP_ALIASES = {"object_cube": "red_cube", "object_box": "delivery_zone", "blue box": "delivery_zone",
               "box": "delivery_zone", "the box": "delivery_zone", "delivery": "delivery_zone"}

# app primitives with no safe equivalent in the controller (it only moves between taught targets)
REFUSED_PRIMITIVES = {
    "move_left", "move_right", "move_forward", "move_backward", "move_up", "move_down",
    "rotate_roll", "rotate_pitch", "rotate_yaw", "move_joint", "move_joints", "move_to_pose", "close_gripper",
}


class Refused(Exception):
    """A request the bridge won't forward. Nothing was sent to the robot."""


class Bridge:
    def __init__(self, controller: str, vision: str, scene_path: Path, vision_file: Path,
                 task_timeout_s: float = 180.0, min_conf: float = 0.6):
        self.controller = controller.rstrip("/")
        self.vision = vision.rstrip("/")
        self.vision_file = vision_file
        self.task_timeout_s = task_timeout_s
        self.min_conf = min_conf      # same as the controller's safety min_detection_confidence
        self.scene = yaml.safe_load(scene_path.read_text(encoding="utf-8"))
        self.http = httpx.Client(timeout=10.0)
        self.active_task: Optional[str] = None

    # ------------------------------------------------------------------ scene helpers
    @property
    def objects(self) -> dict[str, dict]:
        return self.scene.get("objects") or {}

    @property
    def destinations(self) -> dict[str, dict]:
        return self.scene.get("destinations") or {}

    def _labels(self, oid: str) -> set[str]:
        return {oid.lower(), oid.lower().replace("_", " ")} | {
            str(s).lower() for s in self.objects[oid].get("class_labels") or []}

    def resolve_object(self, q: Any) -> str:
        """Registered object id from an id, a spoken name ("red cube") or a vision track number."""
        s = str(q or "").strip().lower()
        if not s:
            raise Refused("which object? say e.g. 'the red cube'")
        s = s.removeprefix("the ").strip()
        s = APP_ALIASES.get(s, s)
        if s in self.objects:
            return s
        if s.lstrip("#").isdigit():                       # vision tracker id, e.g. from vision_select_object
            return self._object_from_track(int(s.lstrip("#")))
        for oid in self.objects:
            if s in self._labels(oid):
                return oid
        for oid in self.objects:                          # loose: "the red cube please"
            if any(lbl in s for lbl in self._labels(oid) if len(lbl) > 3):
                return oid
        raise Refused(f"'{q}' isn't an object the arm knows. Known: {', '.join(self.objects)}")

    def resolve_destination(self, q: Any) -> str:
        s = str(q or "").strip().lower().removeprefix("the ").strip()
        if not s:
            if len(self.destinations) == 1:
                return next(iter(self.destinations))
            raise Refused(f"where to? Known destinations: {', '.join(self.destinations)}")
        s = APP_ALIASES.get(s, s)
        if s in self.destinations:
            return s
        for d in self.destinations:
            if s in (d, d.replace("_", " ")) or d.replace("_", " ") in s:
                return d
        raise Refused(f"'{q}' isn't a destination the arm knows. Known: {', '.join(self.destinations)}")

    def approach_target(self, name: str) -> str:
        if name in self.objects:
            return self.objects[name]["approach_target"]
        return self.destinations[name]["approach_target"]

    # ------------------------------------------------------------------ vision
    def _vision_names(self, v: dict) -> set[str]:
        label = str(v.get("label") or "").lower()
        name = str(v.get("name") or "").lower()
        color_it = str(v.get("color") or "").lower()
        color_en = COLORS_IT_EN.get(color_it, color_it)
        out = {label, name}
        for c in {color_it, color_en} - {""}:
            for base in {label, name} - {""}:
                out |= {f"{c} {base}", f"{base} {c}", f"{c}_{base}"}
        return out - {""}

    def match_vision(self, v: dict) -> Optional[str]:
        names = self._vision_names(v)
        # prefer an object whose labels include the colour-qualified name, then any label match
        hits = [oid for oid in self.objects if names & self._labels(oid)]
        return hits[0] if hits else None

    def vision_frame(self) -> dict:
        r = self.http.get(f"{self.vision}/state/arm", timeout=3.0)
        r.raise_for_status()
        return r.json()

    def _object_from_track(self, track: int) -> str:
        if not self.vision:
            raise Refused("vision server is not configured, so object numbers mean nothing to the arm")
        frame = self.vision_frame()
        v = next((o for o in frame.get("objects") or [] if o.get("id") == track), None)
        if v is None:
            raise Refused(f"object #{track} isn't visible right now")
        oid = self.match_vision(v)
        if oid is None:
            color = COLORS_IT_EN.get(str(v.get('color') or '').lower(), v.get('color') or '')
            raise Refused(f"#{track} looks like a {color} {v.get('label')}, which the arm "
                          f"has no taught pick for. Known: {', '.join(self.objects)}")
        return oid

    def sync_vision(self, need: Optional[str] = None) -> Optional[dict]:
        """Write the current frame (in controller ids) for the controller's vision_file detector.
        Returns the matching vision object for `need`, or raises Refused if it isn't visible."""
        if not self.vision:
            return None
        try:
            frame = self.vision_frame()
        except Exception as e:
            raise Refused(f"can't reach the vision server ({e.__class__.__name__}); not moving without it")
        objs, seen, weak = [], None, None
        for v in frame.get("objects") or []:
            oid = self.match_vision(v)
            if not oid:
                continue
            if float(v.get("conf") or 0) < self.min_conf:      # the controller would reject it and latch a FAULT
                if oid == need and (weak is None or v.get("conf", 0) > weak.get("conf", 0)):
                    weak = v
                continue
            labels = self.objects[oid].get("class_labels") or [oid]
            objs.append({"label": labels[0], "name": oid, "conf": v.get("conf", 0.0), "vision_id": v.get("id"),
                         "color": v.get("color"), "bbox": v.get("bbox")})
            if oid == need and (seen is None or v.get("conf", 0) > seen.get("conf", 0)):
                seen = v
        payload = {"source": "arm", "frame_id": frame.get("frame_id"), "ts": frame.get("ts", time.time()),
                   "objects": objs, "written_by": "motiq_bridge"}
        self.vision_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self.vision_file.parent, delete=False, suffix=".tmp") as f:
            json.dump(payload, f)
        os.replace(f.name, self.vision_file)              # atomic: the controller never reads half a file
        if frame.get("safety_stop"):
            raise Refused(f"vision safety stop: {frame.get('safety_reason') or 'something in the workspace'}")
        if need and seen is None and weak is not None:
            raise Refused(f"the camera thinks it sees the {need.replace('_', ' ')} but is only "
                          f"{float(weak.get('conf') or 0):.0%} sure (needs {self.min_conf:.0%}); "
                          "move it into clear view and try again")
        if need and seen is None:
            raise Refused(f"the camera can't see the {need.replace('_', ' ')} right now")
        return seen

    # ------------------------------------------------------------------ controller
    def _get(self, path: str) -> dict:
        r = self.http.get(f"{self.controller}{path}")
        r.raise_for_status()
        return r.json()

    def run_task(self, actions: list[tuple[str, dict]], did: str) -> dict:
        task_id = f"app_{int(time.time() * 1000)}_{secrets.token_hex(2)}"
        task = {"schema_version": "1.0", "task_id": task_id,
                "actions": [{"action_id": f"a{i + 1}", "action": a, "parameters": p}
                            for i, (a, p) in enumerate(actions)]}
        r = self.http.post(f"{self.controller}/tasks", content=json.dumps(task))
        if r.status_code >= 400:
            err = (r.json().get("error") or {}) if r.headers.get("content-type", "").startswith("application/json") else {}
            code = err.get("code", str(r.status_code))
            msg = err.get("message", r.text[:200])
            if code == "FAULT_LATCHED":
                msg += (" An operator must reset it on the robot PC: "
                        "curl -X POST localhost:8765/operator/reset -d '{\"operator\":\"<name>\"}'")
            return {"ok": False, "error": msg, "code": code, "task_id": task_id}

        self.active_task = task_id
        deadline = time.monotonic() + self.task_timeout_s
        try:
            while True:
                st = self._get(f"/tasks/{task_id}")
                if st["status"] in TERMINAL:
                    break
                if time.monotonic() > deadline:
                    self.http.post(f"{self.controller}/tasks/{task_id}/cancel")
                    return {"ok": False, "error": f"timed out after {self.task_timeout_s:.0f}s, task cancelled",
                            "task_id": task_id}
                time.sleep(0.25)
        finally:
            self.active_task = None

        out: dict[str, Any] = {"ok": st["status"] == "SUCCEEDED", "task_id": task_id, "status": st["status"],
                               "state": st["state"], "speed_percent": st.get("speed_percent")}
        if out["ok"]:
            out["did"] = did
        else:
            e = st.get("error") or {}
            out["error"] = e.get("message") or st["status"].lower()
            out["code"] = e.get("code")
        try:
            robot = self._get("/status").get("robot") or {}
            if robot.get("joints_deg"):
                out["joints_deg"] = robot["joints_deg"]        # app syncs its arm model from this
            out["gripper"] = robot.get("gripper")
            out["holding"] = robot.get("holding")
            if "pick_object" in (a for a, _ in actions) and not out["ok"] and st["state"] == "VERIFYING_GRIP":
                out["object_detected"] = False
        except Exception:
            pass
        return out

    def speed(self, percent: Any = None) -> tuple[int, dict]:
        """Read (percent=None) or set the controller's speed. Returns (http status, body)."""
        if percent is None:
            r = self.http.get(f"{self.controller}/robot/speed")
        else:
            r = self.http.post(f"{self.controller}/robot/speed", json={"percent": percent})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400:
            err = body.get("error") or {}
            return r.status_code, {"ok": False, "error": err.get("message", r.text[:200]), "code": err.get("code")}
        return 200, {"ok": True, **body}

    def stop(self) -> dict:
        res: dict[str, Any] = {"ok": True}
        if self.active_task:
            try:
                self.http.post(f"{self.controller}/tasks/{self.active_task}/cancel", timeout=2.0)
                res["cancelled"] = self.active_task
            except Exception as e:
                res["cancel_error"] = str(e)
        try:
            self.http.post(f"{self.controller}/robot/stop", timeout=2.0).raise_for_status()
        except Exception as e:
            res.update(ok=False, error=f"controller stop failed: {e}")
        return res

    # ------------------------------------------------------------------ tools
    def tool(self, name: str, a: dict) -> dict:
        name = name.lower()
        if name in ("pick", "pick_object"):
            oid = self.resolve_object(a.get("object") or a.get("target"))
            self.sync_vision(need=oid)
            return self.run_task([("identify_object", {"object_id": oid}), ("pick_object", {"object_id": oid})],
                                 f"picked up the {oid.replace('_', ' ')}")
        if name in ("place", "place_object"):
            dest = self.resolve_destination(a.get("destination"))
            return self.run_task([("place_object", {"destination_id": dest})], f"placed it at {dest.replace('_', ' ')}")
        if name in ("shake", "shake_object"):
            return self.run_task([("shake_object", self._shake(a))], "shook it")
        if name == "pick_and_place":
            oid = self.resolve_object(a.get("object") or a.get("target"))
            dest = self.resolve_destination(a.get("destination"))
            self.sync_vision(need=oid)
            steps = [("identify_object", {"object_id": oid}), ("pick_object", {"object_id": oid})]
            if int(a.get("shake_cycles") or 0) > 0:
                steps.append(("shake_object", self._shake({"cycles": a["shake_cycles"]})))
            steps.append(("place_object", {"destination_id": dest}))
            return self.run_task(steps, f"moved the {oid.replace('_', ' ')} to {dest.replace('_', ' ')}")
        if name == "home":
            return self.run_task([("perform_motion", {"motion_id": "go_home"})], "went home")
        if name in ("open_gripper", "release"):
            return self.run_task([("release_object", {})], "opened the gripper")
        if name == "move_above":
            tgt = str(a.get("target") or "")
            try:
                t = self.resolve_object(tgt)
            except Refused:
                t = self.resolve_destination(tgt)
            return self.run_task([("move_to_target", {"target_id": self.approach_target(t)})],
                                 f"moved above the {t.replace('_', ' ')}")
        if name == "status":
            return {"ok": True, **self._get("/status")}
        if name in REFUSED_PRIMITIVES:
            raise Refused(f"the arm controller only moves between taught positions, so '{name}' isn't available "
                          "on the real arm. Use pick, place, shake, pick_and_place, move_above or home.")
        raise Refused(f"unknown tool '{name}'")

    def _shake(self, a: dict) -> dict:
        patterns = self.scene.get("patterns") or {}
        pid = str(a.get("pattern") or next(iter(patterns), "gentle_shake"))
        return {"pattern_id": pid, "cycles": max(1, min(10, int(a.get("cycles") or 2)))}

    def health(self) -> dict:
        out: dict[str, Any] = {"ok": True, "bridge": "motiq", "model": "UR5e (robot_controller)"}
        try:
            h = self._get("/health")
            st = self._get("/robot/state")
            out["controller"] = {"backend": h.get("backend"), "state": st.get("executor_state"),
                                 "mode": st.get("mode"), "gripper": st.get("gripper_state"),
                                 "fault": (st.get("latched_error") or {}).get("message")}
            out["speed_percent"] = st.get("speed_percent")
        except Exception as e:
            out.update(ok=False, controller={"error": f"unreachable: {e.__class__.__name__}"})
        if self.vision:
            try:
                r = self.http.get(f"{self.vision}/health", timeout=2.0)
                out["vision"] = {"ok": r.status_code == 200}
            except Exception as e:
                out["vision"] = {"ok": False, "error": e.__class__.__name__}
        else:
            out["vision"] = "not configured"
        return out


def create_app(bridge: Bridge, key: str) -> FastAPI:
    app = FastAPI(title="MOTIQ arm bridge")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if key and request.method != "OPTIONS" and not secrets.compare_digest(
                request.headers.get("x-bridge-key", ""), key):
            return JSONResponse({"ok": False, "error": "bad or missing X-Bridge-Key"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    def health():
        h = bridge.health()
        return JSONResponse(h, status_code=200 if h["ok"] else 503)

    @app.get("/camera.jpg")
    def camera(raw: bool = False):
        if not bridge.vision:
            return Response(status_code=404)
        try:
            r = bridge.http.get(f"{bridge.vision}/frame/arm.jpg", params={"raw": str(raw).lower()}, timeout=3.0)
            return Response(r.content, media_type="image/jpeg", status_code=r.status_code,
                            headers={"Cache-Control": "no-store"})
        except Exception:
            return Response(status_code=502)

    # sync handlers run in FastAPI's threadpool, so /tools/stop is served while a task is running
    @app.post("/tools/stop")
    def stop():
        return bridge.stop()

    @app.get("/speed")
    def get_speed():
        try:
            code, body = bridge.speed()
        except httpx.HTTPError as e:
            return JSONResponse({"ok": False, "error": f"controller unreachable: {e.__class__.__name__}"}, status_code=502)
        return JSONResponse(body, status_code=code)

    @app.post("/speed")
    async def set_speed(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict) or body.get("percent") is None:
            return JSONResponse({"ok": False, "error": 'body must be {"percent": <number>}'}, status_code=400)
        from starlette.concurrency import run_in_threadpool
        try:
            code, res = await run_in_threadpool(bridge.speed, body["percent"])
        except httpx.HTTPError as e:
            return JSONResponse({"ok": False, "error": f"controller unreachable: {e.__class__.__name__}"}, status_code=502)
        log.info("speed -> %s", res.get("speed_percent", res.get("error")))
        return JSONResponse(res, status_code=code)

    @app.post("/tools/{name}")
    async def tool(name: str, request: Request):
        try:
            args = await request.json()
        except Exception:
            args = {}
        args = args if isinstance(args, dict) else {}
        from starlette.concurrency import run_in_threadpool
        try:
            res = await run_in_threadpool(bridge.tool, name, args)
        except Refused as e:
            return JSONResponse({"ok": False, "refused": True, "error": str(e)}, status_code=400)
        except httpx.HTTPError as e:
            return JSONResponse({"ok": False, "error": f"controller unreachable: {e.__class__.__name__}"},
                                status_code=502)
        log.info("tool %s %s -> %s", name, args, {k: res.get(k) for k in ("ok", "did", "error")})
        return JSONResponse(res, status_code=200 if res.get("ok") else 409)

    return app


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--controller", default=os.environ.get("CONTROLLER_URL", "http://127.0.0.1:8765"))
    ap.add_argument("--vision", default=os.environ.get("VISION_URL", ""),
                    help="computer-vision server, e.g. http://127.0.0.1:8000 ('' = no vision)")
    ap.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    ap.add_argument("--vision-file", type=Path, default=DEFAULT_VISION_FILE,
                    help="same path as the controller's --vision-file")
    ap.add_argument("--task-timeout", type=float, default=180.0)
    ap.add_argument("--min-conf", type=float, default=0.6, help="match the controller's min_detection_confidence")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    key = os.environ.get("BRIDGE_KEY", "")
    if args.host not in ("127.0.0.1", "localhost", "::1") and len(key) < 8:
        ap.error("set BRIDGE_KEY (8+ chars) before exposing the bridge on the network; "
                 "the app sends it as EXPO_PUBLIC_BRIDGE_KEY")
    bridge = Bridge(args.controller, args.vision, args.scene, args.vision_file, args.task_timeout, args.min_conf)
    log.info("controller=%s vision=%s vision_file=%s", bridge.controller, bridge.vision or "-", bridge.vision_file)
    uvicorn.run(create_app(bridge, key), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
