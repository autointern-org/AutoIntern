from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


API = "https://www-api.ibm.com/search/api/v2"
BODY = {
    "appId": "careers",
    "scopes": ["careers2"],
    "_source": ["url", "title", "field_keyword_05", "field_keyword_08", "field_keyword_18"],
    "query": {"bool": {"must": [{"match": {"title": {"query": "Intern"}}}]}},
    "size": 50,
}


class IBMAdapter:
    API = API

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        response = self.session.post(self.API, json=BODY, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        hits = (payload.get("hits") or {}).get("hits") if isinstance(payload, dict) else None
        jobs: list[Job] = []
        if not isinstance(hits, list):
            return jobs
        for raw in hits:
            if isinstance(raw, dict):
                jobs.append(self._normalize(raw))
        return jobs

    def _normalize(self, raw: dict[str, Any]) -> Job:
        source = raw.get("_source") if isinstance(raw.get("_source"), dict) else raw
        job_id = raw.get("_id") or source.get("id") or source.get("url")
        title = source.get("title")
        location = _join_keywords(source.get("field_keyword_05"))
        country = _join_keywords(source.get("field_keyword_18"))
        if location and country and country not in location:
            location = f"{location}, {country}"
        url = source.get("url") or ""
        if url and str(url).startswith("/"):
            url = f"https://www.ibm.com{url}"
        return Job(
            id=f"ibm:{job_id}",
            company="ibm",
            title=compact_text(str(title or "")),
            location=compact_text(location) or "Unspecified",
            url=compact_text(str(url)),
            jd_text=html_to_text(_join_keywords(source.get("field_keyword_08"))),
            posted_at=source.get("date") or source.get("posted_at"),
        )


def _join_keywords(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(compact_text(str(item)) for item in value if item)
    if value in (None, ""):
        return ""
    return compact_text(str(value))
