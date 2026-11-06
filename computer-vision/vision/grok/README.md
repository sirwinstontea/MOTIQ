# vision/grok

Vision B, the "brain": everything that uses Grok to understand images. It names objects, works out which object a command means, describes the scene, and verifies whether a robot action worked.

It's a generic toolkit with no objects or demos hard-coded, and it works with any object list. It runs **per event** (one command, one call), never per frame. Prompts are in Italian and ask for JSON only.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate             # Linux/macOS: source .venv/bin/activate
pip install -r vision/grok/requirements.txt
copy .env.example .env             # Linux/macOS: cp .env.example .env
```

| Variable | Code default | Meaning |
|---|---|---|
| `XAI_API_KEY` | | xAI or OpenRouter key, required unless in mock mode |
| `XAI_BASE_URL` | `https://api.x.ai/v1` | OpenAI-compatible endpoint. OpenRouter: `https://openrouter.ai/api/v1` |
| `XAI_MODEL` | `grok-4.7` | Model with image input. On OpenRouter: `x-ai/grok-4.7` |
| `GROK_MOCK` | `0` | `1` gives deterministic answers with no network |
| `GROK_TIMEOUT` | `8` | Seconds per call, retry included |
| `GROK_REASONING` | `minimal` | `minimal`, `low`, `medium`, `high`. Sent to OpenRouter only. Empty uses the model default. |

On `detect_objects`, the model default took 29–55 s, `low` 14 s and `minimal` 10 s. Set `GROK_TIMEOUT` near 30 for that task.

## Object format

```json
{"source": "arm", "frame_id": 1842, "ts": 1730700000.12,
 "objects": [{"id": 3, "label": "cup", "name": null, "conf": 0.87,
              "bbox": [0.41, 0.30, 0.12, 0.15], "color": null}]}
```

`bbox` is x, y, width and height normalised to 0–1, with x, y at the top left. Functions accept the `objects` list as dicts or `TrackedObject`.

## Tasks

| Function (`tasks.py`) | Result |
|---|---|
| `name_objects(marked_img, objects)` | Short names of 2–5 words with colour and distinctive traits |
| `resolve_command(marked_img, objects, text, context=None)` | `target_id`, `destination_id`, `action`, `ambiguous` + `question`, `confidence`, `reason` |
| `describe_scene(img, objects)` | At most 2 sentences, written to be spoken |
| `verify_action(img_before, img_after, objects_before, objects_after, action, target_id, destination_id=None)` | `success`, `reason`, `suggestion`, `confidence` |
| `detect_objects(img, known=None, start_id=1000, max_objects=15, min_conf=0.5)` | Finds objects outside YOLO's classes, with `tags` and `size_cm`. Rejected boxes are listed in `meta["discard_reasons"]`. |
| `explain_changes(...)` | Names what changed between a reference and the current scene |
| **`select(marked_img, objects, text, context=None, use_local=True)`** | **Public entry point.** Tries the local shortcut, then Grok. `res.source` is `local` or `grok`. |

`action` is one of `pick`, `place`, `point`, `go_to`, `follow`, `look`, `none`.

**Rules for every task**

- They never raise. On error, `ok=False` and the reason is in `error`.
- **Safety:** every `target_id` and `destination_id` from Grok is checked against the IDs present. An invented ID gives `ok=False`.
- An ambiguous command without a question gives `ok=False`. An absent object gives `ok=True`, `target_id=null` and a `reason`.
- An empty object list makes no Grok call.

## Files

| File | Purpose |
|---|---|
| `client.py` | `encode_image` and `ask_json`. Tries `json_schema`, falls back to `json_object`, retries once on invalid JSON or timeout. Logs calls without images to `logs/grok_calls.jsonl`. |
| `schemas.py` | Input and result models |
| `prompts.py` | One prompt per task |
| `tasks.py` | The tasks above |
| `local_resolver.py` | `resolve_local(objects, text)`. Matches names, labels, tags, Italian synonyms and colours, plurals, and spoken IDs ("prendi il 3"). |
| `voice_tools.py` | `TOOLS`, `dispatch`, `VisionState`, `FakeVisionState` |
| `marks.py` | `draw_marks`, `object_at`, `reacquire` |
| `open_vocab.py` | `OpenVocab`: YOLO-World in its own thread, following classes Grok found. IDs from 2000. `load_vocab`, `save_vocab`. |
| `changes.py` | "What changed" mode: `find_changes`, `draw_changes` |
| `geometry.py` | `iou`, `refine_box` (GrabCut) |
| `handoff.py` | `send_selection` writes `logs/selection.json` atomically |
| `cli.py` | Command line |
| [`eval/`](eval) | Synthetic dataset and evaluation |
| [`tests/`](tests) | Offline tests |

## Voice tools

1. Send `voice_tools.TOOLS` in `session.update` → `session.tools`.
2. On each `response.function_call_arguments.done` event:

   ```python
   result = dispatch(event["name"], event["arguments"], state)   # arguments can stay a JSON string
   ```

   Reply with `conversation.item.create` → `{"type": "function_call_output", "call_id": event["call_id"], "output": json.dumps(result)}`.
3. After answering **all** calls, send `response.create`.

Tools: `list_objects(source)`, `select_object(source, description)`, `describe_scene(source)`, `verify_last_action(source)`. Each result has `ok` and `say`, the short sentence to speak.

`state` implements `VisionState`: `get_frame`, `get_marked_frame`, `get_objects`, `get_last_action`. `vision/eyes` provides the live one. `FakeVisionState(folder)` reads `<source>.jpg` + `<source>.json` to work without a robot.

## Command line

```bash
python -m vision.grok.cli resolve  --image X.jpg --objects X.json --text "prendi la tazza rossa" [--no-local]
python -m vision.grok.cli name     --image X.jpg --objects X.json
python -m vision.grok.cli describe --image X.jpg --objects X.json
python -m vision.grok.cli verify   --before A.jpg --after B.jpg --objects-before A.json --objects-after B.json --action pick --target 3 [--destination 5]
```

Exit code 0 means `ok`. Try it without a key using `GROK_MOCK=1`.

## Live demo

```bash
python demo_live.py          # live window
python demo_live.py --once   # one photo: YOLO + Grok, saves logs/webcam_marked.jpg
```

| Key | Action |
|---|---|
| **G** | Grok finds other objects and YOLO-World learns to follow them |
| **N** | Grok names objects |
| **S** | Type a command |
| **Space**, **D** | Describe the scene |
| Click | Select an object |
| **Z**, right-click, **0** | Zoom, reset zoom |
| **P**, **X** | Save a reference, show what changed |
| **C** | Forget everything learned |
| **H**, **Q** | Help, quit |

`demo_live.py` writes two files to `logs/`:

- `selection.json`: the choice for the arm, rewritten on each click, command or re-acquisition. Readers act when `ts` changes.
- `vocab.json`: learned classes, `{label: {name, color, tags, size_cm}}`.

## Test and evaluate

```bash
python -m pytest vision/grok/tests           # always in mock mode
python -m vision.grok.eval.make_dataset      # regenerate the synthetic dataset
python -m vision.grok.eval.run_eval          # writes eval/results.md
```

Latest live run, 3 November 2026, `x-ai/grok-4.7` via OpenRouter, synthetic data with 4 scenes and 20 commands ([`results.md`](eval/results.md)):

| Metric | With local shortcut | Grok only |
|---|---|---|
| ID accuracy | 20/20 | 20/20 |
| Ambiguity precision and recall | 6/6 | 6/6 |
| Absent objects rejected | 4/4 | 4/4 |
| Latency p50 / p95 | 2404 / 4958 ms | 2749 / 6187 ms |
| Resolved locally | 9/20 | 0/20 |

These numbers verify the pipeline on simple shapes, not real-scene accuracy.

## Limits

- The local shortcut doesn't handle negation ("non la rossa") or English verbs.
- Voice context is per process, so it serves one user at a time.
- Mock answers don't reason about position.
- Chat Completions is a legacy xAI endpoint. If it goes away, only `client._call_api` needs changing.
- `detect_objects` boxes are approximate and static.
