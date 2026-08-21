from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


API = "https://www.atlassian.com/endpoint/careers/listings"


class AtlassianAdapter:
    API = API

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        response = self.session.get(self.API, timeout=self.timeout)
        response.raise_for_status()
        return [self._normalize(raw) for raw in _job_dicts(_listings_json(response))]

    def _normalize(self, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id") or raw.get("jobId") or raw.get("slug") or raw.get("postingId")
        title = raw.get("title") or raw.get("name") or raw.get("text")
        location = raw.get("location") or raw.get("locations") or raw.get("subtitle")
        url = raw.get("url") or raw.get("absolute_url") or raw.get("applyUrl") or raw.get("link")
        if url and str(url).startswith("/"):
            url = f"https://www.atlassian.com{url}"
        elif not url and job_id:
            url = f"https://www.atlassian.com/company/careers/details/{job_id}"
        description = raw.get("description") or raw.get("content") or raw.get("jobDescription") or ""
        return Job(
            id=f"atlassian:{job_id}",
            company="atlassian",
            title=compact_text(str(title or "")),
            location=_location_text(location) or "Unspecified",
            url=compact_text(str(url or "")),
            jd_text=html_to_text(str(description)),
            posted_at=raw.get("posted_at") or raw.get("postedDate") or raw.get("createdAt"),
        )


def _listings_json(response: Any) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        status = getattr(response, "status_code", "?")
        text = str(getattr(response, "text", "") or "").strip()[:120]
        raise RuntimeError(
            f"Atlassian listings were not JSON (HTTP {status}): {text or 'empty body'}"
        ) from exc


def _job_dicts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("jobs", "listings", "data", "results", "values", "entities"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            jobs = value.get("jobs")
            if isinstance(jobs, dict):
                return [item for item in jobs.values() if isinstance(item, dict)]
            nested = _job_dicts(value)
            if nested:
                return nested
    return []


def _location_text(value: Any) -> str:
    if isinstance(value, str):
        return compact_text(value)
    if isinstance(value, list):
        parts = [_location_text(item) for item in value]
        return "; ".join(part for part in parts if part)
    if isinstance(value, dict):
        return compact_text(str(value.get("name") or value.get("city") or value.get("location") or ""))
    return ""
