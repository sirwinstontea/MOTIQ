"""End-to-end Planning Agent orchestrating: Tokenize (Vision Storyboard + Grok Voice Audio) -> Plan -> Learn -> Validate -> Execute."""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schemas import RunState, VideoTokens, SkillPlan, SkillDef, StepRecord
from .tokenizer import sample_video_frames
from .voice_extractor import GrokVoiceClient
from .grok_client import GrokClient
from .library import SkillLibrary
from .validator import PlanValidator
from .prompts import TOKENIZER_SYSTEM_PROMPT, PLANNER_SYSTEM_PROMPT, SYNTHESIZER_SYSTEM_PROMPT
from .executors.base import BaseExecutor, MockExecutor


class PlanningAgent:
    def __init__(
        self,
        library: Optional[SkillLibrary] = None,
        client: Optional[GrokClient] = None,
        voice_client: Optional[GrokVoiceClient] = None
    ):
        self.library = library or SkillLibrary()
        self.client = client or GrokClient()
        self.voice_client = voice_client or GrokVoiceClient()
        self.validator = PlanValidator()

    def run_pipeline(
        self,
        video_path: Optional[str] = None,
        tokens_input: Optional[VideoTokens] = None,
        voice_prompt: Optional[str] = None,
        executor: Optional[BaseExecutor] = None,
        run_log_dir: Optional[Path] = None
    ) -> RunState:
        run_id = f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        state = RunState(run_id=run_id, status="created")

        log_path = None
        if run_log_dir:
            run_log_dir.mkdir(parents=True, exist_ok=True)
            log_path = run_log_dir / f"{run_id}.json"

        def persist_state():
            if log_path:
                try:
                    with open(log_path, "w", encoding="utf-8") as f:
                        f.write(state.model_dump_json(indent=2))
                except Exception:
                    pass

        persist_state()

        # Step 1: Multimodal Tokenization (OpenCV Storyboard Grid + Grok Voice Audio Extraction)
        if video_path and not tokens_input:
            state.status = "tokenizing"
            state.events.append(f"Sampling video frames & building Storyboard Grid from {video_path}")
            persist_state()

            sampled = sample_video_frames(video_path, fps=2.0)
            frames = sampled["frames"]
            storyboard_url = sampled["storyboard_data_url"]
            storyboard_path = sampled["storyboard_path"]
            state.events.append(f"Saved visual Storyboard Grid ({len(frames)} frames) to {storyboard_path}")

            # 1b. Extract audio from video and run through Grok Voice Realtime API
            voice_info = self.voice_client.analyze_video_audio(video_path, fallback_prompt=voice_prompt)
            voice_transcript = voice_info.get("transcript", "")
            voice_intent = voice_info.get("intent_summary", "")
            state.events.append(
                f"Grok Voice audio extraction [{voice_info.get('source')}]: "
                f"transcript='{voice_transcript or 'none'}'"
            )
            persist_state()

            user_text = (
                f"Analyze this human task demonstration.\n"
                f"- Total sampled frames in Storyboard Grid: {len(frames)}\n"
                f"- Grok Voice Audio Transcript from video: \"{voice_transcript}\"\n"
                f"- Grok Voice Intent Summary: \"{voice_intent}\"\n"
                f"Combine the voice instructions and the numbered visual storyboard tiles (#1, #2, ...) "
                f"to produce accurate action tokens."
            )

            messages = [
                {"role": "system", "content": TOKENIZER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": storyboard_url}},
                    ]
                }
            ]
            tokens_data = self.client.chat_json(messages)
            tokens_data["storyboard_path"] = storyboard_path
            if voice_transcript and not tokens_data.get("voice_transcript"):
                tokens_data["voice_transcript"] = voice_transcript
            state.tokens = VideoTokens.model_validate(tokens_data)
        elif tokens_input:
            state.tokens = tokens_input
        else:
            raise ValueError("Either video_path or tokens_input must be provided.")

        state.events.append(f"Detected {len(state.tokens.tokens)} action tokens and {len(state.tokens.scene_objects)} objects.")
        persist_state()

        # Step 2: Plan (Vision Tokens + Voice Transcript -> SkillPlan)
        state.status = "planning"
        persist_state()

        available_skills = self.library.signatures()
        plan_messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps({
                    "voice_transcript": state.tokens.voice_transcript,
                    "action_tokens": [t.model_dump() for t in state.tokens.tokens],
                    "available_skills": available_skills,
                    "scene_objects": [o.model_dump() for o in state.tokens.scene_objects]
                })
            }
        ]
        plan_data = self.client.chat_json(plan_messages)
        state.plan = SkillPlan.model_validate(plan_data)
        state.events.append(f"Formulated plan: {state.plan.goal} ({len(state.plan.calls)} calls)")
        persist_state()

        # Step 3: Learn missing skills if any
        if state.plan.missing_skills:
            state.status = "synthesizing"
            persist_state()
            for missing in state.plan.missing_skills:
                synth_messages = [
                    {"role": "system", "content": SYNTHESIZER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps({
                            "missing_skill": missing.model_dump(),
                            "voice_transcript": state.tokens.voice_transcript,
                            "all_tokens": [t.model_dump() for t in state.tokens.tokens]
                        })
                    }
                ]
                new_skill_data = self.client.chat_json(synth_messages)
                new_skill = SkillDef.model_validate(new_skill_data)
                self.library.add_learned(new_skill)
                state.learned_skills.append(new_skill)
                state.events.append(f"Learned and registered new skill: '{new_skill.id}' (needs_validation)")

        # If executor is None (e.g., tokenize-only CLI command), stop here cleanly
        if executor is None:
            state.status = "completed"
            persist_state()
            return state

        # Step 4: Expand calls into concrete primitives
        expanded_primitives = []
        for call in state.plan.calls:
            call_dict = call.model_dump()
            expanded_primitives.extend(self.library.expand(call_dict))

        # Step 5: Validate physics and limits
        state.status = "validating"
        persist_state()
        is_valid, validation_errors = self.validator.validate(expanded_primitives)
        if not is_valid:
            state.status = "failed"
            state.events.append(f"Validation failed: {', '.join(validation_errors)}")
            persist_state()
            return state

        # Step 6: Execute on robot / simulator
        state.status = "executing"
        persist_state()
        executor.connect()

        for idx, prim in enumerate(expanded_primitives):
            record = StepRecord(step_id=idx + 1, primitive=prim["primitive"], args=prim.get("args", {}), status="running")
            state.steps.append(record)
            persist_state()

            feedback = executor.execute(prim["primitive"], prim.get("args", {}))
            record.feedback = feedback
            record.status = "done" if feedback.success else "failed"
            persist_state()

            if not feedback.success:
                state.status = "failed"
                state.events.append(f"Step {idx + 1} ({prim['primitive']}) failed execution.")
                persist_state()
                return state

        state.status = "completed"
        state.events.append("Pipeline completed successfully.")
        persist_state()
        return state
