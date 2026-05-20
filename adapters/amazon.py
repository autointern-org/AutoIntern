from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text


class AmazonAdapter:
    API = "https://www.amazon.jobs/en/search.json?normalized_country_code[]=USA&base_query=intern&job_type[]=Internship"

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()

    def fetch(self) -> list[Job]:
        response = self.session.get(self.API, timeout=self.timeout)
        response.raise_for_status()
        jobs = response.json().get("jobs") or []
        return [self._normalize(raw) for raw in jobs if isinstance(raw, dict)]

    def _normalize(self, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id") or raw.get("job_id") or raw.get("slug")
        url = raw.get("job_path") or raw.get("url") or raw.get("apply_url")
        if url and str(url).startswith("/"):
            url = f"https://www.amazon.jobs{url}"
        description = raw.get("description") or raw.get("basic_qualifications") or raw.get("preferred_qualifications") or ""
        return Job(
            id=f"amazon:{job_id}",
            company="amazon",
            title=compact_text(raw.get("title")),
            location=compact_text(raw.get("normalized_location") or raw.get("location") or "Unspecified"),
            url=compact_text(str(url or "")),
            jd_text=html_to_text(description),
            posted_at=raw.get("posted_date") or raw.get("updated_time"),
        )
