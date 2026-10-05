from __future__ import annotations

import os
from typing import Any

import requests

from adapters.base import Job
from core.postdate import posted_age_days


NTFY_SERVER = "https://ntfy.sh"
MAX_PUSHES_PER_RUN = 8
CATCHUP_DAYS = 7


def is_priority(job: Job, *, tier1: bool) -> bool:
    """Drop-everything postings: tier-1 company, Summer 2027 term, not PhD-only.
    Location is already US or unknown by the time a job reaches posting."""
    return tier1 and job.term_flag == "term_target" and job.degree_flag != "phd_likely"


class PushNotifier:
    """Phone push through ntfy (https://ntfy.sh). The topic name is the only
    secret: anyone who knows it can read the stream, so it lives in a GitHub
    secret and is long and random."""

    def __init__(
        self,
        topic: str | None,
        *,
        server: str = NTFY_SERVER,
        dry_run: bool = False,
        timeout: int = 15,
        session: requests.Session | None = None,
    ) -> None:
        self.topic = (topic or "").strip()
        self.server = server.rstrip("/")
        self.dry_run = dry_run
        self.timeout = timeout
        self.session = session or requests.Session()
        self.sent = 0

    @classmethod
    def from_env(cls, *, dry_run: bool = False) -> "PushNotifier":
        return cls(os.getenv("NTFY_TOPIC"), server=os.getenv("NTFY_SERVER") or NTFY_SERVER, dry_run=dry_run)

    @property
    def enabled(self) -> bool:
        return bool(self.topic)

    def push_jobs(self, jobs: list[Job]) -> int:
        if not jobs or not self.enabled:
            return 0
        room = MAX_PUSHES_PER_RUN - self.sent
        if room <= 0:
            return 0
        individual = jobs if len(jobs) <= room else jobs[: room - 1]
        pushed = 0
        for job in individual:
            if self._publish(job_payload(job)):
                pushed += 1
        overflow = jobs[len(individual):]
        if overflow and self._publish(summary_payload(overflow)):
            pushed += 1
        self.sent += pushed
        return pushed

    def _publish(self, payload: dict[str, Any]) -> bool:
        payload = {"topic": self.topic, **payload}
        if self.dry_run:
            print(f"[dry-run] push: {payload.get('title')}")
            return True
        try:
            response = self.session.post(self.server, json=payload, timeout=self.timeout)
            response.raise_for_status()
            return True
        except Exception as exc:
            print(f"[push] warning: ntfy publish failed: {exc}")
            return False


def job_payload(job: Job) -> dict[str, Any]:
    age = posted_age_days(job.posted_at)
    catch_up = age is not None and age >= CATCHUP_DAYS
    when = f"posted {age}d ago (catch-up)" if catch_up else ("posted today" if age == 0 else (f"posted {age}d ago" if age is not None else "just seen"))
    return {
        "title": f"{job.company}: {job.title}"[:250],
        "message": f"{job.location} · {when}",
        "click": job.url,
        "priority": 3 if catch_up else 5,
        "tags": ["hourglass" if catch_up else "rotating_light"],
        "actions": [{"action": "view", "label": "Open posting", "url": job.url}] if job.url else [],
    }


def summary_payload(jobs: list[Job]) -> dict[str, Any]:
    lines = [f"• {job.company}: {job.title}" for job in jobs[:15]]
    if len(jobs) > 15:
        lines.append(f"…and {len(jobs) - 15} more")
    return {
        "title": f"{len(jobs)} more priority postings: see Discord",
        "message": "\n".join(lines),
        "priority": 4,
        "tags": ["rotating_light"],
    }
