from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from adapters.base import Job
from core.push import MAX_PUSHES_PER_RUN, PushNotifier, is_priority, job_payload


class FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, json: dict[str, Any], timeout: int) -> FakeResponse:
        self.posts.append({"url": url, "json": json})
        return FakeResponse(self.status_code)


def job(i: int = 0, **kw: Any) -> Job:
    values = dict(
        id=f"g:{i}",
        company="google",
        title=f"Software Engineering Intern {i}",
        location="Mountain View, CA, USA",
        url=f"https://example.com/{i}",
        jd_text="",
        posted_at=datetime.now(UTC).date().isoformat(),
        term_flag="term_target",
        degree_flag="undergrad_ok",
    )
    values.update(kw)
    return Job(**values)


def test_priority_rule() -> None:
    assert is_priority(job(), tier1=True)
    assert not is_priority(job(), tier1=False)
    assert not is_priority(job(term_flag="term_unknown"), tier1=True)
    assert not is_priority(job(degree_flag="phd_likely"), tier1=True)


def test_push_publishes_json_with_topic_and_click() -> None:
    session = FakeSession()
    notifier = PushNotifier("secret-topic", session=session)
    assert notifier.push_jobs([job(1)]) == 1
    post = session.posts[0]
    assert post["url"] == "https://ntfy.sh"
    assert post["json"]["topic"] == "secret-topic"
    assert post["json"]["title"] == "google: Software Engineering Intern 1"
    assert post["json"]["click"] == "https://example.com/1"
    assert post["json"]["priority"] == 5


def test_catch_up_postings_collapse_into_one_summary_per_run() -> None:
    old = [job(i, posted_at=(datetime.now(UTC) - timedelta(days=30)).date().isoformat()) for i in range(20, 25)]
    assert job_payload(old[0])["priority"] == 3 and "catch-up" in job_payload(old[0])["message"]
    session = FakeSession()
    notifier = PushNotifier("t", session=session)
    assert notifier.push_jobs(old + [job(1)]) == 2
    titles = [post["json"]["title"] for post in session.posts]
    assert titles[0] == "google: Software Engineering Intern 1"
    assert titles[1] == "5 older priority postings became visible: see Discord"
    assert notifier.push_jobs(old) == 0  # one catch-up summary per run


def test_push_caps_per_run_and_summarizes_overflow() -> None:
    session = FakeSession()
    notifier = PushNotifier("t", session=session)
    sent = notifier.push_jobs([job(i) for i in range(MAX_PUSHES_PER_RUN + 5)])
    assert sent == MAX_PUSHES_PER_RUN
    assert len(session.posts) == MAX_PUSHES_PER_RUN
    assert "more priority postings" in session.posts[-1]["json"]["title"]
    assert notifier.push_jobs([job(99)]) == 0


def test_push_disabled_without_topic_and_survives_errors() -> None:
    assert PushNotifier("", session=FakeSession()).push_jobs([job()]) == 0
    failing = PushNotifier("t", session=FakeSession(status_code=500))
    assert failing.push_jobs([job()]) == 0
