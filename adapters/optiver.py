from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


API = "https://www.optiver.com/en/api/v1/jobs?level=internship"


class OptiverAdapter:
    API = API

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        response = self.session.get(self.API, timeout=self.timeout)
        response.raise_for_status()
        return [self._normalize(raw) for raw in _job_dicts(response.json())]

    def _normalize(self, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id") or raw.get("slug") or raw.get("jobId")
        title = _maybe_rendered(raw.get("title")) or raw.get("name")
        location = raw.get("location") or raw.get("office") or raw.get("city")
        url = raw.get("url") or raw.get("link") or raw.get("apply_url") or raw.get("absolute_url")
        if url and str(url).startswith("/"):
            url = f"https://www.optiver.com{url}"
        elif not url and raw.get("slug"):
            url = f"https://www.optiver.com/join-us/jobs/{raw.get('slug')}/"
        description = raw.get("description") or raw.get("content") or raw.get("excerpt") or ""
        if isinstance(description, dict):
            description = description.get("rendered") or ""
        return Job(
            id=f"optiver:{job_id}",
            company="optiver",
            title=compact_text(str(title or "")),
            location=_location_text(location) or "Unspecified",
            url=compact_text(str(url or "")),
            jd_text=html_to_text(str(description)),
            posted_at=raw.get("posted_at") or raw.get("date") or raw.get("created_at"),
        )


def _job_dicts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("jobs", "data", "results", "items", "values"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _maybe_rendered(value: Any) -> str:
    if isinstance(value, dict):
        return compact_text(str(value.get("rendered") or value.get("name") or ""))
    return compact_text(str(value or ""))


def _location_text(value: Any) -> str:
    if isinstance(value, str):
        return compact_text(value)
    if isinstance(value, list):
        parts = [_location_text(item) for item in value]
        return "; ".join(part for part in parts if part)
    if isinstance(value, dict):
        return compact_text(
            str(value.get("title") or value.get("name") or value.get("city") or value.get("location") or "")
        )
    return ""
