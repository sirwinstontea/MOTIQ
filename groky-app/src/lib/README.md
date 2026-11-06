# lib

| File | Purpose |
|---|---|
| `armApproval.ts` | Motion approval. Before the arm moves, simulated or real, Motion states the plan with `propose_plan` and needs a spoken or typed yes in any supported language. |
| `fullAccess.ts` | Lets Motion run skills and multi-step tasks on the real arm without approving each skill first. ON at each start. The yes in `armApproval.ts` is still required. |
| `cameras.ts` | Camera list and headers. No URL means the tablet's own camera. |
| `chooser.ts` | Client for [`robot-chooser`](../../../robot-chooser), used by Settings |
| `config.ts` | `EXPO_PUBLIC_*` values, inlined at build time, so they must be read with literal keys |
| `recorder.ts` | "Show Motion" demonstration capture, as a video file or JPEG frames |
| `vision.ts` | `look`, a camera question through Grok |
| `time.ts` | Relative time labels |
