# data/skills

`skill_library.json` is the persistent skill library of [`vision/planner`](../../vision/planner). It is loaded at plan time and extended when the planner learns a skill.

`learned/` is ignored by Git and holds per-machine output. Built-in skills are never overwritten, and relearning a skill bumps its version.
