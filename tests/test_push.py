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


def test_catch_up_postings_push_at_lower_priority() -> None:
    old = job(2, posted_at=(datetime.now(UTC) - timedelta(days=30)).date().isoformat())
    payload = job_payload(old)
    assert payload["priority"] == 3 and "catch-up" in payload["message"]


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
