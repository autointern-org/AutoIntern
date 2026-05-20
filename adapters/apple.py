from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text


class AppleAdapter:
    API = "https://jobs.apple.com/api/role/search"

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()

    def fetch(self) -> list[Job]:
        response = self.session.post(
            self.API,
            json={"query": "intern", "filters": {"locations": ["postLocation-USA"]}},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [self._normalize(raw) for raw in _find_jobs(response.json())]

    def _normalize(self, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id") or raw.get("positionId") or raw.get("jobId") or raw.get("postingNumber")
        title = raw.get("postingTitle") or raw.get("title") or raw.get("name")
        url = raw.get("url") or raw.get("jobDetailUrl") or raw.get("canonicalUrl")
        if url and str(url).startswith("/"):
            url = f"https://jobs.apple.com{url}"
        elif not url and job_id:
            url = f"https://jobs.apple.com/en-us/details/{job_id}"
        location = raw.get("locations") or raw.get("location") or raw.get("postLocation")
        return Job(
            id=f"apple:{job_id}",
            company="apple",
            title=compact_text(title),
            location=_location_text(location) or "Unspecified",
            url=compact_text(str(url or "")),
            jd_text=html_to_text(raw.get("description") or raw.get("summary") or raw.get("team") or ""),
            posted_at=raw.get("postDate") or raw.get("postedDate") or raw.get("createdDate"),
        )


def _find_jobs(data: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            title_like = node.get("postingTitle") or node.get("title") or node.get("name")
            id_like = node.get("id") or node.get("positionId") or node.get("jobId") or node.get("postingNumber")
            if title_like and id_like:
                found.append(node)
                return
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(data)
    return found


def _location_text(value: Any) -> str:
    if isinstance(value, str):
        return compact_text(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("name") or item.get("displayName") or item.get("city") or "")
        return ", ".join(compact_text(part) for part in parts if compact_text(part))
    if isinstance(value, dict):
        return compact_text(value.get("name") or value.get("displayName") or value.get("city"))
    return ""
