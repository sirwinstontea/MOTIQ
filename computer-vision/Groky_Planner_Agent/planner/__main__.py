"""Command Line Interface for the Grok Vision + Grok Voice Robot Planner."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .agent import PlanningAgent
from .executors.base import MockExecutor
from .library import SkillLibrary
from .schemas import VideoTokens


def main():
    parser = argparse.ArgumentParser(description="Groky_App: Video + Audio Demonstration to Robot Skills via Grok Vision and Grok Voice")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. run command
    run_parser = subparsers.add_parser("run", help="Run end-to-end pipeline from video (Vision + Voice) or tokens")
    run_parser.add_argument("--video", type=str, help="Path to demonstration mp4 video")
    run_parser.add_argument("--tokens", type=str, help="Path to pre-computed VideoTokens JSON")
    run_parser.add_argument("--voice-prompt", type=str, default=None, help="Optional voice override if video has no audio track")
    run_parser.add_argument("--executor", choices=["mock", "pybullet"], default="mock", help="Execution backend")
    run_parser.add_argument("--robot", default="UR5e", help="Arm model name")

    # 2. skills command
    skills_parser = subparsers.add_parser("skills", help="Inspect available skills in the library")
    skills_parser.add_argument("--list", action="store_true", help="List all skills")
    skills_parser.add_argument("--show", type=str, help="Show full definition of a skill ID")

    # 3. tokenize command
    tok_parser = subparsers.add_parser("tokenize", help="Extract video Storyboard Grid + Grok Voice audio tokens")
    tok_parser.add_argument("--video", required=True, type=str, help="Path to mp4 video")
    tok_parser.add_argument("--voice-prompt", type=str, default=None, help="Optional voice override if video is silent")
    tok_parser.add_argument("--out", type=str, default="logs/tokens.json", help="Output path for tokens JSON")

    args = parser.parse_args()

    lib = SkillLibrary()

    if args.command == "tokenize":
        print(f"Tokenizing video (OpenCV Storyboard + Grok Voice Audio): {args.video}...")
        agent = PlanningAgent(library=lib)
        state = agent.run_pipeline(video_path=args.video, voice_prompt=args.voice_prompt, executor=None)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if state.tokens:
            out_path.write_text(state.tokens.model_dump_json(indent=2), encoding="utf-8")
            print(f"Storyboard saved to: {state.tokens.storyboard_path}")
            print(f"Voice transcript:    {state.tokens.voice_transcript}")
        print(f"Tokens saved to:     {args.out}")
        return

    if args.command == "skills":
        if args.show:
            if args.show in lib.skills:
                print(json.dumps(lib.skills[args.show], indent=2))
            else:
                print(f"Skill '{args.show}' not found.")
        else:
            print("=== AVAILABLE ROBOT SKILLS ===")
            for sig in lib.signatures():
                print(f"- {sig['id']} [{sig['origin']}, {sig['status']}]: {sig['description']}")
        return

    if args.command == "run":
        if not args.video and not args.tokens:
            print("Error: Must provide either --video or --tokens")
            return

        tokens_data = None
        if args.tokens:
            with open(args.tokens, "r", encoding="utf-8") as f:
                tokens_data = VideoTokens.model_validate_json(f.read())

        executor = MockExecutor()

        agent = PlanningAgent(library=lib)
        runs_dir = Path(__file__).resolve().parent.parent.parent / "logs" / "runs"
        print(f"Starting PlanningAgent execution (Executor: {args.executor})...")
        state = agent.run_pipeline(
            video_path=args.video,
            tokens_input=tokens_data,
            voice_prompt=args.voice_prompt,
            executor=executor,
            run_log_dir=runs_dir
        )
        print(f"\nExecution finished! Final status: {state.status.upper()}")
        print(f"Run ID: {state.run_id}")
        print("Events:")
        for ev in state.events:
            print(f"  * {ev}")


if __name__ == "__main__":
    main()
