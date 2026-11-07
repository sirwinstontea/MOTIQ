# vision/planner

Turns a video demonstration into a sequence of skill calls and runs it on a simulated UR5e. Skills that don't exist yet are defined by Grok as compositions of known primitives. **The module never executes generated code.**

It doesn't depend on `vision/eyes` or `vision/grok`, and it doesn't use OpenCV.

```text
video ─ffmpeg─┬─ LQ 64×36 grey, every frame ─► motion energy ─► keyframes (start, peak, end)
              ├─ HQ 720p, same index and PTS ◄──────── lookup ─┘
              └─ audio ─► Grok STT ─► timed phrases ─► voice keyframes + context
                                │
             HQ keyframes + phrases ─► Grok vision ─► action tokens (VideoTokens)
                                │
 skill_library.json ◄─ Grok: synthesise missing skills ◄─ Grok: plan (SkillPlan)
        │
        └─► expansion ─► validator (physical rules) ─► PyBullet or mock executor ─► run_<id>.json
```

The app's Show Motion path uses only the tokenizing half, through the [teach server](../../to-app). The app then writes the MOTIQ `skill.json`. The full pipeline below is the command-line route.

## Install

```bash
cd computer-vision
python3 -m venv .venv && .venv/bin/pip install -r vision/planner/requirements.txt
cp .env.example .env   # XAI_API_KEY, XAI_BASE_URL, XAI_MODEL, XAI_STT_API_KEY
```

Speech-to-text needs an xAI key. ffmpeg comes from the system when installed, otherwise from `imageio-ffmpeg`.

## Usage

```bash
# Keyframes only, with a motion.svg debug plot, no Grok
python -m vision.planner index --video data/replays/sample_arm.mp4 --debug

# Video → action tokens
python -m vision.planner tokenize --video demo.mp4 --out logs/tokens.json

# Full pipeline, offline
python -m vision.planner --mock run --tokens tests/fixtures/planner/tokens_unscrew.json --executor mock

# Prepare the PyBullet station: adds Gripper (TCP 150 mm), scene objects as boxes, and goes home
python -m vision.planner.pybullet_setup [--scene tests/fixtures/planner/tokens_unscrew.json]

# Full pipeline on PyBullet
python -m vision.planner run --video demo.mp4 --executor pybullet [--robot UR5e] [--tool Gripper] \
    [--record-program Demo] [--collisions] [--replan-on-fail] [--require-approved]

# Skill library
python -m vision.planner skills
python -m vision.planner skills --show svitare
python -m vision.planner skills --approve svitare      # needs_validation → approved
```

`--no-voice` disables speech for tokenizing. PyBullet must be open with a UR5e loaded.

## The two live JSON files

| File | Contents |
|---|---|
| `data/skills/skill_library.json` | Built-in skills (`home`, `pick`, `place`, `pick_and_place`) and learned ones, with `status`, `version` and event `history` |
| `logs/runs/run_<id>.json`, `latest.json` | Session state: tokens, plan, learned skills, steps with robot feedback, events. Rewritten atomically on each transition, so it can be polled. |

A skill is a `body` list where each item is one of:

```json
{"primitive": "MOVE_DOWN", "args": {"distance_m": "$depth"}}
{"skill": "pick", "args": {"target": "$obj"}}
{"repeat": "$turns", "body": []}
```

Primitives come from the closed catalog in `skill_pick_place_ur5e.json`.

## Safety

- **Validator.** Checks the expanded sequence before it runs: closed catalog, parameter bounds, `z` above `min_z_above_table_m`, gripper open before descending, lift before travel, speed limits and reach. Errors go back to Grok, with at most 3 plan iterations and 3 synthesis attempts per skill.
- **PyBullet.** The arm moves in the controller's cell. This planner does not command a robot.
- **Learned skills.** They start as `needs_validation`. With `--require-approved`, a run stops if the plan uses one.

Units: skills and primitives use metres and degrees. Scene positions and executor TCP poses use millimetres.

## Files

| File | Purpose |
|---|---|
| `frames.py` | Dual-stream ffmpeg extraction |
| `motion.py` | Motion energy, segmentation, keyframe selection |
| `voice.py` | Words to timed phrases |
| `tokenizer.py` | Chunks images to Grok and merges tokens |
| `agent.py` | Plan, learn, validate and execute loop |
| `library.py` | Skill library, recursive expansion, cycle checks |
| `primitives.py` | Catalog loader |
| `validator.py` | Physical rule checks |
| `schemas.py` | Pydantic contracts |
| `prompts.py` | Grok system and user prompts |
| `grok_client.py` | Chat and speech-to-text client, plus the mock |
| `mock_rules.py` | Deterministic mock answers |
| `runlog.py` | Atomic run log |
| `pybullet_setup.py` | Station preparation |
| [`executors/`](executors) | PyBullet and in-memory executors |

## Test

```bash
GROK_MOCK=1 .venv/bin/python -m pytest tests/test_planner.py -v
```
