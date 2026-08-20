from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text, posted_at_value
from core.http import new_session


class LeverAdapter:
    API = "https://api.lever.co/v0/postings/{slug}?mode=json"

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

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for slug in self.board_slugs:
            response = self.session.get(self.API.format(slug=slug), timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            rows = payload if isinstance(payload, list) else payload.get("data") or []
            for raw in rows:
                if isinstance(raw, dict):
                    jobs.append(self._normalize(slug, raw))
        return jobs

    def _normalize(self, slug: str, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id")
        categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
        location = categories.get("location") or raw.get("location") or "Unspecified"
        description = raw.get("descriptionPlain") or raw.get("description") or raw.get("content") or ""
        return Job(
            id=f"lever:{slug}:{job_id}",
            company=self.company_names.get(slug, slug),
            title=compact_text(raw.get("text") or raw.get("title")),
            location=compact_text(str(location)),
            url=compact_text(str(raw.get("hostedUrl") or raw.get("applyUrl") or "")),
            jd_text=html_to_text(str(description)),
            posted_at=posted_at_value(raw.get("createdAt")),
        )
