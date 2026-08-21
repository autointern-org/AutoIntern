from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


API = "https://www-api.ibm.com/search/api/v2"
PAGE_SIZE = 100
MAX_PAGES = 20
BODY = {
    "appId": "careers",
    "scopes": ["careers2"],
    "_source": ["url", "title", "field_keyword_05", "field_keyword_08", "field_keyword_18"],
    "query": {"bool": {"must": [{"match": {"title": {"query": "Intern"}}}]}},
}


class IBMAdapter:
    API = API

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        seen: set[str] = set()
        offset = 0
        for _ in range(MAX_PAGES):
            body = {**BODY, "size": PAGE_SIZE, "from": offset}
            response = self.session.post(self.API, json=body, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            hits = (payload.get("hits") or {}).get("hits") if isinstance(payload, dict) else None
            if not isinstance(hits, list) or not hits:
                break
            for raw in hits:
                if not isinstance(raw, dict):
                    continue
                job = self._normalize(raw)
                if job.id in seen:
                    continue
                seen.add(job.id)
                jobs.append(job)
            offset += len(hits)
            total = _hit_total(payload)
            if len(hits) < PAGE_SIZE:
                break
            if total is not None and offset >= total:
                break
        return jobs

    def _normalize(self, raw: dict[str, Any]) -> Job:
        source = raw.get("_source") if isinstance(raw.get("_source"), dict) else raw
        url = source.get("url") or ""
        if url and str(url).startswith("/"):
            url = f"https://www.ibm.com{url}"
        job_id = _stable_job_id(raw, source, str(url))
        title = source.get("title")
        location = _join_keywords(source.get("field_keyword_05"))
        country = _join_keywords(source.get("field_keyword_18"))
        if location and country and country not in location:
            location = f"{location}, {country}"
        return Job(
            id=f"ibm:{job_id}",
            company="ibm",
            title=compact_text(str(title or "")),
            location=compact_text(location) or "Unspecified",
            url=compact_text(str(url)),
            jd_text=html_to_text(_join_keywords(source.get("field_keyword_08"))),
            posted_at=source.get("date") or source.get("posted_at"),
        )


def _stable_job_id(raw: dict[str, Any], source: dict[str, Any], url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("jobId", "jobid", "job_id"):
        values = query.get(key) or []
        if values and values[0]:
            return str(values[0])
    segments = [part for part in parsed.path.split("/") if part]
    if segments:
        last = segments[-1]
        if last.lower() not in {"job", "jobs", "jobdetail", "careers"}:
            return last
    return str(raw.get("_id") or source.get("id") or url)


def _hit_total(payload: dict[str, Any]) -> int | None:
    total = (payload.get("hits") or {}).get("total") if isinstance(payload, dict) else None
    if isinstance(total, dict):
        value = total.get("value")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if isinstance(total, int):
        return total
    return None


def _join_keywords(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(compact_text(str(item)) for item in value if item)
    if value in (None, ""):
        return ""
    return compact_text(str(value))
