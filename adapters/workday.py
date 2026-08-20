from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


WorkdayBoard = tuple[str, str, str]


class WorkdayAdapter:
    def __init__(
        self,
        boards: Iterable[WorkdayBoard],
        *,
        company_names: dict[WorkdayBoard, str] | None = None,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.boards = list(boards)
        self.company_names = company_names or {}
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for board in self.boards:
            jobs.extend(self._fetch_board(board))
        return jobs

    def _fetch_board(self, board: WorkdayBoard) -> list[Job]:
        host, tenant, site = board
        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        jobs: list[Job] = []
        offset = 0
        limit = 20
        for _ in range(25):
            try:
                response = self.session.post(
                    url,
                    json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": "intern"},
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                print(f"[workday] failed to fetch {host}/{tenant}/{site}: {exc}")
                break
            payload = response.json()
            rows = _find_workday_jobs(payload)
            total = _as_int(payload.get("total")) if isinstance(payload, dict) else 0
            for raw in rows:
                jobs.append(self._normalize(board, raw))
            offset += limit
            if not rows or (total and offset >= total) or len(rows) < limit:
                break
        return jobs

    def _normalize(self, board: WorkdayBoard, raw: dict[str, Any]) -> Job:
        host, tenant, site = board
        job_id = raw.get("bulletFields", [None])[0] if isinstance(raw.get("bulletFields"), list) else None
        job_id = raw.get("jobReqId") or raw.get("requisitionId") or raw.get("id") or job_id or raw.get("externalPath")
        external_path = raw.get("externalPath") or raw.get("url")
        if external_path and str(external_path).startswith("/"):
            url = f"https://{host}{external_path}"
        else:
            url = str(external_path or f"https://{host}/wday/cxs/{tenant}/{site}/job/{job_id}")

        jd_text = " ".join(
            str(value)
            for value in (
                raw.get("description"),
                raw.get("summary"),
                raw.get("timeType"),
                raw.get("workerSubType"),
            )
            if value
        )
        return Job(
            id=f"workday:{tenant}:{site}:{job_id}",
            company=self.company_names.get(board, tenant),
            title=compact_text(raw.get("title")),
            location=compact_text(raw.get("locationsText") or raw.get("location") or "Unspecified"),
            url=compact_text(url),
            jd_text=html_to_text(jd_text),
            posted_at=raw.get("postedOn") or raw.get("startDate") or raw.get("postedDate"),
        )


def _find_workday_jobs(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("jobPostings", "jobs", "postings"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
