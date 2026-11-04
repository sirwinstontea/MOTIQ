"""Audio extractor from video (.mp4 -> 24kHz PCM) and Grok Voice Realtime API client."""
from __future__ import annotations
import asyncio
import base64
import json
import os
import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, Optional

SAMPLE_RATE = 24000
DEFAULT_VOICE_MODEL = os.environ.get("XAI_VOICE_MODEL", "grok-voice-latest")


def _get_ffmpeg_exe() -> Optional[str]:
    """Finds ffmpeg binary from imageio_ffmpeg or system PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def extract_audio_pcm_from_video(video_path: str, out_wav_path: Optional[Path] = None) -> Optional[bytes]:
    """
    Extracts the audio track from an MP4 video file as 24kHz, 16-bit mono PCM bytes
    ready for Grok Voice Realtime API (wss://api.x.ai/v1/realtime).
    Returns None if the video has no audio stream.
    """
    vpath = Path(video_path)
    if not vpath.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    # Check if there is a companion .wav file next to the video
    companion_wav = vpath.with_suffix(".wav")
    target_wav = out_wav_path or Path("logs/extracted_audio.wav")
    target_wav.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_exe = _get_ffmpeg_exe()
    source_input = str(companion_wav) if companion_wav.exists() else str(vpath)

    cmd = [
        ffmpeg_exe,
        "-y",
        "-i", source_input,
        "-vn",                  # no video
        "-acodec", "pcm_s16le", # 16-bit PCM
        "-ar", str(SAMPLE_RATE),# 24kHz required by Grok Voice
        "-ac", "1",             # mono
        str(target_wav)
    ]

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        if proc.returncode != 0 or not target_wav.exists() or target_wav.stat().st_size <= 44:
            return None

        with wave.open(str(target_wav), "rb") as wf:
            pcm_bytes = wf.readframes(wf.getnframes())
            return pcm_bytes if len(pcm_bytes) > 0 else None
    except Exception:
        return None


class GrokVoiceClient:
    """
    Client for xAI Grok Voice Realtime API (wss://api.x.ai/v1/realtime).
    Supports extracting spoken intent/transcript from video audio tracks now,
    and is ready for live microphone streaming later.
    """
    def __init__(self, api_key: Optional[str] = None, voice_model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("XAI_API_KEY", "")
        self.voice_model = voice_model or DEFAULT_VOICE_MODEL
        self.use_mock = os.environ.get("GROK_MOCK", "0") == "1" or not self.api_key

    def analyze_video_audio(self, video_path: str, fallback_prompt: Optional[str] = None) -> Dict[str, Any]:
        """
        1. Extracts PCM audio from the video.
        2. Sends it to Grok Voice Realtime WebSocket (or uses fallback/mock).
        3. Returns {'has_audio': bool, 'transcript': str, 'intent_summary': str}.
        """
        if fallback_prompt:
            return {
                "has_audio": True,
                "source": "voice_prompt_override",
                "transcript": fallback_prompt,
                "intent_summary": fallback_prompt
            }

        pcm_bytes = extract_audio_pcm_from_video(video_path)

        # Also check if there is a companion .txt transcript next to the video
        companion_txt = Path(video_path).with_suffix(".txt")
        if not pcm_bytes and companion_txt.exists():
            txt = companion_txt.read_text(encoding="utf-8").strip()
            return {
                "has_audio": True,
                "source": "companion_txt",
                "transcript": txt,
                "intent_summary": txt
            }

        if self.use_mock:
            return {
                "has_audio": bool(pcm_bytes),
                "source": "grok_voice_mock",
                "transcript": "Prendo la vite arancione dal tavolo e la metto dentro il secchiello.",
                "intent_summary": "Action: pick_and_place | Target: orange_screw | Destination: bucket"
            }

        if not pcm_bytes:
            return {
                "has_audio": False,
                "source": "silent_video",
                "transcript": "",
                "intent_summary": ""
            }

        try:
            return asyncio.run(self._send_pcm_to_grok_voice(pcm_bytes))
        except Exception as e:
            return {
                "has_audio": True,
                "source": f"grok_voice_error ({type(e).__name__})",
                "transcript": "",
                "intent_summary": str(e)
            }

    async def _send_pcm_to_grok_voice(self, pcm_bytes: bytes) -> Dict[str, Any]:
        """Streams PCM audio to wss://api.x.ai/v1/realtime and collects transcript + voice assistant reply."""
        import websockets

        url = f"wss://api.x.ai/v1/realtime?model={self.voice_model}"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        transcript_parts = []
        reply_parts = []

        async with websockets.connect(url, additional_headers=headers, close_timeout=5) as ws:
            # 1. Configure Grok Voice session
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "instructions": (
                        "Ascolta l'audio estratto dal video dimostrativo di un operatore umano. "
                        "Riassumi in una frase chiara quale oggetto sta prendendo e dove lo sta mettendo."
                    ),
                    "voice": "eve",
                    "audio": {
                        "input": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}},
                        "output": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}}
                    }
                }
            }))

            # 2. Send PCM audio in chunks of ~0.5s (24000 bytes)
            chunk_size = SAMPLE_RATE * 2 // 2
            for offset in range(0, len(pcm_bytes), chunk_size):
                chunk = pcm_bytes[offset:offset + chunk_size]
                b64_chunk = base64.b64encode(chunk).decode("ascii")
                await ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": b64_chunk
                }))

            # 3. Commit audio buffer and ask Grok Voice to respond
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await ws.send(json.dumps({"type": "response.create"}))

            # 4. Collect events until response.done
            while True:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=15.0)
                ev = json.loads(raw_msg)
                ev_type = ev.get("type", "")

                if ev_type == "conversation.item.input_audio_transcription.completed":
                    if ev.get("transcript"):
                        transcript_parts.append(ev["transcript"])
                elif ev_type in ("response.audio_transcript.delta", "response.text.delta"):
                    if ev.get("delta"):
                        reply_parts.append(ev["delta"])
                elif ev_type == "response.done":
                    break

        return {
            "has_audio": True,
            "source": "grok_voice_realtime",
            "transcript": " ".join(transcript_parts).strip(),
            "intent_summary": "".join(reply_parts).strip()
        }
