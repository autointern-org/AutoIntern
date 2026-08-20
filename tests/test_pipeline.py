from __future__ import annotations

from typing import Any

from adapters.base import Job
from core.config import CompanyConfig
from core.discord import DiscordMessage
from core.kv import DEFAULT_SEEN_TTL_SECONDS, StateStore
from core.pipeline import scan


class FakeKV:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}
        self.puts: list[tuple[str, int | None]] = []

    @property
    def enabled(self) -> bool:
        return True

    def get_json(self, key: str) -> dict[str, Any] | None:
        value = self.values.get(key)
        return dict(value) if value else None

    def put_json(self, key: str, value: dict[str, Any], *, ttl_seconds: int | None = None) -> None:
        self.values[key] = dict(value)
        self.puts.append((key, ttl_seconds))

    def list_keys(self, prefix: str) -> list[str]:
        return [key for key in self.values if key.startswith(prefix)]


class FakeAdapter:
    def __init__(self, jobs: list[Job]) -> None:
        self.jobs = jobs

    def fetch(self) -> list[Job]:
        return self.jobs


class FakeClassifier:
    api_key = "test"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_resume_config(self, job: Job) -> str:
        self.calls.append(job.id)
        return "resume_config: focused software internship resume"


class FakeDiscord:
    def __init__(self, dismissed_message_ids: set[str] | None = None) -> None:
        self.dismissed_message_ids = dismissed_message_ids or set()
        self.posts: list[tuple[Job, str, int]] = []
        self.recaps: list[tuple[str, list[Job]]] = []
        self.issues: list[tuple[str, str]] = []
        self.thread_ids: dict[str, str] = {}

    def post_job(self, job: Job, resume_config: str, *, color: int) -> DiscordMessage:
        self.posts.append((job, resume_config, color))
        return DiscordMessage(id=f"message-{job.id}", channel_id="channel-1", payload={})

    def post_jobs_for_company(
        self, company: str, jobs_with_resume: list[tuple[Job, str, int]]
    ) -> list[DiscordMessage]:
        if len(jobs_with_resume) <= 5:
            return [self.post_job(job, resume, color=color) for job, resume, color in jobs_with_resume]
        self.recaps.append((company, [job for job, _, _ in jobs_with_resume]))
        messages = [DiscordMessage(id=f"summary-{company}", channel_id="channel-1", payload={})]
        for job, resume, color in jobs_with_resume:
            messages.append(self.post_job(job, resume, color=color))
        return messages

    def post_recap(self, company: str, jobs: list[Job], *, color: int) -> DiscordMessage:
        self.recaps.append((company, list(jobs)))
        return DiscordMessage(id=f"recap-{company}", channel_id="channel-1", payload={})

    def post_issue(self, title: str, body: str) -> DiscordMessage:
        self.issues.append((title, body))
        return DiscordMessage(id=f"issue-{title}", channel_id="issues", payload={})

    def has_dismiss_reaction(self, message_id: str, channel_id: str | None = None) -> bool:
        return message_id in self.dismissed_message_ids


def make_job(**overrides: Any) -> Job:
    values = {
        "id": "greenhouse:anthropic:1",
        "company": "anthropic",
        "title": "Software Engineer Intern",
        "location": "San Francisco, CA",
        "url": "https://example.com/job",
        "jd_text": "Python and distributed systems",
        "posted_at": "2026-05-19",
    }
    values.update(overrides)
    return Job(**values)


def test_scan_notifies_new_matching_jobs_and_records_state() -> None:
    job = make_job()
    state = StateStore()
    discord = FakeDiscord()
    classifier = FakeClassifier()

    result = scan(
        adapters=[FakeAdapter([job])],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="S")},
        state=state,
        discord=discord,
        classifier=classifier,
    )

    assert result.fetched == 1
    assert result.matched == 1
    assert result.notified == 1
    assert result.recaps == 1
    assert discord.recaps[0][0] == "anthropic"
    assert classifier.calls == []
    assert discord.posts == []
    assert state.is_seen(job.id)
    assert state.is_bootstrapped("anthropic")


def test_scan_posts_new_jobs_after_first_look() -> None:
    first = make_job(id="job-old")
    state = StateStore()
    discord = FakeDiscord()
    scan(
        adapters=[FakeAdapter([first])],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="S")},
        state=state,
        discord=discord,
        classifier=FakeClassifier(),
    )
    discord.recaps.clear()
    discord.posts.clear()
    classifier = FakeClassifier()
    newer = make_job(id="job-new")

    result = scan(
        adapters=[FakeAdapter([first, newer])],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="S")},
        state=state,
        discord=discord,
        classifier=classifier,
    )

    assert result.recaps == 0
    assert result.notified == 1
    assert classifier.calls == [newer.id]
    assert discord.posts[0][0].id == newer.id
    assert discord.posts[0][2] == 0xEF4444


def test_scan_marks_dismissed_reactions_before_notifying() -> None:
    state = StateStore()
    state.record_notification(
        job_id="greenhouse:anthropic:1",
        company="anthropic",
        title="Software Engineer Intern",
        url="https://example.com/job",
        message_id="message-1",
        channel_id="channel-1",
    )
    discord = FakeDiscord(dismissed_message_ids={"message-1"})

    result = scan(
        adapters=[FakeAdapter([make_job(id="greenhouse:anthropic:1")])],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse")},
        state=state,
        discord=discord,
        classifier=FakeClassifier(),
    )

    assert result.dismissed == 1
    assert result.notified == 0
    assert state.is_dismissed("greenhouse:anthropic:1")


def test_scan_refreshes_seen_ttl_without_reposting() -> None:
    job = make_job()
    kv = FakeKV()
    state = StateStore(kv)
    state.record_notification(
        job_id=job.id,
        company=job.company,
        title=job.title,
        url=job.url,
        message_id="message-1",
        channel_id="channel-1",
    )
    kv.puts.clear()
    discord = FakeDiscord()
    state.mark_bootstrapped(job.company)

    result = scan(
        adapters=[FakeAdapter([job])],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse")},
        state=state,
        discord=discord,
        classifier=FakeClassifier(),
    )

    assert result.notified == 0
    assert result.skipped_seen == 1
    assert discord.posts == []
    assert (f"job:{job.id}", DEFAULT_SEEN_TTL_SECONDS) in kv.puts
    assert state.is_seen(job.id)
