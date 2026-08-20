from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


INTERN_TYPE = 3
API = "https://www.tesla.com/cua-api/apps/careers/state?region=5"


class TeslaAdapter:
    API = API

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        response = self.session.get(self.API, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        listings = payload.get("listings") if isinstance(payload, dict) else payload
        jobs: list[Job] = []
        if not isinstance(listings, list):
            return jobs
        for raw in listings:
            if not isinstance(raw, dict):
                continue
            try:
                listing_type = int(raw.get("y"))
            except (TypeError, ValueError):
                listing_type = None
            if listing_type != INTERN_TYPE:
                continue
            jobs.append(self._normalize(raw))
        return jobs

    def _normalize(self, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id") or raw.get("jobId")
        title = raw.get("t") or raw.get("title") or raw.get("name")
        location = raw.get("l") or raw.get("location") or "Unspecified"
        description = raw.get("d") if isinstance(raw.get("d"), str) else raw.get("description") or ""
        url = raw.get("url") or raw.get("applyUrl")
        if not url and job_id:
            url = f"https://www.tesla.com/careers/search/job/{job_id}"
        return Job(
            id=f"tesla:{job_id}",
            company="tesla",
            title=compact_text(str(title or "")),
            location=compact_text(str(location)),
            url=compact_text(str(url or "")),
            jd_text=html_to_text(str(description or "")),
            posted_at=raw.get("posted") or raw.get("posted_at"),
        )
