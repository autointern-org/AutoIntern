from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


class GemAdapter:
    """Gem job boards (jobs.gem.com/<slug>): the public job-board API returns
    every published posting with its description in one request."""

    API = "https://api.gem.com/job_board/v0/{slug}/job_posts/"

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
        self.session = session or new_session()
        self.board_errors: list[tuple[str, str]] = []
        self.listing_counts: dict[str, int] = {}

    def fetch(self) -> list[Job]:
        self.board_errors = []
        self.listing_counts = {}
        jobs: list[Job] = []
        for slug in self.board_slugs:
            name = self.company_names.get(slug, slug)
            try:
                response = self.session.get(self.API.format(slug=slug), timeout=self.timeout)
                response.raise_for_status()
                rows = response.json()
            except Exception as exc:
                print(f"[gem] {name} fetch failed: {exc}")
                self.board_errors.append((name, str(exc)))
                continue
            rows = rows if isinstance(rows, list) else (rows.get("job_posts") or [] if isinstance(rows, dict) else [])
            self.listing_counts[name] = len(rows)
            jobs.extend(self._normalize(slug, name, raw) for raw in rows if isinstance(raw, dict))
        return jobs

    def _normalize(self, slug: str, name: str, raw: dict[str, Any]) -> Job:
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        offices = raw.get("offices") if isinstance(raw.get("offices"), list) else []
        office_names = [
            compact_text(str((office.get("location") or {}).get("name") or office.get("name") or ""))
            for office in offices
            if isinstance(office, dict)
        ]
        location_text = compact_text(str(location.get("name") or "")) or "; ".join(n for n in office_names if n)
        if not location_text and raw.get("location_type") == "remote":
            location_text = "Remote"
        return Job(
            id=f"gem:{slug}:{raw.get('id')}",
            company=name,
            title=compact_text(str(raw.get("title") or "")),
            location=location_text or "Unspecified",
            url=compact_text(str(raw.get("absolute_url") or f"https://jobs.gem.com/{slug}")),
            jd_text=compact_text(str(raw.get("content_plain") or "")) or html_to_text(str(raw.get("content") or "")),
            posted_at=raw.get("first_published_at") or raw.get("created_at"),
            location_names=tuple(n for n in office_names if n),
        )
