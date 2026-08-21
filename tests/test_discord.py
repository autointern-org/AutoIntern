from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job
from core.discord import DiscordClient, PREVIEW_MAX, build_job_embed, has_checkmark_reaction


MAIN_WEBHOOK = "https://discord.com/api/webhooks/1/main-token"
FORUM_WEBHOOK = "https://discord.com/api/webhooks/2/forum-token"


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class RecordingSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._counter = 0

    def post(self, url: str, json: dict[str, Any] | None = None, timeout: int = 30) -> FakeResponse:
        self._counter += 1
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        payload = json or {}
        if payload.get("thread_name"):
            channel_id = f"thread-{payload['thread_name']}"
        elif "thread_id=" in url:
            channel_id = url.split("thread_id=", 1)[1].split("&", 1)[0]
        else:
            channel_id = "main-channel"
        return FakeResponse({"id": str(self._counter), "channel_id": channel_id})


def make_job(**overrides: Any) -> Job:
    values = {
        "id": "job-1",
        "company": "anthropic",
        "title": "Software Engineer Intern",
        "location": "San Francisco, CA",
        "url": "https://example.com/job",
        "jd_text": "",
        "posted_at": "2026-05-19",
    }
    values.update(overrides)
    return Job(**values)


def make_company_jobs(count: int, *, company: str = "TikTok") -> list[tuple[Job, str, int]]:
    return [
        (
            make_job(
                id=f"job-{index}",
                company=company,
                title=f"Intern {index}",
                url=f"https://example.com/{index}",
            ),
            "resume_config: test",
            0xEF4444,
        )
        for index in range(count)
    ]


def test_discord_embed_contains_resume_config_and_footer() -> None:
    job = make_job()

    embed = build_job_embed(job, "resume_config: test", color=0xEF4444)

    assert embed["title"] == "🚨 anthropic - Software Engineer Intern"
    assert embed["url"] == "https://example.com/job"
    assert "```text\nresume_config: test\n```" in embed["description"]
    assert embed["footer"]["text"] == "React ✅ to dismiss"


def test_build_job_embed_omits_posted_when_none() -> None:
    job = make_job(posted_at=None)

    embed = build_job_embed(job, "resume_config: test", color=0xEF4444)

    assert "**Posted:**" not in embed["description"]
    assert "Unknown" not in embed["description"]
    assert "**Location:** San Francisco, CA" in embed["description"]


def test_build_job_embed_includes_flags() -> None:
    job = make_job(location_unknown=True, term_flag="term_unknown", degree_flag="phd_likely")

    embed = build_job_embed(job, "resume_config: test", color=0xEF4444)

    assert "**Flags:** location_unknown, phd_likely, term_unknown" in embed["description"]


def test_has_checkmark_reaction() -> None:
    assert has_checkmark_reaction({"reactions": [{"count": 1, "emoji": {"name": "✅"}}]})
    assert not has_checkmark_reaction({"reactions": [{"count": 1, "emoji": {"name": "❌"}}]})


def test_post_jobs_for_company_sends_inline_at_preview_max() -> None:
    session = RecordingSession()
    client = DiscordClient(MAIN_WEBHOOK, forum_webhook_url=FORUM_WEBHOOK, session=session)

    messages = client.post_jobs_for_company("TikTok", make_company_jobs(PREVIEW_MAX))

    assert len(messages) == PREVIEW_MAX
    assert len(session.calls) == PREVIEW_MAX
    assert all(MAIN_WEBHOOK in call["url"] for call in session.calls)
    assert all(FORUM_WEBHOOK not in call["url"] for call in session.calls)
    assert all("thread_name" not in (call["json"] or {}) for call in session.calls)
    assert all("new intern postings" not in call["json"]["embeds"][0]["title"] for call in session.calls)


def test_post_jobs_for_company_overflow_posts_summary_and_forum_thread() -> None:
    session = RecordingSession()
    client = DiscordClient(MAIN_WEBHOOK, forum_webhook_url=FORUM_WEBHOOK, session=session)
    jobs = make_company_jobs(PREVIEW_MAX + 1)

    messages = client.post_jobs_for_company("TikTok", jobs)

    assert len(messages) == PREVIEW_MAX + 2
    summary = session.calls[0]
    assert MAIN_WEBHOOK in summary["url"]
    assert "wait=true" in summary["url"]
    embed = summary["json"]["embeds"][0]
    assert embed["title"] == "TikTok — 6 new intern postings"
    assert embed["footer"]["text"] == "Details in forum thread"
    assert "[Intern 0](https://example.com/0)" in embed["description"]
    assert embed["color"] == 0xEF4444

    forum_calls = [call for call in session.calls if FORUM_WEBHOOK in call["url"]]
    assert len(forum_calls) == PREVIEW_MAX + 1
    assert forum_calls[0]["json"]["thread_name"] == "TikTok"
    assert "wait=true" in forum_calls[0]["url"]
    assert "thread_id=" not in forum_calls[0]["url"]
    for call in forum_calls[1:]:
        assert "thread_name" not in call["json"]
        assert "thread_id=thread-TikTok" in call["url"]
        assert "wait=true" in call["url"]
    assert client.thread_ids["TikTok"] == "thread-TikTok"


def test_dry_run_overflow_includes_forum_thread_name(capsys) -> None:
    client = DiscordClient(MAIN_WEBHOOK, forum_webhook_url=FORUM_WEBHOOK, dry_run=True)

    messages = client.post_jobs_for_company("TikTok", make_company_jobs(PREVIEW_MAX + 1))

    output = capsys.readouterr().out
    assert "thread_name" in output
    assert "TikTok" in output
    forum_thread_payloads = [message.payload for message in messages if message.payload.get("thread_name")]
    assert len(forum_thread_payloads) == 1
    assert forum_thread_payloads[0]["thread_name"] == "TikTok"


def test_overflow_without_forum_webhook_fails_open_to_main(capsys) -> None:
    session = RecordingSession()
    client = DiscordClient(MAIN_WEBHOOK, session=session)

    messages = client.post_jobs_for_company("TikTok", make_company_jobs(PREVIEW_MAX + 1))

    output = capsys.readouterr().out
    assert "DISCORD_FORUM_WEBHOOK_URL" in output
    assert len(messages) == PREVIEW_MAX + 2
    assert all(MAIN_WEBHOOK in call["url"] for call in session.calls)
    assert session.calls[0]["json"]["embeds"][0]["title"] == "TikTok — 6 new intern postings"
    assert session.calls[1]["json"]["embeds"][0]["title"] == "🚨 TikTok - Intern 0"


def test_fetch_message_skips_rate_limit(capsys) -> None:
    class RateLimitedSession:
        def get(self, url: str, **kwargs: Any) -> Any:
            class Response:
                status_code = 429

                def json(self) -> dict[str, Any]:
                    return {}

                def raise_for_status(self) -> None:
                    raise RuntimeError("429 should not raise")

            return Response()

    client = DiscordClient(MAIN_WEBHOOK, session=RateLimitedSession())
    assert client.fetch_message("1539815221068046357") is None
    assert "rate limited" in capsys.readouterr().out


def test_post_issue_timeout_does_not_raise(capsys) -> None:
    class TimeoutSession:
        def post(self, url: str, **kwargs: Any) -> Any:
            raise requests.exceptions.ReadTimeout("read timed out")

    client = DiscordClient(
        MAIN_WEBHOOK,
        issues_webhook_url="https://discord.com/api/webhooks/3/issues-token",
        session=TimeoutSession(),
    )
    assert client.post_issue("AtlassianAdapter fetch failed", "empty body") is None
    assert "failed to post to Discord" in capsys.readouterr().out
