# Agent Bots Studio

**Teach remotely. Review with AI. Execute with confidence.**

Agent Bots Studio is a robotics and AI project for the **Grok Hackathon Pavia (4–5 November 2026)**. Our goal is to make robot task teaching more intuitive: a person demonstrates an activity while explaining it, Grok interprets the intended goal, and the user can review and refine a reusable skill before requesting physical execution.

The longer-term vision is a **robot-independent teaching and supervision interface** for collaborative arms and, eventually, mobile robots such as Unitree quadrupeds.

> **Development status (3 November 2026):** This repository contains a working codebase for computer-vision and Grok-related demonstrations. **Remote Teach, skill preview, robot-specific feasibility validation and full robot execution are proposed/in development, not completed features.** See [Project Architecture](PROJECT_ARCHITECTURE.md) for the planned interfaces and milestones.

## The idea

A person records a short demonstration—for example, placing a red cube in a blue box—while explaining what they are doing. The intended workflow is:

1. **Teach:** Capture the demonstration video and spoken explanation.
2. **Understand:** Extract key frames and a transcript; use Grok to infer objects, actions, goals and constraints.
3. **Preview:** Display the proposed task as an editable, simple animation (initially 2D).
4. **Refine:** Let the user correct Grok's interpretation and review an updated skill.
5. **Check feasibility:** Compare task requirements with a configurable hardware profile; identify missing information or known limits.
6. **Approve:** Obtain explicit user approval of the skill. Physical operation also requires independent validation and operator authorization.
7. **Execute & verify:** When the hardware integration is ready, carry out an approved task on a robot and verify the observed outcome.

**Example:** "Put the red cube in the blue box and keep it upright." If the cube is moved, the robot should re-detect it and adapt within its validated workspace rather than blindly replaying recorded joint coordinates.

**Important scope distinction:** This concept is *task-level programming from demonstration*. The MVP does **not** claim to train a new low-level motor policy from a single video, generate collision-free trajectories from Grok alone or transfer skills to arbitrary robots without calibration.

## Current code and roadmap

| Area | Path | Status |
| --- | --- | --- |
| Part A — Vision, object detection and tracking | `vision/eyes/` | **Existing** |
| Part B — Grok client and visual task understanding | `vision/grok/` | **Existing** |
| Local live computer-vision demo | `demo_live.py` | **Existing** |
| Existing vision tests | `tests/test_eyes.py` | **Existing** |
| Part C — Robot planner and execution interface | `vision/planner/` | **Planned** (`PROMPT_PARTE_C.md`) |
| Part D — Remote Teach and reusable skills | `vision/teach/` | **Planned / under development** |
| Generic hardware capabilities and feasibility checks | `robots/` | **Planned** |
| 2D animated skill preview | `preview/` | **Planned** |
| Remote application and multi-robot delivery | `app/`, Unitree adapter | **Optional extensions** |

The status refers to the reviewed shared `main` branch on 3 November 2026. Features under development may first appear on teammates' branches.

### High-level architecture (target)

```text
Human demonstration (video + voice)
               |
               v
       Remote Teach (Part D) <---- Vision / object observations (Part A)
               |
               v
     Grok interpretation (Part B)
               |
               v
        Versioned Skill JSON
               |
        +------+-------+
        |              |
        v              v
  2D task preview   Hardware capability check
        |              |
        +------+-------+
               v
       User review / revision
               |
               v
        Approved skill
               |
               v
       Robot planner (Part C)
               |
               v
 Deterministic validation + operator authorization
               |
               v
      Robot SDK / mock adapter
               |
               v
       Observe and verify
```

A **Skill** describes *what* should happen (e.g., `PICK`, `PLACE`, `VERIFY`); a **RobotPlan** describes validated, robot-specific execution steps. They are different artifacts. Markdown can display a plan, but structured JSON/Pydantic models should be the software integration contract.

## Closed-loop agent behavior (proposed)

The agent must not assume that issuing a command means the task has succeeded. Each physical action follows **state → validated action → sensor/controller feedback → observed state update**, and the mission supervisor chooses whether to continue, re-observe, recover, or stop. A successful SDK response does not by itself verify the actual goal (for example, a gripper may close without picking up the object).

Remote Teach has its own review loop: demonstrations produce **versioned** skill drafts; user edits invalidate previous approval until the updated preview and feasibility checks are reviewed. In both loops, Grok interprets high-level goals while deterministic software and the robot controller enforce motion limits and stop conditions.

See [Agent State, Action & Feedback Design](docs/AGENT_STATE_ACTION_FEEDBACK.md) for the proposed schemas, state transitions, sample feedback events and mock failure tests. **This closed-loop implementation is planned, not yet claimed as working in `main`.**

## Getting started with the existing demo

**Requirements:** Git, Python **3.11+**, a compatible webcam for live mode, and an optional xAI/OpenRouter API key for real Grok calls. Model-related dependencies may download additional files on first run.

### Windows (Git CMD, CMD or VS Code terminal)

```bat
git clone https://github.com/piniz-dev/computer-vision-Hackathon.git
cd computer-vision-Hackathon
setup.bat
```

`setup.bat` creates `.venv`, installs dependencies for Parts A and B, and creates a local `.env` from `.env.example` if one does not already exist.

To run the existing demo:

```bat
run_demo.bat
```

Or activate the environment and run manually:

```bat
.venv\Scripts\activate
python demo_live.py
```

Without a webcam, try the replay mode:

```bat
python demo_live.py --replay
```

### Linux / macOS

```bash
git clone https://github.com/piniz-dev/computer-vision-Hackathon.git
cd computer-vision-Hackathon
bash setup.sh
source .venv/bin/activate
python demo_live.py
```

### Grok API configuration

Edit the local `.env` file created by setup. To test without making API calls, set:

```dotenv
GROK_MOCK=1
```

For real requests, set `GROK_MOCK=0` and configure `XAI_API_KEY`, `XAI_BASE_URL`, and `XAI_MODEL` according to the provider you are using. See [.env.example](.env.example) and the [Grok module guide](vision/grok/README.md).

**Never commit `.env`, API keys, personal recordings or private data.**

### Run tests

From the repository root, in the activated environment:

```bash
python -m pytest -v
```

Tests validate software behavior; they do not certify that physical robot motion is safe.

## Hackathon milestones

| Priority | Demo milestone | Definition of success |
| --- | --- | --- |
| **M0** | Agree on shared contracts | `Skill`, `SceneObservation`, `RobotPlan` and `FeasibilityReport` are compatible across modules |
| **M1 — Core** | Record and understand | Short narrated video becomes a valid, editable Skill JSON |
| **M2 — Core** | Review and preview | User can correct the skill and inspect a 2D conceptual animation |
| **M3 — Core** | Preliminary hardware check | Missing/unsupported robot capabilities are reported honestly; mock execution is testable |
| **M4 — Conditional** | Physical arm demo | One calibrated, operator-approved pick-and-place succeeds and the result is verified |
| **M5 — Optional** | Arm + Unitree | A supervised transport/delivery mission is integrated after the arm workflow is stable |

The team should prefer **one complete, repeatable task** over many partially implemented functions. Suggested demo metrics are time to teach, interpretation accuracy, correction count, task success, adaptation to a moved object, and verification accuracy. Report measured values only.

## Safety and feasibility

- Grok interprets goals and proposes task-level operations; **it must not directly command unrestricted joint motion**.
- A conceptual 2D animation is **not** a collision-aware simulation or evidence of physical reachability.
- Unknown DOF, workspace, gripper, calibration or collision data must result in **`needs_validation`**, not automatic approval.
- Physical execution requires calibrated coordinates, robot-specific motion checks, the robot's own safety mechanisms and authorization by a responsible operator.
- A modified skill invalidates previous review/approval; it must be checked again before execution.
- The system must support safe interruption and visible failure reporting. Never bypass the controller's safety features.

## Collaboration and branches

We use feature branches and pull requests to protect `main`.

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c feature/your-module-name
# Work, test, and commit your changes
git add <your-files>
git commit -m "Describe your change"
git push -u origin feature/your-module-name
```

Open a pull request to merge into `main` after review. Coordinate changes to shared files such as `demo_live.py` and agree on JSON contracts before implementing cross-module integrations. Do not push directly to `main` or use `git push --force` in shared branches.

**Proposed workstreams:** Vision (A), Grok (B), Planner (C), Remote Teach (D), hardware feasibility/robot SDKs, UI/preview, and integration/testing. Module owners are to be agreed by the team; see [Project Architecture](PROJECT_ARCHITECTURE.md).

## Documentation

- [Project architecture and team plan](PROJECT_ARCHITECTURE.md) — overall architecture, module boundaries, proposed tree, roadmap and integration contracts.
- [Agent State, Action & Feedback Design](docs/AGENT_STATE_ACTION_FEEDBACK.md) — proposed closed-loop mission model, feedback contracts, recovery and verifiable success criteria.
- [Remote Teach architecture](REMOTE_TEACH_ARCHITECTURE.md) — detailed specification for recording, interpretation, revisions, preview and approval (add this file to the shared branch when reviewed).
- [Part C planning proposal](PROMPT_PARTE_C.md) — existing robot-planning specification; its Markdown execution format is **under review** in favor of validated structured commands.
- [Vision / Eyes documentation](vision/eyes/README.md) — existing perception pipeline.
- [Grok documentation](vision/grok/README.md) — existing API client, structured outputs and mock mode.

## Project links

- **Repository:** https://github.com/piniz-dev/computer-vision-Hackathon
- **Event:** https://pavia.grokbot.it/

---

*Agent Bots Studio is an evolving hackathon prototype. Documentation distinguishes implemented features from planned functionality; real-world use requires appropriate engineering validation and operational safeguards.*
