"""Client Grok (xAI) minimale, OpenAI-compatible, indipendente da vision/grok.

- Chat/vision: POST {XAI_BASE_URL}/chat/completions (xAI o OpenRouter), risposta JSON
  validata con Pydantic; se non valida, un nuovo tentativo con l'errore nel prompt.
- STT: POST {XAI_STT_URL} (default https://api.x.ai/v1/stt), multipart, parole con timestamp.
  Funziona solo con una chiave xAI (non OpenRouter): XAI_STT_API_KEY o XAI_API_KEY.

Con GROK_MOCK=1 o senza chiave si usa MockGrok (deterministico, offline).
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .primitives import ROOT

T = TypeVar("T", bound=BaseModel)


class GrokError(RuntimeError):
    pass


class STTWord(BaseModel):
    text: str
    start: float
    end: float


class STTResult(BaseModel):
    text: str = ""
    language: Optional[str] = None
    duration: Optional[float] = None
    words: list[STTWord] = []


class GrokLike(Protocol):
    is_mock: bool

    def chat_json(self, task: str, system: str, user: str, model: type[T],
                  images: Optional[list[bytes]] = None, context: Optional[dict[str, Any]] = None) -> T: ...

    def transcribe(self, audio_path: Path, language: Optional[str] = None,
                   keyterms: Optional[list[str]] = None) -> STTResult: ...


def load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:  # pragma: no cover
        pass


def _extract_json(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i >= 0 and j > i:
            text = text[i:j + 1]
    return text


class GrokClient:
    is_mock = False

    def __init__(self, api_key: str, base_url: str = "https://api.x.ai/v1", model: str = "grok-4.7",
                 timeout: float = 60.0, reasoning: Optional[str] = None,
                 stt_url: str = "https://api.x.ai/v1/stt", stt_api_key: Optional[str] = None,
                 stt_model: str = "grok-voice-transcribe-2.0", max_json_retries: int = 1):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.reasoning = reasoning or None
        self.stt_url = stt_url
        self.stt_model = stt_model
        self.max_json_retries = max_json_retries
        # La chiave OpenRouter non vale per l'endpoint STT di xAI.
        if stt_api_key:
            self.stt_api_key: Optional[str] = stt_api_key
        elif "openrouter" not in self.base_url:
            self.stt_api_key = api_key
        else:
            self.stt_api_key = None
        self.calls: list[dict[str, Any]] = []  # telemetria minima (task, durata, token)

    @classmethod
    def from_env(cls) -> "GrokClient | MockGrok":
        load_env()
        key = os.getenv("XAI_API_KEY", "").strip()
        if os.getenv("GROK_MOCK", "0") == "1" or not key:
            return MockGrok()
        # Timeout per le chiamate di pianificazione/visione: almeno 60 s (GROK_TIMEOUT è tarato
        # per la detection rapida).
        timeout = max(float(os.getenv("GROK_TIMEOUT", "60")), float(os.getenv("PLANNER_TIMEOUT", "90")))
        return cls(
            api_key=key,
            base_url=os.getenv("XAI_BASE_URL", "https://api.x.ai/v1"),
            model=os.getenv("XAI_MODEL", "grok-4.7"),
            timeout=timeout,
            reasoning=os.getenv("GROK_REASONING") or None,
            stt_url=os.getenv("XAI_STT_URL", "https://api.x.ai/v1/stt"),
            stt_api_key=os.getenv("XAI_STT_API_KEY") or None,
            stt_model=os.getenv("XAI_STT_MODEL", "grok-voice-transcribe-2.0"),
        )

    # ---- HTTP ------------------------------------------------------------
    def _post(self, url: str, headers: dict[str, str], **kw: Any) -> httpx.Response:
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                r = httpx.post(url, headers=headers, timeout=self.timeout, **kw)
            except httpx.HTTPError as e:
                last = e
            else:
                if r.status_code in (429, 500, 502, 503, 504):
                    last = GrokError(f"HTTP {r.status_code}: {r.text[:300]}")
                elif r.status_code >= 400:
                    raise GrokError(f"HTTP {r.status_code} da {url}: {r.text[:500]}")
                else:
                    return r
            time.sleep(1.5 * (attempt + 1))
        raise GrokError(f"chiamata a {url} fallita: {last}")

    def _chat(self, messages: list[dict[str, Any]]) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        if self.reasoning and "openrouter" in self.base_url:
            body["reasoning"] = {"effort": self.reasoning}
        r = self._post(f"{self.base_url}/chat/completions",
                       headers={"Authorization": f"Bearer {self.api_key}"}, json=body)
        data = r.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise GrokError(f"risposta inattesa: {json.dumps(data)[:500]}")
        if isinstance(content, list):  # alcuni provider restituiscono parti
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        self.calls.append({"usage": data.get("usage")})
        return content or ""

    # ---- API pubblica ----------------------------------------------------
    def chat_json(self, task: str, system: str, user: str, model: type[T],
                  images: Optional[list[bytes]] = None, context: Optional[dict[str, Any]] = None) -> T:
        content: Any = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(img).decode("ascii"),
                    "detail": "high"}}
                for img in images
            ]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system},
                                          {"role": "user", "content": content}]
        t0 = time.time()
        last_err = ""
        for attempt in range(self.max_json_retries + 1):
            raw = self._chat(messages)
            try:
                out = model.model_validate_json(_extract_json(raw))
                self.calls[-1].update(task=task, seconds=round(time.time() - t0, 2), attempt=attempt)
                return out
            except (ValidationError, ValueError) as e:
                last_err = str(e)[:1500]
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": "Il JSON non è valido rispetto allo schema richiesto:\n"
                                                f"{last_err}\nCorreggi e restituisci SOLO il JSON."},
                ]
        raise GrokError(f"[{task}] JSON non valido dopo {self.max_json_retries + 1} tentativi: {last_err}")

    def transcribe(self, audio_path: Path, language: Optional[str] = None,
                   keyterms: Optional[list[str]] = None) -> STTResult:
        if not self.stt_api_key:
            raise GrokError("STT non disponibile: serve una chiave xAI (XAI_STT_API_KEY) — "
                            "la chiave OpenRouter non vale per api.x.ai/v1/stt")
        # I campi opzionali vanno PRIMA di `file` (vedi docs xAI).
        fields: list[tuple[str, Any]] = [("model", (None, self.stt_model))]
        if language:
            fields.append(("language", (None, language)))
        for kt in (keyterms or [])[:100]:
            fields.append(("keyterm", (None, kt[:50])))
        with open(audio_path, "rb") as f:
            fields.append(("file", (Path(audio_path).name, f, "audio/wav")))
            r = self._post(self.stt_url, headers={"Authorization": f"Bearer {self.stt_api_key}"}, files=fields)
        return STTResult.model_validate(r.json())


# ---------------------------------------------------------------------------
# Mock deterministico
# ---------------------------------------------------------------------------

Handler = Callable[[str, str, dict[str, Any]], Any]


class MockGrok:
    """Grok finto: risposte in coda (per i test) oppure regole deterministiche (demo offline).

    `responses[task]` è una lista di dict/BaseModel/callable consumata in ordine; quando la
    coda è vuota si usa il comportamento rule-based di `mock_rules`.
    """
    is_mock = True

    def __init__(self, responses: Optional[dict[str, list[Any]]] = None,
                 stt: Optional[STTResult] = None):
        self.responses = {k: list(v) for k, v in (responses or {}).items()}
        self.stt = stt
        self.calls: list[dict[str, Any]] = []

    def chat_json(self, task: str, system: str, user: str, model: type[T],
                  images: Optional[list[bytes]] = None, context: Optional[dict[str, Any]] = None) -> T:
        ctx = context or {}
        self.calls.append({"task": task, "n_images": len(images or []), "user": user[:2000]})
        queue = self.responses.get(task) or []
        if queue:
            r = queue.pop(0)
            if callable(r):
                r = r(system, user, ctx)
        else:
            from . import mock_rules
            r = mock_rules.respond(task, ctx)
        if isinstance(r, BaseModel):
            r = r.model_dump(mode="json")
        return model.model_validate(r)

    def transcribe(self, audio_path: Path, language: Optional[str] = None,
                   keyterms: Optional[list[str]] = None) -> STTResult:
        self.calls.append({"task": "stt", "audio": str(audio_path)})
        return self.stt or STTResult()
