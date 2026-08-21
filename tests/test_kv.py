from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tests.test_pipeline import FakeKV

from core.kv import CloudflareKV, HEALTH_TTL_SECONDS, SEEN_LIST_TTL_SECONDS, StateStore, now_iso
from core.kv import PRUNE_AFTER_MISSING_SECONDS


def test_record_notification_batches_into_one_seen_put() -> None:
    kv = FakeKV()
    state = StateStore(kv)
    state.record_notification(
        job_id="greenhouse:stripe:1",
        company="Stripe",
        title="Intern",
        url="https://example.com/1",
        message_id="m1",
        channel_id="c1",
    )
    state.record_notification(
        job_id="greenhouse:stripe:2",
        company="stripe",
        title="Intern 2",
        url="https://example.com/2",
        message_id="m2",
        channel_id="c1",
    )
    assert kv.puts == []
    state.flush_seen("stripe")
    assert kv.puts == [("seen:stripe", SEEN_LIST_TTL_SECONDS)]
    jobs = kv.values["seen:stripe"]["jobs"]
    assert set(jobs) == {"greenhouse:stripe:1", "greenhouse:stripe:2"}
    kv.clear_io()
    state.flush_seen("stripe")
    assert kv.puts == []


def test_record_health_skips_unchanged_counts() -> None:
    kv = FakeKV()
    state = StateStore(kv)
    state.record_health("anthropic", fetched=3, matched=1)
    state.flush_health()
    assert kv.puts == [("health:all", HEALTH_TTL_SECONDS)]
    kv.clear_io()
    state.record_health("anthropic", fetched=3, matched=1)
    assert kv.puts == []
    state.record_health("anthropic", fetched=4, matched=1)
    state.flush_health()
    assert kv.puts == [("health:all", HEALTH_TTL_SECONDS)]
    assert kv.values["health:all"]["companies"]["anthropic"]["fetched"] == 4


def test_is_seen_reads_legacy_job_key_once_and_migrates_in_memory() -> None:
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
    assert state.is_seen(job_id, company="stripe")
    assert state.is_seen(job_id, company="stripe")
    assert kv.gets.count(f"job:{job_id}") == 1
    assert kv.gets.count("seen:stripe") == 1
    assert "seen:stripe" not in kv.values
    state.flush_seen("stripe")
    assert job_id in kv.values["seen:stripe"]["jobs"]


def test_is_dismissed_true_for_seen_flag_dismissed_key_and_legacy_job() -> None:
    kv = FakeKV()
    state = StateStore(kv)
    state.record_notification(
        job_id="job-a",
        company="anthropic",
        title="Intern",
        url="https://example.com/a",
        message_id="m-a",
        channel_id="c1",
    )
    state.mark_dismissed("job-a", company="anthropic")
    assert state.is_dismissed("job-a", company="anthropic")

    kv.values["dismissed:job-b"] = {"job_id": "job-b", "dismissed_at": now_iso()}
    assert state.is_dismissed("job-b", company="anthropic")

    kv.values["job:job-c"] = {
        "job_id": "job-c",
        "company": "anthropic",
        "title": "Intern",
        "url": "https://example.com/c",
        "message_id": "m-c",
        "channel_id": "c1",
        "dismissed": True,
        "notified_at": now_iso(),
    }
    assert state.is_dismissed("job-c", company="anthropic")


def test_list_undismissed_reads_seen_docs_and_leftover_job_keys() -> None:
    kv = FakeKV()
    state = StateStore(kv)
    state.record_notification(
        job_id="seen-job",
        company="anthropic",
        title="Intern",
        url="https://example.com/seen",
        message_id="m-seen",
        channel_id="c1",
    )
    state.flush_seen("anthropic")
    kv.values["job:legacy-job"] = {
        "job_id": "legacy-job",
        "company": "anthropic",
        "title": "Intern",
        "url": "https://example.com/legacy",
        "message_id": "m-legacy",
        "channel_id": "c1",
        "dismissed": False,
        "notified_at": now_iso(),
    }
    kv.values["job:seen-job"] = {
        "job_id": "seen-job",
        "company": "anthropic",
        "title": "Intern",
        "url": "https://example.com/seen",
        "message_id": "m-seen-dup",
        "channel_id": "c1",
        "dismissed": False,
        "notified_at": now_iso(),
    }
    kv.clear_io()

    notes = state.list_undismissed_notifications(days=7)
    ids = {note["job_id"] for note in notes}
    assert ids == {"seen-job", "legacy-job"}
    assert kv.gets.count("job:seen-job") == 0
    assert kv.gets.count("job:legacy-job") == 1


def test_prune_seen_only_after_missing_for_a_week() -> None:
    kv = FakeKV()
    state = StateStore(kv)
    for job_id in ("keep", "drop"):
        state.record_notification(
            job_id=job_id,
            company="anthropic",
            title="Intern",
            url=f"https://example.com/{job_id}",
            message_id=f"m-{job_id}",
            channel_id="c1",
        )
    state.flush_seen("anthropic")
    kv.clear_io()
    t0 = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)

    state.prune_seen("anthropic", {"keep", "drop"}, now=t0)
    state.flush_seen("anthropic")
    assert kv.puts == []

    # First miss only stamps missing_since; nothing is forgotten.
    state.prune_seen("anthropic", {"keep"}, now=t0)
    state.flush_seen("anthropic")
    assert kv.puts == [("seen:anthropic", SEEN_LIST_TTL_SECONDS)]
    assert set(kv.values["seen:anthropic"]["jobs"]) == {"keep", "drop"}
    assert kv.values["seen:anthropic"]["jobs"]["drop"]["missing_since"] == t0.isoformat()
    kv.clear_io()

    # Repeated misses inside the window do not rewrite KV or forget the job.
    state.prune_seen("anthropic", {"keep"}, now=t0 + timedelta(hours=6))
    state.flush_seen("anthropic")
    assert kv.puts == []
    assert "drop" in kv.values["seen:anthropic"]["jobs"]

    # Reappearing clears the stamp, so a flaky board never re-pings.
    state.prune_seen("anthropic", {"keep", "drop"}, now=t0 + timedelta(days=3))
    state.flush_seen("anthropic")
    assert kv.puts == [("seen:anthropic", SEEN_LIST_TTL_SECONDS)]
    assert "missing_since" not in kv.values["seen:anthropic"]["jobs"]["drop"]
    kv.clear_io()

    # Gone for the full window -> forgotten.
    t1 = t0 + timedelta(days=3)
    state.prune_seen("anthropic", {"keep"}, now=t1)
    state.prune_seen("anthropic", {"keep"}, now=t1 + timedelta(seconds=PRUNE_AFTER_MISSING_SECONDS))
    state.flush_seen("anthropic")
    assert set(kv.values["seen:anthropic"]["jobs"]) == {"keep"}


def test_prune_seen_drops_legacy_misses_counter() -> None:
    kv = FakeKV()
    kv.values["seen:anthropic"] = {
        "company": "anthropic",
        "jobs": {"old": {"title": "Intern", "url": "https://example.com/old", "misses": 1}},
    }
    state = StateStore(kv)
    state.prune_seen("anthropic", {"old"})
    state.flush_seen("anthropic")
    assert "misses" not in kv.values["seen:anthropic"]["jobs"]["old"]


def test_is_seen_true_for_same_url_new_id() -> None:
    kv = FakeKV()
    state = StateStore(kv)
    state.record_notification(
        job_id="ibm:hash-old",
        company="ibm",
        title="Data Engineer Intern",
        url="https://careers.ibm.com/careers/JobDetail?jobId=128645",
        message_id="m1",
        channel_id="c1",
    )
    state.flush_seen("ibm")

    assert state.is_seen(
        "ibm:128645",
        company="ibm",
        url="https://careers.ibm.com/careers/JobDetail?jobId=128645",
    )
    state.flush_seen("ibm")
    assert "ibm:128645" in kv.values["seen:ibm"]["jobs"]


def test_wipe_scan_state_deletes_prefixed_keys() -> None:
    kv = FakeKV()
    kv.values["job:old"] = {"job_id": "old"}
    kv.values["seen:stripe"] = {"jobs": {}}
    kv.values["bootstrapped:stripe"] = {"company": "stripe"}
    kv.values["keep-me"] = {"ok": True}

    from core.kv import wipe_scan_state

    deleted = wipe_scan_state(kv)
    assert deleted == 3
    assert "keep-me" in kv.values
    assert "job:old" not in kv.values
    assert "seen:stripe" not in kv.values
    assert "bootstrapped:stripe" not in kv.values


def test_cloudflare_kv_retries_read_timeout(monkeypatch: Any) -> None:
    import requests

    monkeypatch.setattr("core.kv.sleep", lambda *_args, **_kwargs: None)

    class FlakySession:
        def __init__(self) -> None:
            self.gets = 0

        def get(self, *_args: Any, **_kwargs: Any) -> Any:
            self.gets += 1
            if self.gets < 3:
                raise requests.exceptions.ReadTimeout("timeout")

            class Response:
                status_code = 200
                text = '{"fetched": 4}'

                def json(self) -> dict[str, int]:
                    return {"fetched": 4}

                def raise_for_status(self) -> None:
                    return None

            return Response()

    session = FlakySession()
    kv = CloudflareKV(account_id="a", namespace_id="n", api_token="t", session=session)
    assert kv.get_json("health:x") == {"fetched": 4}
    assert session.gets == 3
