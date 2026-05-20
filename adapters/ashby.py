from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text


class AshbyAdapter:
    API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"

    def __init__(
        self,
        org_slugs: Iterable[str],
        *,
        company_names: dict[str, str] | None = None,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.org_slugs = list(org_slugs)
        self.company_names = company_names or {}
        self.timeout = timeout
        self.session = session or requests.Session()

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for slug in self.org_slugs:
            try:
                response = self.session.get(self.API.format(slug=slug), timeout=self.timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                print(f"[ashby] failed to fetch {slug}: {exc}")
                continue
            for raw in response.json().get("jobs", []):
                jobs.append(self._normalize(slug, raw))
        return jobs

    def _normalize(self, slug: str, raw: dict[str, Any]) -> Job:
        location = raw.get("location")
        if isinstance(location, dict):
            location_text = location.get("name") or location.get("displayName") or location.get("location")
        else:
            location_text = location

        job_id = raw.get("id") or raw.get("jobId") or raw.get("externalLinkId")
        return Job(
            id=f"ashby:{slug}:{job_id}",
            company=self.company_names.get(slug, slug),
            title=compact_text(raw.get("title")),
            location=compact_text(str(location_text or "Unspecified")),
            url=compact_text(
                raw.get("jobUrl")
                or raw.get("externalLink")
                or raw.get("applyUrl")
                or f"https://jobs.ashbyhq.com/{slug}/{job_id}"
            ),
            jd_text=html_to_text(raw.get("descriptionHtml") or raw.get("descriptionPlain") or raw.get("description")),
            posted_at=raw.get("publishedAt") or raw.get("postedAt") or raw.get("createdAt"),
        )
