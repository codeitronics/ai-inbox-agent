"""One JSON-completion interface over Anthropic, OpenAI, Gemini and DeepSeek, plus recorded answers for the demo."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from .config import Settings

JSON_RULE = "\n\nRespond with a single JSON object and nothing else."


class LLM(Protocol):
    provider: str
    model: str

    def json(self, task: str, system: str, prompt: str, key: str | None = None) -> dict[str, Any]:
        """`task` and `key` (the email id) let the recorded client replay the right answer; live clients ignore them."""


def _parse(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1] if start >= 0 else text)


class LiveLLM:
    def __init__(self, provider: str, model: str, settings: Settings):
        self.provider, self.model = provider, model
        if provider == "anthropic":
            from anthropic import Anthropic

            self._client = Anthropic(api_key=settings.anthropic_api_key)
        elif provider in ("openai", "deepseek"):
            from openai import OpenAI

            self._client = (
                OpenAI(api_key=settings.openai_api_key)
                if provider == "openai"
                else OpenAI(api_key=settings.deepseek_api_key, base_url="https://api.deepseek.com")
            )
        elif provider == "gemini":
            from google import genai

            self._client = genai.Client(api_key=settings.gemini_api_key)
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def json(self, task: str, system: str, prompt: str, key: str | None = None) -> dict[str, Any]:
        prompt = prompt + JSON_RULE
        if self.provider == "anthropic":
            res = self._client.messages.create(
                model=self.model, max_tokens=1500, system=system, messages=[{"role": "user", "content": prompt}]
            )
            text = "".join(b.text for b in res.content if getattr(b, "type", "") == "text")
        elif self.provider == "gemini":
            from google.genai import types

            res = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json"),
            )
            text = res.text or ""
        else:
            res = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            text = res.choices[0].message.content or ""
        return _parse(text)


class RecordedLLM:
    """Replays answers recorded for the demo mailbox, so the demo runs without keys and screenshots are repeatable.

    Emails without a recording (e.g. a real inbox with no key) get a simple keyword-based fallback.
    """

    provider = "recorded"
    model = "recorded-demo"

    def __init__(self, path: Path | None = None):
        path = path or Path(__file__).parent / "demo" / "recordings.json"
        self._rec: dict[str, dict[str, Any]] = json.loads(path.read_text()) if path.exists() else {}

    def json(self, task: str, system: str, prompt: str, key: str | None = None) -> dict[str, Any]:
        hit = self._rec.get(key or "", {}).get(task)
        if hit is not None:
            return dict(hit)
        logger.debug(f"No recording for {key}/{task}; using fallback")
        return _fallback(task, prompt)


def _fallback(task: str, prompt: str) -> dict[str, Any]:
    low = prompt.lower()
    if task == "classify":
        if any(w in low for w in ("unsubscribe", "% off", "sale ends", "webinar")):
            return {"category": "promotional", "priority": "low", "confidence": 0.6, "reasoning": "Marketing language."}
        if any(w in low for w in ("urgent", "asap", "today", "outage", "escalat")):
            return {"category": "urgent", "priority": "high", "confidence": 0.6, "reasoning": "Time-sensitive wording."}
        return {"category": "general", "priority": "medium", "confidence": 0.5, "reasoning": "No strong signals."}
    if task == "summarize":
        body = prompt.split("Body:", 1)[-1].strip().split("\n\n")[0]
        return {"summary": body[:240], "key_points": [], "action_items": [], "sentiment": "neutral"}
    if task in ("draft", "refine"):
        subject = re.search(r"Subject: (.*)", prompt)
        return {
            "subject": f"Re: {subject.group(1).strip() if subject else ''}",
            "body": "Thanks for your note. I'm looking into this and will come back to you shortly.",
            "tone": "professional",
            "confidence": 0.3,
        }
    if task == "digest":
        return {"headline": "Your inbox this morning", "paragraph": ""}
    return {}


def get_llm(settings: Settings) -> LLM:
    provider = settings.resolved_provider()
    if provider == "recorded":
        return RecordedLLM()
    return LiveLLM(provider, settings.resolved_model(), settings)
