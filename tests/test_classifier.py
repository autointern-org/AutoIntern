from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from adapters.base import Job
from core.classifier import AnthropicProvider, Classifier, GeminiProvider, build_classifier_from_env


def make_job() -> Job:
    return Job(
        id="test:1",
        company="anthropic",
        title="Software Engineer Intern",
        location="SF",
        url="https://example.com",
        jd_text="Python, distributed systems",
        posted_at="2026-05-19",
    )


def test_gemini_provider_parses_response() -> None:
    session = MagicMock()
    session.post.return_value.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "target_role: intern\ncompany: anthropic"}]}}]
    }
    session.post.return_value.raise_for_status = MagicMock()

    provider = GeminiProvider("gemini-key", model="gemini-3.1-flash-lite", session=session)
    text = provider.generate(system="system prompt", user="user prompt")

    assert "target_role: intern" in text
    session.post.assert_called_once()
    call_kwargs = session.post.call_args.kwargs
    assert call_kwargs["headers"]["x-goog-api-key"] == "gemini-key"
    assert "gemini-3.1-flash-lite" in session.post.call_args.args[0]


def test_anthropic_provider_parses_response() -> None:
    session = MagicMock()
    session.post.return_value.json.return_value = {
        "content": [{"type": "text", "text": "target_role: intern\ncompany: anthropic"}]
    }
    session.post.return_value.raise_for_status = MagicMock()

    provider = AnthropicProvider("anthropic-key", session=session)
    text = provider.generate(system="system prompt", user="user prompt")

    assert "target_role: intern" in text
    assert session.post.call_args.kwargs["headers"]["x-api-key"] == "anthropic-key"


def test_build_classifier_from_env_defaults_to_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESUME_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("RESUME_LLM_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")

    classifier = build_classifier_from_env()

    assert classifier.provider.provider == "gemini"
    assert classifier.provider.model == "gemini-3.1-flash-lite"
    assert classifier.api_key == "test-gemini"


def test_classifier_reads_skill_context(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill = tmp_path / "skill.md"
    skill.write_text("Tailor resumes.", encoding="utf-8")
    session = MagicMock()
    session.post.return_value.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "resume_angle: systems"}]}}]
    }
    session.post.return_value.raise_for_status = MagicMock()

    classifier = Classifier(
        GeminiProvider("key", session=session),
        skill_context_path=skill,
    )
    result = classifier.generate_resume_config(make_job())

    assert result == "resume_angle: systems"
    payload = session.post.call_args.kwargs["json"]
    assert payload["systemInstruction"]["parts"][0]["text"] == "Tailor resumes."
