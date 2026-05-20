from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text


class GoogleAdapter:
    API = "https://careers.google.com/api/v3/search/?q=intern&jex=INTERN"

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()

    def fetch(self) -> list[Job]:
        response = self.session.get(self.API, timeout=self.timeout)
        response.raise_for_status()
        return [self._normalize(raw) for raw in _find_jobs(response.json())]

    def _normalize(self, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id") or raw.get("job_id") or raw.get("req_id") or raw.get("slug")
        title = raw.get("title") or raw.get("name")
        locations = raw.get("locations") or raw.get("locations_display") or raw.get("location")
        location = _location_text(locations)
        url = raw.get("apply_url") or raw.get("url") or raw.get("share_url")
        if url and str(url).startswith("/"):
            url = f"https://careers.google.com{url}"
        elif not url and job_id:
            url = f"https://careers.google.com/jobs/results/{job_id}/"
        jd_text = raw.get("description") or raw.get("qualifications") or raw.get("responsibilities") or ""
        return Job(
            id=f"google:{job_id}",
            company="google",
            title=compact_text(title),
            location=location or "Unspecified",
            url=compact_text(str(url or "")),
            jd_text=html_to_text(_join_nested_text(jd_text)),
            posted_at=raw.get("publish_date") or raw.get("posted_at") or raw.get("date"),
        )


def _find_jobs(data: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            title_like = node.get("title") or node.get("name")
            id_like = node.get("id") or node.get("job_id") or node.get("req_id")
            if title_like and id_like and ("location" in node or "locations" in node or "locations_display" in node):
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
                parts.append(item.get("display") or item.get("name") or item.get("city") or "")
        return ", ".join(compact_text(part) for part in parts if compact_text(part))
    if isinstance(value, dict):
        return compact_text(value.get("display") or value.get("name") or value.get("city"))
    return ""


def _join_nested_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_join_nested_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_join_nested_text(item) for item in value.values())
    return ""
