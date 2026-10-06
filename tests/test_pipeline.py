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
        self.forum_webhook_url = "https://discord.com/api/webhooks/2/forum"
        self.forum_posts: list[tuple[str, list[Job], str | None]] = []
        self.inline_lists = False

    @property
    def lists_enabled(self) -> bool:
        return bool(self.forum_webhook_url) or self.inline_lists

    def post_job(self, job: Job, resume_config: str, *, color: int) -> DiscordMessage:
        self.posts.append((job, resume_config, color))
        return DiscordMessage(id=f"message-{job.id}", channel_id="channel-1", payload={})

    def post_jobs_for_company(
        self, company: str, jobs_with_resume: list[tuple[Job, str, int]]
    ) -> list[DiscordMessage]:
        if len(jobs_with_resume) <= 5:
            messages = [self.post_job(job, resume, color=color) for job, resume, color in jobs_with_resume]
            if self.forum_webhook_url:
                self.post_forum_jobs(company, jobs_with_resume, ping_kind="single")
            return messages
        self.recaps.append((company, [job for job, _, _ in jobs_with_resume]))
        messages = [DiscordMessage(id=f"summary-{company}", channel_id="channel-1", payload={})]
        if self.forum_webhook_url:
            messages.extend(self.post_forum_jobs(company, jobs_with_resume, ping_kind="batch"))
            return messages
        for job, resume, color in jobs_with_resume:
            messages.append(self.post_job(job, resume, color=color))
        return messages

    def post_recap(self, company: str, jobs: list[Job], *, color: int) -> DiscordMessage:
        self.recaps.append((company, list(jobs)))
        return DiscordMessage(id=f"recap-{company}", channel_id="channel-1", payload={})

    def post_forum_jobs(
        self,
        company: str,
        jobs_with_resume: list[tuple[Job, str, int]],
        *,
        ping_kind: str | None = None,
    ) -> list[DiscordMessage]:
        self.forum_posts.append((company, [job for job, _, _ in jobs_with_resume], ping_kind))
        self.thread_ids.setdefault(company, f"thread-{company}")
        return [
            DiscordMessage(id=f"forum-{job.id}", channel_id=self.thread_ids[company], payload={})
            for job, _, _ in jobs_with_resume
        ]

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
    if "url" not in overrides:
        values["url"] = f"https://example.com/{values['id']}"
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


def test_scan_continues_when_health_read_fails() -> None:
    job = make_job()

    class BoomHealthKV(FakeKV):
        def get_json(self, key: str) -> dict[str, Any] | None:
            if key.startswith("health:"):
                raise RuntimeError("health timeout")
            return super().get_json(key)

    result = scan(
        adapters=[FakeAdapter([job])],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="S")},
        state=StateStore(BoomHealthKV()),
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )

    assert result.notified == 1
    assert result.recaps == 1


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
    assert classifier.calls == []
    assert discord.posts[0][0].id == newer.id
    assert "resume_angle: Emphasize the closest projects" in discord.posts[0][1]
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


def test_scan_does_not_reping_job_that_blinks_out_of_a_fetch() -> None:
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

    # Two healthy fetches without the job (the old 2-miss prune window).
    for _ in range(2):
        kv.clear_io()
        result = scan(
            adapters=[FakeAdapter([kept])],
            configs=configs,
            state=StateStore(kv),
            discord=FakeDiscord(),
            classifier=FakeClassifier(),
        )
        assert result.notified == 0
        assert gone.id in kv.values["seen:anthropic"]["jobs"]

    # The job comes back: no new Discord ping.
    kv.clear_io()
    result = scan(
        adapters=[FakeAdapter([kept, gone])],
        configs=configs,
        state=StateStore(kv),
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )
    assert result.notified == 0
    assert gone.id in kv.values["seen:anthropic"]["jobs"]


def test_scan_prunes_at_most_once_a_day() -> None:
    from datetime import UTC, datetime, timedelta

    kept = make_job(id="job-keep")
    gone = make_job(id="job-gone")
    kv = FakeKV()
    configs = {"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse")}
    scan(adapters=[FakeAdapter([kept, gone])], configs=configs, state=StateStore(kv), discord=FakeDiscord(), classifier=FakeClassifier())
    # The first-look run performed a prune pass and recorded when.
    first = kv.values["health:all"]["pruned_at"]
    assert first

    # 15 minutes later: the job is missing but no prune pass runs -> no stamp, no seen write.
    kv.clear_io()
    scan(adapters=[FakeAdapter([kept])], configs=configs, state=StateStore(kv), discord=FakeDiscord(), classifier=FakeClassifier())
    assert "missing_since" not in kv.values["seen:anthropic"]["jobs"][gone.id]
    assert not any(key.startswith("seen:") for key, _ in kv.puts)
    assert kv.values["health:all"]["pruned_at"] == first

    # Pretend a day passed: the pass runs, stamps the missing job, and records the new time.
    kv.values["health:all"]["pruned_at"] = (datetime.now(UTC) - timedelta(days=1, minutes=1)).isoformat()
    kv.clear_io()
    scan(adapters=[FakeAdapter([kept])], configs=configs, state=StateStore(kv), discord=FakeDiscord(), classifier=FakeClassifier())
    assert kv.values["seen:anthropic"]["jobs"][gone.id]["missing_since"]
    assert kv.values["health:all"]["pruned_at"] != first
    assert kept.id in kv.values["seen:anthropic"]["jobs"]


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
    assert result.issues == 1
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


def test_scan_stamps_interns_missing_when_fetch_succeeded_with_zero_matches() -> None:
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
    assert intern.id in kv.values["seen:anthropic"]["jobs"]
    kv.clear_io()

    result = scan(
        adapters=[FakeAdapter([staff])],
        configs=configs,
        state=StateStore(kv),
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )

    assert result.fetched == 1
    assert result.matched == 0
    # Still remembered, so it cannot re-ping; the daily prune pass will stamp
    # it as missing and forget it after a week of absence.
    assert intern.id in kv.values["seen:anthropic"]["jobs"]
    assert not any(key.startswith("seen:") for key, _ in kv.puts)


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


def test_scan_reports_partial_board_errors_once() -> None:
    nvidia = make_job(id="eightfold:nvidia:1", company="nvidia")

    class PartialAdapter:
        board_errors = [("microsoft", "eightfold apply.careers.microsoft.com status 429")]

        def fetch(self) -> list[Job]:
            return [nvidia]

    discord = FakeDiscord()
    result = scan(
        adapters=[PartialAdapter()],
        configs={
            "microsoft": CompanyConfig(name="microsoft", adapter="eightfold"),
            "nvidia": CompanyConfig(name="nvidia", adapter="eightfold"),
        },
        state=StateStore(),
        discord=discord,
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )

    assert result.issues == 1
    assert discord.issues == [
        ("microsoft fetch failed", "eightfold apply.careers.microsoft.com status 429")
    ]
    assert result.fetched == 1
    assert result.matched == 1


def test_scan_first_look_overflow_posts_forum_listing() -> None:
    jobs = [make_job(id=f"job-{index}") for index in range(6)]
    kv = FakeKV()
    discord = FakeDiscord()
    result = scan(
        adapters=[FakeAdapter(jobs)],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="S")},
        state=StateStore(kv),
        discord=discord,
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )

    assert result.recaps == 1
    assert result.notified == 6
    assert len(discord.forum_posts) == 1
    assert len(discord.forum_posts[0][1]) == 6
    assert discord.forum_posts[0][2] == "first_look"
    assert kv.values["thread:anthropic"]["thread_id"] == "thread-anthropic"
    assert kv.values["seen:anthropic"]["jobs"]["job-0"]["message_id"] == "forum-job-0"


def test_scan_backfills_forum_when_bootstrapped_without_thread() -> None:
    jobs = [make_job(id=f"job-{index}") for index in range(6)]
    kv = FakeKV()
    first = FakeDiscord()
    first.forum_webhook_url = None
    scan(
        adapters=[FakeAdapter(jobs)],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="S")},
        state=StateStore(kv),
        discord=first,
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )
    assert first.forum_posts == []
    assert "thread:anthropic" not in kv.values

    discord = FakeDiscord()
    result = scan(
        adapters=[FakeAdapter(jobs)],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="S")},
        state=StateStore(kv),
        discord=discord,
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )

    assert result.recaps == 0
    assert result.notified == 0
    assert len(discord.forum_posts) == 1
    assert len(discord.forum_posts[0][1]) == 6
    assert discord.forum_posts[0][2] == "listing"
    assert kv.values["thread:anthropic"]["thread_id"] == "thread-anthropic"


def test_scan_copies_single_ping_to_forum() -> None:
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
        skip_dismissals=True,
    )
    discord = FakeDiscord()
    newer = make_job(id="job-new")

    result = scan(
        adapters=[FakeAdapter([first, newer])],
        configs=configs,
        state=StateStore(kv),
        discord=discord,
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )

    assert result.notified == 1
    assert discord.posts[0][0].id == newer.id
    assert len(discord.forum_posts) == 1
    assert discord.forum_posts[0][1][0].id == newer.id
    assert discord.forum_posts[0][2] == "single"
    assert kv.values["seen:anthropic"]["jobs"][newer.id]["message_id"] == "message-job-new"
    assert kv.values["seen:anthropic"]["jobs"][newer.id]["channel_id"] == "channel-1"


def test_scan_resume_llm_failure_uses_placeholder() -> None:
    first = make_job(id="job-old")
    state = StateStore()
    configs = {"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="S")}
    scan(
        adapters=[FakeAdapter([first])],
        configs=configs,
        state=state,
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )

    class BoomClassifier(FakeClassifier):
        def generate_resume_config(self, job: Job) -> str:
            raise RuntimeError("HTTP 429")

    discord = FakeDiscord()
    newer = make_job(id="job-new")
    result = scan(
        adapters=[FakeAdapter([first, newer])],
        configs=configs,
        state=state,
        discord=discord,
        classifier=BoomClassifier(),
        skip_dismissals=True,
        skip_claude=False,
    )

    assert result.notified == 1
    assert "resume_angle: Emphasize the closest projects" in discord.posts[0][1]


def test_scan_same_url_new_id_does_not_reping() -> None:
    job = make_job(
        id="ibm:hash-old",
        company="ibm",
        url="https://careers.ibm.com/careers/JobDetail?jobId=128645",
    )
    kv = FakeKV()
    state = StateStore(kv)
    configs = {"ibm": CompanyConfig(name="ibm", adapter="ibm", tier="S")}
    scan(
        adapters=[FakeAdapter([job])],
        configs=configs,
        state=state,
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )
    discord = FakeDiscord()
    renamed = make_job(id="ibm:128645", company="ibm", url=job.url)

    result = scan(
        adapters=[FakeAdapter([renamed])],
        configs=configs,
        state=StateStore(kv),
        discord=discord,
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )

    assert result.notified == 0
    assert result.skipped_seen == 1
    assert discord.posts == []
    assert discord.forum_posts == []


def test_scan_marks_all_fresh_seen_when_only_summary_returns() -> None:
    jobs = [make_job(id=f"job-{index}", url=f"https://example.com/{index}") for index in range(6)]
    kv = FakeKV()
    state = StateStore(kv)
    configs = {"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="S")}
    scan(
        adapters=[FakeAdapter(jobs[:1])],
        configs=configs,
        state=state,
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )

    class SummaryOnlyDiscord(FakeDiscord):
        def post_jobs_for_company(
            self, company: str, jobs_with_resume: list[tuple[Job, str, int]]
        ) -> list[DiscordMessage]:
            self.recaps.append((company, [job for job, _, _ in jobs_with_resume]))
            return [DiscordMessage(id=f"summary-{company}", channel_id="channel-1", payload={})]

    discord = SummaryOnlyDiscord()
    result = scan(
        adapters=[FakeAdapter(jobs)],
        configs=configs,
        state=StateStore(kv),
        discord=discord,
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )

    assert result.notified == 5
    seen = kv.values["seen:anthropic"]["jobs"]
    for job in jobs[1:]:
        assert seen[job.id]["message_id"] == "summary-anthropic"


def test_build_adapters_wires_workable_and_smartrecruiters() -> None:
    from adapters.smartrecruiters import SmartRecruitersAdapter
    from adapters.workable import WorkableAdapter
    from core.pipeline import build_adapters

    adapters = build_adapters(
        [
            CompanyConfig(name="hugging-face", adapter="workable", slug="huggingface"),
            CompanyConfig(name="servicenow", adapter="smartrecruiters", slug="ServiceNow"),
            CompanyConfig(name="rippling", adapter="rippling", slug="rippling"),
        ]
    )
    assert [type(adapter).__name__ for adapter in adapters] == ["WorkableAdapter", "SmartRecruitersAdapter", "RipplingAdapter"]
    workable = adapters[0]
    assert isinstance(workable, WorkableAdapter) and workable.account_slugs == ["huggingface"]
    assert workable.company_names == {"huggingface": "hugging-face"}
    smart = adapters[1]
    assert isinstance(smart, SmartRecruitersAdapter) and smart.company_slugs == ["ServiceNow"]


def test_unknown_adapter_companies_are_reported() -> None:
    from core.pipeline import build_adapters, unknown_adapter_companies

    companies = [
        CompanyConfig(name="ok", adapter="greenhouse", slug="ok"),
        CompanyConfig(name="mystery", adapter="nosuchats"),
    ]
    assert [company.name for company in unknown_adapter_companies(companies)] == ["mystery"]
    assert [type(adapter).__name__ for adapter in build_adapters(companies)] == ["GreenhouseAdapter"]


def test_whitelist_uses_only_implemented_adapters() -> None:
    from core.config import Whitelist
    from core.pipeline import unknown_adapter_companies

    companies = Whitelist.load("config/whitelist.yaml").companies
    assert [(company.name, company.adapter) for company in unknown_adapter_companies(companies)] == []


def test_health_is_one_doc_written_once_per_run() -> None:
    kv = FakeKV()
    configs = {
        "anthropic": CompanyConfig(name="anthropic", adapter="greenhouse"),
        "stripe": CompanyConfig(name="stripe", adapter="greenhouse"),
    }
    jobs = [make_job(id="a1"), make_job(id="s1", company="stripe")]
    scan(adapters=[FakeAdapter(jobs)], configs=configs, state=StateStore(kv), discord=FakeDiscord(), classifier=FakeClassifier())
    health_puts = [key for key, _ in kv.puts if key.startswith("health:")]
    assert health_puts == ["health:all"]
    assert set(kv.values["health:all"]["companies"]) == {"anthropic", "stripe"}
    kv.clear_io()
    # Same counts next run: nothing to write.
    scan(adapters=[FakeAdapter(jobs)], configs=configs, state=StateStore(kv), discord=FakeDiscord(), classifier=FakeClassifier())
    assert not any(key.startswith("health:") for key, _ in kv.puts)


def test_scan_stops_posting_once_kv_refuses_writes() -> None:
    from core.kv import KVWriteBlocked

    class QuotaKV(FakeKV):
        def __init__(self, allowed: int) -> None:
            super().__init__()
            self.allowed = allowed

        def put_json(self, key: str, value: dict[str, Any], *, ttl_seconds: int | None = None) -> None:
            if self.allowed <= 0:
                raise KVWriteBlocked(f"KV put {key} rejected with 429")
            self.allowed -= 1
            super().put_json(key, value, ttl_seconds=ttl_seconds)

    kv = QuotaKV(allowed=0)
    discord = FakeDiscord()
    configs = {
        "anthropic": CompanyConfig(name="anthropic", adapter="greenhouse"),
        "stripe": CompanyConfig(name="stripe", adapter="greenhouse"),
    }
    jobs = [make_job(id="a1"), make_job(id="s1", company="stripe")]
    # Bootstrap run: the first company posts, its seen write is refused, the second company is deferred.
    result = scan(adapters=[FakeAdapter(jobs)], configs=configs, state=StateStore(kv), discord=discord, classifier=FakeClassifier(), skip_dismissals=True)
    assert result.recaps == 1
    assert result.deferred == 1
    assert result.issues == 1
    assert kv.values == {}

    # Writes work again: both companies post, and from now on nothing repeats.
    kv = QuotaKV(allowed=100)
    discord = FakeDiscord()
    result = scan(adapters=[FakeAdapter(jobs)], configs=configs, state=StateStore(kv), discord=discord, classifier=FakeClassifier(), skip_dismissals=True)
    assert result.recaps == 2 and result.deferred == 0
    result = scan(adapters=[FakeAdapter(jobs)], configs=configs, state=StateStore(kv), discord=FakeDiscord(), classifier=FakeClassifier(), skip_dismissals=True)
    assert result.notified == 0 and result.recaps == 0


def test_scan_persists_checked_ids_for_meta() -> None:
    class CheckingAdapter(FakeAdapter):
        checked_ids = {"1", "2"}
        intern_ids = {"2"}

    kv = FakeKV()
    state = StateStore(kv)
    scan(
        adapters=[CheckingAdapter([])],
        configs={"checking": CompanyConfig(name="checking", adapter="meta")},
        state=state,
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )
    assert kv.values["checked-scope:all"]["companies"]["checking"] == {"checked": ["1", "2"], "interns": ["2"]}
    assert StateStore(kv).get_checked_ids("checking") == ({"1", "2"}, {"2"})


def test_build_adapters_seeds_meta_from_state() -> None:
    from adapters.meta import MetaAdapter
    from core.pipeline import build_adapters

    kv = FakeKV()
    state = StateStore(kv)
    state.record_checked_ids("meta", {"10", "11"}, {"11"})
    adapters = build_adapters([CompanyConfig(name="meta", adapter="meta")], state=state)
    assert isinstance(adapters[0], MetaAdapter)
    assert adapters[0].known_ids == {"10", "11"}
    assert adapters[0].intern_ids == {"11"}


def test_run_scan_skips_reaction_check_unless_enabled(monkeypatch: Any) -> None:
    import core.pipeline as pipeline

    captured: dict[str, Any] = {}

    def fake_scan(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return pipeline.ScanResult()

    monkeypatch.setattr(pipeline, "scan", fake_scan)
    monkeypatch.delenv("CHECK_DISMISS_REACTIONS", raising=False)
    pipeline.run_scan(dry_run=True)
    assert captured["skip_dismissals"] is True
    monkeypatch.setenv("CHECK_DISMISS_REACTIONS", "1")
    pipeline.run_scan(dry_run=True)
    assert captured["skip_dismissals"] is False


def test_build_adapters_seeds_linkedin_from_state() -> None:
    from adapters.linkedin import LinkedInAdapter
    from core.pipeline import build_adapters

    kv = FakeKV()
    state = StateStore(kv)
    state.record_checked_ids("linkedin", {"1", "2"}, {"2"})
    adapters = build_adapters([CompanyConfig(name="linkedin", adapter="linkedin", slug="1337")], state=state)
    assert isinstance(adapters[0], LinkedInAdapter)
    assert adapters[0].company_id == "1337"
    assert adapters[0].known_ids == {"1", "2"} and adapters[0].intern_ids == {"2"}


def test_build_adapters_wires_tier_c_boards_with_known_ids() -> None:
    from adapters.avature import AvatureAdapter
    from adapters.citadel import CitadelAdapter
    from adapters.deshaw import DEShawAdapter
    from adapters.radancy import RadancyAdapter
    from core.pipeline import build_adapters

    kv = FakeKV()
    state = StateStore(kv)
    state.record_checked_ids("citadel", {"a"}, {"a"})
    adapters = build_adapters(
        [
            CompanyConfig(name="two-sigma", adapter="avature", host="careers.twosigma.com", site="careers/OpenRoles"),
            CompanyConfig(name="citadel", adapter="citadel", host="www.citadel.com"),
            CompanyConfig(name="de-shaw", adapter="deshaw"),
            CompanyConfig(name="intuit", adapter="radancy", host="jobs.intuit.com"),
            CompanyConfig(name="palo-alto-networks", adapter="radancy", host="jobs.paloaltonetworks.com", site="/en"),
        ],
        state=state,
    )
    names = [type(a).__name__ for a in adapters]
    assert names == ["AvatureAdapter", "CitadelAdapter", "DEShawAdapter", "RadancyAdapter"]
    citadel = adapters[1]
    assert isinstance(citadel, CitadelAdapter) and citadel.known["citadel"] == ({"a"}, {"a"})
    radancy = adapters[3]
    assert isinstance(radancy, RadancyAdapter) and [b.prefix for b in radancy.boards] == ["", "/en"]


def test_scan_persists_per_board_checked_ids() -> None:
    class MultiAdapter(FakeAdapter):
        checked_by_company = {"citadel": ({"1", "2"}, {"2"}), "citadel-securities": ({"9"}, set())}

    kv = FakeKV()
    scan(
        adapters=[MultiAdapter([])],
        configs={"citadel": CompanyConfig(name="citadel", adapter="citadel")},
        state=StateStore(kv),
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )
    companies = kv.values["checked-scope:all"]["companies"]
    assert companies["citadel"]["interns"] == ["2"]
    assert companies["citadel-securities"]["checked"] == ["9"]


def test_fetch_all_keeps_order_and_isolates_failures() -> None:
    import time as _time

    from core.pipeline import _fetch_all

    class Slow(FakeAdapter):
        def fetch(self) -> list[Job]:
            _time.sleep(0.2)
            return self.jobs

    class Boom:
        def fetch(self) -> list[Job]:
            raise RuntimeError("down")

    a = Slow([make_job(id="a")])
    b = FakeAdapter([make_job(id="b")])
    started = _time.perf_counter()
    outcomes = _fetch_all([a, Boom(), b])
    elapsed = _time.perf_counter() - started
    assert [o[0] for o in outcomes] == [a, outcomes[1][0], b]
    assert [j.id for j in outcomes[0][1]] == ["a"]
    assert isinstance(outcomes[1][1], RuntimeError)
    assert [j.id for j in outcomes[2][1]] == ["b"]
    assert elapsed < 0.6


def test_health_row_for_board_that_listed_jobs_but_matched_none() -> None:
    class Prefiltering(FakeAdapter):
        listing_counts = {"intuit": 432}

    kv = FakeKV()
    state = StateStore(kv)
    scan(
        adapters=[Prefiltering([])],
        configs={"intuit": CompanyConfig(name="intuit", adapter="radancy")},
        state=state,
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
    )
    assert state.get_health("intuit")["fetched"] == 432
    assert state.get_health("intuit")["matched"] == 0


def test_repeated_board_failure_posts_one_issue_per_six_hours() -> None:
    from datetime import UTC, datetime, timedelta

    class Broken:
        board_errors = [("dell", "503 Server Error")]

        def fetch(self) -> list[Job]:
            return []

    kv = FakeKV()
    configs = {"dell": CompanyConfig(name="dell", adapter="oracle")}
    first = FakeDiscord()
    scan(adapters=[Broken()], configs=configs, state=StateStore(kv), discord=first, classifier=FakeClassifier())
    second = FakeDiscord()
    scan(adapters=[Broken()], configs=configs, state=StateStore(kv), discord=second, classifier=FakeClassifier())
    assert len(first.issues) == 1
    assert len(second.issues) == 0
    # Six hours later the same failure is reported again.
    kv.values["health:all"]["issues"]["dell fetch failed"] = (datetime.now(UTC) - timedelta(hours=6, minutes=1)).isoformat()
    third = FakeDiscord()
    scan(adapters=[Broken()], configs=configs, state=StateStore(kv), discord=third, classifier=FakeClassifier())
    assert len(third.issues) == 1


def test_many_board_failures_collapse_into_one_issue() -> None:
    class Outage:
        board_errors = [(f"company-{i}", "500 Server Error") for i in range(8)]

        def fetch(self) -> list[Job]:
            return []

    discord = FakeDiscord()
    result = scan(
        adapters=[Outage()],
        configs={f"company-{i}": CompanyConfig(name=f"company-{i}", adapter="workday") for i in range(8)},
        state=StateStore(FakeKV()),
        discord=discord,
        classifier=FakeClassifier(),
    )
    assert len(discord.issues) == 1
    assert "8 boards failed" in discord.issues[0][0]
    assert "company-7" in discord.issues[0][1]
    assert result.issues == 1


def test_same_posting_on_two_boards_in_a_dedupe_group_pings_once() -> None:
    configs = {
        "amazon": CompanyConfig(name="amazon", adapter="amazon", dedupe_group="amazon"),
        "aws": CompanyConfig(name="aws", adapter="amazon", dedupe_group="amazon"),
    }
    url = "https://www.amazon.jobs/en/jobs/3012345/software-dev-engineer-intern"
    on_amazon = make_job(id="amazon:amazon:3012345", company="amazon", title="Software Dev Engineer Intern", url=url)
    on_aws = make_job(id="amazon:aws:3012345", company="aws", title="Software Dev Engineer Intern", url=url)
    other_aws = make_job(id="amazon:aws:999", company="aws", title="Software Engineer Intern, AWS Lambda", url="https://www.amazon.jobs/en/jobs/3099999/x")
    kv = FakeKV()
    state = StateStore(kv)
    for key in configs:
        state.mark_bootstrapped(key)
    discord = FakeDiscord()
    result = scan(
        adapters=[FakeAdapter([on_amazon, on_aws, other_aws])],
        configs=configs,
        state=state,
        discord=discord,
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )
    titles = [job.title for job, *_ in discord.posted_jobs] if hasattr(discord, "posted_jobs") else None
    assert result.notified == 2
    assert result.skipped_seen == 1
    assert on_aws.id in kv.values["seen:aws"]["jobs"]
    if titles is not None:
        assert titles.count("Software Dev Engineer Intern") == 1


def test_dedupe_matches_workday_req_suffix_variants_but_not_other_reqs() -> None:
    from core.pipeline import _pinged_on_group_mate

    kv = FakeKV()
    state = StateStore(kv)
    state.record_notification(
        job_id="workday:spgi:Kensho_Careers:1",
        company="kensho",
        title="Machine Learning Engineer - Summer Intern 2027",
        url="https://spgi.wd5.myworkdayjobs.com/Kensho_Careers/job/Cambridge-MA/Machine-Learning-Engineer---Summer-Intern-2027_331714",
        message_id="m1",
        channel_id="c1",
    )
    twin = make_job(
        id="workday:spgi:SPGI_Careers:1",
        company="kensho-spgi",
        title="Machine Learning Engineer - Summer Intern 2027",
        url="https://spgi.wd5.myworkdayjobs.com/SPGI_Careers/job/Cambridge-MA/Machine-Learning-Engineer---Summer-Intern-2027_331714-1",
    )
    different_req = make_job(
        id="workday:spgi:SPGI_Careers:2",
        company="kensho-spgi",
        title="Machine Learning Engineer - Summer Intern 2027",
        url="https://spgi.wd5.myworkdayjobs.com/SPGI_Careers/job/New-York/Machine-Learning-Engineer---Summer-Intern-2027_339999",
    )
    assert _pinged_on_group_mate(state, ["kensho"], twin, dry_run=False)
    assert twin.id in kv.values.get("seen:kensho-spgi", {}).get("jobs", {}) or twin.id in state.seen_entries("kensho-spgi")
    assert not _pinged_on_group_mate(state, ["kensho"], different_req, dry_run=False)
    assert not _pinged_on_group_mate(state, [], twin, dry_run=False)


def test_lifetime_notes_need_three_closed_postings() -> None:
    from core.lifetimes import company_notes, fastest_closers

    stats = {"companies": {"cisco": [2.0, 3.0, 4.0], "ibm": [40.0, 50.0], "google": [20.0, 30.0, 25.0]}}
    notes = company_notes(stats)
    assert set(notes) == {"cisco", "google"}
    assert "~3 days at cisco" in notes["cisco"] and "closes fast" in notes["cisco"]
    assert "closes fast" not in notes["google"]
    assert [name for name, _ in fastest_closers(stats)] == ["cisco", "google"]


def test_scan_attaches_lifetime_notes_to_job_embeds() -> None:
    kv = FakeKV()
    kv.values["stats:lifetimes"] = {"companies": {"anthropic": [1.0, 2.0, 3.0]}}
    state = StateStore(kv)
    state.mark_bootstrapped("anthropic")
    discord = FakeDiscord()
    scan(
        adapters=[FakeAdapter([make_job(id="new-1")])],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse")},
        state=state,
        discord=discord,
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )
    assert "**Typically open:** ~2 days at anthropic" in discord.company_notes["anthropic"]
    # No prune pass ran for lifetimes on this tick unless due; stats are only written when dirty.
    assert all(key != "stats:lifetimes" for key, _ in kv.puts)


def test_scan_pushes_only_priority_fresh_jobs() -> None:
    from core.push import PushNotifier

    class RecordingPush(PushNotifier):
        def __init__(self) -> None:
            super().__init__("t", dry_run=True)
            self.batches: list[list[str]] = []

        def push_jobs(self, jobs: list[Job]) -> int:
            self.batches.append([job.title for job in jobs])
            return len(jobs)

    kv = FakeKV()
    state = StateStore(kv)
    state.mark_bootstrapped("anthropic")
    push = RecordingPush()
    target = make_job(id="p1", title="Software Engineer Intern, Summer 2027")
    unknown_term = make_job(id="p2", title="Software Engineer Intern")
    result = scan(
        adapters=[FakeAdapter([target, unknown_term])],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="1")},
        state=state,
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
        skip_dismissals=True,
        push=push,
    )
    assert push.batches == [["Software Engineer Intern, Summer 2027"]]
    assert result.pushed == 1


def test_recall_gap_threshold() -> None:
    from core.pipeline import recall_gap

    assert recall_gap((61, 30)) == (61, 30)
    assert recall_gap((1109, 500)) == (1109, 500)
    assert recall_gap((100, 98)) is None  # board changed between pages
    assert recall_gap((10, 8)) is None  # fewer than 3 missing
    assert recall_gap((0, 0)) is None and recall_gap(None) is None


def test_scan_reports_parse_gaps_in_one_issue() -> None:
    class Gappy(FakeAdapter):
        source_totals = {"google": (61, 30), "intel": (61, 61), "apple": (900, 600)}

    discord = FakeDiscord()
    scan(
        adapters=[Gappy([])],
        configs={"google": CompanyConfig(name="google", adapter="google")},
        state=StateStore(FakeKV()),
        discord=discord,
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )
    assert len(discord.issues) == 1
    title, body = discord.issues[0]
    assert "Possible missed postings" in title
    assert "google: parsed 30 of 61" in body and "apple: parsed 600 of 900" in body and "intel" not in body


def test_sharding_splits_workday_round_robin_and_keeps_others_on_shard_zero() -> None:
    from core.pipeline import _health_scope, parse_shard, shard_companies

    companies = [CompanyConfig(name=f"wd{i}", adapter="workday") for i in range(7)] + [
        CompanyConfig(name="figma", adapter="greenhouse"),
        CompanyConfig(name="google", adapter="google"),
    ]
    shards = [shard_companies(companies, i, 3) for i in range(3)]
    names = [sorted(c.name for c in shard) for shard in shards]
    assert names[0] == ["figma", "google", "wd0", "wd3", "wd6"]
    assert names[1] == ["wd1", "wd4"] and names[2] == ["wd2", "wd5"]
    assert sorted(n for shard in names for n in shard) == sorted(c.name for c in companies)
    assert parse_shard("2/5") == (2, 5)
    assert parse_shard("5/5") is None and parse_shard("x") is None and parse_shard(None) is None
    assert _health_scope("", "0/4") == "all" and _health_scope("", "2/4") == "shard-2"
    assert _health_scope("tesla,linkedin", None) == "only-linkedin-tesla"


def test_select_companies_applies_shard_after_skip(monkeypatch: Any) -> None:
    from core.pipeline import select_companies

    companies = [CompanyConfig(name="wd-a", adapter="workday"), CompanyConfig(name="wd-b", adapter="workday"), CompanyConfig(name="tesla", adapter="tesla")]
    monkeypatch.setenv("SCAN_SKIP_COMPANIES", "tesla")
    monkeypatch.setenv("SCAN_SHARD", "1/2")
    assert [c.name for c in select_companies(companies)] == ["wd-b"]
    monkeypatch.setenv("SCAN_SHARD", "0/2")
    assert [c.name for c in select_companies(companies)] == ["wd-a"]


def test_build_adapters_wires_icims() -> None:
    from adapters.icims import ICIMSAdapter
    from core.pipeline import build_adapters

    adapters = build_adapters([CompanyConfig(name="sas", adapter="icims", host="careers-sas.icims.com")], state=StateStore(FakeKV()))
    assert isinstance(adapters[0], ICIMSAdapter) and adapters[0].boards[0].host == "careers-sas.icims.com"


def test_first_look_for_a_new_company_costs_one_kv_write() -> None:
    kv = FakeKV()
    scan(
        adapters=[FakeAdapter([make_job(id="n1"), make_job(id="n2")])],
        configs={"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse")},
        state=StateStore(kv),
        discord=FakeDiscord(),
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )
    company_puts = [key for key, _ in kv.puts if key.endswith(":anthropic")]
    assert company_puts == ["seen:anthropic"]
    assert kv.values["seen:anthropic"]["bootstrapped_at"]
    # Next run treats it as bootstrapped from the seen doc alone.
    assert StateStore(kv).is_bootstrapped("anthropic")


def test_legacy_bootstrap_key_still_counts() -> None:
    kv = FakeKV()
    kv.values["bootstrapped:stripe"] = {"company": "stripe", "bootstrapped_at": "2026-08-01T00:00:00+00:00"}
    assert StateStore(kv).is_bootstrapped("stripe")
    assert not StateStore(kv).is_bootstrapped("figma")


def test_steady_state_run_reads_no_per_job_dismissed_keys() -> None:
    kv = FakeKV()
    configs = {"anthropic": CompanyConfig(name="anthropic", adapter="greenhouse")}
    jobs = [make_job(id=f"j{i}") for i in range(4)]
    scan(adapters=[FakeAdapter(jobs)], configs=configs, state=StateStore(kv), discord=FakeDiscord(), classifier=FakeClassifier(), skip_dismissals=True)
    kv.clear_io()
    scan(adapters=[FakeAdapter(jobs)], configs=configs, state=StateStore(kv), discord=FakeDiscord(), classifier=FakeClassifier(), skip_dismissals=True)
    assert not any(key.startswith(("dismissed:", "job:", "thread:", "bootstrapped:")) for key in kv.gets), kv.gets
    assert kv.gets.count("seen:anthropic") == 1


def test_checked_ids_live_in_one_scope_doc_written_at_most_every_three_hours() -> None:
    from datetime import UTC, datetime, timedelta

    kv = FakeKV()
    kv.values["checked:meta"] = {"checked": ["1"], "interns": []}  # legacy per-company key
    state = StateStore(kv)
    assert state.get_checked_ids("meta") == ({"1"}, set())
    state.record_checked_ids("meta", {"1"}, set())
    state.record_checked_ids("meta", {"1", "2"}, set())
    state.record_checked_ids("linkedin", {"9"}, {"9"})
    assert kv.gets.count("checked:meta") == 1
    assert kv.puts == []
    state.flush_checked()
    assert [key for key, _ in kv.puts] == ["checked-scope:all"]
    assert set(kv.values["checked-scope:all"]["companies"]) == {"meta", "linkedin"}
    kv.clear_io()
    later_state = StateStore(kv)
    later_state.record_checked_ids("meta", {"1", "2", "3"}, set())
    later_state.flush_checked()
    assert kv.puts == []  # within three hours of the last write
    later_state.flush_checked(now=datetime.now(UTC) + timedelta(hours=3, minutes=1))
    assert [key for key, _ in kv.puts] == ["checked-scope:all"]
    assert StateStore(kv).get_checked_ids("meta") == ({"1", "2", "3"}, set())


def test_failed_first_look_is_retried_and_other_companies_continue() -> None:
    class FlakyDiscord(FakeDiscord):
        def post_recap(self, company: str, jobs: list[Job], *, color: int) -> DiscordMessage:
            if company == "anthropic":
                raise RuntimeError("429 Too Many Requests")
            return super().post_recap(company, jobs, color=color)

    kv = FakeKV()
    configs = {
        "anthropic": CompanyConfig(name="anthropic", adapter="greenhouse"),
        "stripe": CompanyConfig(name="stripe", adapter="greenhouse"),
    }
    jobs = [make_job(id="a1"), make_job(id="s1", company="stripe")]
    result = scan(adapters=[FakeAdapter(jobs)], configs=configs, state=StateStore(kv), discord=FlakyDiscord(), classifier=FakeClassifier(), skip_dismissals=True)
    assert result.recaps == 1
    assert not StateStore(kv).is_bootstrapped("anthropic") and StateStore(kv).is_bootstrapped("stripe")
    result = scan(adapters=[FakeAdapter(jobs)], configs=configs, state=StateStore(kv), discord=FakeDiscord(), classifier=FakeClassifier(), skip_dismissals=True)
    assert result.recaps == 1  # anthropic's first look on the retry; stripe not repeated


def _defense_channel() -> FakeDiscord:
    channel = FakeDiscord()
    channel.forum_webhook_url = None
    channel.inline_lists = True
    return channel


def test_defense_companies_post_to_their_channel_and_never_push() -> None:
    from core.push import PushNotifier

    class RecordingPush(PushNotifier):
        def __init__(self) -> None:
            super().__init__("t", dry_run=True)
            self.batches: list[list[str]] = []

        def push_jobs(self, jobs: list[Job]) -> int:
            self.batches.append([job.title for job in jobs])
            return len(jobs)

    state = StateStore(FakeKV())
    state.mark_bootstrapped("l3harris")
    state.mark_bootstrapped("anthropic")
    main, defense, push = FakeDiscord(), _defense_channel(), RecordingPush()
    l3 = make_job(id="d1", company="l3harris", title="Software Engineer Intern, Summer 2027", url="https://x/d1")
    ant = make_job(id="a1", title="Software Engineer Intern, Summer 2027", url="https://x/a1")
    result = scan(
        adapters=[FakeAdapter([l3, ant])],
        configs={
            "l3harris": CompanyConfig(name="l3harris", adapter="radancy", tier="1", channel="defense"),
            "anthropic": CompanyConfig(name="anthropic", adapter="greenhouse", tier="1"),
        },
        state=state,
        discord=main,
        channels={"defense": defense},
        classifier=FakeClassifier(),
        skip_dismissals=True,
        push=push,
    )
    assert [job.id for job, _, _ in defense.posts] == ["d1"]
    assert [job.id for job, _, _ in main.posts] == ["a1"]
    assert defense.forum_posts == [] and main.forum_posts[0][0] == "anthropic"
    assert push.batches == [["Software Engineer Intern, Summer 2027"]] and result.pushed == 1
    assert state.is_seen("d1", company="l3harris")


def test_defense_first_look_lists_every_job_in_the_channel() -> None:
    state = StateStore(FakeKV())
    main, defense = FakeDiscord(), _defense_channel()
    jobs = [
        make_job(id=f"n{i}", company="northrop-grumman", title=f"Software Engineer Intern {i}", url=f"https://x/n{i}")
        for i in range(7)
    ]
    scan(
        adapters=[FakeAdapter(jobs)],
        configs={"northrop-grumman": CompanyConfig(name="northrop-grumman", adapter="workday", channel="defense")},
        state=state,
        discord=main,
        channels={"defense": defense},
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )
    assert main.recaps == [] and main.forum_posts == [] and main.posts == []
    assert defense.recaps[0][0] == "northrop-grumman"
    assert [job.id for job in defense.forum_posts[0][1]] == [f"n{i}" for i in range(7)]
    assert state.is_bootstrapped("northrop-grumman")


def test_defense_company_without_new_jobs_does_not_repost_its_list() -> None:
    state = StateStore(FakeKV())
    state.mark_bootstrapped("rtx")
    jobs = [make_job(id=f"r{i}", company="rtx", title=f"Software Engineer Intern {i}", url=f"https://x/r{i}") for i in range(7)]
    for job in jobs:
        state.record_notification(job_id=job.id, company="rtx", title=job.title, url=job.url, message_id="m", channel_id="c")
    defense = _defense_channel()
    scan(
        adapters=[FakeAdapter(jobs)],
        configs={"rtx": CompanyConfig(name="rtx", adapter="workday", channel="defense")},
        state=state,
        discord=FakeDiscord(),
        channels={"defense": defense},
        classifier=FakeClassifier(),
        skip_dismissals=True,
    )
    assert defense.forum_posts == [] and defense.posts == []
