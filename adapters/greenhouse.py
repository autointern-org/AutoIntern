from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text


class GreenhouseAdapter:
    API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    def __init__(
        self,
        board_slugs: Iterable[str],
        *,
        company_names: dict[str, str] | None = None,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.board_slugs = list(board_slugs)
        self.company_names = company_names or {}
        self.timeout = timeout
        self.session = session or requests.Session()

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for slug in self.board_slugs:
            try:
                response = self.session.get(self.API.format(slug=slug), timeout=self.timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                print(f"[greenhouse] failed to fetch {slug}: {exc}")
                continue
            for raw in response.json().get("jobs", []):
                jobs.append(self._normalize(slug, raw))
        return jobs

    def _normalize(self, slug: str, raw: dict[str, Any]) -> Job:
        location_name = _location_string(raw.get("location"), raw.get("offices"))

        job_id = raw.get("internal_job_id") or raw.get("id")
        return Job(
            id=f"greenhouse:{slug}:{job_id}",
            company=self.company_names.get(slug, slug),
            title=compact_text(raw.get("title")),
            location=location_name or "Unspecified",
            url=compact_text(raw.get("absolute_url") or raw.get("hosted_url")),
            jd_text=html_to_text(raw.get("content")),
            posted_at=raw.get("first_published") or raw.get("updated_at"),
        )


def _location_string(location: Any, offices: Any) -> str:
    parts: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if value is None:
            return
        text = compact_text(value if isinstance(value, str) else str(value))
        if not text:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        parts.append(text)

    if isinstance(location, dict):
        add(location.get("name"))
    elif isinstance(location, str):
        add(location)
    for office in offices or []:
        if not isinstance(office, dict):
            continue
        add(office.get("name"))
        add(office.get("location"))
    return "; ".join(parts)
