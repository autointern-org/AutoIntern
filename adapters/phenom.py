from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


MAX_PAGES = 20


@dataclass
class PhenomBoard:
    company: str
    host: str
    variant: str = "widgets"


class PhenomAdapter:
    def __init__(
        self,
        boards: Iterable[PhenomBoard],
        *,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.boards = list(boards)
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for board in self.boards:
            if board.variant == "get":
                jobs.extend(self._fetch_get(board))
            else:
                jobs.extend(self._fetch_widgets(board))
        return jobs

    def _fetch_widgets(self, board: PhenomBoard) -> list[Job]:
        response = self.session.post(
            f"https://{board.host}/widgets",
            json={
                "ddoKey": "refineSearch",
                "pageName": "search-results",
                "keywords": "intern",
                "siteType": "external",
                "from": 0,
                "size": 50,
                "jobs": True,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = _widget_jobs(payload)
        return [self._normalize_widget(board, raw) for raw in rows if isinstance(raw, dict)]

    def _fetch_get(self, board: PhenomBoard) -> list[Job]:
        jobs: list[Job] = []
        for page in range(1, MAX_PAGES + 1):
            url = f"https://{board.host}/api/jobs?keywords=intern&limit=100&page={page}"
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("jobs") if isinstance(payload, dict) else payload
            if not isinstance(rows, list) or not rows:
                break
            for item in rows:
                jobs.append(self._normalize_get(board, item))
            if len(rows) < 100:
                break
        return jobs

    def _normalize_widget(self, board: PhenomBoard, raw: dict[str, Any]) -> Job:
        job_id = raw.get("jobId") or raw.get("jobSeqNo") or raw.get("reqId")
        return Job(
            id=f"phenom:{board.company}:{job_id}",
            company=board.company,
            title=compact_text(raw.get("title")),
            location=compact_text(str(raw.get("cityStateCountry") or raw.get("location") or "Unspecified")),
            url=compact_text(str(raw.get("applyUrl") or raw.get("jobUrl") or "")),
            jd_text=html_to_text(str(raw.get("descriptionTeaser") or raw.get("description") or "")),
            posted_at=raw.get("postedDate") or raw.get("posted_date"),
        )

    def _normalize_get(self, board: PhenomBoard, item: Any) -> Job:
        raw = item.get("data") if isinstance(item, dict) and isinstance(item.get("data"), dict) else item
        if not isinstance(raw, dict):
            raw = {}
        meta = raw.get("meta_data") if isinstance(raw.get("meta_data"), dict) else {}
        job_id = raw.get("slug") or raw.get("id") or raw.get("jobId")
        url = raw.get("apply_url") or meta.get("canonical_url") or raw.get("url") or ""
        return Job(
            id=f"phenom:{board.company}:{job_id}",
            company=board.company,
            title=compact_text(raw.get("title")),
            location=compact_text(str(raw.get("full_location") or raw.get("location") or "Unspecified")),
            url=compact_text(str(url)),
            jd_text=html_to_text(str(raw.get("description") or raw.get("teaser") or "")),
            posted_at=raw.get("posted_date") or raw.get("postedDate"),
        )


def _widget_jobs(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    for key in ("eagerLoadRefineSearch", "refineSearch"):
        block = payload.get(key)
        if isinstance(block, dict):
            data = block.get("data") if isinstance(block.get("data"), dict) else block
            jobs = data.get("jobs") if isinstance(data, dict) else None
            if isinstance(jobs, list):
                return jobs
    return []
