from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


BASE_API = (
    "https://www.amazon.jobs/en/search.json"
    "?normalized_country_code[]=USA&base_query=intern&result_limit=100&sort=recent"
)


class AmazonAdapter:
    API = BASE_API

    def __init__(
        self,
        companies: Iterable[str] | None = None,
        *,
        slugs: dict[str, str] | None = None,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.companies = list(companies) if companies is not None else ["amazon"]
        self.slugs = slugs or {}
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for company in self.companies:
            url = BASE_API
            if _is_aws(company, self.slugs.get(company)):
                url = f"{BASE_API}&business_category[]=aws"
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            rows = response.json().get("jobs") or []
            for raw in rows:
                if isinstance(raw, dict):
                    jobs.append(self._normalize(company, raw))
        return jobs

    def _normalize(self, company: str, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id") or raw.get("job_id") or raw.get("slug")
        url = raw.get("job_path") or raw.get("url") or raw.get("apply_url")
        if url and str(url).startswith("/"):
            url = f"https://www.amazon.jobs{url}"
        description = (
            raw.get("description")
            or raw.get("basic_qualifications")
            or raw.get("preferred_qualifications")
            or ""
        )
        return Job(
            id=f"amazon:{company}:{job_id}",
            company=company,
            title=compact_text(raw.get("title")),
            location=compact_text(raw.get("normalized_location") or raw.get("location") or "Unspecified"),
            url=compact_text(str(url or "")),
            jd_text=html_to_text(description),
            posted_at=raw.get("posted_date") or raw.get("updated_time"),
        )


def _is_aws(company: str, slug: str | None) -> bool:
    token = (slug or company).lower().replace(" ", "-")
    return token in {"aws", "amazon-web-services", "amazonwebservices"}
