from __future__ import annotations

import re
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
_GENERIC_PATH_SEGMENTS = frozenset(
    {"careers", "en-us", "job", "jobdetail", "jobs", "search", "us-en"}
)
_JOB_ID_SEGMENT_RE = re.compile(r"^[\w-]+$")


class IBMAdapter:
    API = API

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        jobs_by_id: dict[str, Job] = {}
        offset = 0
        total: int | None = None

        for _ in range(MAX_PAGES):
            body = {**BODY, "size": PAGE_SIZE, "from": offset}
            response = self.session.post(self.API, json=body, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            hits_block = payload.get("hits") if isinstance(payload, dict) else None
            if not isinstance(hits_block, dict):
                break

            if total is None:
                total = _total_hits(hits_block.get("total"))

            hits = hits_block.get("hits")
            if not isinstance(hits, list) or not hits:
                break

            for raw in hits:
                if isinstance(raw, dict):
                    job = self._normalize(raw)
                    jobs_by_id[job.id] = job

            if len(hits) < PAGE_SIZE:
                break
            if total is not None and offset + PAGE_SIZE >= total:
                break
            offset += PAGE_SIZE

        return list(jobs_by_id.values())

    def _normalize(self, raw: dict[str, Any]) -> Job:
        source = raw.get("_source") if isinstance(raw.get("_source"), dict) else raw
        title = source.get("title")
        location = _join_keywords(source.get("field_keyword_05"))
        country = _join_keywords(source.get("field_keyword_18"))
        if location and country and country not in location:
            location = f"{location}, {country}"
        url = source.get("url") or ""
        job_id = _job_id_from_hit(raw, source, str(url))
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


def _total_hits(total: Any) -> int | None:
    if isinstance(total, dict):
        value = total.get("value")
        return int(value) if isinstance(value, int) else None
    if isinstance(total, int):
        return total
    return None


def _job_id_from_hit(raw: dict[str, Any], source: dict[str, Any], url: str) -> str:
    if url:
        parsed = urlparse(url)
        for key in ("jobId", "jobid"):
            values = parse_qs(parsed.query).get(key)
            if values and values[0]:
                return values[0]

        segments = [segment for segment in parsed.path.split("/") if segment]
        if segments:
            last = segments[-1]
            if _looks_like_job_id(last):
                return last

    return str(raw.get("_id") or source.get("id") or source.get("url") or "")


def _looks_like_job_id(segment: str) -> bool:
    if segment.lower() in _GENERIC_PATH_SEGMENTS:
        return False
    if segment.isdigit():
        return True
    if not _JOB_ID_SEGMENT_RE.fullmatch(segment):
        return False
    return "-" in segment or "_" in segment or any(char.isdigit() for char in segment)


def _join_keywords(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(compact_text(str(item)) for item in value if item)
    if value in (None, ""):
        return ""
    return compact_text(str(value))
