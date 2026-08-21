from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job
from core.discord import DiscordClient, PREVIEW_MAX, build_job_embed, has_checkmark_reaction, ping_line


MAIN_WEBHOOK = "https://discord.com/api/webhooks/1/main-token"
FORUM_WEBHOOK = "https://discord.com/api/webhooks/2/forum-token"


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


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
    assert "**Ping:** single" in embed["description"]
    assert "```text\nresume_config: test\n```" in embed["description"]
    assert embed["footer"]["text"] == "React ✅ to dismiss"


def test_build_job_embed_omits_posted_when_none() -> None:
    job = make_job(posted_at=None)

    embed = build_job_embed(job, "resume_config: test", color=0xEF4444)

    assert "**Posted:**" not in embed["description"]
    assert "Unknown" not in embed["description"]
    assert "**Location:** San Francisco, CA" in embed["description"]


def test_ping_line_labels_single_batch_and_listings() -> None:
    assert ping_line("single", 1, 1) == "**Ping:** single"
    assert ping_line("single", 2, 3) == "**Ping:** single (2/3)"
    assert ping_line("batch", 2, 12) == "**Ping:** batch 2/12"
    assert ping_line("first_look", 12, 138) == "**Ping:** first look 12/138"
    assert ping_line("listing", 9, 9) == "**Ping:** listing 9/9"


def test_build_job_embed_includes_batch_ping() -> None:
    job = make_job()

    embed = build_job_embed(
        job,
        "resume_config: test",
        color=0xEF4444,
        ping_kind="batch",
        ping_index=2,
        ping_total=12,
    )

    assert "**Ping:** batch 2/12" in embed["description"]


def test_build_job_embed_includes_flags() -> None:
    job = make_job(location_unknown=True, term_flag="term_unknown", degree_flag="phd_likely")

    embed = build_job_embed(job, "resume_config: test", color=0xEF4444)

    assert "**Flags:** location_unknown, phd_likely, term_unknown" in embed["description"]


def test_has_checkmark_reaction() -> None:
    assert has_checkmark_reaction({"reactions": [{"count": 1, "emoji": {"name": "✅"}}]})
    assert not has_checkmark_reaction({"reactions": [{"count": 1, "emoji": {"name": "❌"}}]})


def test_post_jobs_for_company_sends_inline_and_forum_copy_at_preview_max() -> None:
    session = RecordingSession()
    client = DiscordClient(MAIN_WEBHOOK, forum_webhook_url=FORUM_WEBHOOK, session=session)

    messages = client.post_jobs_for_company("TikTok", make_company_jobs(PREVIEW_MAX))

    assert len(messages) == PREVIEW_MAX
    assert [message.id for message in messages] == [str(index) for index in range(1, PREVIEW_MAX + 1)]
    main_calls = [call for call in session.calls if MAIN_WEBHOOK in call["url"]]
    forum_calls = [call for call in session.calls if FORUM_WEBHOOK in call["url"]]
    assert len(main_calls) == PREVIEW_MAX
    assert len(forum_calls) == PREVIEW_MAX
    assert "**Ping:** single (1/5)" in main_calls[0]["json"]["embeds"][0]["description"]
    assert "**Ping:** single (5/5)" in main_calls[-1]["json"]["embeds"][0]["description"]
    assert forum_calls[0]["json"]["thread_name"] == "TikTok"
    for call in forum_calls[1:]:
        assert "thread_name" not in call["json"]
        assert "thread_id=thread-TikTok" in call["url"]
    assert all("new intern postings" not in call["json"]["embeds"][0]["title"] for call in main_calls)
    assert client.thread_ids["TikTok"] == "thread-TikTok"


def test_post_jobs_for_company_copies_single_ping_to_forum() -> None:
    session = RecordingSession()
    client = DiscordClient(MAIN_WEBHOOK, forum_webhook_url=FORUM_WEBHOOK, session=session)
    client.thread_ids["TikTok"] = "thread-TikTok"

    messages = client.post_jobs_for_company("TikTok", make_company_jobs(1))

    assert len(messages) == 1
    assert messages[0].id == "1"
    main_calls = [call for call in session.calls if MAIN_WEBHOOK in call["url"]]
    forum_calls = [call for call in session.calls if FORUM_WEBHOOK in call["url"]]
    assert len(main_calls) == 1
    assert len(forum_calls) == 1
    assert "**Ping:** single" in main_calls[0]["json"]["embeds"][0]["description"]
    assert "**Ping:** single" in forum_calls[0]["json"]["embeds"][0]["description"]
    assert "thread_name" not in forum_calls[0]["json"]
    assert "thread_id=thread-TikTok" in forum_calls[0]["url"]


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
    assert "**Ping:** batch of 6" in embed["description"]
    assert "[Intern 0](https://example.com/0)" in embed["description"]
    assert embed["color"] == 0xEF4444

    forum_calls = [call for call in session.calls if FORUM_WEBHOOK in call["url"]]
    assert len(forum_calls) == PREVIEW_MAX + 1
    assert forum_calls[0]["json"]["thread_name"] == "TikTok"
    assert "**Ping:** batch 1/6" in forum_calls[0]["json"]["embeds"][0]["description"]
    assert "**Ping:** batch 6/6" in forum_calls[-1]["json"]["embeds"][0]["description"]
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


def test_webhook_retries_on_429(monkeypatch: Any) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("core.discord.time.sleep", lambda seconds: sleeps.append(seconds))

    class FlakySession:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, url: str, json: dict[str, Any] | None = None, timeout: int = 30) -> FakeResponse:
            self.calls += 1
            if self.calls == 1:
                return FakeResponse({}, status_code=429)
            return FakeResponse({"id": "ok", "channel_id": "main-channel"})

    session = FlakySession()
    client = DiscordClient(MAIN_WEBHOOK, session=session)
    message = client.post_job(make_job(), "resume", color=1)
    assert message.id == "ok"
    assert sleeps == [5]
    assert session.calls == 2


def test_forum_post_failure_does_not_raise(capsys) -> None:
    class BoomSession:
        def post(self, url: str, json: dict[str, Any] | None = None, timeout: int = 30) -> FakeResponse:
            raise requests.exceptions.ReadTimeout("read timed out")

    client = DiscordClient(MAIN_WEBHOOK, forum_webhook_url=FORUM_WEBHOOK, session=BoomSession())
    messages = client.post_forum_jobs("TikTok", make_company_jobs(2), ping_kind="single")
    assert messages == []
    assert "forum post TikTok job-0 failed" in capsys.readouterr().out
