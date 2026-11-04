# Vision / Grok integration

The MOTIQ repository (`../MOTIQ/computer-vision`) was inspected and **left unchanged**. The relevant
interfaces are:

* `vision/eyes/schemas.py`: `FramePayload {source, frame_id, ts, objects:[EyeObject]}`. Each `EyeObject` has
  `id, label, name, conf, bbox (normalised), color, coords_mm {x, y, angle_deg}, distance_m, size_mm`.
* `vision/grok/handoff.py`: writes `logs/selection.json` =
  `{ts, source, command, action, destination_id, target:{id,label,name,color,tags,conf,bbox,distance_m,coords_mm}}`.
* `vision/grok/schemas.py`: `CommandResult {action: pick|place|point|go_to|follow|look|none, target_id, destination_id, ...}`,
  where IDs are tracker **integers**.

## Adapter (`robot_control/vision.py: VisionEyesFileDetector`)

* Reads either file format. It matches `label` / `name` against the registered object's `class_labels`
  (e.g. `red_cube: [red_cube, cube, red cube, cubo rosso]`) and records a `Detection` with `source="vision_eyes"`,
  the vision `conf`, and an age derived from `ts`.
* **`coords_mm` is ignored.** It's in the camera/table frame, and there's no verified camera→robot calibration in
  this build, so the controller uses taught named targets. If a detection carries a pose, the safety policy
  rejects it.
* Use it with `--detector vision_file --vision-file ../MOTIQ/computer-vision/logs/selection.json`.

## Contract summary

| Direction | Format |
|---|---|
| Vision → controller | `object_id`, `class_label`, `confidence`, `pose` (null in v1), `frame_id`, `timestamp_utc`, `uncertainty`, `source` |
| Controller → AI | `tools` list (`GET /tools`), `GET /scene` (allowed IDs, patterns, detections), `GET /tasks/{id}` |
| AI → controller | strict task JSON (see AI_ACTION_CONTRACT.md); unknown values are rejected |
| Controller → callers | event stream in task status (`GET /tasks/{id}`), never raw exceptions reported as success |

## Mapping Grok output to tasks (suggested glue, owned by the Grok team)

Grok's `CommandResult` uses tracker integer IDs and a different action vocabulary. Glue code should map it to
**registered string IDs**, for example `{action: "pick", target_id: 3}` with track 3 labelled `cube` →
`identify_object(red_cube)` + `pick_object(red_cube)`. It should refuse when the label doesn't map to a
registered object. Don't change the Grok schemas; do the translation in this adapter layer.

## Networking

The API binds to `127.0.0.1` with no authentication. For cross-PC use, put an authenticated, operator-approved
gateway in front of it. Don't expose the actuator endpoint on the LAN as part of this build.

```bash
curl -s localhost:8765/health
curl -s localhost:8765/scene
curl -s -X POST localhost:8765/tasks -H 'content-type: application/json' -d @examples/pick_shake_place.json
curl -s localhost:8765/tasks/demo_001
curl -s -X POST localhost:8765/tasks/demo_001/cancel
curl -s -X POST localhost:8765/robot/stop
```
