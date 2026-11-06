# Groky_Planner_Agent

The earlier video-to-robot planner, kept as method B in [`benchmark/`](../benchmark). New work belongs in [`vision/planner`](../vision/planner).

```text
Video -> storyboard grid + Grok Voice transcript -> plan -> learn skill -> validate -> execute
```

| Path | Contents |
|---|---|
| [`planner/`](planner) | The pipeline |
| [`data/skills/`](data/skills) | Skill library |
| `skill_pick_place_ur5e.json` | UR5e primitive catalog that the planner loads |
| `example_storyboard.jpg` | Example of the storyboard grid sent to Grok Vision |

Set up its own environment and run from this folder:

```bash
python -m venv .venv
.venv/bin/pip install -r planner/requirements.txt
.venv/bin/python -m planner --help
```
