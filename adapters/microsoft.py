from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text


class MicrosoftAdapter:
    API = "https://gcsservices.careers.microsoft.com/search/api/v1/search?q=intern&l=en_us&pg=1&pgSz=20&o=Recent"

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()

    def fetch(self) -> list[Job]:
        response = self.session.get(self.API, timeout=self.timeout)
        response.raise_for_status()
        return [self._normalize(raw) for raw in _find_job_dicts(response.json())]

    def _normalize(self, raw: dict[str, Any]) -> Job:
        job_id = raw.get("jobId") or raw.get("id") or raw.get("job_id") or raw.get("requisitionId")
        title = raw.get("title") or raw.get("postingTitle") or raw.get("name")
        location = _join_location(raw)
        url = raw.get("url") or raw.get("applyUrl") or raw.get("externalPath")
        if url and str(url).startswith("/"):
            url = f"https://jobs.careers.microsoft.com/global/en/job{url}"
        elif job_id and not url:
            url = f"https://jobs.careers.microsoft.com/global/en/job/{job_id}"

        description = (
            raw.get("description")
            or raw.get("jobDescription")
            or raw.get("overview")
            or raw.get("summary")
            or ""
        )

        return Job(
            id=f"microsoft:{job_id}",
            company="microsoft",
            title=compact_text(title),
            location=location or "Unspecified",
            url=compact_text(str(url or "")),
            jd_text=html_to_text(str(description)),
            posted_at=raw.get("postedDate") or raw.get("postingDate") or raw.get("createdDate"),
        )


def _find_job_dicts(data: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            title_like = node.get("title") or node.get("postingTitle") or node.get("name")
            id_like = node.get("jobId") or node.get("id") or node.get("requisitionId")
            if title_like and id_like:
                candidates.append(node)
                return
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(data)
    return candidates


def _join_location(raw: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("city", "state", "country", "countryRegion", "primaryWorkLocation"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())

    locations = raw.get("locations")
    if isinstance(locations, list):
        for item in locations:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.extend(
                    str(item[key])
                    for key in ("city", "state", "country", "countryRegion", "displayName")
                    if item.get(key)
                )
    elif isinstance(locations, str):
        parts.append(locations)

    seen: set[str] = set()
    deduped = []
    for part in parts:
        cleaned = compact_text(str(part))
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return ", ".join(deduped)
