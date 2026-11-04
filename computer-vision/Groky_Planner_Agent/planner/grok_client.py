"""Grok (xAI) Client using OpenAI-compatible chat/completions endpoint with JSON schema output and Mock fallback."""
from __future__ import annotations
import json
import os
from typing import Any, Dict, List, Optional
import httpx

from .schemas import VideoTokens, SkillPlan, SkillDef

DEFAULT_XAI_BASE_URL = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
DEFAULT_MODEL = os.environ.get("XAI_MODEL", "grok-2-vision-1212")


class GrokClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("XAI_API_KEY", "")
        self.base_url = (base_url or DEFAULT_XAI_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.use_mock = os.environ.get("GROK_MOCK", "0") == "1" or not self.api_key

    def chat_json(self, messages: List[Dict[str, Any]], temperature: float = 0.1) -> Dict[str, Any]:
        """Calls Grok API expecting a valid JSON object in response."""
        if self.use_mock:
            return self._mock_response(messages)

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": temperature
        }

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)

    def _mock_response(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Deterministic mock responses for testing without calling xAI APIs."""
        prompt_text = ""
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        prompt_text += item.get("text", "")
            else:
                prompt_text += str(content)

        # 1. Planning mock (check first before tokenization keywords)
        if "robotic task planner" in prompt_text.lower() or "available_skills" in prompt_text.lower():
            return {
                "goal": "Pick orange screw and place inside bucket",
                "calls": [
                    {
                        "skill": "pick_and_place",
                        "args": {"target": "orange_screw", "destination": "bucket"},
                        "token_ids": ["tok_1", "tok_2"]
                    }
                ],
                "missing_skills": []
            }

        # 2. Video + Voice tokenization mock
        if "perception agent" in prompt_text.lower() or "storyboard" in prompt_text.lower() or "video frames" in prompt_text.lower():
            return {
                "video_path": "mock_demo.mp4",
                "fps_sampled": 2.0,
                "storyboard_path": "logs/storyboard.jpg",
                "voice_transcript": "Prendo la vite arancione dal tavolo e la metto dentro il secchiello.",
                "scene_objects": [
                    {"name": "orange_screw", "role": "source"},
                    {"name": "bucket", "role": "container"}
                ],
                "tokens": [
                    {
                        "id": "tok_1",
                        "t_start_s": 0.0,
                        "t_end_s": 1.5,
                        "verb": "reach and grasp",
                        "canonical": "grasp",
                        "objects": [{"name": "orange_screw", "role": "source"}],
                        "gripper": "closed",
                        "notes": "Confirmed by Storyboard Tile #2 and Grok Voice transcript"
                    },
                    {
                        "id": "tok_2",
                        "t_start_s": 1.5,
                        "t_end_s": 3.0,
                        "verb": "move and release inside bucket",
                        "canonical": "release",
                        "objects": [{"name": "bucket", "role": "container"}],
                        "gripper": "open",
                        "notes": "Confirmed by Storyboard Tile #5 and Grok Voice transcript"
                    }
                ]
            }

        # 3. Fallback generic response
        return {"status": "ok", "mock": True}
