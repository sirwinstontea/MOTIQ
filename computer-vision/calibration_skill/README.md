> **DEPRECATO / DEPRECATED (2026-11-05).** Questa skill muove il braccio da sola e usa la posa del **TCP attivo** come
> se fosse la flangia: entrambe le cose sono vietate da `MOTIQ_Codex_Calibration_Skill_EN.md`. Non importatela e non
> approvatela per il braccio vero. Usate **Skills → Calibration** nell'app (wizard senza movimenti, flangia verificata,
> scacchiera 21,8 mm): vedi `computer-vision/vision/eyes/README.md`, sezione *Skills → Calibration*.
> The `/calibration/arm/{reset,sample,solve,look_pose}` endpoints below remain only for backward compatibility.

# calibration_skill

An app skill that calibrates the wrist-mounted RealSense on the UR **without touching the sheet with the tool tip**. Vision never moves the robot. The app moves it in small steps through the bridge, which needs the external `ur_jog` driver, and asks vision to save a sample at each pose.

> [!IMPORTANT]
> The app does not support these steps yet: `calibrate_*` is not in `groky-app/src`. Apply the patch in section 1 before running the skill on the arm.

| File | Purpose |
|---|---|
| [`skill_calibra_camera_braccio.json`](skill_calibra_camera_braccio.json) | The skill: schema 1.1.0, 27 steps, 12 poses. Import it in Skills → *Import skill*. |
| [`make_skill.py`](make_skill.py) | Regenerates the JSON. Edit `POSES` for other poses. Checks steps ≤ 10 cm and joints ≤ 20°. |

The vision server exposes four POST routes. The body can be empty.

| Route | What it does |
|---|---|
| `/calibration/arm/reset` | Clears samples |
| `/calibration/arm/sample` | Stores the sheet as seen now plus the arm pose, read through RTDE. Returns `ok:false` if the arm is moving, the sheet is out of view, or the pose matches the last one. |
| `/calibration/arm/solve` | Hand-eye solve. Writes `data/calibration/hand_eye.json` and `markers_mm.json` with marker centres in the base frame. Returns `ok:false` with under 3 samples or a spread over 5 mm. |
| `/calibration/arm/look_pose` | From the observation pose, writes `fixed_pose.json` and activates it without a restart |

## 1. App patch

In `groky-app/src/skills/schema.ts`, widen the type:

```ts
physical_tool?: 'ur_down_up' | 'ur_visit' | 'calibrate_reset' | 'calibrate_sample' | 'calibrate_solve' | 'calibrate_look_pose';
```

In `groky-app/src/skills/runner.ts`, import `env` from `../lib/config`, and add this above `runSkill`:

```ts
const CALIBRATION = {
  calibrate_reset: 'reset', calibrate_sample: 'sample', calibrate_solve: 'solve', calibrate_look_pose: 'look_pose',
} as const;

// the vision server never moves the arm: it only saves a sample, solves, or writes the calibration files
async function visionCalibrate(tool: keyof typeof CALIBRATION): Promise<Res> {
  if (!env.visionServer) return { ok: false, error: 'EXPO_PUBLIC_VISION_SERVER is not set' };
  try {
    const res = await fetch(`${env.visionServer}/calibration/arm/${CALIBRATION[tool]}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    });
    const d = await res.json().catch(() => ({}));
    const note = tool === 'calibrate_sample' && d.ok ? `sample ${d.n}` : tool === 'calibrate_solve' && d.ok
      ? `spread ${d.spread_mm} mm` : undefined;
    return { ok: !!d.ok, note, error: d.ok ? undefined : String(d.reason ?? `HTTP ${res.status}`) };
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}
```

Inside `once`, **before** the `ur_down_up` block:

```ts
if (device === 'arm' && real && step.physical_tool?.startsWith('calibrate_')) {
  const r = await visionCalibrate(step.physical_tool as keyof typeof CALIBRATION);
  // a missed sample is not fatal: solve checks there are enough
  if (!r.ok && step.physical_tool === 'calibrate_sample') return { ok: true, note: `sample skipped: ${r.error}` };
  return r;
}
```

`index.ts` needs no change, because `calibrate_*` steps contain a `WAIT`, which is allowed on the arm.

## 2. On the camera PC

```bash
.venv\Scripts\python.exe -m pip install ur_rtde
run_server.bat --robot-ip <UR IP>
```

Set `EXPO_PUBLIC_VISION_SERVER=http://<PC-IP>:8000` in the app. With `--robot-ip`, vision reads the pose through `RTDEReceiveInterface`, which can't move the robot.

## 3. Before pressing Play

1. Print the ArUco sheet at **100%** (black square side 50.8 mm). Keep it flat and still for the whole run.
2. Use the pendant to bring the arm to the **observation pose**: camera down, flange about 50–55 cm above the table, sheet centred. Check `http://<PC-IP>:8000/stream/arm`. The skill starts and ends here.
3. Close RealSense Viewer and `run_demo.bat`.
4. Import the JSON, read the moves, and **approve** it for physical execution. It makes 12 poses in small steps: at most 6 cm, wrists at most 20° at a time.
5. Keep a hand on the **stop** button and the E-stop within reach.

## 4. What to watch

- Steps 2–24: a small move, then `calibrate_sample`. The app shows `sample 1 … sample 12`. A skipped sample doesn't stop the run. You need at least 3 and should aim for 10–12.
- Step 26 (`calibrate_solve`): the **spread must be under 5 mm**. Common causes of failure are a sheet that isn't flat or moved, a loose camera mount, or lag between pose and image.
- Step 27 (`calibrate_look_pose`): back at the start pose, check that reprojection error is under 1 px and `table_check_mm` is within ±5.
- Finally, remove the sheet and record the empty table:

  ```bash
  curl.exe -X POST http://localhost:8000/empty_table/arm
  ```

## 5. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Almost every sample reports the sheet not seen | The pose is too low or the sheet is off-centre. A 10° tilt shifts the image by about 8 cm. |
| Arm pose not available | The server didn't start with `--robot-ip`, or `ur_rtde` isn't installed |
| The arm is moving | The sample ran too early. Increase the `WAIT` before `calibrate_sample` in `make_skill.py`. |
| High spread in `solve` | Flatten the sheet and use more poses with different rotations |
| `markers_mm.json` still has sheet positions | `calibrate_solve` failed, so base-frame centres were never written |

`wrist_1` and `wrist_2` tilt the camera on two axes and `wrist_3` rotates it. If your arm behaves differently, change `POSES` and regenerate. The 12 poses need rotation about at least two axes with the sheet always in view. The active TCP doesn't affect the result but must stay the same for the whole run.

## Test

```bash
python -m pytest -q tests/test_calibration_skill.py
```
