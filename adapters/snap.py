from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


API = "https://careers.snap.com/api/jobs"


class SnapAdapter:
    API = API

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        response = self.session.get(self.API, timeout=self.timeout)
        response.raise_for_status()
        jobs: list[Job] = []
        for raw in _job_dicts(response.json()):
            jobs.append(self._normalize(raw))
        return jobs

    def _normalize(self, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id") or raw.get("jobId") or raw.get("reqId") or raw.get("slug")
        title = raw.get("title") or raw.get("name") or raw.get("text")
        location = (
            raw.get("location")
            or raw.get("locations")
            or raw.get("city")
            or raw.get("primary_location")
            or raw.get("offices")
        )
        url = raw.get("url") or raw.get("absolute_url") or raw.get("applyUrl") or raw.get("hostedUrl")
        if not url and job_id:
            url = f"https://careers.snap.com/job?id={job_id}"
        description = raw.get("description") or raw.get("content") or raw.get("jobDescription") or ""
        return Job(
            id=f"snap:{job_id}",
            company="snap",
            title=compact_text(str(title or "")),
            location=_location_text(location) or "Unspecified",
            url=compact_text(str(url or "")),
            jd_text=html_to_text(str(description)),
            posted_at=raw.get("posted_at") or raw.get("postedDate") or raw.get("updatedAt") or raw.get("createdAt"),
        )


def _job_dicts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [_unwrap_job(item) for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("jobs", "body", "data", "results", "jobPostings", "values"):
        value = payload.get(key)
        if isinstance(value, list):
            return [_unwrap_job(item) for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _job_dicts(value)
            if nested:
                return nested
    return []


def _unwrap_job(item: dict[str, Any]) -> dict[str, Any]:
    source = item.get("_source")
    if isinstance(source, dict):
        raw = dict(source)
        if not raw.get("id") and item.get("_id") is not None:
            raw["id"] = item["_id"]
        return raw
    return item


def _location_text(value: Any) -> str:
    if isinstance(value, str):
        return compact_text(value)
    if isinstance(value, list):
        parts = [_location_text(item) for item in value]
        return "; ".join(part for part in parts if part)
    if isinstance(value, dict):
        return compact_text(
            str(
                value.get("name")
                or value.get("displayName")
                or value.get("city")
                or value.get("location")
                or ""
            )
        )
    return ""
