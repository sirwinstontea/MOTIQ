# skills

The MOTIQ skill format (schema 1.1.0) and everything that produces, checks and runs it.

| File | Purpose |
|---|---|
| `schema.ts` | Types mirroring [`templates/pick_place_ur5e.json`](templates), plus `robot.device` and a top-level `app` block |
| `index.ts` | Validation, labels, real arm rules such as `realArmProblems` and `physicalBlock` |
| `generate.ts` | Prompt to draft with Grok. `makeSkill` fills defaults. Drafts are never saved or approved here. |
| `fromVision.ts` | Teach by showing: live camera, picked video, or recording |
| `fromVoice.ts` | Turns a voice command that ran into a re-runnable draft, with no extra Grok call |
| `nameSkill.ts` | Generates a skill name |
| `dryrun.ts` | Runs a skill against the arm model for feasibility and preview data |
| `hardware.ts` | Can this cell (UR5e, tool, camera) physically run the skill? |
| `runner.ts` | Executes step by step and applies failure policies and safety gates |
| `store.ts` | Learned skills on the robot PC through the teach server, with a tablet copy in AsyncStorage |
