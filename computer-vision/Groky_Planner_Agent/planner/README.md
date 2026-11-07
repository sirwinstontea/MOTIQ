# Groky_Planner_Agent/planner

| File | Purpose |
|---|---|
| `__main__.py` | Command line |
| `agent.py` | `PlanningAgent.run_pipeline`: tokenize, plan, learn, validate, execute |
| `tokenizer.py` | Samples frames with OpenCV, burns in timestamps, builds the storyboard grid |
| `voice_extractor.py` | Extracts 24 kHz PCM from video and calls the Grok Voice realtime API |
| `grok_client.py` | Grok chat completions with JSON schema output and a mock fallback |
| `prompts.py` | System prompts for tokenizing, planning and skill synthesis |
| `schemas.py` | Pydantic models for tokens, plans, skills and run state |
| `primitives.py` | UR5e primitive catalog from `skill_pick_place_ur5e.json` |
| `library.py` | Skill persistence and recursive expansion |
| `validator.py` | Checks an expanded sequence against robot rules and limits |
| [`executors/`](executors) | Mock and PyBullet execution |
| `requirements.txt` | Dependencies |
