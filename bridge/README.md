# MOTIQ arm bridge

Connects the three parts:

```
groky-app (tablet, Grok Voice)
   │  EXPO_PUBLIC_ARM_URL ─ X-Bridge-Key ─▶  bridge :8080  ──▶  robot_controller 127.0.0.1:8765 ──▶ RoboDK / mock
   │                                            │  ▲                    ▲ reads runs/vision_bridge.json
   │                                            ▼  │ writes ────────────┘
   └─ EXPO_PUBLIC_VISION_SERVER (vision_* tools) ─▶ computer-vision :8000  (GET /state/arm, /frame/arm.jpg)
```

* **App → bridge**: `POST /tools/{name}` with the app's existing bridge protocol. The bridge turns each call into a
  controller task (AI_ACTION_CONTRACT.md), waits for it and returns `{ok, did | error, joints_deg, ...}`.
* **Vision → controller**: before a pick, the bridge reads `/state/arm` and maps tracked objects to registered IDs
  using the scene's `class_labels` (colour-aware: `cube` + `rosso` → `red cube`). It writes them to the file the
  controller's `vision_file` detector reads. If the object isn't visible, the bridge refuses the pick up front, so
  no FAULT gets latched. `coords_mm` isn't used, and picks go to the taught targets.
* **Vision → app**: Grok gets the vision server's `vision_list_objects`, `vision_select_object`, … tools directly.
  An object number from `vision_select_object` can be passed to `arm_pick_and_place`.
* `GET /camera.jpg` proxies the vision server's annotated frame, so the app's Arm camera shows it.

## Tools

| App tool | Controller task |
|---|---|
| `arm_pick_and_place {object, destination?, shake_cycles?}` | identify → pick → (shake) → place |
| `arm_pick {object}` | identify → pick |
| `arm_place {destination?}` | place |
| `arm_shake {cycles?}` | shake_object `gentle_shake` |
| `arm_home` | perform_motion `go_home` |
| `arm_open_gripper` | release_object |
| `arm_move_above {target}` | move_to_target `<object approach>` |
| `stop` / stop button | cancel active task + `/robot/stop` |
| `arm_move_left`, rotate, move_joint, close_gripper, … | **refused**: the controller only moves between taught targets. These still work in the app's simulator. |

`object` is a scene ID (`red_cube`), a name (`red cube`, `cubo rosso`, `object_cube`), or a vision track number (`3`).
Destinations can be named `delivery_zone`, `blue box` or `box`.

## Run (robot PC)

```bash
cd MOTIQ
BRIDGE_KEY=pick-a-secret ./bridge/start_stack.sh                 # RoboDK sim + vision if computer-vision/.venv exists
BRIDGE_KEY=pick-a-secret BACKEND=mock VISION=0 ./bridge/start_stack.sh   # no RoboDK, no camera
```

Then in `groky-app/.env` (restart with `npx expo start -c`):

```
EXPO_PUBLIC_ARM_URL=http://<robot-pc-ip>:8080
EXPO_PUBLIC_BRIDGE_KEY=pick-a-secret
EXPO_PUBLIC_VISION_SERVER=http://<robot-pc-ip>:8000
```

The vision server must listen on the LAN for the tablet to reach `:8000`. The bridge itself only needs it on localhost.

## After a stop or a failed task

The controller latches CANCELLED/FAULT, and new tasks are refused until an operator resets it **on the robot PC**:

```bash
curl -X POST localhost:8765/operator/reset -H 'content-type: application/json' -d '{"operator":"<your name>"}'
```

That endpoint only accepts loopback callers and the bridge never forwards it, so Grok can't clear a fault on its own.
