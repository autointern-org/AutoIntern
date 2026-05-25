from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

import requests

from adapters.base import Job


DEFAULT_PROVIDER = "gemini"
DEFAULT_MODELS = {
    "gemini": "gemini-3.1-flash-lite",
    "anthropic": "claude-sonnet-4-6",
}

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
GEMINI_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class ResumeLLMProvider(Protocol):
    provider: str
    model: str
    api_key: str | None

    def generate(self, *, system: str, user: str) -> str: ...


class GeminiProvider:
    provider = "gemini"

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODELS["gemini"],
        timeout: int = 60,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.session = session or requests.Session()

    def generate(self, *, system: str, user: str) -> str:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is required for Gemini resume config generation")
        response = self.session.post(
            GEMINI_GENERATE_URL.format(model=self.model),
            headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": 1800},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return _extract_gemini_text(response.json()).strip()


class AnthropicProvider:
    provider = "anthropic"

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODELS["anthropic"],
        timeout: int = 60,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.session = session or requests.Session()

    def generate(self, *, system: str, user: str) -> str:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for Anthropic resume config generation")
        response = self.session.post(
            ANTHROPIC_MESSAGES_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 1800,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return _extract_anthropic_text(response.json()).strip()


class Classifier:
    def __init__(
        self,
        provider: ResumeLLMProvider,
        *,
        skill_context_path: str | Path = "config/skill_context.md",
    ) -> None:
        self.provider = provider
        self.skill_context_path = Path(skill_context_path)

    @property
    def api_key(self) -> str | None:
        return self.provider.api_key

    def generate_resume_config(self, job: Job) -> str:
        system = self.skill_context_path.read_text(encoding="utf-8")
        user = (
            f"JD for {job.company} - {job.title}:\n\n"
            f"{job.jd_text}\n\n"
            "Return the resume config in the default output template."
        )
        return self.provider.generate(system=system, user=user)


def build_classifier_from_env(
    *,
    skill_context_path: str | Path = "config/skill_context.md",
    timeout: int = 60,
    session: requests.Session | None = None,
) -> Classifier:
    provider_name = (os.getenv("RESUME_LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    model = (os.getenv("RESUME_LLM_MODEL") or "").strip() or DEFAULT_MODELS.get(
        provider_name, DEFAULT_MODELS[DEFAULT_PROVIDER]
    )
    llm = _build_provider(provider_name, model=model, timeout=timeout, session=session)
    return Classifier(llm, skill_context_path=skill_context_path)


def _build_provider(
    name: str,
    *,
    model: str,
    timeout: int,
    session: requests.Session | None,
) -> ResumeLLMProvider:
    if name == "gemini":
        return GeminiProvider(
            os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            model=model,
            timeout=timeout,
            session=session,
        )
    if name == "anthropic":
        return AnthropicProvider(
            os.getenv("ANTHROPIC_API_KEY"),
            model=model,
            timeout=timeout,
            session=session,
        )
    raise ValueError(f"Unsupported RESUME_LLM_PROVIDER: {name!r}. Use 'gemini' or 'anthropic'.")


def _extract_anthropic_text(payload: dict[str, Any]) -> str:
    blocks = payload.get("content") or []
    text_parts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text", "")))
    return "\n".join(part for part in text_parts if part)


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    text_parts: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                text_parts.append(str(part["text"]))
    return "\n".join(text_parts)
