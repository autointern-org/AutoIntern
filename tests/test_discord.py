from __future__ import annotations

from adapters.base import Job
from core.discord import build_job_embed, has_checkmark_reaction


def test_discord_embed_contains_resume_config_and_footer() -> None:
    job = Job(
        id="job-1",
        company="anthropic",
        title="Software Engineer Intern",
        location="San Francisco, CA",
        url="https://example.com/job",
        jd_text="",
        posted_at="2026-05-19",
    )

    embed = build_job_embed(job, "resume_config: test", color=0xEF4444)

    assert embed["title"] == "🚨 anthropic - Software Engineer Intern"
    assert embed["url"] == "https://example.com/job"
    assert "```text\nresume_config: test\n```" in embed["description"]
    assert embed["footer"]["text"] == "React ✅ to dismiss"


def test_has_checkmark_reaction() -> None:
    assert has_checkmark_reaction({"reactions": [{"count": 1, "emoji": {"name": "✅"}}]})
    assert not has_checkmark_reaction({"reactions": [{"count": 1, "emoji": {"name": "❌"}}]})
