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
        location = raw.get("location") or {}
        offices = raw.get("offices") or []
        office_locations = [
            compact_text(office.get("location") or office.get("name"))
            for office in offices
            if isinstance(office, dict)
        ]
        location_name = compact_text(location.get("name")) if isinstance(location, dict) else ""
        if not location_name and office_locations:
            location_name = ", ".join(item for item in office_locations if item)

        return Job(
            id=f"greenhouse:{slug}:{raw.get('id')}",
            company=self.company_names.get(slug, slug),
            title=compact_text(raw.get("title")),
            location=location_name or "Unspecified",
            url=compact_text(raw.get("absolute_url") or raw.get("hosted_url")),
            jd_text=html_to_text(raw.get("content")),
            posted_at=raw.get("first_published") or raw.get("updated_at"),
        )
