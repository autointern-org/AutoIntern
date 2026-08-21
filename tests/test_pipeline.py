from __future__ import annotations

from typing import Any

from adapters.base import Job
from core.config import CompanyConfig
from core.discord import DiscordMessage
from core.kv import DEFAULT_SEEN_TTL_SECONDS, SEEN_LIST_TTL_SECONDS, StateStore, now_iso
from core.pipeline import scan, select_companies


class FakeKV:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}
        self.puts: list[tuple[str, int | None]] = []
        self.gets: list[str] = []

    @property
    def enabled(self) -> bool:
        return True

    def get_json(self, key: str) -> dict[str, Any] | None:
        self.gets.append(key)
        value = self.values.get(key)
        return dict(value) if value else None

    def put_json(self, key: str, value: dict[str, Any], *, ttl_seconds: int | None = None) -> None:
        self.values[key] = dict(value)
        self.puts.append((key, ttl_seconds))

    def list_keys(self, prefix: str) -> list[str]:
        return [key for key in self.values if key.startswith(prefix)]

    def delete_keys(self, keys: list[str]) -> None:
        for key in keys:
            self.values.pop(key, None)

    def clear_io(self) -> None:
        self.puts.clear()
        self.gets.clear()


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
        self.reaction_checks: list[str] = []

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
        self.reaction_checks.append(message_id)
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
    kv = FakeKV()
    state = StateStore(kv)
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
    assert not any(key.startswith("job:") for key, _ in kv.puts)
    assert ("seen:anthropic", SEEN_LIST_TTL_SECONDS) in kv.puts
    assert job.id in kv.values["seen:anthropic"]["jobs"]


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


def test_scan_checks_shared_recap_message_once() -> None:
    state = StateStore()
    for job_id in ("job-a", "job-b"):
        state.record_notification(
            job_id=job_id,
            company="anthropic",
            title="Software Engineer Intern",
            url="https://example.com/job",
            message_id="recap-1",
            channel_id="channel-1",
        )
    discord = FakeDiscord(dismissed_message_ids={"recap-1"})
    state.mark_bootstrapped("anthropic")

    result = scan(
        adapters=[FakeAdapter([])],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse")},
        state=state,
        discord=discord,
        classifier=FakeClassifier(),
    )

    assert discord.reaction_checks == ["recap-1"]
    assert result.dismissed == 2
    assert state.is_dismissed("job-a")
    assert state.is_dismissed("job-b")


def test_scan_skips_still_listed_jobs_without_rewriting_seen() -> None:
    job = make_job()
    kv = FakeKV()
    state = StateStore(kv)
    configs = {"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse")}
    scan(
        adapters=[FakeAdapter([job])],
        configs=configs,
        state=state,
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )
    kv.clear_io()
    discord = FakeDiscord()

    result = scan(
        adapters=[FakeAdapter([job])],
        configs=configs,
        state=StateStore(kv),
        discord=discord,
        classifier=FakeClassifier(),
    )

    assert result.notified == 0
    assert result.skipped_seen == 1
    assert discord.posts == []
    assert discord.recaps == []
    assert not any(key.startswith("job:") for key, _ in kv.puts)
    assert not any(key.startswith("seen:") for key, _ in kv.puts)
    assert not any(key.startswith("health:") for key, _ in kv.puts)
    assert not any(key.startswith("job:") for key in kv.gets)
    assert (f"job:{job.id}", DEFAULT_SEEN_TTL_SECONDS) not in kv.puts
    assert state.is_seen(job.id)


def test_scan_prunes_job_that_disappeared_on_healthy_fetch() -> None:
    kept = make_job(id="job-keep")
    gone = make_job(id="job-gone")
    kv = FakeKV()
    state = StateStore(kv)
    configs = {"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse")}
    scan(
        adapters=[FakeAdapter([kept, gone])],
        configs=configs,
        state=state,
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )
    assert kept.id in kv.values["seen:anthropic"]["jobs"]
    assert gone.id in kv.values["seen:anthropic"]["jobs"]
    kv.clear_io()

    result = scan(
        adapters=[FakeAdapter([kept])],
        configs=configs,
        state=StateStore(kv),
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )

    assert result.notified == 0
    assert gone.id not in kv.values["seen:anthropic"]["jobs"]
    assert kept.id in kv.values["seen:anthropic"]["jobs"]
    assert any(key == "seen:anthropic" for key, _ in kv.puts)


def test_scan_does_not_prune_when_fetch_returns_no_jobs() -> None:
    job = make_job()
    kv = FakeKV()
    state = StateStore(kv)
    configs = {"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse")}
    scan(
        adapters=[FakeAdapter([job])],
        configs=configs,
        state=state,
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )
    kv.clear_io()

    result = scan(
        adapters=[FakeAdapter([])],
        configs=configs,
        state=StateStore(kv),
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )

    assert result.fetched == 0
    assert result.notified == 0
    assert job.id in kv.values["seen:anthropic"]["jobs"]
    assert not any(key.startswith("seen:") for key, _ in kv.puts)


def test_scan_does_not_prune_on_failed_adapter_fetch() -> None:
    job = make_job()
    kv = FakeKV()
    state = StateStore(kv)
    configs = {"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse")}
    scan(
        adapters=[FakeAdapter([job])],
        configs=configs,
        state=state,
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )
    kv.clear_io()

    class BoomAdapter:
        def fetch(self) -> list[Job]:
            raise RuntimeError("board down")

    result = scan(
        adapters=[BoomAdapter()],
        configs=configs,
        state=StateStore(kv),
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )

    assert result.notified == 0
    assert result.issues >= 1
    assert job.id in kv.values["seen:anthropic"]["jobs"]
    assert not any(key.startswith("seen:") for key, _ in kv.puts)


def test_scan_survives_issue_webhook_timeout() -> None:
    job = make_job()

    class BoomDiscord(FakeDiscord):
        def post_issue(self, title: str, body: str) -> DiscordMessage:
            raise RuntimeError("HTTPSConnectionPool read timed out")

    class BoomAdapter:
        def fetch(self) -> list[Job]:
            raise RuntimeError("board down")

    result = scan(
        adapters=[BoomAdapter(), FakeAdapter([job])],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="S")},
        state=StateStore(),
        discord=BoomDiscord(),
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )

    assert result.issues >= 1
    assert result.fetched == 1
    assert result.recaps == 1


def test_scan_prunes_all_interns_when_fetch_succeeded_with_zero_matches() -> None:
    intern = make_job()
    kv = FakeKV()
    state = StateStore(kv)
    configs = {"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse")}
    scan(
        adapters=[FakeAdapter([intern])],
        configs=configs,
        state=state,
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )
    kv.clear_io()
    staff = make_job(id="staff-1", title="Software Engineer")

    result = scan(
        adapters=[FakeAdapter([staff])],
        configs=configs,
        state=StateStore(kv),
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )

    assert result.fetched == 1
    assert result.matched == 0
    assert result.notified == 0
    assert intern.id not in kv.values["seen:anthropic"]["jobs"]
    assert any(key == "seen:anthropic" for key, _ in kv.puts)


def test_scan_new_job_after_first_look_writes_seen_once() -> None:
    first = make_job(id="job-old")
    kv = FakeKV()
    state = StateStore(kv)
    configs = {"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="S")}
    scan(
        adapters=[FakeAdapter([first])],
        configs=configs,
        state=state,
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )
    kv.clear_io()
    newer = make_job(id="job-new")

    result = scan(
        adapters=[FakeAdapter([first, newer])],
        configs=configs,
        state=StateStore(kv),
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )

    seen_puts = [key for key, _ in kv.puts if key == "seen:anthropic"]
    assert result.notified == 1
    assert len(seen_puts) == 1
    assert newer.id in kv.values["seen:anthropic"]["jobs"]
    assert first.id in kv.values["seen:anthropic"]["jobs"]
    assert not any(key.startswith("job:") for key, _ in kv.puts)


def test_is_seen_true_for_legacy_job_key_without_seen_doc() -> None:
    kv = FakeKV()
    state = StateStore(kv)
    job_id = "greenhouse:stripe:123"
    kv.values[f"job:{job_id}"] = {
        "job_id": job_id,
        "company": "stripe",
        "title": "Software Engineer Intern",
        "url": "https://example.com/job",
        "message_id": "m1",
        "channel_id": "c1",
        "dismissed": False,
        "notified_at": now_iso(),
    }

    assert "seen:stripe" not in kv.values
    assert state.is_seen(job_id, company="stripe")
    assert state.is_seen(job_id)
    assert kv.gets.count(f"job:{job_id}") == 1


def test_select_companies_only_and_skip(monkeypatch: Any) -> None:
    companies = [
        CompanyConfig(name="google", adapter="google"),
        CompanyConfig(name="tesla", adapter="tesla"),
        CompanyConfig(name="tiktok", adapter="tiktok"),
    ]
    monkeypatch.delenv("SCAN_ONLY_COMPANIES", raising=False)
    monkeypatch.delenv("SCAN_SKIP_COMPANIES", raising=False)
    assert [company.name for company in select_companies(companies)] == ["google", "tesla", "tiktok"]

    monkeypatch.setenv("SCAN_SKIP_COMPANIES", "tesla")
    assert [company.name for company in select_companies(companies)] == ["google", "tiktok"]

    monkeypatch.setenv("SCAN_ONLY_COMPANIES", "tesla")
    monkeypatch.delenv("SCAN_SKIP_COMPANIES", raising=False)
    assert [company.name for company in select_companies(companies)] == ["tesla"]
