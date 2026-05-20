from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from adapters.base import Job


ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"
ANTHROPIC_VERSION = "2023-06-01"


class Classifier:
    def __init__(
        self,
        api_key: str | None,
        *,
        skill_context_path: str | Path = "config/skill_context.md",
        model: str = DEFAULT_MODEL,
        timeout: int = 60,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key
        self.skill_context_path = Path(skill_context_path)
        self.model = model
        self.timeout = timeout
        self.session = session or requests.Session()

    def generate_resume_config(self, job: Job) -> str:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for resume config generation")

        system = self.skill_context_path.read_text(encoding="utf-8")
        user = f"JD for {job.company} - {job.title}:\n\n{job.jd_text}\n\nReturn the resume config in the default output template."
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
        return _extract_text(response.json()).strip()


def _extract_text(payload: dict[str, Any]) -> str:
    blocks = payload.get("content") or []
    text_parts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text", "")))
    return "\n".join(part for part in text_parts if part)
